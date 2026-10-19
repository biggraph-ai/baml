# Panini: a new object-oriented agentic language

## Status and scope

Panini is a new language, not a BAML dialect, compatibility mode, or alternate
spelling of another language. It incorporates the useful ideas proven by typed
prompt languages—schema-constrained model calls, provider configuration,
retries, tests, and streaming—but assigns them Panini syntax, semantics,
diagnostics, runtime behavior, and versioning.

This clean break is intentional. It avoids carrying contextual grammar,
deprecated forms, two method syntaxes, and legacy runtime assumptions into a
language whose primary abstraction is a stateful, policy-governed agent graph.
Migration is a separate source-to-source tool and never a parser mode.

## Product principles

1. **One language and one canonical syntax.** Python and C++ frontends must
   accept and reject the same programs and construct the same normalized AST.
2. **Object-oriented at the surface.** Classes, interfaces, agents, visibility,
   packages, typed fields, and methods follow familiar Java/C# conventions.
3. **Composition before inheritance.** Interfaces and injected capabilities are
   preferred. Class inheritance is single and deliberately limited.
4. **Model calls are typed methods.** Instructions are method bodies with declared
   inputs, outputs, errors, provider, capabilities, and policies.
5. **Orchestration is visible.** Graphs, transitions, bounds, parallel branches,
   joins, and compensation are compiler-visible declarations.
6. **Safety claims are precise.** A scheduling bound is not a proof that node
   code terminates; compensation is not rollback; a lock is not serializability.
7. **Every representation is inspectable.** Tokens, CST, AST, symbols, types,
   effects, graph IR, and backend IR have deterministic debug formats.

## Canonical source model

### Packages, data, and interfaces

```panini
package com.biggraph.compliance;

public class WorkflowState {
  public string inputQuery;
  public List<string> logs;
  public int iteration;
}

public class AuditResult {
  public bool violating;
  public string reason;
}

public interface Auditor {
  AuditResult analyze(float amount, string country) throws AuditError;
}
```

Fields and method parameters use the Java/C# order `Type name`. Semicolons are
required for field and simple-statement boundaries. The declarative graph header
uses `state: WorkflowState` to label its state binding; this is graph metadata,
not a second field or method-parameter syntax.

### Agents and typed inference methods

```panini
@Model("openai/gpt-4.1")
public agent FinancialAuditor implements Auditor {
  private float riskThreshold = 10000.0;

  public AuditResult analyze(float amount, string country) throws AuditError {
    provider OpenAI;
    capabilities [LookupSanctions];
    instruction #"
      Analyze transaction {{ amount }} in {{ country }}.
      Use threshold {{ this.riskThreshold }}.
      {{ context.outputSchema }}
    "#;
  }
}
```

An inference method is not an untyped string macro. Its declared return type defines
the response contract. The compiler generates request rendering, response
validation, typed capability dispatch, streaming projections, and retry integration.
Provider calls remain effects and must appear in the method's inferred or
declared effect set.

An `agent` is a restricted class whose lifecycle and state persistence are
runtime-managed. Static schema fields and runtime checkpointed fields must be
distinguishable in the semantic specification; visibility alone is insufficient.

### Providers and retry policies

Provider settings are typed objects, even if the initial lexer treats the body
as a single configuration token:

```panini
provider OpenAI {
  adapter: "openai";
  model: "gpt-4.1";
  retry: StandardRetry;
}

policy StandardRetry {
  rule RetryTransient: error.transient && attempts < 3 => retry();
  rule StopOtherwise: true => fail(error);
}
```

Retry is a policy library feature rather than a special top-level grammar form.
Configuration must eventually receive its own typed grammar and AST; opaque
`PROVIDER_BLOCK` tokenization is acceptable only for the parser prototype.

The complete reserved-word policy and migration vocabulary are specified in
[`panini-keywords.md`](panini-keywords.md).

### Typed orchestration graphs

```panini
graph ComplianceGraph(state: WorkflowState) -> WorkflowState {
  node init using WorkflowSteps.init;
  node process using WorkflowSteps.process max_visits 3 timeout seconds(30);

  start -> init;
  init -> process;
  process -> when WorkflowRules.underLimit(state)
             => process
             otherwise => end;
}
```

Graph nodes refer to typed static or capability-bound methods. A basic state
node has an effective signature like:

```text
(WorkflowState, GraphContext) -> WorkflowState throws E effects F
```

Guards must be pure, total Boolean methods. Visit counters and deadlines are
owned by the runtime and cannot be changed through workflow state. Before code
generation, graph verification performs reachability, terminal-path,
strongly-connected-component, bound, type-flow, and backend-capability checks.

### Parallel execution and merging

```panini
parallel review {
  branch risk using ReviewSteps.analyzeRisk;
  branch sanctions using ReviewSteps.checkSanctions;
  join reviewed merge using ReviewSteps.merge;
}
```

Every branch receives an immutable snapshot. A join is valid only when branch
writes are disjoint or an explicit, typed, deterministic merge method resolves
them. Shared mutable state plus a named mutex is not the default concurrency
model.

### Effects and compensation

```panini
node reserve using PaymentSteps.reserve
  compensate using PaymentSteps.release;
```

External operations have declared capabilities and idempotency requirements.
Compensation is a durable, fallible saga step with its own result and error
types. The runtime records whether forward work was scheduled, started,
completed, and compensated.

### Policies

```panini
policy ComplianceLimits for ComplianceGraph {
  invariant state.iteration >= 0;
  rule EscalateHighRisk: state.riskScore > 0.95
      => WorkflowActions.escalate(state);
}
```

All invariants must hold. Rules are evaluated in declaration order and the first
matching rule wins; unreachable rules are diagnostics. Predicates are pure and
total. Panini does not use implicit “specificity” because it is difficult to
define predictably across inheritance, graph scope, and runtime state.

## Object model boundaries

### Supported initially

- classes with typed fields and methods;
- agents as runtime-managed class-like types;
- interfaces with default methods;
- single class inheritance;
- multiple interface implementation;
- generics with invariant type parameters;
- explicit constructors and dependency injection;
- virtual dispatch only where declared; and
- package-qualified access control.

### Deferred until semantics are complete

- method overloading and implicit numeric conversions;
- multiple class inheritance;
- operator overloading;
- reflection-based mutation;
- implicit actor reentrancy;
- covariant mutable collections; and
- policy overrides through inheritance.

Familiar syntax must not be confused with copying all Java or C# behavior.
Panini should choose the smallest object model that supports readable agent
systems and remains statically analyzable.

## One language, two frontend implementations

“Two frontend” means two independent implementations of the **same** lexical,
syntactic, AST, and diagnostic contracts:

```text
                  +-> Python: Lark ----------------+
source.panini ----|                                 |-> canonical AST JSON
                  +-> C++: re2c + Bison lalr1.cc --+
```

It does not mean BAML mode versus Panini mode or functional syntax versus OO
syntax. The Python frontend is the executable reference and development tool.
The C++ frontend is the production implementation. Neither frontend may invent
target-specific syntax.

Both frontends run the same corpus containing:

- token fixtures with exact spans and lexer modes;
- valid programs with normalized AST JSON;
- invalid programs with diagnostic codes and primary spans;
- ambiguity and precedence cases;
- incomplete-source recovery fixtures; and
- round-trip formatter fixtures.

CI fails if accepted language, normalized AST, or required diagnostic locations
diverge. Diagnostic prose may differ during the prototype, but stable codes may
not.

## Compiler and runtime architecture

```text
source
  -> mode-aware lexer
  -> lossless CST
  -> normalized AST
  -> package/name resolution
  -> type, effect, and capability checking
  -> graph and policy verification
  -> portable typed IR
  -> reference interpreter
  -> backend adapters (LangGraph, durable workflow engines, native runtime)
```

The reference interpreter defines semantics. A backend is conforming only if it
preserves those semantics or rejects unsupported features. Generated LangGraph
code is an adapter output, never the definition of Panini behavior.

Panini initially generates native host SDKs for Python and TypeScript, followed
by C#/.NET, Java, and Go, plus OpenAPI/JSON Schema as a universal target. The
target architecture, type mappings, async contract, and capability checks are
specified in [`panini-targets.md`](panini-targets.md).

## Migration from BAML

Removing BAML from the language design does not require abandoning existing
users. Build a separate `baml-to-panini` migration command that:

1. parses a supported BAML version with its original parser;
2. maps schemas, enums, clients, tests, and model functions into Panini AST;
3. wraps top-level functions in generated static service classes;
4. emits canonical `.panini` source;
5. reports constructs with no semantics-preserving translation; and
6. never runs implicitly during Panini compilation.

This keeps the Panini compiler and specification clean while providing an
explicit adoption path. Migration fixtures should compare observable model
request and response behavior, not merely formatted source.

## Normative artifacts

Panini version 1 is not ready until these are versioned together:

1. token and lexer-mode specification;
2. conflict-free canonical grammar;
3. CST and AST schemas;
4. type, object, effect, graph, and policy semantics;
5. stable diagnostic catalog;
6. cross-frontend conformance corpus;
7. portable typed IR schema;
8. reference interpreter behavior; and
9. formatter style specification.

The current Bison grammar proves only that the prototype's syntactic core is
LALR(1). Opaque instruction/provider tokens and incomplete semantic rules remain
prototype limitations.

## Delivery plan

### Phase 0: freeze the language identity

- remove compatibility syntax from the normative grammar;
- choose `.panini`, package layout, keyword casing, and semicolon rules;
- finalize Java-order fields and parameters;
- publish the token and AST schema; and
- create the conformance repository layout.

### Phase 1: dual parser proof

- implement the Python Lark lexer/parser and AST dumper;
- switch the Bison prototype to the C++ `lalr1.cc` skeleton and add re2c;
- compare token streams and AST JSON in CI; and
- implement deterministic representation dumps.

### Phase 2: typed OO core

- implement packages, classes, interfaces, agents, generics, constructors,
  access control, name resolution, and type checking;
- add typed provider configuration; and
- implement inference-method request/response validation.

### Phase 3: graph reference semantics

- implement the graph IR, local interpreter, checkpoints, scheduling bounds,
  deadlines, cancellation, guards, and graph analysis;
- add deterministic replay tests; and
- expose DOT and JSON graph dumps.

### Phase 4: effects and policies

- implement capability/effect checking, parallel merge validation, idempotency,
  compensation, invariants, and ordered rules.

### Phase 5: migration and backends

- build the explicit BAML migration tool;
- generate Python, TypeScript, C#/.NET, OpenAPI, LangGraph, and
  durable-workflow adapters; and
- reject programs requiring unsupported backend capabilities.

## Decision

Proceed with Panini as a standalone OO agentic language. Preserve the proven
functionality of typed schemas and model calls at the semantic level, not by
embedding BAML grammar or exposing two source-language modes. Use the two
frontends to cross-check one specification, and make the reference interpreter
and inspectable IR—not generated backend code—the authority for behavior.


## Executable M0/M1 status

The Python reference frontend now preserves semantic AST v2 separately from
legacy AST v1, and resolves/types a deliberately small local static-method
graph profile into PNIR. See [the semantic profile](panini-semantic-profile.md)
for accepted features, CLI behavior, schemas, and stable diagnostics. Broader
language/runtime features described above remain design goals unless explicitly
listed there. Graph structure/budget verification and runtime enforcement are
not implemented by M0/M1.
