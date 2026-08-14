# Panini reference compiler

This directory contains the Python reference frontend for the Panini language
specified in [`../docs`](../docs/panini-language-design.md). It is deliberately
an independent project: Panini is a new language rather than a BAML parser mode.

The first milestone implements the inspectable frontend pipeline described by
the specification:

```text
.panini source -> tokens -> LALR concrete syntax tree -> normalized AST JSON
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
panini check examples/audit.panini --emit ast
```

`check` currently performs strict syntax checking and emits the normalized AST.
Name resolution, type/effect checking, graph verification, PNIR emission, and
the independent C++ frontend remain subsequent compiler phases.

## Project layout

- `src/panini_compiler/panini.lark` is the executable reference grammar.
- `lexer.py` provides the shared token shape and byte-accurate spans.
- `parser.py` constructs strict LALR trees and deterministic normalized ASTs.
- `cli.py` implements the representation-debugging commands.
- `tests/` is the beginning of the shared conformance corpus.
