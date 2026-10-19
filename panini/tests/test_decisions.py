import json
from pathlib import Path

import pytest

from panini_compiler.cli import main
from panini_compiler.decisions import DecisionError, decide
from panini_compiler.parser import PaniniParser
from panini_compiler.resolver import Resolver
from panini_compiler.verifier import verify_program


MANIFEST = {"Local": {"output": "probabilities", "version": "mock-v1"}}
SOURCE = """
enum Intent { approve, review }
class S { string reason; Decision<Intent> result; }
agent Triage {
  public Decision<Intent> classify(S s) {
    decision_model Local;
    decide s.reason over { approve: "Eligible"; review: "Uncertain"; } abstain_below 0.8;
  }
}
class T { public static S approve(S s); public static S review(S s); }
graph G(s: S) {
  node triage using Triage.classify writes s.result max_visits 1;
  node approve using T.approve;
  node review using T.review;
  start -> triage;
  triage -> when s.result.abstained == false && s.result.choice == Intent.approve
              => approve otherwise => review;
  approve -> end;
  review -> end;
}
"""


@pytest.fixture(scope="module")
def parser():
    return PaniniParser.create()


def compiled(parser, source=SOURCE, manifest=MANIFEST):
    return verify_program(
        Resolver(manifest).resolve(parser.semantic_ast(source, "decision.panini"))
    )


def spec(parser):
    program = compiled(parser)
    assert not program.diagnostics
    return next(s["decision"] for s in program.symbols if "decision" in s)


def test_typed_decision_ir_and_binding(parser):
    result = compiled(parser)
    assert not result.diagnostics
    assert result.to_dict()["schemaVersion"] == 3
    node = result.graphs[0]["nodes"][0]
    assert node["outputBinding"]["type"] == "Decision<Intent>"
    method = next(s for s in result.symbols if s["id"] == node["callable"])
    assert method["effects"] == ["inference"]
    assert method["decision"]["threshold"] == 0.8
    assert [o["name"] for o in method["decision"]["options"]] == ["approve", "review"]
    assert result.graphs[0]["verification"]["dispatchBound"] == 3


@pytest.mark.parametrize(
    "old,new,code",
    [
        ('review: "Uncertain";', "", "E2502"),
        ('review: "Uncertain";', 'approve: "Again";', "E2502"),
        ('review: "Uncertain";', 'other: "Unknown";', "E2502"),
        ("Decision<Intent> result", "Decision<S> result", "E2501"),
        ("Decision<Intent> result", "int result", "E2003"),
        ("writes s.result", "writes s.absent", "E2503"),
        ("writes s.result", "writes other.result", "E2503"),
        ("writes s.result", "", "E2003"),
        ("0.8;", "1.1;", "E2506"),
        ("0.8;", "-0.1;", "E2506"),
        ("0.8;", "s.reason;", "E2506"),
        ("decide s.reason", "decide s.result", "E2501"),
        ("decision_model Local", "decision_model Missing", "E2504"),
        ("s.result.abstained == false && ", "", "E2505"),
        ("s.result.abstained == false && ", "s.result.abstained == false || ", "E2505"),
        (
            "s.result.abstained == false && s.result.choice == Intent.approve",
            "s.result.choice == Intent.approve && s.result.abstained == false",
            "E2505",
        ),
        ('review: "Uncertain";', 'review: "bad\\q";', "E2003"),
    ],
)
def test_invalid_decision_contracts(parser, old, new, code):
    result = compiled(parser, SOURCE.replace(old, new))
    assert code in {d.code for d in result.diagnostics}
    assert not result.graphs


@pytest.mark.parametrize(
    "condition",
    [
        "!s.result.abstained && s.result.choice == Intent.approve",
        "s.result.choice != null && s.result.choice == Intent.approve",
        "s.result.abstained || s.result.choice == Intent.approve",
        "false == s.result.abstained && s.result.choice == Intent.approve",
        "(s.result.abstained == false) && s.result.choice == Intent.approve",
    ],
)
def test_short_circuit_choice_refinements(parser, condition):
    source = SOURCE.replace(
        "s.result.abstained == false && s.result.choice == Intent.approve", condition
    )
    assert not compiled(parser, source).diagnostics


@pytest.mark.parametrize(
    "manifest",
    [
        {},
        {"Local": {"output": "scores", "version": "v1"}},
        {"Local": {"output": "probabilities"}},
        {"Local": "not-a-manifest"},
    ],
)
def test_adapter_capabilities_are_explicit(parser, manifest):
    assert "E2504" in {d.code for d in compiled(parser, manifest=manifest).diagnostics}


def test_threshold_boundary_abstention_and_ties(parser):
    definition = spec(parser)
    response = {
        "probabilities": {"approve": 0.8, "review": 0.2},
        "modelVersion": "mock-v1",
    }
    assert decide(definition, response).choice == "approve"
    response["probabilities"] = {"approve": 0.79, "review": 0.21}
    result = decide(definition, response)
    assert result.choice is None and result.abstained
    definition["threshold"] = 0.5
    response["probabilities"] = {"review": 0.5, "approve": 0.5}
    assert decide(definition, response).choice == "approve"
    definition["options"].reverse()
    assert decide(definition, response).choice == "review"


@pytest.mark.parametrize(
    "probabilities",
    [
        {"approve": float("nan"), "review": 0.5},
        {"approve": float("inf"), "review": 0},
        {"approve": 0.2, "review": 0.2},
        {"approve": 0.8},
        {"approve": -0.1, "review": 1.1},
        {"approve": True, "review": 0},
        {"approve": "0.8", "review": 0.2},
        {"approve": 10**1000, "review": 0},
    ],
)
def test_bad_probabilities_fail_instead_of_becoming_decisions(parser, probabilities):
    with pytest.raises(DecisionError):
        decide(
            spec(parser), {"probabilities": probabilities, "modelVersion": "mock-v1"}
        )


def test_backend_version_mismatch(parser):
    with pytest.raises(DecisionError):
        decide(
            spec(parser),
            {"probabilities": {"approve": 0.8, "review": 0.2}, "modelVersion": "wrong"},
        )


def test_decision_cli_requires_manifest_and_checks_all_emit_modes(tmp_path, capsys):
    file = tmp_path / "decision.panini"
    file.write_text(SOURCE)
    manifest = tmp_path / "adapters.json"
    manifest.write_text(json.dumps(MANIFEST))
    assert main(["check", str(file), "--diagnostics", "json"]) == 1
    assert "E2504" in {d["code"] for d in json.loads(capsys.readouterr().err)}
    for emit in ["ast", "cst", "semantic-ast", "pnir"]:
        assert (
            main(["check", str(file), "--adapters", str(manifest), "--emit", emit]) == 0
        )
        assert capsys.readouterr().out
    manifest.write_text("[]")
    assert (
        main(["check", str(file), "--adapters", str(manifest), "--diagnostics", "json"])
        == 1
    )
    assert json.loads(capsys.readouterr().err)[0]["code"] == "E2504"


@pytest.mark.parametrize("replacement,threshold", [("0", 0.0), ("1", 1.0)])
def test_threshold_endpoints(parser, replacement, threshold):
    result = compiled(parser, SOURCE.replace("0.8;", replacement + ";"))
    assert not result.diagnostics
    definition = next(s["decision"] for s in result.symbols if "decision" in s)
    assert definition["threshold"] == threshold


def test_default_threshold_and_no_vendor_execution(parser):
    result = compiled(parser, SOURCE.replace(" abstain_below 0.8", ""))
    assert not result.diagnostics
    assert (
        next(s["decision"] for s in result.symbols if "decision" in s)["threshold"] == 0
    )


def test_verified_decision_schema_golden(parser):
    root = Path(__file__).parents[1]
    source = (root / "examples" / "decision.panini").read_text()
    manifest = json.loads((root / "examples" / "decision-adapters.json").read_text())
    result = verify_program(
        Resolver(manifest).resolve(parser.semantic_ast(source, "decision.panini"))
    )
    assert not result.diagnostics
    assert result.to_dict() == json.loads(
        (root / "tests" / "fixtures" / "decision-pnir.json").read_text()
    )


@pytest.mark.parametrize(
    "condition",
    [
        "(s.result.abstained == false || true) && s.result.choice == Intent.approve",
        "s.result.abstained == true && s.result.choice == Intent.approve",
        "!s.result.abstained || s.result.choice == Intent.approve",
    ],
)
def test_unsafe_refinement_is_not_inferred(parser, condition):
    source = SOURCE.replace(
        "s.result.abstained == false && s.result.choice == Intent.approve", condition
    )
    assert "E2505" in {d.code for d in compiled(parser, source).diagnostics}
