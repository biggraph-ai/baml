# M6: portable reference artifacts and evaluation

The first portable artifact freezes the existing sequential reference profile.
The authoritative interpreter remains `Executor`.

## Artifact contract

From `panini/` after installing the project:

```
panini build examples/retry.panini -o retry.pnir
panini inspect retry.pnir
panini capabilities
```

`build` accepts `--adapters`, `--effects`, and `--model` JSON manifests. It refuses
compiler diagnostics, including unknown or unsupported finite-model results.
`inspect` validates the artifact and emits checked PNIR. Artifact failures exit
nonzero with E2801 on stderr. `check` retains its structured compiler diagnostics.

The envelope has exactly five fields: `format` (`panini-reference-artifact-v1`),
`requires` (`["sequential-effects-v1"]`), `inputs`, `program`, and `sha256`.
Inputs contain source text, its display path, and the three nullable manifests.
Program is PNIR schemaVersion 3. The digest covers the other fields as SHA-256
of UTF-8 JSON with sorted keys, compact separators and no NaN values. Bindings,
credentials and runtime journal contents are supplied separately. Source and
manifest contents are visible to artifact readers.

Use `build_artifact(source, path, ...)` and
`load_artifact(json_text_or_dict, supported=...)` from
`panini_compiler.artifact`. Loading checks target profile support and digest,
recompiles the embedded inputs, and compares the entire canonical PNIR before
returning a `CheckResult`. Unknown versions, requirements and changed PNIR fail
closed. Explicit profile support describes reference interpreter semantics;
runtime preflight still checks platform isolation support and bindings.

This self-checking source bundle is not a signature scheme or hostile-input
sandbox. The digest detects corruption; recompilation prevents trusting edited
check markers or proof claims. Default `ArtifactLimits` cap source at 1 MiB, artifact JSON at 16 MiB, JSON
nesting at 128, JSON values at 200,000, and finite exploration at 10,000 states.
The API accepts `limits=ArtifactLimits(...)`; `build` and `inspect` expose
`--max-source-bytes`, `--max-artifact-bytes`, `--max-json-depth`, and `--max-states`.
CLI files are read with a bounded read before decoding. JSON duplicate keys and
nonfinite constants are rejected when loading artifacts. These budgets do not
bound parser/compiler CPU time or provide a hostile-input sandbox; callers still
control trust and process isolation. Future incompatible compilation output requires an explicit
migration or new format; the current loader requires exact reference output.

## Frozen semantics and conformance

`panini/tests/fixtures/artifact-v1.json` freezes the envelope, complete PNIR,
identities and source spans. PNIR 3 has kind, profile, symbols, graphs, checks,
deferred obligations, diagnostics and nullable analysis. Semantic AST v2
expressions retain their structure. Spans are half-open UTF-8 byte offsets with
the original display filename; that path need not exist on the receiving machine.
Identities follow source order. Compiler diagnostics preserve code, severity,
primary span, related spans and witness order. Empty diagnostics do not discharge
listed deferred obligations.

Interpreter semantics remain in [effects and verification](panini-effects-verification.md)
and [graphs and decisions](panini-graph-decisions.md): pre-dispatch budgets,
deterministic guards, typed state commits, ordered policies, durable receipts,
bounded reconciliation and reverse-order compensation. Checkpoint schema 2 is
separate from the program artifact. Existing runtime fault tests and artifact
round-trip execution enforce the reference behavior. Portability adds no remote
atomicity, arbitrary method-body proofs or temporal liveness guarantees.

## Evaluation

```
cd panini
python evaluation/run.py --nodes 100 --repeats 3 > evaluation/reference-results.json
```

The committed JSON records actual local timings and environment metadata. Each
stage reports raw samples and a median in seconds. Parser initialization is
measured separately. Resolution and lowering are fused and reported together.
Topology verification and finite-model exploration copy prepared inputs; they
exclude parsing/resolution. Serialization is separate. Finite exploration uses
the checked-in refund model with its explicit assumptions.

Isolated SCC and reachability kernels cover deep chains, large SCCs, dense
synthetic adjacency and disconnected vertices. Dense adjacency is synthetic
because the source profile has bounded outdegree. Run multiple sizes and
machines before drawing scaling conclusions; no timing thresholds are CI gates.

The separately labeled `corpus.json` has two valid controls and four faults:
unknown method, unreachable node, unbounded cycle and unsupported import. The
report records expected/actual diagnostic sets, false positives, per-class
recall and exact diagnostic-set accuracy. Unsupported cases are a separate
class. A regression test checks the labels. This smoke corpus does not estimate
real-world defect prevalence or general recall; runtime fault coverage remains
in the larger runtime suite.

External framework baselines and real decision-provider latency remain
unmeasured. Comparisons require equivalent lint/runtime settings and explicit
task quality, input length, option count, hardware, retries, confidence and
abstention policy, and network conditions. The harness fills no paper tables or
provider-performance claims.


### Review validation and expanded evaluation

The CLI commands above are implemented in `cli.py` and covered both in process
and as installed-wheel subprocesses. See
[the evaluation protocol](../panini/evaluation/README.md) for pinned dependencies,
clean-checkout validation commands, corpus provenance and repeated measurements.
The original six-case smoke corpus/results remain intact. Expanded regression
results are descriptive; independently authored cases and a second-machine run
remain outstanding. A proof is conditional invariant safety under user-supplied
initial/domain/summary and effect contracts. Runtime checking of modeled domains
and summaries does not prove the model faithfully describes an external system.
