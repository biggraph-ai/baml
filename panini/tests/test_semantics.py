"""M0/M1 acceptance tests; topology and runtime proofs are deliberately absent."""

import json
from pathlib import Path

import pytest

from panini_compiler.cli import main
from panini_compiler.parser import PaniniParser
from panini_compiler.resolver import Resolver


BASE = """
class State { bool retry; int count; }
class Steps {
  public static State attempt(State state);
  public static State undo(State state);
}
graph Retry(state: State) -> State {
  node a using Steps.attempt max_visits 3 timeout seconds(2) compensate using Steps.undo;
  start -> a;
  a -> when state.retry => a otherwise => end;
}
"""


@pytest.fixture(scope="module")
def parser():
    return PaniniParser.create()


def resolve(parser, source=BASE):
    return Resolver().resolve(parser.semantic_ast(source, "test.panini"))


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.get("children", []):
            yield from walk(child)


def test_semantic_ast_preserves_operators_and_legacy_v1(parser):
    source = (
        "class X { public static bool f(bool x) { return !x && x || x ?? false; } }"
    )
    ast = parser.semantic_ast(source)
    assert ast["schemaVersion"] == 2
    assert [
        x["text"]
        for x in walk(ast)
        if x.get("kind") == "Token"
        and x["text"] in {"!", "&&", "||", "??", "static", "public"}
    ] == ["public", "static", "!", "&&", "||", "??"]
    assert parser.ast(source)["schemaVersion"] == 1
    assert ast == parser.semantic_ast(source)
    ids = [n["id"] for n in walk(ast)]
    assert len(ids) == len(set(ids))


def test_all_operator_spellings_and_contract_kinds(parser):
    source = "class X { int f(int a) { a += 1; return -a + 2 * 3 / 1 % 2 - 1; } bool g(int a) { return a <= 2 && a >= 1 || a != 3 && a == 1 || a < 2 && a > 0; } }"
    spellings = {
        n["text"] for n in walk(parser.semantic_ast(source)) if n["kind"] == "Token"
    }
    assert {
        "+=",
        "-",
        "+",
        "*",
        "/",
        "%",
        "<=",
        ">=",
        "!=",
        "==",
        "<",
        ">",
        "&&",
        "||",
    } <= spellings
    kinds = {n["kind"] for n in walk(parser.semantic_ast(BASE))}
    assert {"MaxVisitsContract", "TimeoutContract", "CompensateContract"} <= kinds


def test_utf8_spans_and_empty_file(parser):
    source = "class Café { bool prêt; }"
    ast = parser.semantic_ast(source, "unicode.panini")
    for node in walk(ast):
        if node["kind"] == "Token":
            span = node["span"]
            assert source.encode()[span["start"] : span["end"]].decode() == node["text"]
    assert parser.semantic_ast("")["span"] == {"file": "<stdin>", "start": 0, "end": 0}


def test_resolution_pnir_is_deterministic_and_does_not_mutate_ast(parser):
    ast = parser.semantic_ast(BASE)
    before = json.dumps(ast)
    result = Resolver().resolve(ast)
    assert not result.diagnostics
    assert json.dumps(ast) == before
    assert result.to_dict() == Resolver().resolve(ast).to_dict()
    graph = result.graphs[0]
    symbols = {s["id"] for s in result.symbols}
    assert graph["stateType"] in symbols
    assert graph["nodes"][0]["callable"] in symbols
    assert graph["nodes"][0]["contracts"][2]["callable"] in symbols
    assert graph["transitions"][0]["branches"][1]["target"] == "$end"
    assert graph["transitions"][0]["branches"][0]["guard"]["resolvedType"] == "bool"


@pytest.mark.parametrize(
    "old,new,code",
    [
        ("using Steps.attempt", "using Missing.step", "E2001"),
        ("state: State", "state: Missing", "E2001"),
        ("state.retry =>", "state.missing =>", "E2001"),
        ("state.retry =>", "state.count =>", "E2003"),
        ("state.retry =>", "state.count == true =>", "E2003"),
        ("state.retry =>", "state.count = 1 =>", "E2000"),
        ("state.retry =>", "Steps.attempt(state) =>", "E2000"),
        ("max_visits 3", "max_visits 0", "E2004"),
        ("timeout seconds(2)", "timeout seconds(0)", "E2004"),
        ("timeout seconds(2)", "timeout seconds(-1)", "E2004"),
        ("timeout seconds(2)", "timeout arbitrary(2)", "E2004"),
        ("max_visits 3", "max_visits 3 max_visits 4", "E2002"),
        ("public static State attempt", "public State attempt", "E2000"),
        ("public static State attempt", "public static bool attempt", "E2003"),
        ("State attempt(State state)", "State attempt(int state)", "E2003"),
        ("using Steps.undo", "using Missing.undo", "E2001"),
        ("otherwise => end", "otherwise => missing", "E2001"),
        ("start -> a", "start -> missing", "E2001"),
        ("a -> when", "missing -> when", "E2001"),
        ("start -> a;", "invariant state.retry; start -> a;", "E2000"),
    ],
)
def test_invalid_semantics(parser, old, new, code):
    result = resolve(parser, BASE.replace(old, new))
    assert code in {d.code for d in result.diagnostics}
    assert not result.graphs


@pytest.mark.parametrize(
    "suffix",
    [
        "import foo.bar;",
        "agent A {}",
        "policy Limits { invariant true; }",
        "class G<T> {}",
        "class Child extends State {}",
        "type Alias = State;",
    ],
)
def test_unsupported_features(parser, suffix):
    source = suffix + BASE if suffix.startswith("import") else BASE + suffix
    assert "E2000" in {d.code for d in resolve(parser, source).diagnostics}


def test_duplicate_members_nodes_and_graphs(parser):
    for source in [
        BASE + BASE,
        BASE.replace("int count;", "int count; int count;"),
        BASE.replace("start -> a;", "node a using Steps.undo; start -> a;"),
    ]:
        assert "E2002" in {d.code for d in resolve(parser, source).diagnostics}


def test_enum_nested_field_and_parenthesized_guards(parser):
    source = "enum Mode { ready, done } class Detail { Mode mode; } " + BASE.replace(
        "bool retry;", "bool retry; Detail detail;"
    ).replace("state.retry =>", "(state.retry && state.detail.mode == Mode.ready) =>")
    assert not resolve(parser, source).diagnostics
    source = source.replace("Mode.ready", "Mode.missing")
    assert "E2001" in {d.code for d in resolve(parser, source).diagnostics}


def test_multiple_graphs_and_attribute_wrapper(parser):
    assert (
        len(
            resolve(
                parser,
                BASE
                + "graph Second(s: State) { node b using Steps.undo; start -> b; b -> end; }",
            ).graphs
        )
        == 2
    )
    assert "E2000" in {d.code for d in resolve(parser, "@Tag " + BASE).diagnostics}


@pytest.mark.parametrize("emit", ["ast", "cst", "semantic-ast", "pnir"])
def test_check_never_bypasses_semantics(tmp_path, capsys, emit):
    file = tmp_path / "bad.panini"
    file.write_text(BASE.replace("using Steps.attempt", "using Missing.step"))
    assert main(["check", str(file), "--emit", emit, "--diagnostics", "json"]) == 1
    output = capsys.readouterr()
    assert not output.out
    assert "E2001" in {d["code"] for d in json.loads(output.err)}


def test_cli_valid_and_parse_remains_syntax_only(tmp_path, capsys):
    file = tmp_path / "valid.panini"
    file.write_text(BASE)
    assert main(["check", str(file)]) == 0
    assert json.loads(capsys.readouterr().out)["profile"] == "local-static-state-v1"
    file.write_text(BASE.replace("using Steps.attempt", "using Missing.step"))
    assert main(["parse", str(file)]) == 0
    assert json.loads(capsys.readouterr().out)["schemaVersion"] == 1


def test_m1_does_not_claim_m2_verification(parser):
    # Valid names/types, but unbounded cycle and no end path: intentionally M2.
    source = BASE.replace(" max_visits 3", "").replace(
        "a -> when state.retry => a otherwise => end;", "a -> a;"
    )
    result = resolve(parser, source)
    assert not result.diagnostics
    assert "graph-topology" in result.to_dict()["deferred"]


@pytest.mark.parametrize("stage", ["ast-v1", "ast-v2", "pnir"])
def test_schema_golden_files(parser, stage):
    fixtures = Path(__file__).parent / "fixtures"
    source = (fixtures / "semantic.panini").read_text()
    actual = (
        parser.ast(source, "semantic.panini")
        if stage == "ast-v1"
        else parser.semantic_ast(source, "semantic.panini")
        if stage == "ast-v2"
        else Resolver()
        .resolve(parser.semantic_ast(source, "semantic.panini"))
        .to_dict()
    )
    assert actual == json.loads((fixtures / f"{stage}.json").read_text())


def test_unicode_diagnostic_points_to_exact_expression(parser):
    source = (
        BASE.replace("class State", "class État")
        .replace(": State", ": État")
        .replace("State attempt(State", "État attempt(État")
        .replace("State undo(State", "État undo(État")
        .replace("-> State", "-> État")
        .replace("state.retry =>", "state.absent =>")
    )
    diagnostics = resolve(parser, source).diagnostics
    missing = next(d for d in diagnostics if "absent" in d.message)
    assert (
        source.encode()[missing.span["start"] : missing.span["end"]].decode()
        == "state.absent"
    )
    assert ":10:" in missing.render(source)


def test_wrong_compensation_signature_and_invalid_generics(parser):
    bad = BASE.replace("State undo(State state)", "bool undo(State state)")
    assert "E2003" in {d.code for d in resolve(parser, bad).diagnostics}
    for bad in [
        BASE.replace("int count;", "List<int> count;"),
        BASE.replace("State attempt(State state)", "State attempt<T>(State state)"),
    ]:
        assert "E2000" in {d.code for d in resolve(parser, bad).diagnostics}


def test_local_state_parameter_shadows_enum_name(parser):
    source = "enum state { retry } " + BASE
    result = resolve(parser, source)
    assert not result.diagnostics
    guard = result.graphs[0]["transitions"][0]["branches"][0]["guard"]
    assert guard["resolvedType"] == "bool"


def test_syntax_errors_have_json_diagnostics(tmp_path, capsys):
    file = tmp_path / "syntax.panini"
    file.write_text("class Broken {")
    assert main(["check", str(file), "--diagnostics", "json"]) == 1
    assert json.loads(capsys.readouterr().err)[0]["code"] == "E1001"


def test_equality_is_not_lexed_as_assignment(parser):
    source = BASE.replace("state.retry =>", "state.count == 1 =>")
    assert parser.ast(source)["schemaVersion"] == 1
    assert not resolve(parser, source).diagnostics


def test_ast_v1_empty_and_resolver_version_boundary(parser):
    assert parser.ast("")["schemaVersion"] == 1
    with pytest.raises(ValueError, match="AST v2"):
        Resolver().resolve(parser.ast(BASE))
    resolver = Resolver()
    assert not resolver.resolve(parser.semantic_ast("")).diagnostics
    with pytest.raises(ValueError, match="new Resolver"):
        resolver.resolve(parser.semantic_ast(BASE))


def test_method_metadata_and_symbol_source_order(parser):
    source = "class Failure {} " + BASE.replace(
        "State attempt(State state);", "State attempt(State state) throws Failure;"
    )
    result = resolve(parser, source)
    assert not result.diagnostics
    method = next(s for s in result.symbols if s["name"] == "Steps.attempt")
    assert method["throws"] == ["Failure"]
    assert method["parameters"][0]["typeSymbol"] == result.graphs[0]["stateType"]
    assert [s["span"]["start"] for s in result.symbols] == sorted(
        s["span"]["start"] for s in result.symbols
    )


@pytest.mark.parametrize(
    "member", ["void field;", "int f(void arg);", "void f() throws void;"]
)
def test_void_is_only_valid_as_a_method_return(parser, member):
    assert "E2003" in {
        d.code for d in resolve(parser, "class Invalid { " + member + " }").diagnostics
    }
