"""Typed probability decision protocol; no vendor integration or calibration claim."""

from __future__ import annotations

import math
from dataclasses import dataclass


class DecisionError(ValueError):
    pass


def valid_adapter(spec: object) -> bool:
    return (
        isinstance(spec, dict)
        and spec.get("output") == "probabilities"
        and isinstance(spec.get("version"), str)
        and bool(spec["version"].strip())
    )


@dataclass(frozen=True)
class Decision:
    choice: str | None
    abstained: bool
    probabilities: dict[str, float]
    model: str
    model_version: str

    def to_dict(self) -> dict:
        return {
            "choice": self.choice,
            "abstained": self.abstained,
            "probabilities": dict(self.probabilities),
            "model": self.model,
            "modelVersion": self.model_version,
        }


def decide(spec: dict, response: object) -> Decision:
    """Validate a probability adapter response, then threshold its largest entry.

    Every option is required. Ties use source option order. Below threshold means
    no actionable choice; equality is accepted. Never normalize arbitrary scores.
    """
    if not isinstance(response, dict) or set(response) != {
        "probabilities",
        "modelVersion",
    }:
        raise DecisionError("Expected probabilities and modelVersion")
    if response["modelVersion"] != spec["version"]:
        raise DecisionError("Adapter model version does not match compiled manifest")
    probabilities = response["probabilities"]
    labels = [o["name"] for o in spec["options"]]
    if not isinstance(probabilities, dict) or set(probabilities) != set(labels):
        raise DecisionError("Probability labels must exactly cover the decision enum")
    for value in probabilities.values():
        if (
            type(value) not in {int, float}
            or not 0 <= value <= 1
            or not math.isfinite(value)
        ):
            raise DecisionError("Probabilities must be finite numbers in [0,1]")
    if not math.isclose(sum(probabilities.values()), 1.0, rel_tol=0, abs_tol=1e-9):
        raise DecisionError("Probabilities must sum to one (absolute tolerance 1e-9)")
    choice = max(labels, key=lambda label: probabilities[label])
    abstained = probabilities[choice] < spec["threshold"]
    return Decision(
        None if abstained else choice,
        abstained,
        {label: float(probabilities[label]) for label in labels},
        spec["model"],
        spec["version"],
    )
