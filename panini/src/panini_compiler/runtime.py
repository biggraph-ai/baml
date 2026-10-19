"""Minimal synchronous reference executor (M2R/M3), not a production host sandbox.

Callbacks and checkpoint storage are trusted. No deadlines, process isolation,
external effects, or compensation execution are implemented. Those contracts
are rejected, rather than silently ignored. Source method bodies are not run.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from typing import Callable

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
    ):
        self._program = deepcopy(
            program.to_dict() if isinstance(program, CheckResult) else program
        )
        if (
            self._program.get("schemaVersion") != 2
            or self._program.get("profile") != PROFILE
            or self._program.get("diagnostics")
        ):
            raise RuntimeFailure("InvalidProgram", "Expected error-free PNIR v2")
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
            if any(
                c["kind"] not in {"MaxVisitsContract", "WritesContract"}
                for c in node["contracts"]
            ):
                raise RuntimeFailure(
                    "UnsupportedContract",
                    "Timeout and compensation execution require the M4 runtime",
                )
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
        self._state = deepcopy(state)
        self._validate(self._state, self._symbols[self._graph["stateType"]]["name"])
        self._counts = {node: 0 for node in self._node_map}
        self._next = self._graph["starts"][0]["target"]
        self._status = "ready"
        self._failure = None
        self._busy = False

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

    def _fail(self, code: str, message: str) -> None:
        self._status = "failed"
        self._failure = {"code": code, "message": message}

    def step(self) -> str:
        if self._busy:
            raise RuntimeFailure(
                "ReentrantDispatch", "Only one invocation may be active"
            )
        if self._status != "ready":
            return self._status
        node = self._node_map[self._next]
        if self._counts[node["id"]] >= self._limits[node["id"]]:
            self._fail(
                "BudgetExceeded", f"Dispatch budget exhausted for {node['name']}"
            )
            return self._status
        self._counts[node["id"]] += 1
        self._busy = True
        try:
            method = self._symbols[node["callable"]]
            snapshot = deepcopy(self._state)
            if "decision" in method:
                spec = method["decision"]
                callback = self._adapters.get(spec["model"])
                if callback is None:
                    raise RuntimeFailure(
                        "MissingAdapter", "No callback registered for decision adapter"
                    )
                prompt = evaluate(
                    spec["input"], snapshot, spec["parameter"], self._symbols
                )
                response = callback(prompt, deepcopy(spec["options"]))
                output = decide(spec, response).to_dict()
            else:
                callback = self._capabilities.get(method["name"])
                if callback is None:
                    raise RuntimeFailure(
                        "MissingCapability", f"No callback for {method['name']}"
                    )
                output = callback(snapshot)
            if "outputBinding" in node:
                binding = node["outputBinding"]
                self._validate(output, binding["type"])
                updated = deepcopy(self._state)
                updated[binding["field"]] = deepcopy(output)
            else:
                updated = deepcopy(output)
            self._validate(updated, self._symbols[self._graph["stateType"]]["name"])
            branches = self._transitions[node["id"]]["branches"]
            if "guard" in branches[0]:
                condition = evaluate(
                    branches[0]["guard"],
                    updated,
                    self._graph["stateParameter"],
                    self._symbols,
                )
                target = branches[0 if condition else 1]["target"]
            else:
                target = branches[0]["target"]
            # Commit once output validation and guard evaluation both succeed.
            self._state = updated
            self._next = target
            if target == "$end":
                self._status = "ended"
        except RuntimeFailure as error:
            self._fail(error.code, str(error))
        except Exception as error:
            self._fail(
                "NodeFailed",
                f"Invocation or result validation failed ({type(error).__name__})",
            )
        finally:
            self._busy = False
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

    def checkpoint(self) -> dict:
        """Between-step checkpoint for trusted storage, not a durable commit log."""
        if self._busy:
            raise RuntimeFailure(
                "InvalidCheckpoint", "Checkpoint only between invocations"
            )
        return deepcopy(
            {
                "schemaVersion": 1,
                "program": self._fingerprint,
                "graph": self._graph["name"],
                "state": self._state,
                "counts": self._counts,
                "dispatches": self.dispatches,
                "next": self._next,
                "status": self._status,
                "failure": self._failure,
            }
        )

    @classmethod
    def restore(
        cls, program: CheckResult | dict, checkpoint: dict, **bindings
    ) -> Executor:
        required = {
            "schemaVersion",
            "program",
            "graph",
            "state",
            "counts",
            "dispatches",
            "next",
            "status",
            "failure",
        }
        if not isinstance(checkpoint, dict) or set(checkpoint) != required:
            raise RuntimeFailure("InvalidCheckpoint", "Malformed checkpoint fields")
        saved = deepcopy(checkpoint)
        executor = cls(program, saved["graph"], saved["state"], **bindings)
        counts = saved.get("counts")
        if (
            saved.get("schemaVersion") != 1
            or saved.get("program") != executor._fingerprint
            or not isinstance(counts, dict)
            or set(counts) != set(executor._counts)
            or any(
                type(n) is not int or n < 0 or n > executor._limits[k]
                for k, n in counts.items()
            )
            or type(saved.get("dispatches")) is not int
            or saved.get("dispatches") != sum(counts.values())
            or saved.get("status") not in {"ready", "ended", "failed"}
            or saved.get("next") not in {*executor._node_map, "$end"}
            or (saved["status"] == "ended") != (saved["next"] == "$end")
            or (saved["status"] == "failed") != isinstance(saved.get("failure"), dict)
        ):
            raise RuntimeFailure(
                "InvalidCheckpoint", "Checkpoint does not match program or budget state"
            )
        executor._counts = counts
        executor._next = saved["next"]
        executor._status = saved["status"]
        executor._failure = saved["failure"]
        return executor
