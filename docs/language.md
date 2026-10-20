# The Panini language

Panini expresses an agent workflow as typed state and a directed graph. Ordinary
nodes call registered host functions; decision nodes select from a declared enum
through a model adapter. The graph, rather than an agent's free-form response,
defines the permitted next steps.

The executable semantic profile is `sequential-effects-v1`. It supports a single
source file, optional package naming, local classes/enums, stateless decision
agents, graph contracts and graph-bound policies. Imports and cross-file linking
are not implemented. Syntax-only inspection may accept constructs outside this
profile; `panini check` reports unsupported features explicitly.

## State and host methods

```panini
class State { bool retry; }
class Steps { public static State attempt(State state); }

graph BoundedRetry(state: State) -> State {
  node attempt using Steps.attempt max_visits 3;
  start -> attempt;
  attempt -> when state.retry => attempt otherwise => end;
}
```

State fields use `bool`, signed 64-bit `int`, finite `float`, UTF-8 `string`,
local enums, local classes or supported `Decision<E>` records. `void` is a method
return type, not a state field type. General generics, arrays, optional/union
fields and field initializers are not accepted by the reference profile.

A graph accepts and returns the same state class. The runtime requires every
field, rejects extra fields, and validates nested values before state replacement.
Nesting is bounded at 100 levels. Source declarations are contracts: ordinary
method bodies are opaque and not executed or verified. Python hosts register
the actual callbacks separately.

An ordinary graph callable is a public static method with one state parameter.
It returns the state class, or a compatible field result when a node declares
`writes state.field`. A compensation callable is a state transformer. Overloads,
instance-bound ordinary methods, inheritance and generic dispatch are unsupported.

## Graph control flow

Every graph has one `start`, declared callable nodes and one normal transition
per node. A normal transition is either unconditional or one Boolean guard with
an explicit `otherwise` target. Targets name a declared node or `end`.

The checker rejects duplicate identities, unreachable nodes and nodes with no
topological path to `end`. Normal, error and timeout edges all participate in
reachability and cycle analysis. A topological path to `end` does not guarantee
that execution takes it.

Every node in a cyclic strongly connected component must declare a positive
`max_visits`. Bounding one node does not excuse an unbounded subcycle elsewhere.
The runtime consumes visit budgets before scheduling, including failed attempts,
and retains counts across resume. Workflow state cannot replenish these counts.

The reported forward-dispatch bound is the sum of explicit budgets plus one
for every acyclic node without a budget. It bounds forward attempts, not all
runtime operations, elapsed time, arbitrary callbacks or successful completion.
Budget exhaustion fails with `BudgetExceeded`.

## Guards

Guards support state-field reads, nested non-optional field reads, local enum
members, Boolean/numeric/string literals, parentheses, `!`, `&&`, `||`, scalar
or enum equality, and same-type numeric comparisons. Boolean operators short
circuit. Arbitrary calls, assignment, arithmetic, indexing and `await` are
unsupported in guards. A guard must have type `bool`.

## Decisions and abstention

A stateless agent declares the possible outcomes and the adapter to use:

```panini
enum Intent { approve, review }
class Request { string reason; Decision<Intent> triage; }
agent Triage {
  public Decision<Intent> classify(Request state) {
    decision_model MockClassifier;
    decide state.reason over {
      approve: "A clear eligible request";
      review: "A request needing human review";
    } abstain_below 0.8;
  }
}
```

A decision agent has public decision entry points with one state parameter and
`Decision<E>` return type. It has no mutable fields or arbitrary agent bodies.
A graph binds `Triage.classify` and uses `writes state.triage` to store the result.
See the complete [decision example](../panini/examples/decision.panini).

The adapter manifest pins an identity/version and probability-response protocol.
Every enum label must appear once in the source options and in the response.
Responses contain finite numbers in [0,1], excluding Booleans; their sum must
be within 1e-9 of one. The response version must match the manifest. Missing
labels, invalid values and adapter failures are errors, not abstentions.

The greatest probability selects the choice; ties use source option order.
Strictly below `abstain_below` yields `choice = null` and `abstained = true`.
Equality to the threshold is accepted; the default threshold is zero. These
rules do not establish calibration, accuracy or task suitability.

Before reading a possibly absent choice, a guard must establish non-abstention
or non-nullness in a short-circuit-safe order:

```panini
triage -> when !state.triage.abstained
                && state.triage.choice == Intent.approve
            => approve otherwise => review;
```

State decision fields must be initialized with a complete valid record before
execution. The runnable example uses an explicitly abstained initial result.
A recommendation does not grant capability authorization.

## Effects, failure paths and policies

A node can describe the operational boundaries of a tool action:

```panini
node reserve using Payments.reserve max_visits 1
  timeout seconds(10) on_timeout => review
  on_error => review
  compensate using Payments.release;
```

Durations use positive finite `seconds`, `minutes` or `hours` literals. Duplicate
contracts are invalid. Effects are declared in a manifest; mutating capabilities
need typed receipts, an idempotency/reconciliation contract and either a matching
recovery capability or an explicit irreversible-action policy.

Graph `invariant` expressions are checked at execution boundaries and candidate
commits. A `policy Name for GraphName` can add invariants and ordered rules with
`end` or `throw` actions. Rules are not arbitrary host calls. See the
[runtime guide](runtime.md) for ordering, authorization, uncertain outcomes and
conditional finite analysis.

## Compiler representations and diagnostics

The pipeline is source → tokens/CST → semantic AST → typed PNIR → graph checks,
with optional finite-model analysis. `parse --emit ast` produces syntax AST v1;
`parse --emit semantic-ast` produces token-preserving semantic AST v2. PNIR v3
records resolved symbols/types, source-order identities, graph contracts,
performed checks and deferred obligations. Parsing a broader syntax construct
does not establish semantic support or a proof.

Spans are half-open UTF-8 byte offsets with a display filename. Diagnostics have
stable codes, severity, primary/related spans and optional cycle witnesses.
Representations are deterministic for identical inputs.

| Code family | Meaning |
| --- | --- |
| E1001 | Invalid syntax |
| E2000–E2004 | Unsupported features, unknown/duplicate names, types, limits and durations |
| E2301–E2306 | Graph consistency, transitions, reachability, end paths and cyclic budgets |
| E2401 | Capability and receipt contract errors |
| E2501–E2506 | Decision types, options, output bindings, adapters and safe choice access |
| E2601 | Policy binding, typing and actions |
| E2701–E2703 | Finite counterexample, unknown/resource limit or unsupported analysis |
| E2801 | Artifact build/load failure |

The [Lark grammar](../panini/src/panini_compiler/panini.lark) defines reference
syntax. The optional [Bison prototype](grammars/panini-core.y) checks an action-free
core grammar; it does not supply a native compiler or runtime.
