# Panini reference compiler

This directory contains the Python reference frontend for the Panini language
specified in [`../docs`](../docs/panini-language-design.md). It is deliberately
an independent project: Panini is a new language rather than a BAML parser mode.

The reference compiler implements an inspectable semantic and graph-checking
pipeline plus a minimal callback-based executor:

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
```

`parse` performs syntax checking. `check` resolves/types the
`sequential-decisions-v1` profile, verifies graph structure and dispatch budgets,
and emits PNIR v2. `audit.panini` remains a syntax-only demo with unresolved
references and unsupported features.

`parse --emit ast` remains AST v1; use `--emit semantic-ast` for semantic AST v2.
Every `check --emit` mode validates the program; `--diagnostics json` emits
structured errors. See the [M2/M3 contract](../docs/panini-graph-decisions.md)
for supported syntax, manifests, runtime callbacks, schemas, and diagnostics.
The [M0/M1 profile](../docs/panini-semantic-profile.md) records the earlier stage.

The minimal executor enforces budgets and supports mocked decisions/checkpoints.
It does not execute source method bodies or integrate a real model provider.
Timeouts, compensation, durable external effects, invariant proofs, and the
independent C++ frontend remain future work. A finite dispatch bound is not a
proof of successful completion or host-code termination.

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
