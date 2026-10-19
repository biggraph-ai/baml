"""Explicit finite-state invariant checking under enforceable projection contracts.

This is safety checking, not arbitrary-code termination or temporal liveness.
Opaque inputs/receipts are universally quantified by user-supplied summaries;
unknown summaries never become proofs. Runtime boundary checks enforce the same
finite domains and summary relations on actual callback outputs.
"""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from itertools import product
import json

from .diagnostics import Diagnostic
from .resolver import decision_enum, nodes, tokens


class ModelIssue(ValueError):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


def key(value) -> str:
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"))


def projected(value: object, type_name: str) -> object:
    if decision_enum(type_name) is not None:
        if not isinstance(value, dict):
            raise ModelIssue(
                "unsupported", "Decision domain values require choice and abstained"
            )
        return {"choice": value.get("choice"), "abstained": value.get("abstained")}
    return value


class FiniteModel:
    def __init__(self, program: dict, contract: dict, *, max_states=10000):
        if not isinstance(contract, dict) or set(contract) - {
            "graph",
            "domains",
            "initial",
            "summaries",
            "property",
            "maxTransitions",
        }:
            raise ModelIssue(
                "unsupported", "Unsupported finite-model configuration fields"
            )
        if contract.get("property", "invariant-safety") != "invariant-safety":
            raise ModelIssue(
                "unsupported",
                "Only invariant-safety is implemented; temporal/liveness properties are unsupported",
            )
        if type(max_states) is not int or not 1 <= max_states <= 100000:
            raise ModelIssue("unsupported", "max_states must be in [1,100000]")
        self.program = program
        self.contract = deepcopy(contract)
        self.max_states = max_states
        self.max_transitions = contract.get("maxTransitions", max_states * 20)
        if (
            type(self.max_transitions) is not int
            or not 1 <= self.max_transitions <= 10000000
        ):
            raise ModelIssue("unsupported", "maxTransitions must be in [1,10000000]")
        self._rule_hits = set()
        self.symbols = {s["id"]: s for s in program["symbols"]}
        matches = [g for g in program["graphs"] if g["name"] == contract.get("graph")]
        if len(matches) != 1:
            raise ModelIssue(
                "unsupported", "Finite model must select one declared graph"
            )
        self.graph = matches[0]
        for node in self.graph["nodes"]:
            if (
                any(c["kind"] == "CompensateContract" for c in node["contracts"])
                and self.symbols[node["callable"]].get("effect", {}).get("kind")
                != "mutation"
            ):
                raise ModelIssue(
                    "unsupported",
                    "Finite recovery analysis requires declared capability/receipt contracts",
                )
        self.fields = {
            s["name"].split(".")[-1]: s["type"]
            for s in program["symbols"]
            if s.get("owner") == self.graph["stateType"] and s["kind"] == "Field"
        }
        self.domains = {}
        domains = contract.get("domains")
        if not isinstance(domains, dict) or not domains:
            raise ModelIssue(
                "unsupported", "Explicit nonempty finite domains are required"
            )
        combinations = 1
        for name, descriptor in domains.items():
            if name not in self.fields:
                raise ModelIssue("unsupported", f"Unknown finite field {name}")
            type_name = self.fields[name]
            enum = decision_enum(type_name)
            if type_name == "bool":
                if descriptor != "bool":
                    raise ModelIssue("unsupported", 'Boolean domains use "bool"')
                values = [False, True]
            elif type_name == "int":
                if (
                    not isinstance(descriptor, dict)
                    or set(descriptor) != {"min", "max"}
                    or any(type(descriptor[k]) is not int for k in descriptor)
                    or not -(2**63) <= descriptor["min"] <= descriptor["max"] < 2**63
                ):
                    raise ModelIssue(
                        "unsupported", "Integer domains require signed-64-bit min/max"
                    )
                count = descriptor["max"] - descriptor["min"] + 1
                if count > max_states:
                    raise ModelIssue(
                        "unknown", "Finite domain exceeds the configured resource limit"
                    )
                values = list(range(descriptor["min"], descriptor["max"] + 1))
            elif enum is not None:
                if descriptor != "decision":
                    raise ModelIssue("unsupported", 'Decision domains use "decision"')
                labels = self.enum_labels(enum)
                values = [{"choice": label, "abstained": False} for label in labels] + [
                    {"choice": None, "abstained": True}
                ]
            else:
                labels = self.enum_labels(type_name)
                if descriptor != "enum" or not labels:
                    raise ModelIssue(
                        "unsupported",
                        f"{name} requires an enum/Boolean/bounded-integer/Decision domain; strings and objects must remain opaque",
                    )
                values = labels
            self.domains[name] = values
            combinations *= len(values)
            if combinations > max_states:
                raise ModelIssue(
                    "unknown",
                    "Finite domain product exceeds the configured resource limit",
                )
        self.state_space = [
            dict(zip(self.domains, values))
            for values in product(*self.domains.values())
        ]
        self.initial = contract.get("initial")
        if not isinstance(self.initial, list) or not self.initial:
            raise ModelIssue(
                "unsupported", "Explicit initial projected states are required"
            )
        for state in self.initial:
            self.validate_projection(state)
        self.summaries = contract.get("summaries", {})
        if not isinstance(self.summaries, dict):
            raise ModelIssue("unsupported", "summaries must be an object")
        methods = {s["name"] for s in program["symbols"] if s["kind"] == "Method"}
        for name, summary in self.summaries.items():
            if name not in methods:
                raise ModelIssue(
                    "unsupported", f"Summary references unknown method {name}"
                )
            if not isinstance(summary, dict) or set(summary) - {"cases", "havoc"}:
                raise ModelIssue(
                    "unsupported", "Summaries support cases or explicit havoc"
                )
            if summary.get("havoc") is True:
                if set(summary) != {"havoc"}:
                    raise ModelIssue("unsupported", "Do not mix havoc and cases")
                continue
            if "havoc" in summary or not isinstance(summary.get("cases"), list):
                raise ModelIssue(
                    "unsupported", "A summary requires cases or havoc:true"
                )
            for case in summary["cases"]:
                if (
                    not isinstance(case, dict)
                    or set(case) - {"when", "outputs"}
                    or not isinstance(case.get("outputs"), list)
                    or not case["outputs"]
                ):
                    raise ModelIssue(
                        "unsupported", "Summary cases require when/outputs"
                    )
                for patch in [case.get("when", {}), *case["outputs"]]:
                    self.validate_patch(patch)
        expressions = list(self.graph.get("invariants", []))
        expressions += [
            b["guard"]
            for t in self.graph["transitions"]
            for b in t["branches"]
            if "guard" in b
        ]
        expressions += [
            r["predicate"] for p in self.graph.get("policies", []) for r in p["rules"]
        ]
        for expression in expressions:
            self.check_reads(expression)

    def enum_labels(self, name):
        return [
            s["name"].split(".")[-1]
            for s in self.program["symbols"]
            if s["kind"] == "EnumMember" and s["type"] == name
        ]

    def validate_patch(self, patch):
        if not isinstance(patch, dict) or set(patch) - set(self.domains):
            raise ModelIssue("unsupported", "Summary refers to an opaque/unknown field")
        for name, value in patch.items():
            if key(value) not in {key(v) for v in self.domains[name]}:
                raise ModelIssue(
                    "unsupported", f"Value for {name} is outside its finite domain"
                )

    def validate_projection(self, state):
        self.validate_patch(state)
        if set(state) != set(self.domains):
            raise ModelIssue(
                "unsupported", "Initial/projected state must assign every finite field"
            )

    def project(self, state):
        result = {
            name: projected(state[name], self.fields[name]) for name in self.domains
        }
        self.validate_projection(result)
        return result

    def check_reads(self, expression):
        if expression["kind"] == "Member":
            current = expression
            path = []
            while current["kind"] == "Member":
                path.append(tokens(current, "NAME")[0]["text"])
                current = nodes(current)[0]
                while current["kind"] == "Atom":
                    current = nodes(current)[0]
            if (
                current["kind"] == "Name"
                and tokens(current, "NAME")[0]["text"] == self.graph["stateParameter"]
            ):
                root = path[-1]
                if root not in self.domains:
                    raise ModelIssue(
                        "unsupported",
                        f"Predicate reads opaque field {root}; provide a sound finite projection first",
                    )
        for child in nodes(expression):
            self.check_reads(child)

    def outputs(self, method, state, binding=None):
        if "decision" in method:
            if binding is None:
                raise ModelIssue(
                    "unsupported", "Decision analysis requires explicit writes binding"
                )
            field = binding["field"]
            if field not in self.domains:
                return [deepcopy(state)]
            return [
                {**deepcopy(state), field: deepcopy(value)}
                for value in self.domains[field]
            ]
        summary = self.summaries.get(method["name"])
        if summary is None:
            raise ModelIssue(
                "unknown", f"Missing summary for reachable callable {method['name']}"
            )
        if summary.get("havoc"):
            return deepcopy(self.state_space)
        results = {}
        for case in summary["cases"]:
            if all(
                key(state[name]) == key(value)
                for name, value in case.get("when", {}).items()
            ):
                for patch in case["outputs"]:
                    output = {**deepcopy(state), **deepcopy(patch)}
                    results[key(output)] = output
        if not results:
            raise ModelIssue(
                "unknown",
                f"Missing reachable summary case for {method['name']}: {key(state)}",
            )
        return list(results.values())

    def violation(self, state):
        from .runtime import evaluate

        for expression in self.graph.get("invariants", []):
            if (
                evaluate(expression, state, self.graph["stateParameter"], self.symbols)
                is not True
            ):
                return expression["span"]
        return None

    def policy(self, state):
        from .runtime import evaluate

        for policy in self.graph.get("policies", []):
            for rule in policy["rules"]:
                if evaluate(
                    rule["predicate"], state, self.graph["stateParameter"], self.symbols
                ):
                    self._rule_hits.add((policy["name"], rule["name"]))
                    return rule["action"]["kind"]
        return None

    def analyze(self):
        from .runtime import evaluate

        graph = self.graph
        node_list = graph["nodes"]
        node_map = {n["id"]: n for n in node_list}
        indices = {n["id"]: i for i, n in enumerate(node_list)}
        limits = tuple(
            next(
                (
                    c["value"]
                    for c in n["contracts"]
                    if c["kind"] == "MaxVisitsContract"
                ),
                1,
            )
            for n in node_list
        )
        transitions = {t["source"]: t for t in graph["transitions"]}
        visited = {}
        queue = deque()
        edges = 0
        outcomes = set()

        def outcome(label):
            nonlocal edges
            edges += 1
            if edges > self.max_transitions:
                raise ModelIssue("unknown", "Transition exploration limit reached")
            outcomes.add(label)

        def node_name(location):
            return node_map.get(location, {}).get("name", location)

        def event(location, state, counts, label):
            return {
                "node": node_name(location),
                "state": deepcopy(state),
                "counters": dict(zip([n["name"] for n in node_list], counts)),
                "event": label,
            }

        def enqueue(
            location, state, counts, history, parent, label, phase="forward", attempt=0
        ):
            nonlocal edges
            edges += 1
            if edges > self.max_transitions:
                raise ModelIssue("unknown", "Transition exploration limit reached")
            identity = (phase, location, key(state), counts, history, attempt)
            if identity in visited:
                return identity
            if len(visited) >= self.max_states:
                raise ModelIssue("unknown", "State exploration limit reached")
            visited[identity] = (parent, event(location, state, counts, label))
            queue.append(identity)
            return identity

        def witness(identity, last=None):
            result = []
            while identity is not None:
                previous, step = visited[identity]
                result.append(step)
                identity = previous
            result.reverse()
            if last:
                result.append(last)
            return result

        def finish(state, counts, history, parent, label):
            outcome(label)
            if history:
                enqueue("$recovery", state, counts, history, parent, label, "recover")

        def failure(node, state, counts, history, parent, kind):
            contract = next(
                (
                    c
                    for c in node["contracts"]
                    if c["kind"]
                    == ("TimeoutContract" if kind == "timeout" else "OnErrorContract")
                ),
                None,
            )
            if contract and "target" in contract:
                if contract["target"] == "$end":
                    outcome("handled_" + kind)
                else:
                    enqueue(contract["target"], state, counts, history, parent, kind)
            else:
                finish(state, counts, history, parent, kind)

        def report(status, reason, trace=None, span=None):
            return {
                "status": status,
                "property": "invariant-safety",
                "graph": graph["name"],
                "statesExplored": len(visited),
                "transitionsExplored": edges,
                "reason": reason,
                "counterexample": trace or [],
                "violationSpan": span,
                "outcomes": sorted(outcomes),
                "warnings": (
                    [
                        {
                            "code": "W2602",
                            "severity": "warning",
                            "message": f"Rule {p['name']}.{r['name']} is unreachable under the verified finite contract",
                            "span": r["predicate"]["span"],
                        }
                        for p in graph.get("policies", [])
                        for r in p["rules"]
                        if (p["name"], r["name"]) not in self._rule_hits
                    ]
                    if status == "proved"
                    else []
                ),
                "invariantsChecked": len(graph.get("invariants", [])),
                "contract": deepcopy(self.contract),
                "assumptions": [
                    "finite entry/domain contracts enforced at runtime",
                    "summaries universally quantify omitted opaque inputs and receipts",
                    "declared effects and trusted capability protocol",
                    "no temporal/liveness or external-world invariant claim",
                ],
            }

        try:
            for state in self.initial:
                identity = enqueue(
                    graph["starts"][0]["target"],
                    state,
                    (0,) * len(node_list),
                    (),
                    None,
                    "entry",
                )
                span = self.violation(state)
                if span:
                    return report(
                        "counterexample",
                        "Initial invariant violation",
                        witness(identity),
                        span,
                    )
            while queue:
                identity = queue.popleft()
                phase, location, encoded, counts, history, attempt = identity
                state = json.loads(encoded)
                if phase == "recover":
                    if not history:
                        outcome("recovery_finished")
                        continue
                    compensation = history[-1]
                    if compensation == "$irreversible":
                        outcome("irreversible_manual_recovery")
                        enqueue(
                            "$recovery",
                            state,
                            counts,
                            history[:-1],
                            identity,
                            "irreversible",
                            "recover",
                        )
                        continue
                    method = self.symbols[compensation]
                    effect = method["effect"]
                    outcome("compensation_unknown_or_denied")
                    if attempt + 1 < effect["maxAttempts"]:
                        enqueue(
                            "$recovery",
                            state,
                            counts,
                            history,
                            identity,
                            "compensation_not_committed",
                            "recover",
                            attempt + 1,
                        )
                    else:
                        outcome("recovery_failed")
                    for candidate in self.outputs(method, state):
                        span = self.violation(candidate)
                        if span:
                            return report(
                                "counterexample",
                                "Compensation can propose an invariant-violating state",
                                witness(
                                    identity,
                                    event(
                                        "$recovery",
                                        candidate,
                                        counts,
                                        "candidate_compensation",
                                    ),
                                ),
                                span,
                            )
                        enqueue(
                            "$recovery",
                            candidate,
                            counts,
                            history[:-1],
                            identity,
                            "compensation_success",
                            "recover",
                        )
                    continue
                action = self.policy(state)
                if action == "end":
                    outcome("policy_end")
                    continue
                if action == "throw":
                    finish(state, counts, history, identity, "policy_throw")
                    continue
                node = node_map[location]
                index = indices[location]
                if counts[index] >= limits[index]:
                    finish(state, counts, history, identity, "budget_exhausted")
                    continue
                updated_counts = list(counts)
                updated_counts[index] += 1
                updated_counts = tuple(updated_counts)
                method = self.symbols[node["callable"]]
                effect = method.get("effect", {})
                failure(node, state, updated_counts, history, identity, "error")
                # Binding-level deadlines and local interruption are possible
                # even without source timeout syntax. Include their distinct
                # unhandled-failure path as a conservative over-approximation.
                failure(node, state, updated_counts, history, identity, "timeout")
                if effect:
                    finish(
                        state, updated_counts, history, identity, "authorization_denied"
                    )
                if effect.get("kind") == "mutation":
                    outcome("mutation_unknown")
                for candidate in self.outputs(method, state, node.get("outputBinding")):
                    completed = history
                    if effect.get("kind") == "mutation":
                        compensator = next(
                            (
                                c["callable"]
                                for c in node["contracts"]
                                if c["kind"] == "CompensateContract"
                            ),
                            "$irreversible",
                        )
                        completed = history + (compensator,)
                        finish(
                            state, updated_counts, completed, identity, "commit_denied"
                        )
                    span = self.violation(candidate)
                    if span:
                        return report(
                            "counterexample",
                            "A callback summary can propose an invariant-violating state (runtime guards reject this commit)",
                            witness(
                                identity,
                                event(
                                    location,
                                    candidate,
                                    updated_counts,
                                    "candidate_commit",
                                ),
                            ),
                            span,
                        )
                    action = self.policy(candidate)
                    if action == "end":
                        outcome("policy_end")
                        continue
                    if action == "throw":
                        finish(
                            candidate,
                            updated_counts,
                            completed,
                            identity,
                            "policy_throw",
                        )
                        continue
                    branches = transitions[location]["branches"]
                    if "guard" in branches[0]:
                        target = branches[
                            0
                            if evaluate(
                                branches[0]["guard"],
                                candidate,
                                graph["stateParameter"],
                                self.symbols,
                            )
                            else 1
                        ]["target"]
                    else:
                        target = branches[0]["target"]
                    if target == "$end":
                        outcome("end")
                    else:
                        enqueue(
                            target,
                            candidate,
                            updated_counts,
                            completed,
                            identity,
                            "success",
                        )
                    # A timed-out/errored mutation may have committed. Its
                    # reconciled result takes the declared handler, not a guessed
                    # non-commit path. Unknown outcomes remain blocked terminals.
                    if effect.get("kind") == "mutation":
                        for kind in ["timeout", "error"]:
                            contract = next(
                                (
                                    c
                                    for c in node["contracts"]
                                    if c["kind"]
                                    == (
                                        "TimeoutContract"
                                        if kind == "timeout"
                                        else "OnErrorContract"
                                    )
                                ),
                                None,
                            )
                            if contract and "target" in contract:
                                if contract["target"] == "$end":
                                    outcome("reconciled_" + kind)
                                else:
                                    enqueue(
                                        contract["target"],
                                        candidate,
                                        updated_counts,
                                        completed,
                                        identity,
                                        "reconciled_" + kind,
                                    )
            return report(
                "proved",
                "All reachable candidate commits satisfy invariants under the finite contracts",
            )
        except ModelIssue as issue:
            return report(issue.status, str(issue))
        except (KeyError, TypeError, ValueError) as issue:
            return report(
                "unsupported",
                f"Unsupported finite expression/model: {type(issue).__name__}",
            )


def analyze_program(result, contract: dict, *, max_states=10000):
    try:
        try:
            json.dumps(contract, allow_nan=False)
        except (ValueError, TypeError) as error:
            raise ModelIssue(
                "unsupported", "Model contracts must contain finite JSON values"
            ) from error
        result.analysis = FiniteModel(
            result.to_dict(), contract, max_states=max_states
        ).analyze()
    except ModelIssue as issue:
        result.analysis = {
            "status": issue.status,
            "property": "invariant-safety",
            "reason": str(issue),
            "graph": contract.get("graph") if isinstance(contract, dict) else None,
            "statesExplored": 0,
            "transitionsExplored": 0,
            "counterexample": [],
        }
    if result.analysis is None:
        raise ValueError("Finite verification did not produce a report")
    if result.analysis["status"] == "proved":
        if "finite-invariant-safety" not in result.checks:
            result.checks.append("finite-invariant-safety")
        if "static-invariant-proof" in result.deferred:
            result.deferred.remove("static-invariant-proof")
    if result.analysis["status"] != "proved":
        code = {"counterexample": "E2701", "unknown": "E2702", "unsupported": "E2703"}[
            result.analysis["status"]
        ]
        span = result.analysis.get("violationSpan") or (
            result.graphs[0]["span"]
            if result.graphs
            else {"file": "<model>", "start": 0, "end": 0}
        )
        result.diagnostics.append(Diagnostic(code, result.analysis["reason"], span))
    return result.analysis


def _runtime_model(program, contract):
    # Model contracts have already been checked at compilation. A generous
    # validation ceiling avoids turning the original analysis limit into a new
    # runtime state-space enumeration requirement.
    return FiniteModel(program, contract, max_states=100000)


def validate_runtime_entry(program, contract, state):
    from .runtime import RuntimeFailure

    try:
        model = _runtime_model(program, contract)
        projection = model.project(state)
        if key(projection) not in {key(s) for s in model.initial}:
            raise ModelIssue("unsupported", "State is not in the verified initial set")
    except (ModelIssue, KeyError) as issue:
        raise RuntimeFailure("ModelContractViolation", str(issue)) from issue


def validate_runtime_state(program, contract, state):
    from .runtime import RuntimeFailure

    try:
        _runtime_model(program, contract).project(state)
    except (ModelIssue, KeyError) as issue:
        raise RuntimeFailure("ModelContractViolation", str(issue)) from issue


def validate_runtime_transition(program, contract, method, before, after, binding=None):
    from .runtime import RuntimeFailure

    try:
        model = _runtime_model(program, contract)
        source = model.project(before)
        target = model.project(after)
        if key(target) not in {key(s) for s in model.outputs(method, source, binding)}:
            raise ModelIssue(
                "unsupported", "Callback output violates its finite transition summary"
            )
    except (ModelIssue, KeyError) as issue:
        raise RuntimeFailure("ModelContractViolation", str(issue)) from issue
