"""Linear graph-structure checks under a single-token, global-budget profile.

Terminal reachability is existential. Bounds require runtime enforcement and do
not prove host-call termination or successful workflow completion.
"""

from __future__ import annotations

from collections import deque

from .diagnostics import Diagnostic
from .ir import CheckResult


def reachable(start: list[str], adjacency: dict[str, list[str]]) -> set[str]:
    seen = set(start)
    queue = deque(start)
    while queue:
        for target in adjacency.get(queue.popleft(), []):
            if target not in seen:
                seen.add(target)
                queue.append(target)
    return seen


def components(adjacency: dict[str, list[str]]) -> list[list[str]]:
    """Iterative Kosaraju; supports chains and SCCs beyond Python's stack limit."""
    reverse = {node: [] for node in adjacency}
    for source, targets in adjacency.items():
        for target in targets:
            reverse[target].append(source)
    seen: set[str] = set()
    finish = []
    for root in adjacency:
        if root in seen:
            continue
        seen.add(root)
        stack = [(root, iter(adjacency[root]))]
        while stack:
            node, targets = stack[-1]
            target = next(targets, None)
            if target is None:
                finish.append(node)
                stack.pop()
            elif target not in seen:
                seen.add(target)
                stack.append((target, iter(adjacency[target])))
    seen.clear()
    result = []
    for root in reversed(finish):
        if root in seen:
            continue
        group = []
        todo = [root]
        seen.add(root)
        while todo:
            node = todo.pop()
            group.append(node)
            for target in reverse[node]:
                if target not in seen:
                    seen.add(target)
                    todo.append(target)
        result.append(group)
    return result


def cycle_witness(group: list[str], adjacency: dict[str, list[str]]) -> list[str]:
    """Return actual consecutive cycle edges, rather than an unordered SCC list."""
    allowed = set(group)
    active: dict[str, int] = {}
    done = set()
    for root in group:
        if root in done:
            continue
        path = [root]
        active[root] = 0
        stack = [(root, iter(adjacency[root]))]
        while stack:
            node, targets = stack[-1]
            target = next(targets, None)
            if target is None:
                stack.pop()
                done.add(node)
                active.pop(node)
                path.pop()
            elif target in allowed:
                if target in active:
                    return path[active[target] :] + [target]
                if target not in done:
                    active[target] = len(path)
                    path.append(target)
                    stack.append((target, iter(adjacency[target])))
    return []


def verify_graph(graph: dict) -> tuple[list[Diagnostic], dict | None]:
    diagnostics: list[Diagnostic] = []

    def error(code, message, location, related=(), witness=()):
        diagnostics.append(
            Diagnostic(
                code,
                message,
                location["span"],
                related_spans=tuple(related),
                witness=tuple(witness),
            )
        )

    node_list = graph["nodes"]
    by_id = {n["id"]: n for n in node_list}
    by_name = {n["name"]: n for n in node_list}
    if len(by_id) != len(node_list) or len(by_name) != len(node_list):
        error("E2301", "Duplicate node identity in graph IR", graph)
    for node in node_list:
        if node["name"] in {"start", "end"}:
            error("E2301", f"Reserved graph node name '{node['name']}'", node)
    starts = graph["starts"]
    if len(starts) != 1:
        error(
            "E2301",
            "Graph requires exactly one start declaration",
            graph,
            [s["span"] for s in starts],
        )
    for start in starts:
        if start.get("target") not in by_id:
            error("E2301", "Start must target a declared node", start)
    outgoing: dict[str, list[dict]] = {key: [] for key in by_id}
    adjacency: dict[str, list[str]] = {key: [] for key in by_id}
    terminal_sources = []
    for transition in graph["transitions"]:
        source = transition.get("source")
        if source not in by_id:
            error("E2301", "Unknown transition source in graph IR", transition)
            continue
        outgoing[source].append(transition)
        branches = transition["branches"]
        conditional = any("guard" in b or "otherwise" in b for b in branches)
        if conditional:
            valid = (
                len(branches) == 2
                and "guard" in branches[0]
                and not branches[0].get("otherwise")
                and branches[1].get("otherwise") is True
                and "guard" not in branches[1]
                and isinstance(branches[0]["guard"], dict)
                and branches[0]["guard"].get("resolvedType") == "bool"
            )
        else:
            valid = len(branches) == 1
        if not valid:
            error(
                "E2305",
                "Malformed IR branch: one typed guard and final otherwise, or one unconditional target required",
                transition,
            )
        for branch in branches:
            target = branch.get("target")
            if target == "$end":
                terminal_sources.append(source)
            elif target in by_id:
                adjacency[source].append(target)
            else:
                error("E2301", "Unknown transition target in graph IR", transition)
    for node_id, transitions in outgoing.items():
        if len(transitions) != 1:
            error(
                "E2302",
                f"Node '{by_id[node_id]['name']}' requires exactly one normal transition declaration",
                by_id[node_id],
                [t["span"] for t in transitions],
            )
    budgets = {}
    for node in node_list:
        limits = [c for c in node["contracts"] if c["kind"] == "MaxVisitsContract"]
        if len(limits) > 1 or (
            limits
            and (type(limits[0].get("value")) is not int or limits[0]["value"] <= 0)
        ):
            error("E2301", "Invalid dispatch budget in graph IR", node)
        budgets[node["id"]] = limits[0]["value"] if limits else None
    # Endpoint/cardinality failures make topology conclusions misleading.
    if diagnostics:
        return sorted(
            diagnostics, key=lambda d: (d.span["start"], d.code, d.message)
        ), None
    forward = reachable([starts[0]["target"]], adjacency)
    reverse = {key: [] for key in by_id}
    for source, targets in adjacency.items():
        for target in targets:
            reverse[target].append(source)
    to_end = reachable(terminal_sources, reverse)
    for node in node_list:
        if node["id"] not in forward:
            error("E2303", f"Node '{node['name']}' is unreachable from start", node)
        elif node["id"] not in to_end:
            error(
                "E2304", f"Node '{node['name']}' has no topological path to end", node
            )
    for group in components(adjacency):
        cyclic = len(group) > 1 or group[0] in adjacency[group[0]]
        if cyclic:
            missing = [node for node in group if budgets[node] is None]
            if missing:
                witness = [
                    by_id[node]["name"] for node in cycle_witness(group, adjacency)
                ]
                missing.sort(key=lambda n: by_id[n]["span"]["start"])
                names = ", ".join(by_id[n]["name"] for n in missing)
                error(
                    "E2306",
                    f"Unbounded cycle: nodes [{names}] require max_visits under the all-cyclic-nodes rule",
                    by_id[missing[0]],
                    [by_id[n]["span"] for n in group if n != missing[0]],
                    witness,
                )
    diagnostics.sort(key=lambda d: (d.span["start"], d.code, d.message))
    if diagnostics:
        return diagnostics, None
    return [], {
        "dispatchBound": sum(
            value if value is not None else 1 for value in budgets.values()
        ),
        "cycleRule": "all-cyclic-nodes-bounded",
        "terminalReachability": "possible-not-guaranteed",
        "assumptions": [
            "single-token",
            "complete-static-control-flow",
            "global-per-run-budgets-enforced-before-dispatch",
        ],
        "doesNotProve": [
            "host-call-termination",
            "successful-completion",
            "wall-clock-bound",
            "external-effect-safety",
        ],
    }


def verify_program(result: CheckResult) -> CheckResult:
    if result.diagnostics:
        return result
    for graph in result.graphs:
        diagnostics, report = verify_graph(graph)
        result.diagnostics.extend(diagnostics)
        if report is not None:
            graph["verification"] = report
    result.diagnostics.sort(
        key=lambda d: (d.span["file"], d.span["start"], d.code, d.message)
    )
    if result.diagnostics:
        result.graphs.clear()
    else:
        for check in ["graph-topology", "dispatch-budget-verification"]:
            if check not in result.checks:
                result.checks.append(check)
            if check in result.deferred:
                result.deferred.remove(check)
    return result
