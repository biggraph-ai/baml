# Panini keyword and terminology specification

## Purpose

Panini has its own vocabulary. It does not reserve legacy prompt-language words
merely because an earlier language used them. This document names each Panini
concept, identifies migration mappings, and separates current grammar keywords
from proposed future syntax.

Keywords are lowercase ASCII and case-sensitive. `Provider`, `provider`, and
`PROVIDER` are distinct: only `provider` is a keyword. Type and declaration names
normally use `PascalCase`; methods, fields, graph nodes, and local variables use
`camelCase`.

## Canonical Panini vocabulary

| Panini term | Meaning |
| --- | --- |
| `agent` | Runtime-managed OO type that may own checkpointed state and inference methods |
| `provider` | Named model-service configuration or the provider selection inside an inference method |
| `instruction` | Interpolated model instruction literal; replaces generic “prompt” terminology |
| `capabilities` | Typed callable operations exposed to an inference method; broader and safer than an untyped tool list |
| `policy` | Named set of invariants and ordered rules |
| `graph` | Typed orchestration declaration |
| `node` | Graph step backed by a typed method |
| `max_visits` | Runtime-owned scheduling bound for a node |
| `parallel`, `branch`, `join`, `merge` | Structured concurrency and deterministic state combination |
| `compensate` | Fallible durable saga action associated with forward work |
| `scenario` | Proposed executable test case written in Panini |
| `suite` | Proposed group or generator of scenarios |
| `backend` | Proposed compiler/runtime target configuration |

The words `scenario`, `suite`, and `backend` are design-reserved but are not yet
accepted by `panini-core.y`. Reserving them now prevents ordinary identifiers
from blocking their introduction during the version 1 prototype.

## Migration map

These are source-to-source migration rules, **not aliases in the Panini parser**:

| Legacy spelling or concept | Panini spelling | Migration behavior |
| --- | --- | --- |
| `function Name(a: T) -> R` | `R name(T a)` | Move top-level functions to a generated static service class; use Java/C# parameter order |
| `client` declaration | `provider` declaration | Convert provider/model/retry options to the typed provider object |
| `client X` inside a model call | `provider X;` | Select a previously declared provider |
| `prompt` | `instruction` | Preserve literal content and interpolation while producing an instruction AST node |
| `tools` | `capabilities` | Resolve every item to a typed capability method |
| `retry_policy` | `policy` | Convert retry conditions to ordered policy rules |
| `test` | `scenario` | Convert arguments and assertions to a scenario body |
| `testset` | `suite` | Convert nesting/iteration to a suite declaration |
| `generator` | `backend` configuration | Move code-generation target settings to project configuration |
| `template_string` | Typed method returning `instruction` | Convert parameters to Java/C# order and retain interpolation |
| `type_builder` | Explicit schema construction API | Generate ordinary typed schema operations; no special grammar block |
| free-standing `implements I for T` | `class T implements I` or adapter class | Move implementation into an owned type or generate an explicit adapter |

The migration tool must issue an error when no semantics-preserving conversion
exists. It must never leave a legacy spelling in emitted `.panini` source.

## Current reserved words

The LALR prototype currently reserves these source words:

```text
package import as
class agent interface enum type
extends implements requires
public private protected internal static abstract final override async
provider instruction capabilities throws
graph state node start end policy rule
entry exit invariant when otherwise max_visits timeout
parallel branch join merge using compensate with
let const return throw defer if else while for in match catch spawn await
break continue new
true false null
```

Punctuation tokens such as `->`, `=>`, `??`, and compound assignments are
operators, not keywords.

## Explicitly retired words

The following words have no special meaning in Panini version 1 and should
produce targeted migration diagnostics when they appear where a declaration or
inference directive is expected:

```text
function client prompt tools retry_policy test testset
generator template_string type_builder implement
```

They are not accepted as hidden aliases. During the prototype they should remain
lexically available to the diagnostic layer so it can suggest the canonical
replacement; after the migration window they may become ordinary identifiers or
remain reserved for clear errors. That choice must be fixed before version 1.

## Lexer tokens versus source spelling

The Python and C++ lexers emit shared symbolic token kinds:

| Source | Token |
| --- | --- |
| `provider` | `PROVIDER` |
| `instruction` | `INSTRUCTION` |
| instruction raw text | `INSTRUCTION_LITERAL` |
| `capabilities` | `CAPABILITIES` |
| provider configuration body | `PROVIDER_BLOCK` during the parser prototype |

`INSTRUCTION_LITERAL` and `PROVIDER_BLOCK` are internal token names, not source
keywords. `PROVIDER_BLOCK` must eventually be replaced with tokens for typed
configuration fields so that provider configuration is represented in the CST
and AST rather than hidden inside the lexer.

## Naming test

A proposed keyword should be accepted only if it passes all of these checks:

1. It names one Panini semantic concept, not a particular backend API.
2. It cannot be expressed more clearly with an existing keyword plus a type.
3. Its behavior can be represented in the canonical AST and reference runtime.
4. Python and C++ frontends can tokenize it identically.
5. It does not claim a guarantee stronger than the runtime provides.
6. A migration diagnostic can explain it without relying on an implicit alias.

This keeps Panini familiar to Java/C# developers while giving agent orchestration
precise vocabulary instead of inheriting accidental terminology.
