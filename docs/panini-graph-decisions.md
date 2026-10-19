# M2/M3: graph checks, dispatch budgets, and typed decisions

Historical milestone contract: [M4/M5](panini-effects-verification.md) supersedes
the PNIR/checkpoint versions, runtime capabilities, and deferred-feature status
below. Existing AST v1/v2 compatibility remains as documented.

## Implemented workflow

```bash
cd panini
.venv/bin/panini check examples/retry.panini
.venv/bin/panini check examples/decision.panini --adapters examples/decision-adapters.json
.venv/bin/python examples/run_decision.py
.venv/bin/python -m pytest
```

The sample executes fake local callbacks only, with one confident and one
abstained decision. It performs no model-service call or financial action.

The compiler pipeline is syntax → semantic AST v2 → local name/type resolution
→ PNIR v2 → structural verification. `parse` remains syntax-only, default AST v1
is unchanged, and every `check --emit` mode runs all semantic/graph passes.

## M2: graph-structure and dispatch-budget checking

The initial profile is sequential: one execution token, complete static control
flow, and runtime-owned budgets global to one graph run.

The compiler requires:

- Exactly one start pointing at a declared node.
- Unique node identities; `start` and `end` cannot name callable nodes.
- Exactly one normal transition declaration per node. Multiple separate edges
  from the same source are errors, even if each contains a fallback.
- A conditional IR transition has one Boolean guard and one final `otherwise`;
  an unconditional IR transition has one target. This is IR consistency, not a
  proof about arbitrary guard exhaustiveness.
- Every node is reachable from start; each reachable node has a topological
  path to `end`. This proves a path exists, not that every run takes it.
- Every node in a cyclic SCC has a positive `max_visits` contract. One bounded
  node in an SCC does not excuse unbounded nodes elsewhere in that SCC.

Traversal and SCC analysis use iterative algorithms, tested on 2,500-node chains
and cycles. Topology analyses take O(V+E); diagnostic sorting/rendering is
additional work. Cycle diagnostics contain an actual closed edge path, primary
UTF-8 byte span, and related spans. Ordering is deterministic.

Each accepted graph contains `verification.dispatchBound`:

```text
B = sum(explicit max_visits budgets) + count(nodes with no explicit bound)
```

An unbounded node outside all cycles can be visited at most once under this
profile. For example, an unbounded initializer followed by a retry node with
`max_visits 3` admits at most four dispatches. The report records its assumptions
and says terminal reachability is `possible-not-guaranteed`.

This bounds admitted node dispatches when the runtime enforces the profile.
It does not prove host code returns, a remote operation stops, successful
completion, a wall-clock bound, correct inference, or safe external effects.
A guard can remain true until the runtime returns `BudgetExceeded` instead of
reaching `end`. No uncounted retries, dynamic transitions, or parallel execution
are included in this profile.

## M3: native decision syntax and typing

```panini
enum Intent { approve, review }
class State { string reason; Decision<Intent> triage; }
agent Triage {
  public Decision<Intent> classify(State state) {
    decision_model MockClassifier;
    decide state.reason over {
      approve: "A clear eligible request";
      review: "An ambiguous request needing review";
    } abstain_below 0.8;
  }
}
```

A decision agent is stateless and has public decision methods, with one local
state-class parameter and a `Decision<E>` return type for a local enum E. Graph
binding to `Triage.classify` denotes this declarative decision entry point,
not construction or dispatch of an arbitrary mutable OO instance. Fields,
inheritance, generics, and ordinary agent method bodies remain unsupported.
Ordinary graph methods remain public static `(State) -> State` contracts with
opaque, unverified bodies. Other compatible output types require explicit
`writes`; there is no implicit state/result conversion.

`Decision` is a reserved built-in type constructor, not a source keyword.
Options must cover the enum exactly once. Their descriptions are JSON-style
strings with valid Unicode scalar values. Input must be a string expression in
the pure supported subset. Models are inference effects, never guard calls.

The `decision_model` expression must be a configured identifier or string;
provider constructor calls are not implemented. Pass an adapter manifest with
`--adapters` or `compile_source(..., adapters=manifest)`:

```json
{"MockClassifier": {"output": "probabilities", "version": "deterministic-mock-v1"}}
```

The manifest is identity/protocol/version metadata, never credentials. Only
normalized-probability adapters are currently accepted; arbitrary scores are
not silently normalized. The compiler checks the manifest but does not contact
or prove availability/correctness of a provider. No real vendor adapter ships
with this milestone. Runtime callbacks must be registered separately.

A node writes its typed result into one direct state field:

```panini
node triage using Triage.classify writes state.triage max_visits 1;
```

The input parameter type must match the graph state; the return type must match
the writable field. `writes` replaces only that field, preserving all others.
Nested writes are deferred. Without `writes`, a node returns the entire state.

The result has `choice: E?`, `abstained: bool`, `probabilities`, `model`, and
`modelVersion`. Guards currently expose `choice` and `abstained` only. Accessing
an actionable choice requires a preceding short-circuit refinement, for example:

```panini
triage -> when state.triage.abstained == false
                && state.triage.choice == Intent.approve
            => approve otherwise => review;
```

`!decision.abstained` and `decision.choice != null` also establish the fact.
The right side of `&&` receives true facts from the left; the right side of `||`
receives false facts. A choice read before its check is rejected. Refinement is
conservative and local to the expression; no global invariant proof is implied.

`abstain_below` is an optional finite numeric literal in [0,1], default 0.
Equality to the threshold is accepted; strictly smaller maximum probability
abstains with `choice = null`. Ties use source option order. These are selection
rules, not a probability-calibration or model-accuracy guarantee.

## Reference executor: M2R and mocked M3 adapters

Python API:

```python
from panini_compiler import compile_source, Executor

program = compile_source(source, adapters=manifest)
assert not program.diagnostics
execution = Executor(program, "Workflow", initial_state,
                     capabilities={"Steps.run": local_callback},
                     adapters={"MockClassifier": probability_callback})
result = execution.run()
checkpoint = execution.checkpoint()
resumed = Executor.restore(program, checkpoint,
                           capabilities=capabilities, adapters=adapters)
```

A state callback receives an independent copy of the input state and returns a
new state/result. A model callback receives `(input_text, option_descriptors)`
and returns exactly:

```json
{"probabilities": {"approve": 0.8, "review": 0.2}, "modelVersion": "deterministic-mock-v1"}
```

Every enum label is required. Values must be finite numbers in [0,1], excluding
Booleans, with sum within absolute tolerance 1e-9 of one. Invalid values, wrong
versions, missing callbacks, and callback exceptions are invocation failures;
they do not become abstentions or successful fallback decisions. The model
callback and its metadata are trusted; the executor cannot attest which model
produced the values.

The executor rechecks graph structure instead of trusting a verification marker.
It enforces budgets **before dispatch**, counts failed attempts, rejects recursive
dispatch, validates outputs before atomic state replacement, and evaluates
guards with short circuiting. Workflow state cannot reset runtime counters.
Failures leave the old state committed; they are terminal in this milestone.
`BudgetExceeded` is never successful `end`.

State must provide every declared non-nullable field, with no extra fields.
The runtime validates nested classes, enums, Boolean/int/finite-float/string
values, and Decision records. Integers use a signed 64-bit state contract.
Decision fields need an explicit initial value; the example uses an abstained
record with a valid distribution before classification. A complete output-bound
Decision is validated before replacing it. The reference validator caps nesting
at 100 levels. Guard numeric literals are checked against finite floating-point
values and signed 64-bit integers before execution.

Between-step checkpoints include the program fingerprint, graph, state, next
node, counters, total dispatch count, status, and failure. JSON round trips and
resume preserve budgets; mismatched programs or malformed counters are rejected.
They are **trusted-storage snapshots**, not authenticated/tamper-proof records
or a durable external-effect commit protocol. Resume cannot distinguish a
maliciously rewritten, internally consistent snapshot from trusted data.
Checkpoints during callbacks are rejected. Replaying an old snapshot or running
two restored copies needs a future durable scheduler protocol; do not use this
API to promise exactly-once effects.

The executor is synchronous and single-threaded, with no interruption of a hung
callback. It refuses timeout and compensation contracts instead of ignoring
them. Those declarations may still be statically checked/lowered. Deadline/error
routing, durable recovery, cancellation, authorization, effect analysis, and
compensation execution remain M4; policy proofs remain M5.

## Schemas, diagnostics, and compatibility

PNIR v2 uses profile `sequential-decisions-v1`; v1 is not accepted by the new
executor. The version bump records built-in decision type identities, stateless
agent semantics, output bindings, adapter contracts, graph verification reports,
and explicit performed/deferred checks. AST v1 and semantic AST v2 remain their
existing versions; new productions add node kinds to the latter. Golden fixtures
cover unchanged legacy representations, M1 lowering, and verified decision PNIR.

| Code | Meaning |
| --- | --- |
| E2301 | Invalid graph entry, endpoint, identity, reserved name, or IR budget |
| E2302 | Missing/ambiguous normal transition declaration |
| E2303 | Node unreachable from start |
| E2304 | No topological path from a reachable node to end |
| E2305 | Malformed IR conditional/fallback |
| E2306 | Unbounded cyclic SCC under the strict all-nodes rule |
| E2501 | Invalid decision type, method contract, or input |
| E2502 | Enum options not covered exactly once |
| E2503 | Invalid output binding/decision callable |
| E2504 | Missing/unsupported adapter identity, protocol, version, or manifest |
| E2505 | Actionable choice used without a preceding refinement |
| E2506 | Invalid abstention threshold |

E1001 and E20xx retain their M0/M1 meanings. Type-incompatible `writes` can also
produce E2003, and invalid string escapes produce E2003. Diagnostic JSON now
includes `related_spans` and `witness` arrays. Invalid programs return nonzero
and suppress graph artifacts on stdout. `parse` still accepts syntactically valid
unbounded graphs; `check` now rejects them.

Static `check` reports runtime enforcement as deferred: having a reference
executor does not prove the eventual chosen host enforces this profile. A real
backend must implement the stated semantics or reject unsupported features.
