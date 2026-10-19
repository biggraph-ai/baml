from copy import deepcopy
import json

import pytest

from panini_compiler.cli import main
from panini_compiler.parser import PaniniParser
from panini_compiler.resolver import Resolver
from panini_compiler.verifier import verify_graph, verify_program


HEADER = "class S { bool retry; } class T { public static S run(S s); } "


@pytest.fixture(scope="module")
def parser():
    return PaniniParser.create()


def compile_graph(parser, body):
    return verify_program(
        Resolver().resolve(parser.semantic_ast(HEADER + "graph G(s: S) {" + body + "}"))
    )


@pytest.mark.parametrize(
    "body,code",
    [
        ("node a using T.run; a -> end;", "E2301"),
        ("node a using T.run; start -> a; start -> a; a -> end;", "E2301"),
        ("node a using T.run; start -> a;", "E2302"),
        ("node a using T.run; start -> a; a -> a; a -> end;", "E2302"),
        (
            "node a using T.run; node b using T.run; start -> a; a -> end; b -> end;",
            "E2303",
        ),
        ("node a using T.run max_visits 1; start -> a; a -> a;", "E2304"),
        (
            "node a using T.run; start -> a; a -> when s.retry => a otherwise => end;",
            "E2306",
        ),
        ("node end using T.run; start -> end; end -> end;", "E2301"),
    ],
)
def test_reject_bad_graphs(parser, body, code):
    result = compile_graph(parser, body)
    assert code in {d.code for d in result.diagnostics}
    assert not result.graphs


def test_mixed_dispatch_bound_and_terminal_reachability(parser):
    result = compile_graph(
        parser,
        """
    node init using T.run;
    node retry using T.run max_visits 3;
    start -> init;
    init -> retry;
    retry -> when s.retry => retry otherwise => end;
    """,
    )
    assert not result.diagnostics
    report = result.graphs[0]["verification"]
    assert report["dispatchBound"] == 4
    assert report["terminalReachability"] == "possible-not-guaranteed"
    assert "graph-topology" in result.checks
    assert "runtime-enforcement" in result.deferred


def test_all_bounded_two_node_cycle_and_scc_adversary(parser):
    body = """node a using T.run max_visits 2;
    node b using T.run max_visits 3;
    start -> a;
    a -> b;
    b -> when s.retry => a otherwise => end;"""
    result = compile_graph(parser, body)
    assert result.graphs[0]["verification"]["dispatchBound"] == 5
    # One bounded node in an SCC must not excuse an unbounded subcycle.
    bad = (
        body.replace(
            "node b using T.run max_visits 3;",
            "node b using T.run; node c using T.run;",
        )
        .replace("a -> b;", "a -> when s.retry => b otherwise => end;")
        .replace(
            "b -> when s.retry => a otherwise => end;",
            "b -> c; c -> when s.retry => b otherwise => a;",
        )
    )
    first = compile_graph(parser, bad)
    second = compile_graph(parser, bad)
    assert [d.to_dict() for d in first.diagnostics] == [
        d.to_dict() for d in second.diagnostics
    ]
    cycle = next(d for d in first.diagnostics if d.code == "E2306")
    assert cycle.witness[0] == cycle.witness[-1]
    edges = {("a", "b"), ("b", "c"), ("c", "b"), ("c", "a")}
    assert all(edge in edges for edge in zip(cycle.witness, cycle.witness[1:]))
    assert cycle.related_spans


def ir_chain(size, cycle=False):
    def span(i):
        return {"file": "generated.panini", "start": i, "end": i + 1}

    return {
        "name": "G",
        "span": span(0),
        "nodes": [
            {
                "id": str(i),
                "name": f"n{i}",
                "span": span(i),
                "contracts": (
                    [{"kind": "MaxVisitsContract", "value": 2}] if cycle else []
                ),
            }
            for i in range(size)
        ],
        "starts": [{"target": "0", "span": span(0)}],
        "transitions": [
            {
                "source": str(i),
                "span": span(i),
                "branches": (
                    [{"target": str(i + 1)}]
                    if i + 1 < size
                    else [
                        {"guard": {"resolvedType": "bool"}, "target": "0"},
                        {"otherwise": True, "target": "$end"},
                    ]
                    if cycle
                    else [{"target": "$end"}]
                ),
            }
            for i in range(size)
        ],
    }


@pytest.mark.parametrize("cycle", [False, True])
def test_deep_graphs_use_iterative_traversal(cycle):
    diagnostics, report = verify_graph(ir_chain(2500, cycle))
    assert not diagnostics
    assert report["dispatchBound"] == (5000 if cycle else 2500)


def test_ir_consistency_fallback_endpoints_and_budgets():
    good = ir_chain(2, True)
    for mutate, code in [
        (lambda g: g["transitions"][-1]["branches"].pop(), "E2305"),
        (
            lambda g: g["transitions"][0]["branches"][0].update(target="unknown"),
            "E2301",
        ),
        (lambda g: g["nodes"][0]["contracts"][0].update(value=True), "E2301"),
    ]:
        bad = deepcopy(good)
        mutate(bad)
        assert code in {d.code for d in verify_graph(bad)[0]}


@pytest.mark.parametrize("emit", ["ast", "cst", "semantic-ast", "pnir"])
def test_cli_always_verifies_graphs(tmp_path, capsys, emit):
    file = tmp_path / "cycle.panini"
    file.write_text(
        HEADER
        + "graph G(s: S) { node a using T.run; start -> a; a -> when s.retry => a otherwise => end; }"
    )
    assert main(["check", str(file), "--emit", emit, "--diagnostics", "json"]) == 1
    output = capsys.readouterr()
    assert not output.out
    assert "E2306" in {d["code"] for d in json.loads(output.err)}


def test_cycle_diagnostic_has_exact_utf8_span(parser):
    source = (
        HEADER
        + "graph G(s:S) { node réessayer using T.run; start -> réessayer; réessayer -> when s.retry => réessayer otherwise => end; }"
    )
    result = verify_program(
        Resolver().resolve(parser.semantic_ast(source, "unicode.panini"))
    )
    diagnostic = next(d for d in result.diagnostics if d.code == "E2306")
    assert (
        source.encode()[diagnostic.span["start"] : diagnostic.span["end"]].decode()
        == "node réessayer using T.run;"
    )
    assert diagnostic.witness == ("réessayer", "réessayer")


@pytest.mark.parametrize("handler", ["on_error", "on_timeout"])
def test_exceptional_cycle_cannot_bypass_bounded_node(parser, handler):
    contract = (
        handler + " => b"
        if handler == "on_error"
        else "timeout seconds(1) on_timeout => b"
    )
    body = f"""
    node bounded using T.run max_visits 2;
    node a using T.run {contract};
    node b using T.run on_error => a;
    start -> bounded;
    bounded -> a;
    a -> when s.retry => bounded otherwise => end;
    b -> end;
    """
    result = compile_graph(parser, body)
    diagnostic = next(d for d in result.diagnostics if d.code == "E2306")
    assert "nodes [a, b]" in diagnostic.message
    edges = {("bounded", "a"), ("a", "bounded"), ("a", "b"), ("b", "a")}
    assert all(
        edge in edges for edge in zip(diagnostic.witness, diagnostic.witness[1:])
    )


def test_multiple_exceptional_paths_have_finite_global_budget(parser):
    result = compile_graph(
        parser,
        """
    node a using T.run max_visits 2 timeout seconds(1) on_timeout => c on_error => b;
    node b using T.run max_visits 3 timeout seconds(1) on_timeout => a;
    node c using T.run max_visits 4 on_error => a;
    start -> a; a -> end; b -> end; c -> end;
    """,
    )
    assert not result.diagnostics
    assert result.graphs[0]["verification"]["dispatchBound"] == 9
