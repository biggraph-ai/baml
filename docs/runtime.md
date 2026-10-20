# Workflow execution, effects and verification

The reference runtime executes one active workflow token, validates state
changes, and mediates calls to declared capabilities. Its durable journal tracks
what was invoked, what is known to have completed, and what remains uncertain.
These mechanics make failure handling part of the orchestration contract.

The executable profile is `sequential-effects-v1`, with PNIR v3 and checkpoint
schema v2. The examples use local model/payment mocks. See the
[language guide](language.md) for graph and decision syntax, and the
[target-language guide](targets.md) for host integration availability.

```bash
cd panini
.venv/bin/panini check examples/refund.panini \
  --effects examples/refund-effects.json --model examples/refund-model.json
.venv/bin/python examples/run_refund.py
.venv/bin/python -m pytest
```

The example checks that a reserved balance requires approval, as well as the
balance bounds. It prints a finite invariant-safety proof under its explicit contracts,
a successful mock ledger run, and a downstream failure with recorded
compensation. Compensation does not turn the original workflow failure into
success. The SQLite database is temporary for this example; real integrations
must retain their journal across process restarts.

## Exceptional control flow

```panini
node reserve using Payments.reserve max_visits 1
  timeout seconds(10) on_timeout => review
  on_error => review
  compensate using Payments.release;
```

Timeout and error targets resolve to declared nodes or `end`. Both kinds of
edge participate in entry/reachability/SCC analysis; an exceptional back edge
cannot bypass cyclic-node budgets. Each node still has exactly one normal
transition declaration. Duplicate node contracts are errors.

A known non-mutating timeout/error follows the matching handler using unchanged
state. Without a handler it fails. Invalid outputs, invariant failures, and
explicit authorization denials fail closed rather than bypassing guards.
An uncertain mutation blocks in `pending` until reconciliation establishes a
final outcome. Its timeout/error handler cannot assume that the action did not
commit. A reconciled completion validates/commits the resulting state and takes
the declared handler (or its normal edge if none exists); a final non-commit
uses unchanged state and its handler (or fails).

Budgets count scheduling attempts before invocation, including failed attempts.
Forward retries must be explicit graph visits. Compensation and reconciliation
have their own budgets, never a reset of `max_visits`. Verification reports
separate `dispatchBound`, `compensationAttemptBound`, and
`reconciliationAttemptBound` values.

## Deadline and cancellation semantics

Pure/local callbacks with a source timeout execute in a killable POSIX-fork
process. `IsolatedTask(callback)` requests isolation for an otherwise untimed
local task (one-second default). The parent uses a deadline and bounded process
cleanup. Arbitrary source method bodies are still opaque; registered local
callbacks are trusted to respect their declared effect role.

Registered capability callbacks run with a bounded wait on a private queue per
invocation. The worker is a daemon thread because killing local code cannot
prove that a remote operation stopped. A timed-out worker cannot publish a late
result into a later invocation's state. A capability receives
`context.cancel_requested` and can set `context.cancel_acknowledged`; the journal
records the acknowledgement observed at timeout. Acknowledgement is advisory,
not a final remote non-commit result. Late acknowledgements do not mutate the
scheduler's saved state.

Durations must be positive and finite after conversion to seconds and fit the
platform wait limit. A node's timeout and an effect's `timeoutSeconds` combine
using the shorter deadline. Effect waits default to one second. Pure untimed
callbacks can still hang: no arbitrary-host termination theorem is claimed.
Disk persistence, process-start/cleanup overhead, OS scheduling, provider work,
and network delivery are outside a hard wall-clock completion guarantee.
Process-isolated local deadlines currently require POSIX `fork`; the syntax,
compiler, finite checker, SQLite journal, and thread-based capability protocol
are independent of that local-isolation backend.

## Capability and receipt manifests

Use `--effects file.json` or `compile_source(..., effects=manifest)`:

```json
{
  "Payments.reserve": {
    "kind": "mutation", "capability": "payments.reserve",
    "receiptType": "Receipt", "idempotent": true, "reconcilable": true,
    "timeoutSeconds": 10, "reconcileTimeoutSeconds": 2,
    "maxReconciliations": 3
  },
  "Payments.release": {
    "kind": "recovery", "capability": "payments.release",
    "receiptType": "Receipt", "idempotent": true, "reconcilable": true,
    "timeoutSeconds": 10, "reconcileTimeoutSeconds": 2,
    "maxAttempts": 2, "maxReconciliations": 3
  }
}
```

`receiptType` names a local class; receipt values and input/output states are
validated against their source types. Forward mutations require either a typed
`compensate using` method with a `recovery` capability using the same receipt
contract, or an explicit nonempty `irreversiblePolicy` name. Both alternatives
at once are rejected. An irreversible failure is observable as manual recovery,
not assigned a fictional compensator. The authorizer receives the policy name.

Kinds are `read`, `mutation`, and `recovery`. Read contracts accept a named
capability and optional deadline. Mutations/recovery require explicit
idempotency/reconciliation declarations. Reconciliation budgets default to
three; recovery attempts default to two, each configurable in [1,100].
Metadata is checked before lowering. Unknown fields are rejected; manifests
contain contracts, not credentials or embedded provider secrets.

## Runtime binding and authorization protocol

```python
from panini_compiler import (
    Capability, EffectResult, Executor, Reconciliation, SQLiteJournal,
)

# invoke(context, state) -> EffectResult(value, receipt) for mutations/recovery
# invoke(context, state) -> value for reads
# reconcile(context) -> Reconciliation(...)
bindings = {"Payments.reserve": Capability(invoke, reconcile),
            "Payments.release": Capability(compensate, reconcile_compensation)}
journal = SQLiteJournal("workflow.sqlite3")
execution = Executor(program, "Refund", initial_state,
                     capabilities=bindings, journal=journal,
                     authorizer=authorize)
result = execution.run()
```

An authorizer receives a frozen request envelope containing a copied state,
capability, phase, invocation context, and optional irreversible policy. It must
return exactly `True`. No authorizer means denial for declared capabilities.
Checks occur before invocation/reconciliation/compensation and before Panini
state commit, including after resume. Policies and invariants do not replace
this capability authorization step. Decisions remain advisory.

`InvocationContext` provides `run_id`, `invocation_id`, a stable per-action
`idempotency_key`, a journal-revision fencing token, phase, the typed original
input, and the forward receipt for compensation. Forward retries use new graph
visit identities; recovery retries reuse a stable `:compensate` key. Therefore
adapters must also apply business-level idempotency when different graph visits
represent the same business transaction.

External dispatch is authorized immediately before the adapter call. The later
`commit` authorization phase governs Panini's local state commit. It cannot
retroactively revoke a remote action that already committed. A remote service
must enforce its own authorization/fencing at the external commit boundary if
revocation races must be prevented. The reference runtime cannot attest that
an arbitrary callback used the requested permission or respected its manifest.
There is no prepare/authorize/commit protocol in this reference implementation.
If post-invocation local commit approval is denied, Panini retains the receipt
and attempts recovery; it does not claim to have prevented the external effect.
`test_commit_denial_and_invariant_failure_retain_receipt_and_recover` explicitly
checks that the remote call happened before denial and recovery.

Reconciliation results:

- `completed`: carries the stable typed `EffectResult` for the invocation key.
- `not_committed`: **final** acknowledgement that this key cannot commit later.
  A temporary absence from a provider query is insufficient.
- `unknown`: keeps the run blocked, consuming a bounded reconciliation attempt.

A valid durable completion receipt cannot be erased by a contradictory final
non-commit or replaced by a different receipt. Such conflicts remain explicit
and require reconciliation/manual intervention. Invalid receipts or lost
responses never silently become a non-commit.

## Durable invocation and saga recovery

Mutations require `SQLiteJournal`. Each scheduling/started/completed transition
persists counters, IDs, pending work, state, receipts, recovery attempts, and an
event in one SQLite transaction with full synchronous durability. Compare-and-
swap revisions reject stale writers. `Executor.resume(program, journal, run_id,
...)` reloads that snapshot. Resume requires the same serialized PNIR artifact
(including its source paths and contracts), not a differently re-parsed approximation. A crash with pending work never blindly re-executes
an uncertain remote mutation; call `reconcile()` first. Interrupted non-mutating
work follows error handling and triggers recovery if required.

```python
if execution.status == "pending":
    execution.reconcile()
if execution.status in {"recovery_pending", "recovery_failed"}:
    execution.recover()
resumed = Executor.resume(program, journal, execution.run_id,
                          capabilities=bindings, authorizer=authorize)
```

Known completed effects are compensated in **reverse observed completion
order**. Each compensation has its own persisted attempt count, deadline,
authorization checks, typed receipt, and reconciliation protocol. Recovery
stops at an unresolved later compensator rather than skipping past it.
Completed compensation receipts are persisted before local state commit too.
Duplicate `recover()` calls do not re-invoke completed compensators.

Statuses include `ready`, `ended`, `failed`, `pending`, `recovery_pending`,
`recovery_failed`, and `stale`. The original workflow failure remains in
`failure`; per-action recovery issues/receipts/counters are exposed in
`history`. Exhausted budgets do not imply successful recovery. `stale` requires
reloading the authoritative journal; the stale object's state is not current.

SQLite protects local journal ordering, not an atomic transaction with an
external service. Adapters and checkpoint storage are trusted. This is not a
cryptographically authenticated checkpoint format, a distributed lease service,
or a proof of exactly-once effects. Journal rollback, separate journal copies,
remote idempotency violations, and hidden effects in undeclared callbacks are
outside the protocol's guarantees. Use one authoritative journal and enforce
keys/fences at the service boundary. Plain callbacks with no effect manifest
are trusted local operations; arbitrary effect inference is deferred.

## Runtime invariants and ordered policies

Graph invariants and policy invariants must be pure Boolean expressions in the
supported guard subset. They are checked at entry/resume, before dispatch,
and before every candidate state commit, including compensation outputs.
An unsafe candidate is rejected; a remote effect that already completed still
requires saga recovery. Every `check --emit` mode type-checks these obligations,
but ordinary `check` does not label them statically proved.

```panini
policy Approval for Refund {
  invariant state.balance >= 0;
  rule RequireApproval: !state.approved => throw "approval required";
}
```

Policies require an explicit `for Graph` binding. Rules run in declaration
order; the first match globally wins. Runtime rule actions currently support
`end` and `throw` with a string expression. Arbitrary policy action calls remain
unsupported diagnostics. Rules run at forward boundaries; recovery remains
subject to invariants and capability authorization rather than being skipped
by a policy `end` rule.

## Restricted finite invariant model

Use `--model file.json`, optionally `--max-states N`, or
`compile_source(..., model=contract, max_states=N)`:

```json
{
  "graph": "Refund", "property": "invariant-safety",
  "domains": {"approved": "bool", "balance": {"min": 0, "max": 2}},
  "initial": [{"approved": true, "balance": 2}],
  "summaries": {
    "Payments.reserve": {"cases": [{"when": {"balance": 2}, "outputs": [{"balance": 1}]}]},
    "Payments.release": {"cases": [{"outputs": [{"balance": 2}]}]},
    "Payments.notify": {"cases": [{"outputs": [{}]}]}
  }
}
```

Domains are explicit: `bool`, `enum`, bounded signed-64-bit integer `min/max`,
and `decision` (every enum choice plus abstention). Strings, provider data,
receipts, and object fields may remain opaque but cannot drive model predicates.
Decision models are nondeterministic: all choices/abstention are conservatively
included, without assuming accuracy, calibration, or a particular prompt result.
Confidence buckets can be explicit finite integer/enum state fields governed by
their own summaries. Arbitrary probabilities are not treated as a finite domain.

Each initial state assigns every finite field. Method summaries contain cases
with partial `when` conditions and possible output **patches**. Omitted output
fields retain their input value. All matching cases contribute outcomes. Missing
reachable cases or missing methods return `unknown`; they are not silently
assumed safe. An explicit `{"havoc": true}` summary over-approximates every
finite output state. Unknown methods are never automatically granted summaries.

Summaries are contracts, not proofs of host implementations. They must cover
all relevant omitted opaque inputs and receipts. The runtime validates entry
membership, finite domains on resume, and every actual method's projected
input/output relation before committing. A lying callback raises
`ModelContractViolation` or another guard failure; it cannot silently extend a
proved contract. State/type/invariant validation remains independent.

Exploration includes node counters, normal/exceptional edges, possible call
failure and binding-level timeouts, authorization denial, uncertain mutations,
reconciled completions, exhausted budgets, completion history, and bounded
compensation/retry outcomes. Unknown external outcomes are blocking terminals,
not invented successes. Compensation summaries quantify receipts conservatively.
This is a finite safety over-approximation: failures/counterexamples can be
conservative, and a terminal/pending outcome is not a liveness proof.

Results are explicit:

| Status | Meaning | Strict CLI |
| --- | --- | --- |
| `proved` | Reachable candidate commits satisfy declared invariants under the finite entry/domain/summary and capability contracts | 0 |
| `counterexample` | An abstract initial/candidate-commit invariant violation with a source span and trace | 1, E2701 |
| `unknown` | Missing summary/case or domain/state/transition resource limit | 1, E2702 |
| `unsupported` | Unsupported domain, opaque predicate, malformed contract, or temporal property | 1, E2703 |

Traces record states, nodes/phases, events, and counters. They can be replayed
against mocked callbacks to demonstrate runtime rejection. They are abstract
model counterexamples, not evidence that a remote service actually executed an
unsafe action. `proved` means the declared invariant guards need not reject any
modeled candidate that obeys the contracts; it does not verify model accuracy,
successful completion, arbitrary host termination, external-world invariants,
or recovery success. No LTL/CTL/fairness or general liveness checker is claimed.

Resource limits are deterministic and return `unknown`, never a partial proof.
Defaults are 10,000 states and 20 times that many transition exploration units;
`maxTransitions` can override the latter. The checker limits the finite domain
product before enumerating it. Finite proofs can report W2602 warnings for rules
unreachable within the selected initial/summary contract; generic unbounded
policy reachability is not asserted.

A non-proof strict CLI response emits a `FiniteVerificationReport` on stdout
and diagnostics on stderr, irrespective of `--emit`; no successful executable
artifact is emitted. Compile without `--model` to use runtime guards without a
static-proof claim. PNIR records performed checks separately from obligations
still deferred, including arbitrary host effect inference, external atomicity,
and temporal liveness.

## Execution formats and conformance

The executor accepts PNIR v3 with the declared profile and rechecks graph
structure before dispatch. Checkpoint schema v2 preserves program fingerprints,
state, counters, pending work and completion history. Incompatible programs,
malformed counters and unsupported formats are rejected. Program schemas and
source maps are described in [artifacts](artifacts.md).

Capabilities and finite-model files contain metadata, not callback implementations
or credentials. The conformance suite exercises exceptional edges, authorization,
receipt durability, crash/resume, reconciliation, compensation and model-contract
violations. Passing these tests establishes the tested reference behavior;
it does not prove an arbitrary adapter or external service correct.
