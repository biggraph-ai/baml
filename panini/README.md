# Develop and run Panini

The `panini-compiler` package supplies the `panini` CLI and the
`panini_compiler` Python API. It includes a strict parser, semantic resolver,
graph verifier, sequential runtime and optional finite-state safety checker.
See the [language guide](../docs/language.md) for source constructs and the
[runtime guide](../docs/runtime.md) for execution contracts.

## Install

Python 3.11 or newer is required. From this directory:

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
```

On Windows PowerShell, use the environment's executables directly:

```powershell
py -3.11 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
& .\.venv\Scripts\python.exe -m pytest
& .\.venv\Scripts\panini.exe check examples\retry.panini
```

Activation is optional. Compiler and parser usage do not require a C/C++ compiler.
Timed process-isolated local callbacks require POSIX `fork`; that runtime
restriction does not apply to syntax checking or finite analysis. The recorded
validation environment is Linux/Python 3.12; Windows/macOS runs are not recorded.

## CLI

The commands below assume the environment's executable directory is on `PATH`;
otherwise use `.venv/bin/panini` and `.venv/bin/python` explicitly.

```bash
panini lex examples/audit.panini
panini parse examples/audit.panini --emit cst
panini parse examples/audit.panini --emit ast
panini parse examples/retry.panini --emit semantic-ast
panini check examples/retry.panini
panini check examples/decision.panini --adapters examples/decision-adapters.json
panini check examples/refund.panini --effects examples/refund-effects.json --model examples/refund-model.json
panini build examples/retry.panini -o /tmp/retry.pnir
panini inspect /tmp/retry.pnir
panini capabilities
python examples/run_decision.py
python examples/run_refund.py
```

`parse` inspects syntax; `check` validates names, types and graph structure.
`audit.panini` is a syntax demonstration and is not an executable workflow.
Every `check --emit` mode validates before emitting a representation.
`--diagnostics json` writes structured diagnostics to stderr; invalid programs
return nonzero. Strict finite analysis also returns nonzero for `counterexample`,
`unknown` or `unsupported`. `--max-states` sets the finite exploration budget.

`build` packages checked source and contracts. `inspect` validates the package
by recompiling it. `capabilities` reports the reference profile; it does not
list installed vendor integrations. See [artifacts](../docs/artifacts.md) for
manifest and resource-limit options.

## Python API

This example runs from this directory and uses a local state transformer:

```python
from pathlib import Path
from panini_compiler import compile_source, Executor

source = Path("examples/retry.panini").read_text(encoding="utf-8")
program = compile_source(source, "retry.panini")
assert not program.diagnostics
execution = Executor(
    program,
    "BoundedRetry",
    {"retry": False},
    capabilities={"Steps.attempt": lambda state: state},
)
assert execution.run()["status"] == "ended"
```

Register decision callbacks through `adapters=...`; register effect callbacks as
`Capability` values with an authorizer and a durable `SQLiteJournal` for mutations.
The runnable examples show both patterns. Callback bodies are trusted host code
and are not inferred from source method bodies.

## Build and validate

```bash
python -m build
python -m pip install --force-reinstall dist/panini_compiler-0.1.0-py3-none-any.whl
python -m pytest
```

For a fresh environment with hash-pinned dependencies, an installed-wheel test
suite and example checks, follow [the validation protocol](evaluation/README.md).
The optional grammar check, run from the repository root, is
`tools/check-panini-grammar.sh`; it requires GNU Bison and m4.

## Package layout

| Path | Purpose |
| --- | --- |
| `src/panini_compiler/panini.lark` | Executable syntax definition |
| `parser.py`, `lexer.py`, `resolver.py`, `ir.py` | Source representations, symbols and typed PNIR |
| `verifier.py`, `modelcheck.py` | Graph structure and conditional finite safety analysis |
| `runtime.py`, `execution.py` | Sequential dispatch, state validation and bounded waits |
| `effects.py`, `journal.py` | Capability contracts, receipts and durable run records |
| `decisions.py`, `artifact.py`, `cli.py` | Decision validation, portable bundles and commands |
| `tests/`, `examples/`, `evaluation/` | Conformance, runnable workflows and measured evidence |

Python module names in the table are relative to `src/panini_compiler/`.
