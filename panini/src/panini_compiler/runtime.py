"""Sequential reference runtime with durable effects and guarded state commits.

Remote idempotency/reconciliation and authorization are trusted adapter contracts.
Deadlines bound scheduler waiting, not remote execution or transaction rollback.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import multiprocessing
from threading import TIMEOUT_MAX
from typing import Callable
from uuid import uuid4

from .effects import (
    Capability,
    IsolatedTask,
    EffectResult,
    Reconciliation,
    InvocationContext,
    AuthorizationRequest,
    validate_effect,
)
from .execution import InvocationTimeout, isolated_call, capability_call
from .journal import SQLiteJournal, JournalConflict

from .decisions import decide
from .ir import CheckResult, PROFILE
from .resolver import decision_enum, nodes, text, tokens
from .verifier import verify_graph


class RuntimeFailure(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def evaluate(expression: dict, state: dict, parameter: str, symbols: dict) -> object:
    """Interpret only the compiler's pure guard/input subset, with short circuiting."""
    kind = expression["kind"]
    children = nodes(expression)
    if kind in {"Integer", "Float", "String"}:
        return json.loads(text(expression))
    if kind == "True":
        return True
    if kind == "False":
        return False
    if kind == "Null":
        return None
    if kind == "Atom":
        return evaluate(children[0], state, parameter, symbols)
    if kind == "Name" and text(expression) == parameter:
        return state
    if kind == "Member":
        symbol = symbols.get(expression.get("resolvedSymbol"), {})
        if symbol.get("kind") == "EnumMember":
            return symbol["name"].split(".")[-1]
        base = evaluate(children[0], state, parameter, symbols)
        return base[tokens(expression, "NAME")[0]["text"]]
    if kind == "Unary":
        value = evaluate(children[0], state, parameter, symbols)
        if expression["operator"] == "!":
            return not value
        if expression["operator"] == "-":
            return -value
    if kind == "Binary":
        op = expression["operator"]
        left = evaluate(children[0], state, parameter, symbols)
        if op == "&&":
            return left and evaluate(children[1], state, parameter, symbols)
        if op == "||":
            return left or evaluate(children[1], state, parameter, symbols)
        right = evaluate(children[1], state, parameter, symbols)
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        if op == "<":
            return left < right
        if op == "<=":
            return left <= right
        if op == ">":
            return left > right
        if op == ">=":
            return left >= right
    raise RuntimeFailure(
        "UnsupportedExpression", f"Unsupported runtime expression {kind}"
    )


class Executor:
    def __init__(
        self,
        program: CheckResult | dict,
        graph: str,
        state: dict,
        *,
        capabilities: dict[str, Callable] | None = None,
        adapters: dict[str, Callable] | None = None,
        authorizer: Callable | None = None,
        journal: SQLiteJournal | None = None,
        run_id: str | None = None,
        _restoring: bool = False,
    ):
        self._program = deepcopy(
            program.to_dict() if isinstance(program, CheckResult) else program
        )
        if (
            self._program.get("schemaVersion") != 3
            or self._program.get("profile") != PROFILE
            or self._program.get("diagnostics")
        ):
            raise RuntimeFailure("InvalidProgram", "Expected error-free PNIR v3")
        self._symbols = {s["id"]: s for s in self._program["symbols"]}
        matches = [g for g in self._program["graphs"] if g["name"] == graph]
        if len(matches) != 1:
            raise RuntimeFailure("InvalidProgram", "Select exactly one declared graph")
        self._graph = matches[0]
        diagnostics, report = verify_graph(self._graph)
        if diagnostics:
            raise RuntimeFailure("InvalidProgram", diagnostics[0].message)
        self._report = report
        self._node_map = {n["id"]: n for n in self._graph["nodes"]}
        self._transitions = {t["source"]: t for t in self._graph["transitions"]}
        self._limits = {}
        for node in self._graph["nodes"]:
            self._limits[node["id"]] = next(
                (
                    c["value"]
                    for c in node["contracts"]
                    if c["kind"] == "MaxVisitsContract"
                ),
                1,
            )
        self._fingerprint = hashlib.sha256(
            json.dumps(self._program, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()
        self._capabilities = dict(capabilities or {})
        self._adapters = dict(adapters or {})
        for node in self._graph["nodes"]:
            method = self._symbols[node["callable"]]
            effect = method.get("effect")
            if effect:
                try:
                    validate_effect(effect)
                except ValueError as error:
                    raise RuntimeFailure("UnsupportedContract", str(error)) from error
            timeout = self._contract(node, "TimeoutContract")
            if timeout:
                seconds = (
                    timeout["value"]
                    * {"seconds": 1, "minutes": 60, "hours": 3600}[timeout["unit"]]
                )
                if not 0 < seconds <= TIMEOUT_MAX:
                    raise RuntimeFailure(
                        "UnsupportedDeadline",
                        "Artifact deadline exceeds this runtime's wait limit",
                    )
            callback = self._capabilities.get(method["name"])
            if (
                (timeout or isinstance(callback, IsolatedTask))
                and not effect
                and "decision" not in method
            ):
                if "fork" not in multiprocessing.get_all_start_methods():
                    raise RuntimeFailure(
                        "UnsupportedIsolation",
                        "Local process deadlines require a POSIX fork backend",
                    )
        self._state = deepcopy(state)
        self._validate(self._state, self._symbols[self._graph["stateType"]]["name"])
        self._counts = {node: 0 for node in self._node_map}
        self._next = self._graph["starts"][0]["target"]
        self._status = "ready"
        self._failure = None
        self._busy = False
        self._authorizer = authorizer
        self._journal = journal
        self._revision = 0
        self._run_id = run_id or uuid4().hex
        self._history: list[dict] = []
        self._pending: str | None = None
        self._events: list[dict] = []
        self._recovery_needed = False
        for node in self._graph["nodes"]:
            method = self._symbols[node["callable"]]
            effect = method.get("effect")
            if effect and effect["kind"] == "mutation" and journal is None:
                raise RuntimeFailure(
                    "DurabilityRequired",
                    "Mutating capabilities require a SQLite journal",
                )
            if self._contract(node, "CompensateContract") and not effect:
                raise RuntimeFailure(
                    "UnsupportedContract",
                    "Compensation execution requires a declared mutation effect",
                )
        if not _restoring:
            self._invariants(self._state)
            self._finite_entry()
            self._persist("created")

    def _validate(self, value: object, type_name: str, depth: int = 0) -> None:
        if depth > 100:
            raise RuntimeFailure(
                "InvalidState", "State nesting exceeds reference-runtime limit (100)"
            )
        if type_name == "bool" and type(value) is bool:
            return
        if type_name == "int" and type(value) is int and -(2**63) <= value < 2**63:
            return
        if type_name == "float" and type(value) in {int, float}:
            try:
                if math.isfinite(value):
                    return
            except OverflowError:
                pass
        if type_name == "string" and isinstance(value, str):
            try:
                value.encode("utf-8")
                return
            except UnicodeError:
                pass
        enum = decision_enum(type_name)
        if enum is not None:
            self._validate_decision(value, enum)
            return
        declarations = [
            s
            for s in self._symbols.values()
            if s["name"] == type_name and s["kind"] in {"ClassDecl", "EnumDecl"}
        ]
        if declarations:
            declaration = declarations[0]
            if declaration["kind"] == "EnumDecl":
                labels = {
                    s["name"].split(".")[-1]
                    for s in self._symbols.values()
                    if s.get("owner") == declaration["id"] and s["kind"] == "EnumMember"
                }
                if isinstance(value, str) and value in labels:
                    return
            elif isinstance(value, dict):
                fields = [
                    s
                    for s in self._symbols.values()
                    if s.get("owner") == declaration["id"] and s["kind"] == "Field"
                ]
                if set(value) == {s["name"].split(".")[-1] for s in fields}:
                    for field in fields:
                        self._validate(
                            value[field["name"].split(".")[-1]],
                            field["type"],
                            depth + 1,
                        )
                    return
        raise RuntimeFailure("InvalidState", f"Value does not match {type_name}")

    def _validate_decision(self, value: object, enum: str) -> None:
        if (
            not isinstance(value, dict)
            or set(value)
            != {"choice", "abstained", "probabilities", "model", "modelVersion"}
            or type(value["abstained"]) is not bool
            or not isinstance(value["model"], str)
            or not isinstance(value["modelVersion"], str)
        ):
            raise RuntimeFailure("InvalidState", "Malformed Decision value")
        labels = [
            s["name"].split(".")[-1]
            for s in self._symbols.values()
            if s["kind"] == "EnumMember" and s["type"] == enum
        ]
        spec = {
            "model": value["model"],
            "version": value["modelVersion"],
            "threshold": 0.0,
            "options": [{"name": label} for label in labels],
        }
        try:
            checked = decide(
                spec,
                {
                    "probabilities": value["probabilities"],
                    "modelVersion": value["modelVersion"],
                },
            )
        except ValueError as error:
            raise RuntimeFailure(
                "InvalidState", "Invalid Decision probabilities"
            ) from error
        if value["abstained"]:
            if value["choice"] is not None:
                raise RuntimeFailure(
                    "InvalidState", "An abstained decision cannot have a choice"
                )
        elif value["choice"] != checked.choice:
            # Tie ordering of incoming state may differ from a method's option order;
            # any actual maximal label is valid in externally supplied state.
            if value["choice"] not in labels or value["probabilities"][
                value["choice"]
            ] != max(value["probabilities"].values()):
                raise RuntimeFailure(
                    "InvalidState", "Decision choice must be a maximal option"
                )

    @property
    def state(self) -> dict:
        return deepcopy(self._state)

    @property
    def status(self) -> str:
        return self._status

    @property
    def failure(self) -> dict | None:
        return deepcopy(self._failure)

    @property
    def dispatches(self) -> int:
        return sum(self._counts.values())

    @property
    def history(self) -> list[dict]:
        return deepcopy(self._history)

    @property
    def run_id(self) -> str:
        return self._run_id

    @staticmethod
    def _contract(node: dict, kind: str) -> dict | None:
        return next((c for c in node["contracts"] if c["kind"] == kind), None)

    def _invariants(self, state: dict) -> None:
        for expression in self._graph.get("invariants", []):
            if (
                evaluate(
                    expression, state, self._graph["stateParameter"], self._symbols
                )
                is not True
            ):
                raise RuntimeFailure(
                    "InvariantViolation",
                    f"Invariant at byte {expression['span']['start']} failed",
                )

    def _policy(self) -> bool:
        self._invariants(self._state)
        for policy in self._graph.get("policies", []):
            for rule in policy["rules"]:
                if evaluate(
                    rule["predicate"],
                    self._state,
                    self._graph["stateParameter"],
                    self._symbols,
                ):
                    action = rule["action"]
                    if action["kind"] == "end":
                        self._next = "$end"
                        self._status = "ended"
                        return True
                    message = evaluate(
                        action["expression"],
                        self._state,
                        self._graph["stateParameter"],
                        self._symbols,
                    )
                    raise RuntimeFailure("PolicyViolation", message)
        return False

    def _finite_entry(self) -> None:
        analysis = self._program.get("analysis")
        if (
            analysis
            and analysis.get("status") == "proved"
            and analysis.get("graph") == self._graph["name"]
        ):
            from .modelcheck import validate_runtime_entry

            validate_runtime_entry(self._program, analysis["contract"], self._state)

    def _finite_transition(
        self, method: dict, before: dict, after: dict, binding=None
    ) -> None:
        analysis = self._program.get("analysis")
        if (
            analysis
            and analysis.get("status") == "proved"
            and analysis.get("graph") == self._graph["name"]
        ):
            from .modelcheck import validate_runtime_transition

            validate_runtime_transition(
                self._program, analysis["contract"], method, before, after, binding
            )

    def _context(
        self, record: dict, phase: str, *, recovery=False
    ) -> InvocationContext:
        suffix = ":compensate" if recovery else ""
        return InvocationContext(
            self._run_id,
            record["id"] + suffix,
            record["id"] + suffix,
            self._revision,
            phase,
            deepcopy(record.get("receipt")),
            deepcopy(record["input"]),
        )

    def _authorize(
        self, effect: dict, phase: str, context: InvocationContext, state: dict
    ) -> None:
        request = AuthorizationRequest(
            effect["capability"],
            phase,
            context,
            deepcopy(state),
            effect.get("irreversiblePolicy"),
        )
        if self._authorizer is None or self._authorizer(request) is not True:
            raise RuntimeFailure(
                "AuthorizationDenied",
                f"Capability denied at {phase}: {effect['capability']}",
            )

    def _snapshot(self) -> dict:
        return deepcopy(
            {
                "schemaVersion": 2,
                "program": self._fingerprint,
                "run_id": self._run_id,
                "revision": self._revision,
                "graph": self._graph["name"],
                "state": self._state,
                "counts": self._counts,
                "dispatches": self.dispatches,
                "next": self._next,
                "status": self._status,
                "failure": self._failure,
                "history": self._history,
                "pending": self._pending,
                "recovery_needed": self._recovery_needed,
            }
        )

    def _persist(self, kind: str, record: dict | None = None) -> None:
        event = {"kind": kind, "status": self._status}
        if record is not None:
            event["invocation"] = record["id"]
        if self._journal:
            snapshot = self._snapshot()
            snapshot["revision"] = self._revision + 1
            self._revision = self._journal.save(
                self._run_id, snapshot, self._revision, event
            )
        else:
            self._revision += 1
        self._events.append(event)

    def _fail(self, code: str, message: str) -> None:
        self._status = "failed"
        self._failure = {"code": code, "message": message}
        self._recovery_needed = any(
            r.get("effect", {}).get("kind") == "mutation" and r["status"] == "completed"
            for r in self._history
        )

    def _handler(self, record: dict, reason: str) -> str | None:
        node = self._node_map[record["node"]]
        kind = "TimeoutContract" if reason == "timeout" else "OnErrorContract"
        contract = self._contract(node, kind)
        return contract.get("target") if contract else None

    def _known_failure(
        self, record: dict, reason: str, code: str, message: str
    ) -> None:
        record["status"] = "failed"
        self._pending = None
        target = self._handler(record, reason)
        if target is not None:
            self._next = target
            self._status = "ended" if target == "$end" else "ready"
            self._failure = None
        else:
            self._fail(code, message)

    def _uncertain(self, record: dict, reason: str) -> None:
        record["status"] = "uncertain"
        record["reason"] = reason
        self._pending = record["id"]
        self._status = "pending"
        self._failure = {
            "code": "OutcomeUnknown",
            "message": "Reconcile the invocation key before continuing",
        }

    def _invoke(self, record: dict, node: dict, method: dict):
        effect = method.get("effect")
        timeout_contract = self._contract(node, "TimeoutContract")
        timeout = None
        if timeout_contract:
            timeout = (
                timeout_contract["value"]
                * {"seconds": 1, "minutes": 60, "hours": 3600}[timeout_contract["unit"]]
            )
        if "decision" in method:
            spec = method["decision"]
            callback = self._adapters.get(spec["model"])
            if callback is None:
                raise RuntimeFailure("MissingAdapter", "No registered model adapter")
            prompt = evaluate(
                spec["input"], record["input"], spec["parameter"], self._symbols
            )
            args = (prompt, deepcopy(spec["options"]))
            response = (
                capability_call(callback, args, timeout) if timeout else callback(*args)
            )
            return decide(spec, response).to_dict()
        callback = self._capabilities.get(method["name"])
        if callback is None:
            raise RuntimeFailure(
                "MissingCapability", f"No callback for {method['name']}"
            )
        if effect:
            if not isinstance(callback, Capability) or (
                effect["kind"] == "mutation" and callback.reconcile is None
            ):
                raise RuntimeFailure(
                    "UnsupportedBinding",
                    "Effect requires a Capability binding and mutation reconciler",
                )
            context = self._context(record, "invoke")
            self._authorize(effect, "invoke", context, record["input"])
            record["invoked"] = True
            self._persist("effect_started", record)
            context = self._context(record, "invoke")
            limit = effect.get("timeoutSeconds", timeout or 1.0)
            if timeout:
                limit = min(limit, timeout)
            try:
                return capability_call(
                    callback.invoke,
                    (context, deepcopy(record["input"])),
                    limit,
                    context.cancel_requested,
                )
            except InvocationTimeout:
                record["cancellation"] = {
                    "requested": context.cancel_requested.is_set(),
                    "acknowledged": context.cancel_acknowledged.is_set(),
                }
                raise
        function = callback.invoke if isinstance(callback, IsolatedTask) else callback
        if timeout or isinstance(callback, IsolatedTask):
            return isolated_call(function, (deepcopy(record["input"]),), timeout or 1.0)
        return function(deepcopy(record["input"]))

    def _complete(self, record: dict, output: object, route: str | None = None) -> None:
        node = self._node_map[record["node"]]
        method = self._symbols[node["callable"]]
        effect = method.get("effect")
        if effect and effect["kind"] == "mutation":
            if not isinstance(output, EffectResult):
                self._uncertain(record, record.get("reason", "error"))
                return
            try:
                self._validate(output.receipt, effect["receiptType"])
            except RuntimeFailure:
                self._uncertain(record, record.get("reason", "error"))
                return
            if (
                record.get("known_completed")
                and record.get("receipt") != output.receipt
            ):
                raise RuntimeFailure(
                    "ConflictingReconciliation",
                    "Receipt changed for a known completed invocation",
                )
            record["receipt"] = deepcopy(output.receipt)
            record["known_completed"] = True
            try:
                json.dumps(output.value, allow_nan=False)
                record["output"] = deepcopy(output.value)
            except (ValueError, TypeError):
                record.pop("output", None)
            record["status"] = "completed"
            self._persist("effect_completed", record)
            output = output.value
        if "outputBinding" in node:
            binding = node["outputBinding"]
            self._validate(output, binding["type"])
            updated = deepcopy(self._state)
            updated[binding["field"]] = deepcopy(output)
        else:
            updated = deepcopy(output)
        self._validate(updated, self._symbols[self._graph["stateType"]]["name"])
        self._invariants(updated)
        self._finite_transition(
            method, record["input"], updated, node.get("outputBinding")
        )
        if effect:
            self._authorize(effect, "commit", self._context(record, "commit"), updated)
        branches = self._transitions[node["id"]]["branches"]
        if route is not None:
            target = route
        elif "guard" in branches[0]:
            condition = evaluate(
                branches[0]["guard"],
                updated,
                self._graph["stateParameter"],
                self._symbols,
            )
            target = branches[0 if condition else 1]["target"]
        else:
            target = branches[0]["target"]
        self._state = updated
        self._next = target
        self._pending = None
        record["status"] = "completed"
        self._status = "ended" if target == "$end" else "ready"
        self._failure = None
        self._policy()

    def step(self) -> str:
        if self._busy:
            raise RuntimeFailure(
                "ReentrantDispatch", "Only one invocation may be active"
            )
        if self._status != "ready":
            return self._status
        self._busy = True
        record = None
        try:
            if self._policy():
                self._persist("policy_end")
                return self._status
            node = self._node_map[self._next]
            if self._counts[node["id"]] >= self._limits[node["id"]]:
                self._fail(
                    "BudgetExceeded", f"Dispatch budget exhausted for {node['name']}"
                )
            else:
                self._counts[node["id"]] += 1
                method = self._symbols[node["callable"]]
                record = {
                    "id": f"{self._run_id}:{node['id']}:{self._counts[node['id']]}",
                    "node": node["id"],
                    "method": method["id"],
                    "input": self.state,
                    "status": "scheduled",
                    "invoked": False,
                    "reconciliations": 0,
                }
                if method.get("effect"):
                    record["effect"] = deepcopy(method["effect"])
                compensation = self._contract(node, "CompensateContract")
                if compensation:
                    record["compensator"] = compensation["callable"]
                self._history.append(record)
                self._pending = record["id"]
                self._persist("dispatch_scheduled", record)
                record["status"] = "started"
                self._persist("dispatch_started", record)
                try:
                    output = self._invoke(record, node, method)
                except InvocationTimeout:
                    if (
                        record.get("effect", {}).get("kind") == "mutation"
                        and record["invoked"]
                    ):
                        self._uncertain(record, "timeout")
                    else:
                        self._known_failure(
                            record, "timeout", "Timeout", "Node deadline expired"
                        )
                except RuntimeFailure:
                    if (
                        record.get("effect", {}).get("kind") == "mutation"
                        and record["invoked"]
                    ):
                        self._uncertain(record, "error")
                    else:
                        raise
                except Exception as error:
                    if (
                        record.get("effect", {}).get("kind") == "mutation"
                        and record["invoked"]
                    ):
                        self._uncertain(record, "error")
                    else:
                        self._known_failure(
                            record,
                            "error",
                            "NodeFailed",
                            f"Invocation failed ({type(error).__name__})",
                        )
                else:
                    self._complete(record, output)
        except JournalConflict:
            self._status = "stale"
            self._failure = {
                "code": "StaleRun",
                "message": "Another executor advanced the journal revision",
            }
        except RuntimeFailure as error:
            self._pending = None
            self._fail(error.code, str(error))
        except Exception as error:
            self._pending = None
            self._fail(
                "NodeFailed",
                f"Invocation or validation failed ({type(error).__name__})",
            )
        finally:
            self._busy = False
        if self._status != "stale":
            try:
                self._persist("step_finished", record)
                if self._recovery_needed:
                    self.recover()
            except JournalConflict:
                self._status = "stale"
                self._failure = {
                    "code": "StaleRun",
                    "message": "Journal revision changed",
                }
        return self._status

    def run(self) -> dict:
        while self._status == "ready":
            self.step()
        return {
            "status": self.status,
            "failure": self.failure,
            "state": self.state,
            "dispatches": self.dispatches,
        }

    def reconcile(self) -> str:
        if self._busy:
            raise RuntimeFailure(
                "ReentrantDispatch", "Cannot reconcile an active executor"
            )
        if self._status != "pending" or self._pending is None:
            return self._status
        record = next(r for r in self._history if r["id"] == self._pending)
        effect = record.get("effect", {})
        if effect.get("kind") != "mutation":
            self._known_failure(
                record, "error", "Interrupted", "Non-mutating invocation interrupted"
            )
            self._persist("interrupted", record)
            if self._recovery_needed:
                self.recover()
            return self._status
        if record["reconciliations"] >= effect["maxReconciliations"]:
            self._failure = {
                "code": "ReconciliationExhausted",
                "message": "Outcome remains unknown; manual reconciliation required",
            }
            self._persist("reconciliation_exhausted", record)
            return self._status
        self._busy = True
        try:
            record["reconciliations"] += 1
            self._persist("reconcile_started", record)
            context = self._context(record, "reconcile")
            self._authorize(effect, "reconcile", context, self._state)
            binding = self._capabilities.get(self._symbols[record["method"]]["name"])
            if not isinstance(binding, Capability) or binding.reconcile is None:
                raise RuntimeFailure(
                    "MissingReconciler", "Mutation requires a reconciliation binding"
                )
            outcome = capability_call(
                binding.reconcile,
                (context,),
                effect["reconcileTimeoutSeconds"],
                context.cancel_requested,
            )
            if not isinstance(outcome, Reconciliation) or outcome.status not in {
                "completed",
                "not_committed",
                "unknown",
            }:
                raise RuntimeFailure(
                    "InvalidReconciliation",
                    "Expected an explicit final or unknown outcome",
                )
            if outcome.status == "completed":
                route = self._handler(record, record.get("reason", "error"))
                self._complete(record, outcome.result, route)
            elif outcome.status == "unknown":
                self._failure = {
                    "code": "OutcomeUnknown",
                    "message": "Reconciliation did not establish a final outcome",
                }
            elif outcome.status == "not_committed":
                if record.get("known_completed"):
                    raise RuntimeFailure(
                        "ConflictingReconciliation",
                        "Final non-commit contradicts a durable completion receipt",
                    )
                self._known_failure(
                    record,
                    record.get("reason", "error"),
                    "NodeFailed",
                    "Reconciled final non-commit",
                )
        except JournalConflict:
            self._status = "stale"
            self._failure = {"code": "StaleRun", "message": "Journal revision changed"}
        except RuntimeFailure as error:
            if record["status"] == "completed":
                self._pending = None
                self._fail(error.code, str(error))
            else:
                self._failure = {"code": error.code, "message": str(error)}
        except Exception as error:
            self._failure = {
                "code": "ReconciliationFailed",
                "message": type(error).__name__,
            }
        finally:
            self._busy = False
        if self._status != "stale":
            self._persist("reconcile_finished", record)
            if self._recovery_needed:
                self.recover()
        return self._status

    def recover(self) -> str:
        if self._busy:
            raise RuntimeFailure(
                "ReentrantDispatch", "Cannot compensate an active invocation"
            )
        if self._status not in {"failed", "recovery_failed", "recovery_pending"}:
            return self._status
        self._busy = True
        failed = False
        pending = False
        try:
            for record in reversed(self._history):
                if (
                    record.get("effect", {}).get("kind") != "mutation"
                    or record["status"] == "compensated"
                ):
                    continue
                if record["status"] != "completed":
                    continue
                if "compensator" not in record:
                    failed = True
                    record["recoveryIssue"] = (
                        "Irreversible action requires manual recovery"
                    )
                    continue
                method = self._symbols[record["compensator"]]
                effect = method["effect"]
                recovery = record.setdefault(
                    "recovery", {"attempts": 0, "reconciliations": 0, "status": "ready"}
                )
                binding = self._capabilities.get(method["name"])
                try:
                    if not isinstance(binding, Capability) or binding.reconcile is None:
                        raise RuntimeFailure(
                            "MissingCompensator",
                            "Recovery requires capability and reconciler bindings",
                        )
                    if recovery["status"] == "uncertain":
                        if recovery["reconciliations"] >= effect["maxReconciliations"]:
                            recovery["issue"] = {
                                "code": "ReconciliationExhausted",
                                "message": "Compensation outcome remains unknown; manual reconciliation required",
                            }
                            pending = True
                            break
                        recovery["reconciliations"] += 1
                        self._persist("compensation_reconcile_started", record)
                        context = self._context(record, "reconcile", recovery=True)
                        self._authorize(effect, "reconcile", context, self._state)
                        outcome = capability_call(
                            binding.reconcile,
                            (context,),
                            effect["reconcileTimeoutSeconds"],
                            context.cancel_requested,
                        )
                        if not isinstance(outcome, Reconciliation):
                            raise RuntimeFailure(
                                "InvalidReconciliation",
                                "Expected a compensation outcome",
                            )
                        if outcome.status == "completed":
                            result = outcome.result
                        elif outcome.status == "not_committed":
                            if recovery.get("known_completed"):
                                raise RuntimeFailure(
                                    "ConflictingReconciliation",
                                    "Non-commit contradicts a durable compensation receipt",
                                )
                            recovery["status"] = "ready"
                            result = None
                        else:
                            pending = True
                            break
                    else:
                        result = None
                    if result is None:
                        if recovery["attempts"] >= effect["maxAttempts"]:
                            recovery["issue"] = {
                                "code": "CompensationExhausted",
                                "message": "Recovery attempt budget exhausted",
                            }
                            failed = True
                            break
                        recovery["attempts"] += 1
                        recovery["input"] = self.state
                        self._persist("compensation_scheduled", record)
                        context = self._context(record, "compensate", recovery=True)
                        self._authorize(effect, "compensate", context, self._state)
                        recovery["status"] = "uncertain"
                        self._persist("compensation_started", record)
                        context = self._context(record, "compensate", recovery=True)
                        result = capability_call(
                            binding.invoke,
                            (context, self.state),
                            effect["timeoutSeconds"],
                            context.cancel_requested,
                        )
                    if not isinstance(result, EffectResult):
                        raise RuntimeFailure(
                            "InvalidReceipt", "Recovery requires EffectResult"
                        )
                    self._validate(result.receipt, effect["receiptType"])
                    if (
                        recovery.get("known_completed")
                        and recovery.get("receipt") != result.receipt
                    ):
                        raise RuntimeFailure(
                            "ConflictingReconciliation", "Compensation receipt changed"
                        )
                    recovery["known_completed"] = True
                    recovery["receipt"] = deepcopy(result.receipt)
                    self._persist("compensation_effect_completed", record)
                    self._validate(
                        result.value, self._symbols[self._graph["stateType"]]["name"]
                    )
                    self._invariants(result.value)
                    self._finite_transition(
                        method, recovery.get("input", self._state), result.value
                    )
                    self._authorize(
                        effect,
                        "commit",
                        self._context(record, "commit", recovery=True),
                        result.value,
                    )
                    self._state = deepcopy(result.value)
                    record["status"] = "compensated"
                    recovery["status"] = "completed"
                    recovery["receipt"] = deepcopy(result.receipt)
                    self._persist("compensation_completed", record)
                except Exception as error:
                    if isinstance(error, InvocationTimeout):
                        recovery["cancellation"] = {
                            "requested": context.cancel_requested.is_set(),
                            "acknowledged": context.cancel_acknowledged.is_set(),
                        }
                    recovery["issue"] = {
                        "code": getattr(error, "code", type(error).__name__),
                        "message": str(error),
                    }
                    if recovery["status"] == "uncertain":
                        pending = True
                    else:
                        failed = True
                    # Stop to preserve reverse completion order; never compensate
                    # earlier actions past an unresolved later compensation.
                    break
            if pending:
                self._status = "recovery_pending"
            elif failed:
                self._status = "recovery_failed"
            else:
                self._status = "failed"
                self._recovery_needed = False
            self._persist("recovery_finished")
        except JournalConflict:
            self._status = "stale"
            self._failure = {"code": "StaleRun", "message": "Journal revision changed"}
        finally:
            self._busy = False
        return self._status

    def checkpoint(self) -> dict:
        if self._busy or self._status == "stale":
            raise RuntimeFailure(
                "InvalidCheckpoint",
                "Checkpoint only between invocations of a current executor",
            )
        return self._snapshot()

    @classmethod
    def restore(
        cls, program: CheckResult | dict, checkpoint: dict, **bindings
    ) -> Executor:
        saved = deepcopy(checkpoint)
        required = {
            "schemaVersion",
            "program",
            "run_id",
            "revision",
            "graph",
            "state",
            "counts",
            "dispatches",
            "next",
            "status",
            "failure",
            "history",
            "pending",
            "recovery_needed",
        }
        if (
            not isinstance(saved, dict)
            or not required <= set(saved)
            or saved.get("schemaVersion") != 2
            or not isinstance(saved.get("run_id"), str)
            or not saved["run_id"]
        ):
            raise RuntimeFailure(
                "InvalidCheckpoint", "Expected complete checkpoint schema v2"
            )
        executor = cls(
            program,
            saved["graph"],
            saved["state"],
            run_id=saved["run_id"],
            _restoring=True,
            **bindings,
        )
        counts = saved.get("counts")
        if (
            saved.get("program") != executor._fingerprint
            or not isinstance(counts, dict)
            or set(counts) != set(executor._counts)
            or any(
                type(n) is not int or n < 0 or n > executor._limits[k]
                for k, n in counts.items()
            )
            or type(saved.get("dispatches")) is not int
            or saved["dispatches"] != sum(counts.values())
            or saved.get("next") not in {*executor._node_map, "$end"}
            or saved.get("status")
            not in {
                "ready",
                "ended",
                "failed",
                "pending",
                "recovery_pending",
                "recovery_failed",
            }
            or not isinstance(saved.get("history"), list)
            or type(saved.get("revision")) is not int
            or saved["revision"] < 0
            or type(saved.get("recovery_needed")) is not bool
            or (saved["status"] == "ready" and saved["next"] == "$end")
            or (saved["status"] == "ended" and saved["next"] != "$end")
        ):
            raise RuntimeFailure(
                "InvalidCheckpoint", "Checkpoint does not match program or budgets"
            )
        history_counts = {node: 0 for node in executor._node_map}
        for record in saved["history"]:
            if (
                not isinstance(record, dict)
                or record.get("node") not in executor._node_map
            ):
                raise RuntimeFailure("InvalidCheckpoint", "Invalid invocation history")
            node = executor._node_map[record["node"]]
            history_counts[node["id"]] += 1
            expected_id = f"{saved['run_id']}:{node['id']}:{history_counts[node['id']]}"
            effect = executor._symbols[node["callable"]].get("effect")
            compensation = executor._contract(node, "CompensateContract")
            if (
                record.get("id") != expected_id
                or record.get("method") != node["callable"]
                or record.get("status")
                not in {
                    "scheduled",
                    "started",
                    "failed",
                    "uncertain",
                    "completed",
                    "compensated",
                }
                or record.get("effect") != effect
                or record.get("compensator")
                != (compensation["callable"] if compensation else None)
            ):
                raise RuntimeFailure(
                    "InvalidCheckpoint",
                    "Invocation identities/contracts do not match the program",
                )
        if history_counts != counts:
            raise RuntimeFailure(
                "InvalidCheckpoint", "Dispatch counters do not match invocation history"
            )
        for key in [
            "counts",
            "next",
            "status",
            "failure",
            "history",
            "pending",
            "revision",
            "recovery_needed",
        ]:
            setattr(executor, "_" + key, saved[key])
        # A crash after scheduling is conservatively uncertain. Never blindly
        # invoke the same external mutation again, even with an idempotency key.
        if executor._pending:
            record = next(
                (r for r in executor._history if r["id"] == executor._pending), None
            )
            if record is None:
                raise RuntimeFailure(
                    "InvalidCheckpoint",
                    "Pending invocation missing from journal history",
                )
            executor._status = "pending"
            record["status"] = "uncertain"
        executor._invariants(executor._state)
        analysis = executor._program.get("analysis")
        if analysis and analysis.get("status") == "proved":
            from .modelcheck import validate_runtime_state

            validate_runtime_state(
                executor._program, analysis["contract"], executor._state
            )
        return executor

    @classmethod
    def resume(
        cls,
        program: CheckResult | dict,
        journal: SQLiteJournal,
        run_id: str,
        **bindings,
    ) -> Executor:
        checkpoint, revision = journal.load(run_id)
        if checkpoint["revision"] != revision:
            raise RuntimeFailure("InvalidCheckpoint", "Journal revision mismatch")
        return cls.restore(program, checkpoint, journal=journal, **bindings)
