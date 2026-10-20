# Portable programs and compiler artifacts

A Panini artifact packages source, manifests and checked PNIR so the reference
loader can validate the same program and contracts before execution. Host
callbacks, credentials and live runtime records are supplied separately.

## Build and inspect

From `panini/` with its environment on `PATH`:

```bash
panini build examples/retry.panini -o /tmp/retry.pnir
panini inspect /tmp/retry.pnir
panini capabilities
```

`build` accepts `--adapters`, `--effects` and `--model` JSON manifests. It rejects
compiler diagnostics, including finite-analysis results that are not `proved`.
`inspect` validates a bundle and emits its checked PNIR. Failures return nonzero
with E2801 on stderr. `check` remains the source-oriented diagnostic interface.

## Envelope

The format `panini-reference-artifact-v1` has exactly five top-level fields:

| Field | Content |
| --- | --- |
| `format` | Artifact format identifier |
| `requires` | Required runtime profile: `["sequential-effects-v1"]` |
| `inputs` | Source text, display path and nullable adapter/effect/model manifests |
| `program` | Checked PNIR schemaVersion 3 |
| `sha256` | Digest of the other four fields |

The digest covers UTF-8 JSON with sorted keys, compact separators and no NaN
values. It detects corruption, not publisher identity. Source and manifest
contents are visible to artifact readers; do not embed secrets in either.

## Python API

Run this example from `panini/`:

```python
import json
from pathlib import Path
from panini_compiler import Executor
from panini_compiler.artifact import build_artifact, load_artifact

artifact = build_artifact(
    Path("examples/retry.panini").read_text(encoding="utf-8"), "retry.panini"
)
program = load_artifact(json.dumps(artifact))
execution = Executor(
    program, "BoundedRetry", {"retry": False},
    capabilities={"Steps.attempt": lambda state: state},
)
assert execution.run()["status"] == "ended"
```

Loading checks the envelope, profile requirements and digest, recompiles the
embedded inputs, and compares the entire canonical PNIR. It does not trust
supplied proof/check markers. The returned `CheckResult` can be passed to
`Executor` with separately supplied adapters, capability bindings, authorization
and journal. Target support can be restricted through `supported=...`.

## Resource limits and trust

`ArtifactLimits` defaults to 1 MiB source, 16 MiB artifact JSON, nesting depth
128, 200,000 JSON values and 10,000 finite-model states. Both API entry points
accept `limits=ArtifactLimits(...)`. The CLI exposes `--max-source-bytes`,
`--max-artifact-bytes`, `--max-json-depth` and `--max-states`, and performs bounded
file reads. Loading rejects duplicate JSON keys and nonfinite constants.

These limits do not bound compiler CPU time or create a hostile-input sandbox.
Applications remain responsible for source trust, process isolation and time
budgets. A digest is not a signature. A checked capability declaration does not
certify its external implementation.

## Representations and compatibility

PNIR v3 contains symbols, graphs, types, contracts, checks performed, deferred
obligations, diagnostics and optional finite-analysis results. AST v1 is a
normalized syntax view; semantic AST v2 preserves tokens and operator identity.
Source-order identities and half-open UTF-8 byte spans retain source locations
through compilation. The display filename need not exist on another machine.

The loader requires exact reference compilation, not a best-effort migration.
Unknown formats/profiles or incompatible output fail closed. Checkpoint schema
v2 is a separate runtime format containing state and execution history; a program
artifact is not a checkpoint. Runtime preflight still checks bindings and
platform requirements after an artifact loads.

`tests/fixtures/artifact-v1.json` and the conformance suite pin the envelope,
PNIR and source-map behavior. See [language](language.md), [runtime](runtime.md)
and the [evaluation protocol](../panini/evaluation/README.md) for the corresponding
semantics and reproducibility evidence.
