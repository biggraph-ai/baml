import copy
import hashlib
import json
from pathlib import Path

import pytest

from panini_compiler.artifact import build_artifact, load_artifact, _canonical
from panini_compiler import Executor

SOURCE = (Path(__file__).parents[1] / "examples/retry.panini").read_text()


def test_roundtrip_executes_and_preserves_source_maps():
    artifact = build_artifact(SOURCE, "portable.panini")
    result = load_artifact(json.dumps(artifact))
    assert result.to_dict() == artifact["program"]
    runtime = Executor(
        result,
        "BoundedRetry",
        {"retry": False},
        capabilities={"Steps.attempt": lambda state: state},
    )
    assert runtime.run()["status"] == "ended"


def test_target_rejection():
    with pytest.raises(ValueError, match="capabilities"):
        load_artifact(build_artifact(SOURCE), supported=set())


@pytest.mark.parametrize("resign", [False, True])
def test_modified_pnir_rejected_even_with_recomputed_digest(resign):
    artifact = copy.deepcopy(build_artifact(SOURCE))
    artifact["program"]["graphs"] = []
    if resign:
        artifact.pop("sha256")
        artifact["sha256"] = hashlib.sha256(_canonical(artifact).encode()).hexdigest()
    with pytest.raises(ValueError):
        load_artifact(artifact)


def test_invalid_source_cannot_build():
    with pytest.raises(ValueError):
        build_artifact("graph G(s: Missing) { start -> end; }")


def test_frozen_wire_contract():
    fixture = Path(__file__).with_name("fixtures") / "artifact-v1.json"
    artifact = json.loads(fixture.read_text())
    assert build_artifact(artifact["inputs"]["source"], "portable.panini") == artifact
    assert load_artifact(artifact).to_dict() == artifact["program"]


def test_cli_build_inspect_and_capabilities(tmp_path, capsys):
    from panini_compiler.cli import main

    source = tmp_path / "retry.panini"
    source.write_text(SOURCE)
    target = tmp_path / "retry.pnir"
    assert main(["build", str(source), "-o", str(target)]) == 0
    assert main(["inspect", str(target)]) == 0
    assert json.loads(capsys.readouterr().out)["schemaVersion"] == 3
    assert main(["capabilities"]) == 0
    assert "sequential-effects-v1" in capsys.readouterr().out
    target.write_text("{}")
    assert main(["inspect", str(target)]) == 1
    assert "E2801" in capsys.readouterr().err


def test_labeled_corpus():
    import runpy

    evaluation = runpy.run_path(str(Path(__file__).parents[1] / "evaluation/run.py"))
    report = evaluation["evaluate_corpus"]()
    assert report["falsePositives"] == 0
    assert all(case["actual"] == case["expected"] for case in report["cases"])


def resign(artifact):
    artifact = copy.deepcopy(artifact)
    artifact.pop("sha256", None)
    artifact["sha256"] = hashlib.sha256(_canonical(artifact).encode()).hexdigest()
    return artifact


@pytest.mark.parametrize(
    "field,value",
    [
        ("format", "panini-future-v999"),
        ("requires", ["parallel-v1"]),
    ],
)
def test_unknown_format_and_profile(field, value):
    artifact = build_artifact(SOURCE)
    artifact[field] = value
    with pytest.raises(ValueError, match="Unsupported"):
        load_artifact(resign(artifact))


def test_exact_documented_five_field_envelope():
    artifact = build_artifact(SOURCE)
    assert set(artifact) == {"format", "requires", "inputs", "program", "sha256"}
    for key in artifact:
        bad = copy.deepcopy(artifact)
        del bad[key]
        with pytest.raises(ValueError, match="envelope"):
            load_artifact(bad)
    with pytest.raises(ValueError, match="envelope"):
        load_artifact({**artifact, "extra": True})


def test_edited_source_with_valid_digest_cannot_reuse_checked_program():
    artifact = build_artifact(SOURCE)
    artifact["inputs"]["source"] = SOURCE.replace("max_visits 3", "max_visits 4")
    with pytest.raises(ValueError, match="PNIR"):
        load_artifact(resign(artifact))


def test_tampered_digest():
    artifact = build_artifact(SOURCE)
    artifact["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="digest"):
        load_artifact(artifact)


@pytest.mark.parametrize("name", ["adapters", "effects", "model"])
@pytest.mark.parametrize("value", [[], "bad", 7, {"bad": float("nan")}])
def test_malformed_manifests(name, value):
    with pytest.raises(ValueError):
        build_artifact(SOURCE, **{name: value})
    artifact = build_artifact(SOURCE)
    artifact["inputs"][name] = value
    # NaN is rejected before digest validation; other invalid shapes are resigned.
    if not isinstance(value, dict):
        artifact = resign(artifact)
    with pytest.raises(ValueError):
        load_artifact(artifact)


def test_source_artifact_depth_and_value_limits():
    from panini_compiler.artifact import ArtifactLimits

    artifact = build_artifact(SOURCE)
    for value in [artifact, json.dumps(artifact)]:
        with pytest.raises(ValueError, match="byte limit"):
            load_artifact(value, limits=ArtifactLimits(max_artifact_bytes=10))
        with pytest.raises(ValueError, match="source byte"):
            load_artifact(value, limits=ArtifactLimits(max_source_bytes=10))
        with pytest.raises(ValueError, match="structure limit"):
            load_artifact(value, limits=ArtifactLimits(max_json_depth=2))
        with pytest.raises(ValueError, match="structure limit"):
            load_artifact(value, limits=ArtifactLimits(max_json_values=2))
    with pytest.raises(ValueError, match="source byte"):
        build_artifact("é" * 10, limits=ArtifactLimits(max_source_bytes=10))
    cyclic = []
    cyclic.append(cyclic)
    with pytest.raises(ValueError, match="structure limit"):
        load_artifact(cyclic)


def test_json_duplicate_keys_and_nonfinite_constants():
    for text in ['{"format":1,"format":2}', '{"x":NaN}', '{"x":Infinity}']:
        with pytest.raises(ValueError):
            load_artifact(text)


def test_finite_model_resource_limit_cannot_reuse_proof():
    from panini_compiler.artifact import ArtifactLimits

    root = Path(__file__).parents[1] / "examples"
    artifact = build_artifact(
        (root / "refund.panini").read_text(),
        effects=json.loads((root / "refund-effects.json").read_text()),
        model=json.loads((root / "refund-model.json").read_text()),
    )
    assert artifact["program"]["analysis"]["status"] == "proved"
    with pytest.raises(ValueError, match="E2702"):
        load_artifact(artifact, limits=ArtifactLimits(max_states=1))


def test_cli_budget_failure_preserves_existing_output(tmp_path, capsys):
    from panini_compiler.cli import main

    source = tmp_path / "source.panini"
    source.write_text(SOURCE)
    target = tmp_path / "existing.pnir"
    target.write_text("previous output")
    assert (
        main(["build", str(source), "-o", str(target), "--max-source-bytes", "10"]) == 1
    )
    assert "E2801" in capsys.readouterr().err
    assert target.read_text() == "previous output"


def test_expanded_regression_corpus():
    import runpy

    root = Path(__file__).parents[1] / "evaluation"
    evaluation = runpy.run_path(str(root / "run.py"))
    report = evaluation["evaluate_corpus"](root / "regression-corpus.json")
    assert len(report["cases"]) >= 40
    assert report["falsePositives"] == 0
    assert all(case["actual"] == case["expected"] for case in report["cases"])
    assert report["micro"]["fp"] == report["micro"]["fn"] == 0


def test_corpus_metrics_do_not_count_wrong_diagnostic_as_true_positive(tmp_path):
    import runpy

    evaluate = runpy.run_path(str(Path(__file__).parents[1] / "evaluation/run.py"))[
        "evaluate_corpus"
    ]
    cases = tmp_path / "cases.json"
    cases.write_text(
        json.dumps(
            [
                {
                    "id": "wrong-code",
                    "defect": "resolution",
                    "expected": ["E2001"],
                    "body": "node a using Steps.run; start -> a; a -> when 1 => end otherwise => end;",
                },
                {
                    "id": "false-positive",
                    "defect": None,
                    "expected": [],
                    "body": "node a using Steps.absent; start -> a; a -> end;",
                },
            ]
        )
    )
    report = evaluate(cases)
    assert report["falsePositives"] == 1
    assert report["classes"]["resolution"]["recall"] == 0
    assert report["micro"] == {
        "tp": 0,
        "fp": 2,
        "fn": 1,
        "precision": 0.0,
        "recall": 0.0,
    }
