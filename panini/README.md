# Panini reference compiler

This directory contains the Python reference frontend for the Panini language
specified in [`../docs`](../docs/panini-language-design.md). It is deliberately
an independent project: Panini is a new language rather than a BAML parser mode.

The first milestone implements the inspectable frontend pipeline described by
the specification:

```text
.panini source -> tokens -> LALR concrete syntax tree -> normalized AST JSON
```

## Development

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
