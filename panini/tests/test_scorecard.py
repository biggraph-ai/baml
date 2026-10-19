"""The scorecard must preserve replication units and fail on inconsistent evidence."""

import json
from pathlib import Path
import runpy

import pytest

ROOT = Path(__file__).parents[1] / "evaluation"
API = runpy.run_path(str(ROOT / "scorecard.py"))


def test_nested_samples_are_not_independent_processes():
    records = [
        {"timings": {"parse": {"samplesSeconds": [1, 2, 3], "medianSeconds": -999}}},
        {"timings": {"parse": {"samplesSeconds": [100]}}},
        {"timings": {"parse": {"samplesSeconds": [101]}}},
    ]
    result = API["summarize"](records, "parse")
    assert result["medianOfProcessMedians"] == 100_000
    assert result["processes"] == 3
    assert result["samplesPerProcess"] == [3, 1, 1]


@pytest.mark.parametrize("samples", [[], [float("nan")], [-1], [True]])
def test_invalid_timing_evidence_is_rejected(samples):
    with pytest.raises(ValueError):
        API["summarize"]([{"timings": {"stage": {"samplesSeconds": samples}}}], "stage")


def test_missing_and_unsupported_faults_stay_separate():
    labels = [
        {"id": "fault", "defect": "resolution", "expected": ["E2001"]},
        {"id": "unsupported", "defect": "unsupported", "expected": ["E2000"]},
        {"id": "valid", "defect": None, "expected": []},
    ]
    corpus = {
        "cases": [
            {**labels[0], "actual": ["E2003"]},
            {**labels[1], "actual": ["E2000"]},
            {**labels[2], "actual": ["E2001"]},
        ]
    }
    result = API["detection"](corpus, labels)
    assert result["supportedFaultCases"] == 1
    assert result["supportedFaultsDetected"] == 0
    assert result["unsupportedRejected"] == 1
    assert result["diagnosticMicro"] == {
        "tp": 1,
        "fp": 2,
        "fn": 1,
        "precision": 1 / 3,
        "recall": 1 / 2,
    }
    assert result["falsePositiveControls"] == 1
    corpus["cases"][0]["expected"] = []
    with pytest.raises(ValueError, match="labels"):
        API["detection"](corpus, labels)


def test_generated_scorecard_preserves_evidence_and_missing_experiments():
    generated = API["generate"](ROOT)
    assert generated == json.loads((ROOT / "publication-metrics.json").read_text())
    assert generated["fixedRefundModelTiming"]["stateSpaceScalabilityMeasured"] is False
    assert all(value is None for value in generated["unmeasured"].values())
