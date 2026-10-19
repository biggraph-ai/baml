# M0/M1: semantic AST and local graph typing

Historical milestone contract: [M2/M3](panini-graph-decisions.md) supersedes the
PNIR version, profile, and deferred-feature status below. AST v1/v2 compatibility
remains as documented. This page records the earlier M0/M1 scope.

## Implemented scope

The Python frontend now provides a versioned semantic syntax representation and
an initial name/type pipeline. `parse` remains syntax-only. `check` resolves and
type-checks the `local-static-state-v1` profile and emits PNIR by default.

```bash
cd panini
.venv/bin/panini parse examples/audit.panini --emit ast
.venv/bin/panini parse examples/retry.panini --emit semantic-ast
.venv/bin/panini check examples/retry.panini
.venv/bin/panini check examples/retry.panini --diagnostics json
.venv/bin/python -m pytest
```

`audit.panini` remains a syntax demonstration: it contains unresolved references
and features outside the semantic profile, so semantic `check` rejects it.

## Semantic preservation (M0)

- AST v1 (`parse --emit ast`, `PaniniParser.ast`) retains its existing schema.
- AST v2 (`--emit semantic-ast`, `PaniniParser.semantic_ast`) preserves every
  grammar token, including operator spellings and modifiers. Rule nodes have
  `kind`, source-order `syntax:N` IDs, half-open UTF-8 byte `span`, and `children`.
  IDs are deterministic for a given source, not stable across source edits.
- Binary/unary nodes expose `operator`; modifiers expose `value`; class, agent,
  interface, enum, graph, and member declarations expose `name`.
- Contracts have distinct `MaxVisitsContract`, `TimeoutContract`, and
  `CompensateContract` kinds. Name/type syntax remains structured.
- Token leaves contain `tokenType` and exact `text`; `text` is authoritative for
  operator spelling, including terminals internally named by Lark. Literal
  text retains source escapes; consumers must not mistake it for decoded values.
- Byte offsets are precomputed once for each representation pass.
- AST v2 includes punctuation for faithful semantic preservation, but neither
  AST nor the debug CST preserves ignored whitespace/comments. These are not
  lossless source-formatting representations.
- The grammar remains strict LALR with contextual lexing. Equality operators
  take priority over assignment so `==` is recognized as one operator.

Versioned golden fixtures cover legacy AST v1, semantic AST v2, and PNIR. Schema
changes require deliberate fixture and version review rather than silently
changing the legacy representation.

## Supported semantic profile (M1)

One source file, with an optional package and local non-generic classes/enums.
Names are resolved within this file; the package names the source but does not
provide imports or cross-package lookup. Primitive types are `bool`, `int`,
`float`, `string`, and `void` (method return only). A primitive type name cannot
be redeclared.

State classes contain non-static accessible fields. A graph accepts a local
class as its state and returns the same class (explicitly or by omission).
Fields can refer to other local classes/enums; generic, optional, union, and
array types are deferred. Field initialization expressions are unsupported.

Node and compensation callables must be public static methods, with exactly
one parameter of the graph state class and the same return type. No other
method modifiers or default parameters are supported for graph callables.
Their source-order declaration IDs are retained in PNIR. Declarations are
contracts; bodies are opaque and explicitly **unverified**. Method-body name
resolution, effect inference, and execution are not implemented. Declared
parameter, return, and throws types are checked for known local/primitive types.

Guards support Boolean literals, numeric/string literals, state-field reads,
nested non-optional field reads, enum members, parentheses, `!`, `&&`, `||`,
same-type scalar/enum equality, and same-type numeric comparisons. Negative
numeric literals are accepted. A local state parameter shadows an enum of the
same name. Guards must have type `bool`.

Arbitrary calls, assignment, arithmetic, indexing, `await`, coalescing, and other
expressions are rejected in guards. This keeps the supported expression subset
free of host calls and avoids claiming totality for unspecified arithmetic.
The future runtime must validate complete, non-null state values before guard
evaluation; this compiler has no runtime that establishes that condition.

Contracts:

- `max_visits`: a positive integer literal; duplicate contracts are errors.
- `timeout`: `seconds(N)`, `minutes(N)`, or `hours(N)` for a positive finite
  numeric literal. These are intrinsic duration constructors only in this
  context; arbitrary expressions are rejected.
- `compensate using C.method`: resolve and check the state-transformer signature.
  This checks declaration compatibility, not recovery correctness or rollback.

Imports, attributes, agents, inheritance, generic methods/classes, type aliases,
providers, policies, graph-local state declarations, and graph invariants are
explicitly unsupported in semantic checking. An invariant is never silently
accepted as proved. Unsupported syntax may still be parsed and inspected.

Duplicate declarations, members (including overloads), enum members, node names,
and node contracts are diagnosed before they can silently overwrite valid
entries. Unknown graph targets/sources and starts are name errors.

## PNIR v1

`Resolver().resolve(semantic_ast)` returns a `CheckResult`. Its `to_dict()` emits:

- `schemaVersion: 1`, `kind: Program`, and `profile: local-static-state-v1`;
- a source-ordered symbol table for declarations, fields, methods, enum members,
  and graph state parameters, with IDs, types, and byte spans;
- resolved type IDs (`builtin:T` for primitives), and method parameter metadata;
- graph state IDs, callable IDs, node contracts, start references, and structured
  ordered transition alternatives with resolved node IDs or `$end`;
- structured guard expressions with operator identities, inferred types, and
  resolved field/enum/parameter IDs;
- explicit lists of checks performed and obligations deferred.

Resolver annotations are applied to a copy, leaving the input AST unchanged.
Any semantic errors suppress graph artifacts. The result may still include
partial symbols for diagnosis, but it must not be executed or treated as a
successful artifact. Invalid programs produce deterministic sorted diagnostics.

## Diagnostics and CLI contract

| Code | Meaning |
| --- | --- |
| E1001 | Invalid source syntax |
| E2000 | Unsupported semantic feature or verification obligation |
| E2001 | Unknown type, callable, value, field, enum member, or graph endpoint |
| E2002 | Duplicate declaration, member, modifier, node, or contract |
| E2003 | Incompatible type/signature or non-Boolean guard |
| E2004 | Invalid visit-bound or duration literal |

Semantic diagnostics have a code, message, severity, and byte span. Text output
converts byte offsets to source line/column for display. `check --diagnostics
json` emits a JSON diagnostic array on stderr, including syntax failures.
Errors return exit status 1 and do not emit a successful artifact on stdout.

`check --emit ast`, `semantic-ast`, or `cst` runs the same semantic checks before
emitting the requested representation. No output format bypasses validation.
`parse --emit ast` continues to emit v1; `parse --emit cst` remains a debug tree.

## Execution semantics reserved for later milestones

The planned sequential runtime owns one execution token. A graph must have one
start and one normal transition declaration per node; ordered conditionals end
in one fallback. State is an immutable input snapshot replaced atomically after
a validated successful node return. Dispatch budgets are global to a run,
counted before invocation (including failed attempts), and persisted on resume.
Exhaustion is `BudgetExceeded`, not successful `end`.

These execution rules are **documented contracts, not implemented enforcement**.
M1 does not check entry cardinality, transition uniqueness, reachability, sinks,
SCCs, or cyclic-node bounds; these are M2. For example, a type-correct unbounded
self-loop can pass M1. PNIR explicitly lists graph topology, budget verification,
runtime enforcement, method bodies, and effects as deferred.

The future conservative cycle rule requires a bound on every node in a cyclic
SCC. Under complete sequential control flow and global per-run enforcement,
a conservative dispatch bound is the sum of explicit budgets plus one per
unbounded acyclic node. Even that bound does not prove a node returns, successful
completion, wall-clock termination, correct inference, or reversible effects.
`Decision<T>`, `decide`, `writes`, and timeout/error routing are later extensions.
