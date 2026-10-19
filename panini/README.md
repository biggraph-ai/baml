# Panini reference compiler

This directory contains the Python reference frontend for the Panini language
specified in [`../docs`](../docs/panini-language-design.md). It is deliberately
an independent project: Panini is a new language rather than a BAML parser mode.

This is a reference compiler for bounded sequential agent workflows, with
structural graph checks and optional finite-state invariant analysis under
explicit contracts. It implements an inspectable semantic/graph pipeline,
a durable capability-based executor, and opt-in finite invariant verification:

```text
.panini source -> tokens/CST -> semantic AST -> typed PNIR -> graph checks
```

## Install and run on Windows

Panini requires Python 3.11 or newer. In PowerShell, run these commands
from the repository root:

```powershell
cd panini
py -3.11 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
& .\.venv\Scripts\python.exe -m pytest
& .\.venv\Scripts\panini.exe parse examples\audit.panini --emit ast
```

Activation is intentionally not required, so these commands also work when the
PowerShell execution policy prevents running `Activate.ps1`. If you prefer an
activated environment, run `.\.venv\Scripts\Activate.ps1` first and then use
`python`, `pytest`, and `panini` without their full paths.

From `cmd.exe`, the equivalent setup is:

```batch
cd panini
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest
.venv\Scripts\panini.exe parse examples\audit.panini --emit ast
```

This Python frontend does not need a C/C++ compiler: installing it creates the
`panini.exe` command-line launcher inside `.venv\Scripts`. The repository's
Bison C++ grammar tooling is separate and is not required for this initial
frontend.

### Build an installable package

To produce a wheel and source archive on Windows, run:

```powershell
& .\.venv\Scripts\python.exe -m build
& .\.venv\Scripts\python.exe -m pip install --force-reinstall .\dist\panini_compiler-0.1.0-py3-none-any.whl
& .\.venv\Scripts\panini.exe --help
```

The artifacts are written to `panini\dist`. A wheel is the normal compiled
distribution artifact for this pure-Python project; it is portable across
Windows machines that have a supported Python version.

If `py` is not recognized, install Python from python.org with the Python
Launcher enabled, or replace `py -3.11` with the full path to a Python 3.11+
executable. If Windows Store aliases intercept `python`, using `py` or the
explicit `.venv\Scripts\python.exe` path avoids the alias.

## Development on macOS or Linux

Panini requires Python 3.11 or newer.

```bash
cd panini
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

The installed command exposes the initial compiler debugging surface:

```bash
panini lex examples/audit.panini
panini parse examples/audit.panini --emit cst
panini parse examples/audit.panini --emit ast
panini check examples/retry.panini
panini check examples/decision.panini --adapters examples/decision-adapters.json
python examples/run_decision.py
panini check examples/refund.panini --effects examples/refund-effects.json --model examples/refund-model.json
python examples/run_refund.py
```

`parse` performs syntax checking. `check` resolves/types the
`sequential-effects-v1` profile, verifies graph structure and dispatch budgets,
and emits PNIR v3. `audit.panini` remains a syntax-only design demo.

`parse --emit ast` remains AST v1; `--emit semantic-ast` emits semantic AST v2.
All `check --emit` modes validate. `--diagnostics json` emits structured errors.
Use `--effects` for typed capability/receipt metadata and `--model` for finite
safety analysis. Non-proofs return a nonzero exit code with a counterexample,
`unknown`, or `unsupported` report instead of a proof.

The runtime supports deadlines, exceptional edges, authorized effects, SQLite
journaling/resume, bounded reconciliation/compensation, and runtime invariants
and ordered policies. The finite checker uses explicit domains, initial states,
and checked transition-summary contracts; the runtime enforces those contracts.
See the [M4/M5 contract and examples](../docs/panini-effects-verification.md).
[M2/M3](../docs/panini-graph-decisions.md) and [M0/M1](../docs/panini-semantic-profile.md)
remain historical milestone documentation.

No real provider/payment adapter ships with these examples. Opaque host bodies
are not executed or verified. External idempotency/authorization protocols are
trusted; compensation is fallible and does not imply rollback or success.
General liveness, arbitrary host termination, remote atomicity, and the native
C++ frontend remain outside this reference implementation.

## Project layout

- `src/panini_compiler/panini.lark` is the executable reference grammar.
- `lexer.py` provides the shared token shape and byte-accurate spans.
- `parser.py` constructs strict LALR trees and deterministic normalized ASTs.
- `cli.py` implements the representation-debugging commands.
- `tests/` is the beginning of the shared conformance corpus.
- `resolver.py` checks local symbols, types, decisions, and output bindings.
- `verifier.py` checks graph structure and cyclic budgets with iterative traversal.
- `decisions.py` validates probability results and applies threshold/tie rules.
- `runtime.py` executes trusted callbacks with enforced budgets/checkpoints.
- `effects.py` defines typed capability, receipt, authorization, and reconciliation contracts.
- `journal.py` stores transactional invocation/event history with stale-writer fencing.
- `execution.py` implements local isolation and bounded remote waits.
- `modelcheck.py` explores a restricted finite safety model with explicit proof statuses.

Portable reference artifacts and reproducible M6 measurements are documented in
[artifacts and evaluation](../docs/panini-artifacts-evaluation.md). Use `panini build SOURCE -o program.pnir`, `panini inspect program.pnir`, and `panini capabilities`
to build, validate, and query the reference profile.


For a hash-pinned validation environment, clean-checkout tests, installed-wheel
examples, corpus provenance and repeated measurements, see
[evaluation and reproducibility](evaluation/README.md). Mutation authorization
permits adapter invocation; the later authorization check gates Panini's local
state commit. A remote action may already have happened and need recovery.
