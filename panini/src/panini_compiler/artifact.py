"""Portable reference artifacts with explicit compilation and input budgets.

Digests detect corruption, not forgery. These limits are not a CPU/memory sandbox.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math

from lark.exceptions import UnexpectedInput

from .compiler import compile_source

FORMAT = "panini-reference-artifact-v1"
CAPABILITIES = frozenset({"sequential-effects-v1"})
ENVELOPE_FIELDS = frozenset({"format", "requires", "inputs", "program", "sha256"})


@dataclass(frozen=True)
class ArtifactLimits:
    max_artifact_bytes: int = 16 * 1024 * 1024
    max_source_bytes: int = 1024 * 1024
    max_json_depth: int = 128
    max_json_values: int = 200_000
    max_states: int = 10_000

    def __post_init__(self):
        for name, value in vars(self).items():
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.max_json_depth > 128 or self.max_states > 100_000:
            raise ValueError("Depth must be <= 128 and max_states <= 100000")


DEFAULT_LIMITS = ArtifactLimits()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _json_text(value, limits):
    # Walk before recursive serialization/copying. Cyclic Python containers also
    # exhaust this finite value/depth budget rather than recursing indefinitely.
    stack = [(value, 0)]
    count = estimated_bytes = 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if depth > limits.max_json_depth or count > limits.max_json_values:
            raise ValueError("Artifact JSON structure limit exceeded")
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise ValueError("Artifact JSON keys must be strings")
            estimated_bytes += sum(len(key.encode("utf-8")) for key in item)
            stack.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            stack.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            estimated_bytes += len(item.encode("utf-8"))
        elif type(item) is float:
            if not math.isfinite(item):
                raise ValueError("Artifact JSON numbers must be finite")
        elif item is not None and type(item) not in {bool, int}:
            raise ValueError("Artifact values must be JSON data")
        if estimated_bytes > limits.max_artifact_bytes:
            raise ValueError("Artifact byte limit exceeded")
    encoded = _canonical(value)
    if len(encoded.encode("utf-8")) > limits.max_artifact_bytes:
        raise ValueError("Artifact byte limit exceeded")
    return encoded


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate artifact JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f"Nonfinite JSON constant: {value}")


def _inputs(inputs, limits):
    if not isinstance(inputs, dict) or set(inputs) != {
        "source",
        "path",
        "adapters",
        "effects",
        "model",
    }:
        raise ValueError("Invalid artifact compilation inputs")
    if not isinstance(inputs["source"], str) or not isinstance(inputs["path"], str):
        raise ValueError("Artifact source and path must be text")
    if len(inputs["source"].encode("utf-8")) > limits.max_source_bytes:
        raise ValueError("Artifact source byte limit exceeded")
    for key in ("adapters", "effects", "model"):
        if inputs[key] is not None and not isinstance(inputs[key], dict):
            raise ValueError(f"Artifact {key} manifest must be an object")
    _json_text(inputs, limits)


def _compile(inputs, limits):
    try:
        result = compile_source(**inputs, max_states=limits.max_states)
    except (UnexpectedInput, RecursionError) as error:
        raise ValueError("Invalid or excessively nested artifact source") from error
    if result.diagnostics:
        codes = sorted({diagnostic.code for diagnostic in result.diagnostics})
        raise ValueError(f"Artifact compilation failed: {', '.join(codes)}")
    return result


def build_artifact(
    source,
    path="<artifact>",
    *,
    adapters=None,
    effects=None,
    model=None,
    limits=DEFAULT_LIMITS,
):
    """Bundle source/manifests and checked PNIR, subject to explicit limits."""
    inputs = {
        "source": source,
        "path": str(path),
        "adapters": adapters,
        "effects": effects,
        "model": model,
    }
    _inputs(inputs, limits)
    result = _compile(inputs, limits)
    payload = {
        "format": FORMAT,
        "requires": ["sequential-effects-v1"],
        "inputs": inputs,
        "program": result.to_dict(),
    }
    artifact = {
        **payload,
        "sha256": hashlib.sha256(_json_text(payload, limits).encode()).hexdigest(),
    }
    _json_text(artifact, limits)
    return artifact


def load_artifact(value, *, supported=CAPABILITIES, limits=DEFAULT_LIMITS):
    """Recompile instead of trusting check markers. No source methods execute.

    Byte/depth/value and finite-state budgets apply before recompilation. Callers
    still need a process sandbox/time limit for untrusted source or manifests.
    """
    if isinstance(value, str):
        if len(value.encode("utf-8")) > limits.max_artifact_bytes:
            raise ValueError("Artifact byte limit exceeded")
        try:
            artifact = json.loads(
                value, object_pairs_hook=_pairs, parse_constant=_reject_constant
            )
        except RecursionError as error:
            raise ValueError("Artifact JSON structure limit exceeded") from error
    else:
        artifact = value
    # Validate before copying or traversing untrusted nested data recursively.
    serialized = _json_text(artifact, limits)
    artifact = json.loads(serialized)
    if not isinstance(artifact, dict) or set(artifact) != ENVELOPE_FIELDS:
        raise ValueError("Invalid artifact envelope")
    if artifact["format"] != FORMAT or artifact["requires"] != [
        "sequential-effects-v1"
    ]:
        raise ValueError("Unsupported artifact format or requirements")
    missing = set(artifact["requires"]) - set(supported)
    if missing:
        raise ValueError(f"Unsupported target capabilities: {sorted(missing)}")
    digest = artifact.pop("sha256")
    if digest != hashlib.sha256(_canonical(artifact).encode()).hexdigest():
        raise ValueError("Artifact digest mismatch")
    inputs = artifact["inputs"]
    _inputs(inputs, limits)
    result = _compile(inputs, limits)
    if _canonical(result.to_dict()) != _canonical(artifact["program"]):
        raise ValueError("Artifact PNIR does not match reference compilation")
    return result
