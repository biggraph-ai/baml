# Panini

A reference compiler for bounded sequential agent workflows, with structural
graph checks, a capability-based executor, and optional finite-state invariant
analysis under explicit contracts.

This `alphaxiv` branch contains the standalone Panini research implementation,
examples, tests, language documentation and evaluation evidence. It was branched
from `nag/create-new-project-for-panini-compiler` at commit
`8390c95c0`; the unrelated monorepo implementation has been removed from this
branch's source tree. Git ancestry and original license are preserved.

## Quick start

Python 3.11+ is supported; the pinned evaluation environment uses Python 3.12
on Linux. From this repository root:

```bash
cd panini
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/panini check examples/retry.panini
.venv/bin/python examples/run_decision.py
.venv/bin/python examples/run_refund.py
```

See the [package README](panini/README.md) for Windows setup, CLI commands,
portable artifacts and runtime bindings. The optional Bison grammar check uses
`tools/check-panini-grammar.sh` and requires GNU Bison and m4.

## Research artifact

- [Language design](docs/panini-language-design.md)
- [Runtime effects and conditional verification](docs/panini-effects-verification.md)
- [Portable artifact contract](docs/panini-artifacts-evaluation.md)
- [Evaluation and reproducibility protocol](panini/evaluation/README.md)
- [Scientific claims and evaluation metrics](panini/evaluation/publication-evaluation.md)
- [Recorded scorecard](panini/evaluation/publication-metrics.json)

The `panini/` layout is retained so existing fixtures, examples and evidence paths
remain valid. Historical evaluation records identify the commits actually tested;
removing unrelated source does not turn those observations into new experiments.
The original artifact tag remains attached to its original revision.

The implementation does not prove arbitrary host-code termination, general
liveness, provider accuracy, remote atomicity or successful compensation.
Held-out effectiveness, matched framework baselines and multi-machine results
remain unmeasured. Proposed formal statements are proof obligations, not
completed metatheoretic proofs. No preprint manuscript is included yet.

Licensed under [Apache-2.0](LICENSE).
