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
