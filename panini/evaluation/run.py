"""Run from the checkout with its installed compiler; emit measured JSON only."""

import argparse
from copy import deepcopy
from collections import defaultdict
import json
from pathlib import Path
import platform
import statistics
import time

from panini_compiler import compile_source, PaniniParser
from panini_compiler.modelcheck import analyze_program
from panini_compiler.resolver import Resolver
from panini_compiler.verifier import components, reachable, verify_program

PREFIX = "class State { bool retry; } class Steps { public static State run(State s); } graph G(s: State) {"


def measure(callback, repeats):
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        callback()
        samples.append(time.perf_counter() - start)
    return {"medianSeconds": statistics.median(samples), "samplesSeconds": samples}


def evaluate_corpus():
    cases = json.loads(Path(__file__).with_name("corpus.json").read_text())
    rows = []
    classes = defaultdict(lambda: {"cases": 0, "detected": 0, "correctDiagnostic": 0})
    for case in cases:
        source = case.get("source", PREFIX + case.get("body", "") + "}")
        codes = sorted({d.code for d in compile_source(source).diagnostics})
        rows.append({"id": case["id"], "expected": case["expected"], "actual": codes})
        group = classes[case["defect"] or "negative-control"]
        group["cases"] += 1
        group["detected"] += bool(codes)
        group["correctDiagnostic"] += set(codes) == set(case["expected"])
    for name, group in classes.items():
        group["recall"] = (
            group["detected"] / group["cases"] if name != "negative-control" else None
        )
        group["diagnosticAccuracy"] = group["correctDiagnostic"] / group["cases"]
    return {
        "cases": rows,
        "classes": dict(classes),
        "falsePositives": classes["negative-control"]["detected"],
        "scope": "Hand-labeled smoke corpus; no population-level accuracy claim or external baseline comparison.",
    }


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--nodes", type=int, default=100)
    cli.add_argument("--repeats", type=int, default=3)
    args = cli.parse_args()
    if not 2 <= args.nodes <= 2000 or not 1 <= args.repeats <= 100:
        cli.error("nodes must be 2..2000 and repeats 1..100")
    source = PREFIX + " ".join(f"node n{i} using Steps.run;" for i in range(args.nodes))
    source += (
        " start -> n0; "
        + " ".join(
            f"n{i} -> " + (f"n{i + 1};" if i + 1 < args.nodes else "end;")
            for i in range(args.nodes)
        )
        + "}"
    )
    cold = measure(PaniniParser.create, 1)
    frontend = PaniniParser.create()
    ast = frontend.semantic_ast(source)
    result = Resolver().resolve(ast)
    timings = {
        "parserInitialization": cold,
        "semanticParsing": measure(lambda: frontend.semantic_ast(source), args.repeats),
        "resolutionAndLowering": measure(lambda: Resolver().resolve(ast), args.repeats),
        "topologyVerification": measure(
            lambda: verify_program(deepcopy(result)), args.repeats
        ),
        "serialization": measure(result.to_dict, args.repeats),
    }
    # Graph-kernel workloads isolate O(V+E) passes from frontend work. Dense
    # adjacency is intentionally synthetic: the source profile has bounded outdegree.
    n = args.nodes
    graphs = {
        "deep-chain": {str(i): [str(i + 1)] if i + 1 < n else [] for i in range(n)},
        "large-scc": {str(i): [str((i + 1) % n)] for i in range(n)},
        "dense": {str(i): [str(j) for j in range(n) if j != i] for i in range(n)},
        "disconnected": {str(i): [] for i in range(n)},
    }
    topology = {
        name: {
            "vertices": n,
            "edges": sum(map(len, graph.values())),
            "scc": measure(lambda: components(graph), args.repeats),
            "reachability": measure(lambda: reachable(["0"], graph), args.repeats),
        }
        for name, graph in graphs.items()
    }
    root = Path(__file__).resolve().parents[1]
    model = json.loads((root / "examples/refund-model.json").read_text())
    effects = json.loads((root / "examples/refund-effects.json").read_text())
    refund = (root / "examples/refund.panini").read_text()
    finite_program = compile_source(refund, effects=effects)
    timings["finiteModelExploration"] = measure(
        lambda: analyze_program(deepcopy(finite_program), model),
        args.repeats,
    )
    print(
        json.dumps(
            {
                "schemaVersion": 1,
                "environment": {
                    "python": platform.python_version(),
                    "platform": platform.platform(),
                    "processor": platform.processor(),
                },
                "nodes": n,
                "timings": timings,
                "topology": topology,
                "corpus": evaluate_corpus(),
                "limitations": [
                    "Resolution and lowering are fused.",
                    "Topology verification and finite-model timing include input copies; graph kernels are isolated.",
                    "No adapter latency or external baseline measured.",
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
