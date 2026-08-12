# Panini as a portable, object-oriented BAML superset

## Decision summary

It is feasible to design a Panini language that retains BAML's typed LLM
functions while adding Java/C#-familiar declarations and declarative agent
graphs. A LALR(1) grammar is also feasible for a deliberately chosen **canonical
core syntax**. It is not feasible to turn every contextual and recovery form
accepted by today's BAML parser into one clean portable LALR(1) grammar without
either changing edge-case syntax or moving decisions into the lexer and
semantic passes.

The recommended architecture is therefore:

1. retain valid BAML declarations as the compatibility surface;
2. add Java/C#-familiar syntax as syntactic sugar, not as a second type system;
3. add `agent`, `graph`, and `policy` declarations with precisely limited
   semantics;
4. publish one normative token specification, action-free LALR grammar, AST
   schema, and conformance suite; and
5. lower all accepted syntax into one language-neutral typed IR before runtime
   or backend generation.

`grammars/panini-core.y` is an action-free Bison prototype of that canonical
syntax. Bison can construct its LALR(1) tables without shift/reduce or
reduce/reduce conflicts. It proves that the proposed core is parseable with one
token of lookahead; it does **not** prove that the semantics, type system, or
generated orchestration are correct.

## What LALR(1) buys us—and what it does not

### Benefits

- A deterministic grammar can be implemented by mature parser generators.
- The same productions can be ported mechanically to Python, Java, C#, Rust,
  Go, or C/C++ parser frameworks.
- Conflicts become visible during parser generation instead of being hidden in
  handwritten lookahead.
- A stable CST/AST contract makes alternate implementations and IDE tooling
  practical.
- Parser conformance can be tested independently from type checking and graph
  execution.

### Limits

- A `.y` file is not literally portable source code. Parser-generator syntax,
  lexer integration, semantic actions, error recovery, and generated APIs differ
  by ecosystem.
- LALR(1) only describes syntax. Name resolution, generics, type checking,
  exhaustiveness, effects, policy conflicts, cycle bounds, and concurrency
  safety require later compiler passes.
- Error recovery often matters more to editor usability than accepting valid
  programs. BAML's handwritten parser is intentionally tolerant, so a strict
  generated parser may initially produce worse diagnostics.
- Prompt strings need lexer modes. Delimiters, escapes, interpolation, and raw
  text should be tokenized separately from ordinary code.
- Generic `>` tokens need a policy. A lexer that always emits `>>` forces the
  parser to split it inside nested types; the portable alternative is to let a
  type-mode lexer emit two `>` tokens.

## Why the existing extracted EBNF is not enough

The existing BAML extraction is descriptive documentation, not a
machine-consumable grammar. It intentionally summarizes contextual behavior:

- ordinary and LLM function bodies use the same braces and are selected using
  content lookahead;
- `<` may start generic arguments or be a comparison;
- `{...}` may be an object constructor or a statement block;
- unions and union patterns reuse `|`;
- newline placement participates in a few decisions;
- deprecated or malformed syntax is accepted for targeted diagnostics; and
- several words are contextual rather than reserved keywords.

A portable grammar should not reproduce all of those decisions inside grammar
actions. It should define canonical forms and isolate unavoidable context in a
small, shared lexical contract.

## Proposed language layers

### Layer 1: BAML-compatible typed data and LLM functions

Keep the concepts that make BAML useful:

```baml
class AuditResult {
  violating bool
  reason string
}

function AuditTransaction(amount: float, country: string) -> AuditResult {
  client OpenAI
  prompt #"
    Audit {{ amount }} in {{ country }}.
    {{ ctx.output_format }}
  "#
}
```

Existing source should retain its meaning. Panini should not reinterpret BAML
classes, unions, optional types, clients, prompt templates, retries, or typed
LLM results.

### Layer 2: familiar OO spelling as desugaring

Permit an optional Java/C#-style method spelling:

```panini
public AuditResult audit(float amount, string country) {
  return AuditTransaction(amount, country);
}
```

Lower it immediately to the same function AST as:

```baml
function audit(amount: float, country: string) -> AuditResult {
  return AuditTransaction(amount, country)
}
```

This provides familiarity without duplicating overload resolution, error types,
dispatch, or code generation. The compiler should pick one canonical formatter
style per file or project; freely mixing both styles makes code review and
documentation harder.

### Layer 3: agents are classes with capabilities

`agent` should be a constrained class-like declaration, not a magical runtime
object:

```panini
@Model("openai/gpt-4.1")
agent FinancialAuditor implements Auditor {
  private float riskThreshold = 10000.0;

  public AuditResult analyze(float amount, string country) {
    return AuditTransaction(amount, country);
  }
}
```

The safest default is interface composition. Single `extends` syntax may be
offered for familiarity, but it should desugar to field/default-method reuse or
be prohibited for agents with mutable state. Java-like implementation
inheritance introduces fragile-base-class behavior, initialization ordering,
state aliasing, and policy-override questions that are especially dangerous in
long-running agents.

### Layer 4: graphs describe orchestration topology

```panini
graph ComplianceGraph(state: WorkflowState) -> WorkflowState {
  node init using Init;
  node process using Process max_visits 3 timeout 30s;

  start -> init;
  init -> process;
  process -> when UnderLimit(state) => process otherwise => end;
}
```

Nodes refer to ordinary typed functions. This is important: graph nodes should
not contain a second prompt/function language. The type checker can require a
node signature such as `WorkflowState -> WorkflowState throws E` and a guard
signature such as `WorkflowState -> bool throws never`.

`max_visits` must be maintained by the graph runtime, not by user-writable
state. Otherwise a node can reset the counter and defeat the bound. A graph
bound limits node scheduling only; it cannot prove termination if a node blocks
forever. `timeout` and cancellation behavior are separate contracts.

### Layer 5: policies are checked contracts

```panini
policy ComplianceLimits for ComplianceGraph {
  invariant state.loop_counter >= 0;
  rule RejectHighRisk:
    state.risk_score > 0.95 => Escalate(state);
}
```

Policies need explicit attachment points and deterministic conflict semantics.
Avoid an unspecified “most specific rule wins” mechanism. Prefer one of:

- all invariants must hold;
- rules are evaluated in declared order and the first matching rule wins; or
- priorities are explicit integers and duplicate priorities are compile errors.

Policy predicates should be pure and total. Effects should call typed functions
and participate in the ordinary error/effect system.

## Concurrency and compensation

Avoid presenting a named mutex as proof of serializability. Prefer graph-visible
read/write effects:

```panini
parallel review {
  branch risk using AnalyzeRisk;
  branch sanctions using CheckSanctions;
  join reviewed merge using MergeReview;
}
```

The compiler should require either disjoint state writes or an explicit typed
merge function. External effects need capabilities such as `PaymentWrite` or
`EmailSend`, plus idempotency and retry metadata.

Compensation is a fallible saga action, not rollback:

```panini
node reserve using ReserveFunds
  compensate using ReleaseFunds;
```

Both forward and compensation functions need typed errors. Durable runtimes
must persist whether the forward effect started, completed, or was compensated.

## Portable compiler architecture

```text
source
  -> mode-aware lexer
  -> generated LALR parser
  -> lossless CST
  -> canonical AST
  -> names and scopes
  -> type/effect checking
  -> graph and policy verification
  -> portable typed IR
  -> interpreter or backend adapters
```

### Normative artifacts

To make independent implementations agree, the project needs more than a
grammar:

1. **Token specification**—Unicode policy, keyword rules, comments, numeric
   literals, prompt delimiters, and interpolation modes.
2. **Grammar**—an action-free canonical grammar with an explicit version.
3. **CST schema**—including trivia and spans for formatters and language
   servers.
4. **AST schema**—preferably serialized as versioned JSON/Protocol Buffers so
   parsers in different languages can be compared.
5. **Semantic specification**—names, types, effects, dispatch, graph merges,
   policies, and errors.
6. **Conformance corpus**—valid programs, invalid programs, expected ASTs, and
   diagnostic codes/spans.
7. **Typed IR schema**—the stable boundary between frontend implementations and
   runtimes/backends.

### Implementation choices

For the concrete frontend recommendation, parser/lexer tradeoffs, debug dumps,
and Bison automaton tooling, see
[`panini-frontend-tooling.md`](panini-frontend-tooling.md).

| Ecosystem | Reasonable parser choices | Note |
| --- | --- | --- |
| Python | Lark in LALR mode, PLY, SLY | Good reference implementation; startup and packaging need measurement. |
| Java | CUP, Beaver, Bison Java target | ANTLR is popular but uses adaptive LL rather than LALR. |
| C# | GOLD-compatible tooling, Irony, Gardens Point tools | Tool maturity and error recovery should be prototyped first. |
| Rust | LALRPOP or Bison-generated FFI | Existing handwritten parser can remain the compatibility oracle initially. |
| C/C++ | Bison | Best direct consumer of the prototype `.y` grammar. |

Do not put language-specific AST construction actions into the normative
grammar. Each implementation should map generated reductions into the shared
AST schema and run the same fixtures.

## Compatibility strategy

### Canonical versus compatibility syntax

Define two modes:

- **Panini canonical mode** accepts only deterministic forms represented in the
  LALR grammar.
- **BAML compatibility mode** accepts current BAML files, including contextual
  conveniences, then lowers them to the canonical AST.

This is preferable to forcing every legacy recovery rule into every new parser.
The formatter can provide an automatic compatibility-to-canonical migration.

### Versioning

- Put a `language_version` in project configuration, not in every file.
- Never change an existing production's meaning silently.
- Reserve keywords before assigning semantics, with a diagnostic and migration
  period.
- Version the AST and typed IR separately from the surface grammar.
- Require backend capability declarations; for example, a LangGraph backend may
  not support the same durable compensation semantics as a workflow engine.

## Risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Two spellings for every construct | Fragmented style and documentation | Canonical formatter and project-level style mode. |
| Java inheritance conflicts with BAML interfaces | Duplicate or unsound dispatch rules | One semantic model; OO syntax only desugars to it. |
| Strict generated parser degrades diagnostics | Poor IDE adoption | Keep CST recovery layer; snapshot invalid-input diagnostics. |
| Grammar forks across languages | Programs parse differently | Action-free grammar, versioned AST, shared corpus in CI. |
| Graph “safety” is overstated | Incorrect operational guarantees | Specify assumptions; type effects, timeouts, and persistence. |
| Backend semantics leak into the language | Non-portable programs | Backend-neutral IR plus explicit capability checks. |
| Full BAML syntax is forced into LALR(1) | Grammar conflicts and hacks | Canonical/compatibility split and lexer modes. |

## Recommended delivery plan

### Phase 0: specification and corpus

- Freeze a representative BAML compatibility corpus.
- Assign stable AST node and diagnostic names.
- Decide canonical keyword, semicolon, parameter, and prompt forms.
- Check the prototype grammar for zero LALR conflicts in CI.

### Phase 1: portable parser proof

- Implement one generated parser beside the Rust parser.
- Parse the same valid corpus and compare normalized AST JSON.
- Compare invalid-input diagnostics, but do not require identical wording.
- Add one second-language parser—Python is a low-cost candidate—to prove the
  grammar and AST contract are genuinely portable.

### Phase 2: OO sugar

- Add modifiers, Java/C# method spelling, packages, and imports.
- Lower them to existing classes, interfaces, and functions.
- Do not add inheritance or overloading until dispatch semantics are specified.

### Phase 3: graph core

- Add typed nodes, guards, transitions, runtime-owned visit budgets, timeouts,
  reachability checks, and strongly-connected-component analysis.
- Execute locally before generating third-party backend code.

### Phase 4: policies and effects

- Add pure invariants and deterministic rule ordering.
- Add read/write effect declarations, parallel merge checking, idempotency, and
  fallible compensation.

### Phase 5: backends

- Generate LangGraph only after the graph IR has an executable reference
  semantics.
- Add backend capability validation and golden generated-code tests.

## Recommendation

Proceed, but treat “Java/C#-like” as an **adoption-oriented surface syntax**, not
as the semantic foundation. BAML's typed LLM boundary is the valuable core;
Panini should add analyzable orchestration without weakening it. The first
engineering milestone should be a normative lexer contract, a conflict-free
canonical grammar, and cross-parser AST conformance—not inheritance, mutexes, or
LangGraph code generation.
