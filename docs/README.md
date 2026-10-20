# Panini documentation

Panini treats AI agent orchestration as typed control flow around decisions and
capabilities. The language describes allowed behavior; adapters connect that
behavior to host applications, model providers and services.

Read the guides in this order:

1. [Language](language.md): schemas, graph nodes, branching, decisions and limits.
2. [Runtime](runtime.md): how a graph executes, how effects are authorized and
   recorded, and what happens when an outcome is uncertain.
3. [Artifacts](artifacts.md): package a program and its contracts for inspection
   and loading by the reference runtime.
4. [Target languages](targets.md): use Python today and understand the intended
   boundaries of future host integrations.

For installation and API usage, see the [development guide](../panini/README.md).
For reproducible measurements and research questions, see the
[evaluation guide](../panini/evaluation/README.md).

The executable [Lark grammar](../panini/src/panini_compiler/panini.lark) is the
Python frontend's syntax definition. The [Bison grammar](grammars/panini-core.y)
is an action-free syntax prototype, not a second executable compiler. Syntax
recognition is broader than semantic support: a parsed construct is not
necessarily accepted by `panini check`.
