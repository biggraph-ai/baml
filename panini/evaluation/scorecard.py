"""Derive descriptive metrics from recorded evidence; run no new experiments.

From panini/: python evaluation/scorecard.py --output evaluation/publication-metrics.json
Repeated timings are nested by process; summaries use process medians, not
21 allegedly independent observations. Missing experiments remain null.
"""

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import statistics


def summarize(records, stage):
    if not records:
        raise ValueError("No timing processes")
    medians = []
    for row in records:
        samples = row["timings"][stage]["samplesSeconds"]
        if not samples or any(
            type(value) not in {int, float} or not math.isfinite(value) or value < 0
            for value in samples
        ):
            raise ValueError("Timing samples must be finite nonnegative numbers")
        medians.append(statistics.median(samples) * 1000)
    return {
        "unit": "ms",
        "medianOfProcessMedians": statistics.median(medians),
        "minProcessMedian": min(medians),
        "maxProcessMedian": max(medians),
        "processMedians": medians,
        "processes": len(records),
        "samplesPerProcess": [
            len(row["timings"][stage]["samplesSeconds"]) for row in records
        ],
    }


def detection(corpus, labels):
    by_id = {case["id"]: case for case in labels}
    observed = {case["id"]: case for case in corpus["cases"]}
    if (
        len(by_id) != len(labels)
        or len(observed) != len(corpus["cases"])
        or set(by_id) != set(observed)
    ):
        raise ValueError("Evidence and source corpus must have matching unique IDs")
    groups = defaultdict(lambda: {"cases": 0, "exactCodes": 0, "detected": 0})
    tp = fp = fn = false_alarms = controls = 0
    for ident, case in by_id.items():
        expected = set(case["expected"])
        actual = set(observed[ident]["actual"])
        if expected != set(observed[ident]["expected"]):
            raise ValueError("Evidence labels differ from source labels")
        group = groups[case["defect"] or "valid-control"]
        group["cases"] += 1
        group["exactCodes"] += expected == actual
        group["detected"] += bool(expected & actual)
        tp += len(expected & actual)
        fp += len(actual - expected)
        fn += len(expected - actual)
        if not expected:
            controls += 1
            false_alarms += bool(actual)
    negative = groups["valid-control"]
    unsupported = groups.get("unsupported", {"cases": 0, "detected": 0})
    supported = [
        value
        for key, value in groups.items()
        if key not in {"unsupported", "valid-control"}
    ]
    for name, group in groups.items():
        group["expectedCodeRecall"] = (
            group["detected"] / group["cases"] if name != "valid-control" else None
        )
    return {
        "cases": len(labels),
        "validControls": controls,
        "supportedFaultCases": sum(group["cases"] for group in supported),
        "supportedFaultsDetected": sum(group["detected"] for group in supported),
        "unsupportedCases": unsupported["cases"],
        "unsupportedRejected": unsupported["detected"],
        "exactCodeMatches": sum(group["exactCodes"] for group in groups.values()),
        "diagnosticMicro": {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
        },
        "falsePositiveControls": false_alarms,
        "falsePositiveRateOnControls": false_alarms / controls if controls else None,
        "validControlsAccepted": negative["cases"] - false_alarms,
        "byClass": dict(groups),
        "inference": "Descriptive author-derived regression results; no population confidence interval or held-out effectiveness claim.",
    }


def generate(root):
    names = [
        "review-matrix-results.json",
        "review-validation-results.json",
        "review-hardware.json",
        "regression-corpus.json",
    ]
    inputs = {name: json.loads((root / name).read_text()) for name in names}
    matrix = inputs["review-matrix-results.json"]
    validation = inputs["review-validation-results.json"]
    groups = defaultdict(list)
    for row in matrix["runs"]:
        if row["environment"]["commit"] != validation["revision"]:
            raise ValueError("Timing and validation revisions differ")
        groups[row["nodes"]].append(row)
    stages = (
        "semanticParsing",
        "resolutionAndLowering",
        "topologyVerification",
        "serialization",
    )
    measurements = {
        str(size): {stage: summarize(rows, stage) for stage in stages}
        for size, rows in sorted(groups.items())
    }
    first, last = min(groups), max(groups)
    growth = {}
    for stage in stages:
        ratio = (
            measurements[str(last)][stage]["medianOfProcessMedians"]
            / measurements[str(first)][stage]["medianOfProcessMedians"]
        )
        growth[stage] = {
            "sizeFactor": last / first,
            "timeFactor": ratio,
            "endpointLogLogSlope": math.log(ratio) / math.log(last / first),
            "meaning": "Descriptive endpoint ratio; not an asymptotic complexity estimate.",
        }
    test_outputs = [
        row["testResult"] for row in validation["commands"] if "testResult" in row
    ]
    matched = re.search(r"(\d+) passed in ([\d.]+)s", "\n".join(test_outputs))
    if (
        not matched
        or validation.get("status") != "passed"
        or any(row["exitCode"] != 0 for row in validation["commands"])
    ):
        raise ValueError("No successful recorded test validation")
    return {
        "schemaVersion": 1,
        "kind": "PublicationEvaluationScorecard",
        "revisionMeasured": validation["revision"],
        "sourceSha256": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in names
        },
        "artifactValidation": {
            "passedTests": int(matched[1]),
            "testSeconds": float(matched[2]),
            "successfulCommands": sum(
                row["exitCode"] == 0 for row in validation["commands"]
            ),
            "totalCommands": len(validation["commands"]),
            "machines": 1,
        },
        "hardware": inputs["review-hardware.json"],
        "detection": detection(
            matrix["regressionCorpus"], inputs["regression-corpus.json"]
        ),
        "warmCompilerStages": measurements,
        "initialization": {
            stage: summarize(matrix["runs"], stage)
            for stage in ("parserInitialization", "semanticParserInitialization")
        },
        "fixedRefundModelTiming": {
            **summarize(matrix["runs"], "finiteModelExploration"),
            "stateSpaceScalabilityMeasured": False,
            "note": "Same refund model across every graph-size run; do not interpret this as model-size scaling.",
        },
        "endpointGrowth": growth,
        "unmeasured": {
            name: None
            for name in (
                "heldOutWorkflowDetection",
                "runtimeOverhead",
                "recoveryLatency",
                "modelStateSpaceScaling",
                "peakMemory",
                "crossMachineVariance",
                "matchedBaselineEffect",
                "providerQualityOrLatency",
            )
        },
        "proofStatus": "Theorem statements and proof obligations proposed in publication-evaluation.md; no completed metatheoretic or mechanized proof claimed.",
        "statisticalUnit": "Process for descriptive timing; workflow family for proposed held-out comparisons. Nested timing samples are not independent experiments.",
    }


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--output", type=Path, required=True)
    args = cli.parse_args()
    result = generate(Path(__file__).resolve().parent)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
