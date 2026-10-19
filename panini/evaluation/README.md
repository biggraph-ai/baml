# Evaluation and reproducibility protocol

This is a reference compiler for bounded sequential agent workflows with
structural graph checks and optional finite-state invariant analysis under
explicit contracts. Evaluation does not establish general model correctness,
termination of opaque host code, remote atomicity, or successful compensation.

## Reproduce the validation environment

From `panini/`, with Python 3.12 and uv available:

```
python evaluation/validate.py --revision "$(git rev-parse HEAD)" --output /tmp/panini-validation-results.json
```

The script creates a fresh temporary environment, installs the exact transitive
versions in `requirements-validation.txt` with hashes required, builds a source
distribution and wheel without build isolation (the pinned backend is already
installed), installs the wheel without re-resolving dependencies, asserts the
import comes from site-packages, runs the entire test suite, and executes the
runnable CLI/examples in the README and M0–M6 implementation guides. The full
suite covers API examples, checkpoint/resume, artifact execution and negative
cases. Documentation snippets with placeholder bindings are interface sketches;
CLI proposals in design documents are not implemented commands. Windows and
macOS setup instructions are not validated by a Linux run.

The JSON report retains exact commands, exit codes, test output, stdout hashes,
Python/OS information, pinned requirements digest, wheel digest, and source/input
digests. `--revision` identifies the code being tested; `--archive` optionally
records the SHA-256 of the Git source tar used for a clean extraction. Do not
label a modified tree as a clean revision. The committed validation report names
the tested implementation commit; the later evidence commit only adds results.
Temporary executable paths in the report are historical; rerun the script to
create a new environment. The script requires POSIX for this validation workflow.

To validate an exact commit from a clean source archive, run from repository root:

```
revision=$(git rev-parse HEAD)
git archive --format=tar "$revision" -o /tmp/panini-review-source.tar
mkdir -p /tmp/panini-review-source
tar -xf /tmp/panini-review-source.tar -C /tmp/panini-review-source
cd /tmp/panini-review-source/panini
python evaluation/validate.py --revision "$revision" --archive /tmp/panini-review-source.tar --output /tmp/panini-validation-results.json
```

Use an empty extraction directory. Git archives contain the tracked repository
inputs, not local environments or generated caches. Hash-pinned dependencies can
be refreshed deliberately using the command recorded at the top of the lock
file; do not regenerate them while reproducing a recorded run. uv itself and the
Python interpreter are recorded tools, not installed by the dependency file.
The checked-in dependency lock covers the recorded Linux/Python validation;
other platforms/Python versions may need their own pinned validation lock.

## Corpora and metric definitions

`corpus.json` and `reference-results.json` preserve the original six-case smoke
experiment. `regression-corpus.json` expands this to 40 explicitly labeled
cases: 12 valid controls and 28 faulty or unsupported programs. The new cases
are authored in this implementation session from source-language rules and
control-flow patterns, including method/endpoint resolution, guard types,
unreachable subgraphs, ordinary/error/timeout cycles, invalid syntax and
unsupported imports/instance methods. This is one author's specification-derived
regression set, not independently authored evidence or a held-out benchmark.
Endpoint labels use E2001 because source name resolution precedes IR topology
checking; E2301 is the IR endpoint consistency diagnostic. The expected codes
are checked-in inputs, never generated from compiler output by the harness.

Per-code counts use set membership per case: TP is an expected emitted code,
FP an unexpected emitted code, FN an expected missing code. Precision is
TP/(TP+FP), recall TP/(TP+FN), and absent denominators produce JSON null. Micro
metrics sum code counts; exact-code accuracy requires identical sets. Per-class
recall requires an expected diagnostic to appear, so an arbitrary wrong code
is not counted as detection. False positives count valid controls that emit any
diagnostic. Unsupported examples have their own class. The regression suite
checks both labels and deliberately wrong-code/false-positive metric behavior.
These figures describe the cases, not population detection rates.

Further publication work requires a separately authored, held-out corpus and
comparable framework configurations; the current results do not supply either.

## Performance protocol

From `panini/` with the installed compiler:

```
python evaluation/run.py --nodes 100 --repeats 3
python evaluation/matrix.py --sizes 25 100 400 --runs 3 --repeats 7 > /tmp/panini-matrix-results.json
```

The matrix starts a fresh interpreter for each size/run pair: nine independent
process runs on the same machine, seven samples per stage. It records every
sample plus min, max, mean, median, standard deviation and nearest-rank p95.
Parser initialization and semantic-parser initialization are distinct, measured
once per fresh process before stage sampling. Parsing uses an initialized
semantic parser. Resolution and lowering remain fused; topology and finite-model
stages include a deepcopy of prepared inputs. Serialization measures PNIR dict
materialization, not disk I/O. No initialization samples are silently discarded.
Graph kernels separately cover deep chains, large SCCs, dense synthetic graphs
and disconnected vertices. Dense graphs exercise generic adjacency kernels;
they do not pretend the source graph has unbounded fan-out.

The committed `review-matrix-results.json` records one machine and the precise
code commit. Re-run on a second machine before reporting cross-machine results.
A second machine, real decision-provider latency/quality comparisons and external
framework baselines remain unmeasured. No table in a paper should substitute
these regression results for independent evaluation. Timing values and wheel
archive bytes need not reproduce byte-for-byte; semantic fixtures and corpus
labels must reproduce. Exact source inputs remain in Git, with report hashes.

## Review coverage

- CLI subprocesses build and inspect retry, decision and modeled refund artifacts;
  capability discovery and all implemented debugging emit modes are exercised.
- Artifact tests cover exact five-field shape, versions/profiles, source/PNIR
  edits with recomputed digests, tampered digests, malformed manifests, duplicate
  keys, nonfinite values, UTF-8 byte limits, depth/value limits and state exhaustion.
- Graph adversaries cover multiple exceptional paths and cycles that bypass
  bounded nodes. Existing tests also cover deep graphs and diagnostic witnesses.
- Finite tests cover malformed/missing summaries, domain/transition/state limits,
  abstention with policies, and counterexample replay against runtime guards.
- The mutation-denial test observes the remote call and retained receipt before
  compensation. Invocation authorization is distinct from local state commit;
  there is no external prepare/commit protocol.
