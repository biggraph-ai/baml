"""Portable, self-checking reference artifact. Digests detect corruption, not forgery."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from .compiler import compile_source

FORMAT = "panini-reference-artifact-v1"
CAPABILITIES = frozenset({"sequential-effects-v1"})


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def build_artifact(
    source, path="<artifact>", *, adapters=None, effects=None, model=None
):
    """Bundle source, manifests and checked PNIR without host bindings or credentials."""
    inputs = {
        "source": source,
        "path": str(path),
        "adapters": adapters,
        "effects": effects,
        "model": model,
    }
    if not isinstance(source, str):
        raise ValueError("Artifact source must be text")
    if any(
        value is not None and not isinstance(value, dict)
        for value in (adapters, effects, model)
    ):
        raise ValueError("Artifact manifests must be objects")
    result = compile_source(**inputs)
    if result.diagnostics:
        raise ValueError("Cannot build an artifact with compiler diagnostics")
    payload = {
        "format": FORMAT,
        "requires": ["sequential-effects-v1"],
        "inputs": inputs,
        "program": result.to_dict(),
    }
    return {
        **payload,
        "sha256": hashlib.sha256(_canonical(payload).encode()).hexdigest(),
    }


def load_artifact(value, *, supported=CAPABILITIES):
    """Recompile and compare: supplied check markers and proof claims are not trusted.

    Compilation executes no source methods. Callers still control resource limits and
    trust for source/manifests; this is not a hostile-input sandbox or signature scheme.
    """
    artifact = json.loads(value) if isinstance(value, str) else deepcopy(value)
    if not isinstance(artifact, dict) or set(artifact) != {
        "format",
        "requires",
        "inputs",
        "program",
        "sha256",
    }:
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
    if not isinstance(inputs, dict) or set(inputs) != {
        "source",
        "path",
        "adapters",
        "effects",
        "model",
    }:
        raise ValueError("Invalid artifact compilation inputs")
    if not isinstance(inputs["source"], str) or not isinstance(inputs["path"], str):
        raise ValueError("Invalid artifact source")
    for key in ("adapters", "effects", "model"):
        if inputs[key] is not None and not isinstance(inputs[key], dict):
            raise ValueError(f"Invalid artifact {key}")
    result = compile_source(**inputs)
    if result.diagnostics or _canonical(result.to_dict()) != _canonical(
        artifact["program"]
    ):
        raise ValueError("Artifact PNIR does not match reference compilation")
    return result
