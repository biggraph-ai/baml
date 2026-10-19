# Scientific claims and evaluation metrics for Panini

## Assessment of the statements

The distinction between an engineered artifact and an evaluated scientific claim
is sound. The recorded tests establish regression behavior and reproducibility
on a particular environment; they do not establish novelty, comparative benefit,
real-world defect prevalence, or publication acceptance.

The categorical venue prediction is too strong. Author-written tests are useful
correctness evidence, and publication standards depend on the contribution and
venue. Independent workflows, matched baselines, formal results and additional
machines strengthen particular claims; none is a universal acceptance checklist.
Fifteen to twenty workflows is a sensible pilot scale, not a statistically
justified minimum or a guarantee of acceptance. A second machine probes hardware
sensitivity; it does not establish broad deployment generality.

A calculus and soundness proof are appropriate if the paper claims a new sound
analysis or language design. Their absence must not be concealed by passing
implementation tests. A systems paper may emphasize a different contribution,
but still needs precise semantics and evidence for its central claim. This
report supplies proposed theorem statements and proof obligations, not a
completed proof or a novelty assessment.

The named alternative approaches (ETAS, AgentSPEX, LBAC) are candidate related
work, not automatically runnable or equivalent baselines. Establish the exact
paper/version, artifact availability, semantics and overlapping properties
before choosing comparisons. Do not assign them zero detections because a
Panini-specific construct is unsupported. The same rule applies to LangGraph,
AutoGen and CrewAI. Raw Python is a useful minimum baseline, but is not evidence
about those frameworks until their actual configurations are evaluated.

## Measured scorecard

Reproduce the derived JSON without running experiments:

```
cd panini
python evaluation/scorecard.py --output evaluation/publication-metrics.json
```

Inputs are the committed review matrix, validation log, hardware record and
labeled regression corpus. The JSON records their SHA-256 digests and the tested
implementation revision `07ca7b064ac0c07e6825f7d05dc7d1de26555a88`.
No new runtime, provider or baseline measurements are inferred by this script.

| Quantity | Observed result | What it supports |
| --- | --- | --- |
| Installed-wheel regression suite | 196 tests passed in 37.68 s | Artifact checks in the recorded Linux/Python environment |
| Validation commands | 26/26 succeeded | Recorded build/install/CLI/example workflow |
| Supported faulty source cases rejected with expected code | 24/24 | This specification-derived regression set |
| Unsupported source cases rejected with expected code | 4/4 | Explicit rejection coverage; separate from supported defects |
| Valid controls accepted | 12/12 | No observed false alarms on these controls |
| Exact diagnostic-code sets | 40/40 cases | Diagnostic agreement with the checked-in labels |
| Diagnostic micro counts | TP=28, FP=0, FN=0 | Descriptive code precision/recall of 1.0 on these labels |
| Performance sampling | 3 sizes × 3 processes × 7 samples/stage | Within-machine descriptive timing variation |
| Machines | 1 | No cross-machine conclusion |

The 40 programs were authored from the implementation's specification in one
implementation session. Variants are correlated, not independent random samples.
Do not attach a population confidence interval to their perfect scores or call
these numbers a real-world catch rate. Unsupported rejection is not successful
completion of the corresponding task.

The table below uses the median of the three process medians at each size.
Each process has seven warm samples per stage. The process is the replication
unit; the 21 nested samples are not 21 independent machine experiments.

| Source graph nodes | Parsing (ms) | Resolution + lowering (ms) | Topology verification (ms) | PNIR dict materialization (ms) |
| --- | --- | --- | --- | --- |
| 25 | 2.461 | 2.316 | 0.401 | 0.291 |
| 100 | 8.917 | 8.874 | 1.546 | 1.310 |
| 400 | 48.876 | 51.525 | 6.486 | 5.658 |

The JSON includes every process median and its range. These source graphs are
chains. Other graph shapes are measured by isolated adjacency kernels, not by
pretending that source graphs support arbitrary fan-out. Topology timings
include copying prepared input; materialization is not JSON encoding or disk
I/O. Do not add stage medians and report the sum as measured end-to-end latency.
The table alone neither proves O(V+E) nor establishes a speedup over another tool.

Across nine processes, median parser initialization was 328.981 ms and semantic
parser initialization 177.623 ms. Both are distinct from warm parsing. The fixed
refund model's median of process medians was 1.348 ms. **The refund model did not
change with graph size**, so this is not evidence of state-space scalability.
There are no recorded model-size sweeps or peak-memory measurements.

Hardware: host-visible Intel Xeon Platinum 8573C, x86_64, Python 3.12.14,
Linux/glibc 2.41; the recorded container CPU quota is four CPUs and memory limit
32 GiB. These limits, rather than a processor brand alone, matter for comparison.

## Scientific questions and operational metrics

Use the machine-readable [experiment plan](publication-experiment-plan.json)
for units, formulas and missing-data status. All following experiments are
**proposed**, not completed results.

| Question | Primary metrics | Necessary controls |
| --- | --- | --- |
| Does analysis find relevant defects? | Per-class precision/recall, valid-workflow false-positive rate, eligible/total coverage, diagnostic localization, counterexample replay rate | Independently labeled held-out workflow families; static vs runtime detection reported separately |
| Does verification scale? | Stage latency distributions, peak RSS, explored states/transitions, proved/counterexample/unknown/unsupported counts, timeout rate | Separate graph and domain/budget/history sweeps; equal resource limits and complete reporting of censored runs |
| What does enforcement cost? | Paired latency difference and ratio, throughput at fixed concurrency, bytes/events per operation | Same callback/input/trace; raw Python, checked executor, capability mediation, SQLite durability configurations |
| Do effects remain controlled under faults? | Denied-invocation remote-effect rate, duplicate business effects, uncertainty blocking, reconciliation attempts/time, recovery completion/latency | Fault schedule and service-side oracle independent of runtime logs; distinguish attempted recovery from actual remote reversal |
| Are there comparative benefits? | Paired detection differences, task completion under matched safety constraints, latency/quality trade-off | Versioned Python/framework baseline, equivalent budgets/timeouts/auth/recovery, common supported property subset |

A result schema must distinguish `static_reject`, `runtime_block`, `completed`,
`pending`, `recovery_failed`, `unsupported`, `timeout`, and `harness_error`.
Pending is safe blocking for some properties, not successful task completion.
A tool crash is neither a diagnostic true positive nor an ordinary unsupported
case. Report exclusion counts and reasons for every comparison.

### Benchmark design

Start with 18 workflows across six families: procurement, refund/payment,
incident remediation, code-change pipelines, data approval and service
provisioning. Use three structurally different tasks per family with actual
requirements/provenance and licenses; these are proposed families, not already
collected real-world benchmarks. Include branching, bounded retries, decisions,
normal and exceptional exits, effect receipts and recovery where appropriate.

Obtain independent authorship or adapt external workflows with traceable
provenance. Freeze whole families into development and held-out partitions
before tuning analyses or summaries. Independent reviewers label intended
behavior and injected faults without viewing tool output. Resolve disagreements
with an adjudication log. Paraphrased/template variants stay in the same split.
If the compiler is changed after examining held-out failures, report that test
as development evidence and acquire a new held-out set.

For each workflow retain the valid control and independently specified fault
variants: retry storms, unreachable/error-handler paths, omitted required
compensation or irreversible policy, summary/model mismatch, pre-invocation
authorization denial, post-invocation revocation, crash after remote commit,
reconciliation uncertainty, and decision abstention/policy interactions. Some
faults are expected static rejects, some runtime guards, some explicit unknowns.
An oracle must not require detecting properties outside the declared profile.

For each logical fault record an oracle location and expected property, not just
a tool-specific code. Precision counts reported findings adjudicated against
those labels; recall counts oracle faults detected. Match at most one finding
to a logical fault, report duplicates separately, and preserve missing findings.
Use matched primary locations for localization; list secondary spans separately.
Source-specific code accuracy remains a separate conformance metric.

### Scalability and overhead experiments

For graph analysis, sweep nodes 10/25/100/400/1000/4000 and edge density/shape
where supported. The current harness caps source nodes at 2000; deliberately
extend and validate that generator before claiming a 4000-node source run.
Keep graph-kernel and frontend experiments separate. Record actual V and E.
For model analysis, independently vary finite-domain size, number of modeled
fields, visit budgets, branching, outstanding completion history and recovery
bounds. Record reachable states/transitions, configured limits and proof status.
The product of field domains is not the full search space: node, counters,
history and execution phase also matter. Measure both proved and injected-unsafe
families; fast counterexample discovery is not exhaustive proof throughput.

Measure process peak RSS (MiB) and elapsed monotonic time (ms). Report status
frequencies at common time/memory/state caps. Unknown/timeout runs stay in the
denominator and are censored, not silently discarded from the fast-run mean.
Capture fresh-process initialization separately from warm execution.

For runtime overhead, replay identical deterministic mocked callback traces
before introducing provider variability. Compare raw Python with equivalent
validation/retry rules, the checked executor, capability authorization, and
SQLite durability. Keep any reduced-safety configuration explicitly labeled as
an ablation, not an equivalent baseline. Effectful runs require the journal;
do not disable that requirement to manufacture a comparison. Time callback and
orchestration separately; use several controlled callback durations and state
sizes so constant overhead is visible. The implementation uses SQLite revision
CAS/fencing, not a separately implemented advisory-lock subsystem; measure the
mechanism actually present.

Repeat on at least two machines for a cross-machine claim. Randomize/interleave
paired tool configurations and record OS, CPU quota, storage/fsync settings,
Python, dependencies, concurrency, network and cache conditions. Use workflow-
family-clustered bootstrap intervals for paired effectiveness/overhead deltas
when enough families exist. Pilot variance and a prespecified minimum meaningful
effect determine sample size; 18 workflows alone do not establish power. For
latency tails retain sufficient observations to estimate p95/p99; do not infer a
stable p99 from seven samples. Keep descriptive per-process distributions.

Verify runnable artifacts and semantic overlap before choosing an alternative
verification baseline. Use an explicit compatibility matrix: supported property,
requires encoding, unsupported, or artifact unavailable. Report both common-
subset results and coverage on the full suite. Select and pin actual framework
versions; do not infer framework capability from a bare unconfigured pipeline.

## Formal core and claims to establish

A small core can abstract opaque methods as contract-labeled transitions:

```
Program ::= StateSchema; Graph; Policies; EffectContracts
Node    ::= call(method, visitLimit, errorTarget, timeoutTarget, compensation)
Edge    ::= target | if(pureGuard, target, target)
Config  ::= <pc, state, visitCounts, history, phase, journalRevision>
Phase   ::= ready | invoking | pending | recovering | ended | failed
```

Define typing/guard evaluation, pre-dispatch budget consumption, policy order,
authorization, candidate validation/commit, uncertainty and reconciliation,
compensation and journal resume rules. Match these abstract rules to the actual
interpreter; syntax on this page is a proposed formalization, not a second parser.

**Dispatch-boundedness statement.** For a structurally accepted single-token
graph, let b(v) be its explicit finite visit limit, or 1 for an acyclic node
without one, and B = sum over v of b(v). Assume budgets are enforced before every
forward dispatch and authoritative counts cannot be reset on resume. Then every
execution prefix contains at most B budget-consuming forward dispatch attempts.
Callback failure still consumes its attempt. Compensation and reconciliation
have separately declared bounds; they are not counted in B.

A proof obligation is that the potential Phi = sum(b(v) - count(v)) is initially
B, never negative, and decreases by one on each such dispatch. Exceptional
edges are included in structural checks. This does **not** establish reaching a
terminal/pending state within B operational steps: local callback execution,
recovery, journal operations and repeated pending observations are different
steps, and an arbitrary host callback can fail to return. B bounds neither
wall-clock time nor all external actions. The proposed statement in the prompt
is therefore stronger than the implemented contract.

**Conditional model-safety statement.** Let alpha project concrete states onto
finite domains, I be the declared modeled invariant, and M be the transition
system built from explicit initial states, domains and summaries. If exhaustive
analysis reports `proved`, M over-approximates all relevant forward, exceptional,
policy and recovery behavior, entry/domain assumptions hold, and a simulation
between contract-compliant concrete steps and M is established, then all modeled
reachable candidate commits satisfy I. The open proof obligation is preservation
of that simulation, including compensation and policy/invariant ordering.

For arbitrary callbacks, an unsafe *proposed* candidate can still occur. The
runtime must reject it before local commit; no statement that every raw callback
proposal is safe follows from the model result. An independent runtime theorem
would establish that every accepted local commit preserves the invariant,
assuming correct validators, trusted storage/fencing and correct transition
ordering. Runtime checks of domains/summaries enforce the local contract; they
do not prove user summaries faithfully describe external services or receipts.
Finite exhaustion, missing summaries and unsupported properties must never be
presented as proofs. Tests and counterexample replay are conformance evidence,
not substitutes for these proofs.

## Limitations and submission framing

No claim here proves arbitrary host termination, temporal liveness, LLM quality,
remote atomicity, successful compensation, or absence of external side effects
before a local state check. Invocation approval precedes the adapter call;
post-invocation approval gates only local state commit. A stale-authorization
experiment must distinguish those boundaries. No distributed prepare/commit
protocol is implemented.

For a preprint, report the implemented reference profile, conditional guarantees,
current descriptive results and open proof obligations honestly. For an empirical
conference claim, complete and preregister the matching held-out/baseline
experiments, with independent labels and resource distributions. For a language
soundness claim, complete the calculus and proof argument and validate compiler/
interpreter correspondence. Neither arXiv posting nor a completed checklist
establishes novelty or guarantees peer-review acceptance. No aggregate
"publication readiness percentage" is justified by the current evidence.
