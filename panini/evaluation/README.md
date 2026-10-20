# Evaluation and reproducibility

Panini's evidence concerns a reference compiler for bounded sequential agent
workflows: structural graph checks, runtime enforcement and conditional
finite-state invariant analysis. The measurements distinguish conformance from
realistic effectiveness and comparative performance.

## Recorded evidence

| Record | Scope |
| --- | --- |
| `alphaxiv-validation-results.json` | Clean-archive, installed-wheel validation: 203 tests and 25 successful commands |
| `review-validation-results.json` | Pinned reference validation used by the measured scorecard: 196 tests and 26 commands |
| `review-matrix-results.json` | Nine fresh-process runs: 25/100/400 nodes, three processes per size, seven samples per stage |
| `review-hardware.json` | Host-visible CPU and container resource limits |
| `reference-results.json` | Six-case smoke corpus and a single-size timing run |
| `publication-metrics.json` | Derived scorecard with source hashes and unmeasured fields left null |

Each record names the revision actually tested. Different test counts belong to
different snapshots and are not contradictory observations of one revision.
Machine-specific checkout paths in displayed validation logs are normalized to
`<checkout>`. Their presentation metadata preserves the original report digest;
recorded revisions, measurements, input digests and command/output hashes retain
their original meanings. A hash of an original output is not a hash of redacted
presentation text.

The [scientific evaluation guide](publication-evaluation.md) defines supported
claims, proposed formal statements and experiments. The
[experiment plan](publication-experiment-plan.json) supplies metric formulas and
controls. There is no measured real-provider, matched-framework, held-out or
multi-machine result in these records.

## Fresh validation

From `panini/`, with Python and uv available:

```bash
python evaluation/validate.py --revision "$(git rev-parse HEAD)" --output /tmp/panini-validation-results.json
```

The script creates a new environment, installs `requirements-validation.txt`
with hashes required, builds a source distribution and wheel using the pinned
backend, installs the wheel without dependency resolution, and verifies that
imports come from site-packages. It runs the full suite and the runnable
CLI/API examples. No existing editable installation is used for the checks.
`--matrix-output /tmp/panini-matrix-results.json` also runs the measurement matrix
while that environment exists. The validation workflow is POSIX-specific;
Windows/macOS execution is not established by a Linux result.

For clean-source validation, run from the repository root using a new, empty
extraction directory:

```bash
revision=$(git rev-parse HEAD)
git archive --format=tar "$revision" -o /tmp/panini-source.tar
mkdir /tmp/panini-source
tar -xf /tmp/panini-source.tar -C /tmp/panini-source
cd /tmp/panini-source/panini
python evaluation/validate.py --revision "$revision" --archive /tmp/panini-source.tar --output /tmp/panini-validation-results.json
```

A sandbox with a read-only home may need `UV_CACHE_DIR` set to a writable path.
The report retains commands, exit codes, test output, stdout hashes, Python/OS
versions, requirement/wheel digests, and source/input digests. `--archive` adds
the source tar's digest. Do not label a modified source tree as a clean revision.
Temporary paths in records describe that invocation; rerunning creates new ones.

The dependency lock covers the recorded Linux/Python environment. Python, uv
and operating-system tools are recorded separately. Other environments may need
their own lock. Refresh pinned packages deliberately with the command at the top
of the lock file; do not regenerate it while reproducing an existing run.

## Corpora and diagnostic metrics

`corpus.json` has two valid controls and four faults. The 40-case
`regression-corpus.json` has 12 valid controls, 24 supported faulty programs and
four unsupported programs. It exercises names, guard types, reachability,
normal/error/timeout cycles, syntax and unsupported constructs.

Cases are specification-derived and authored in one implementation session,
not a held-out independent benchmark. Labels are committed inputs, not generated
from compiler output. Source endpoint failures use E2001 because name resolution
precedes IR consistency checking. Code variants are correlated examples, not
independent population samples.

Per-code counts use set membership per case: TP is an expected emitted code,
FP an unexpected emitted code, FN an expected missing code. Precision is
TP/(TP+FP), recall is TP/(TP+FN), and an absent denominator yields JSON null.
Micro metrics sum those counts. Exact-code accuracy requires equal code sets;
false-positive rate counts valid controls that emit any diagnostic. Per-class
detection requires an expected code, so the wrong code does not count as success.
Unsupported cases are reported separately. These are descriptive case results,
not estimates of real-world defect prevalence or general detection rates.

## Performance measurements

From `panini/` with the installed compiler:

```bash
python evaluation/run.py --nodes 100 --repeats 3
python evaluation/matrix.py --sizes 25 100 400 --runs 3 --repeats 7 > /tmp/panini-matrix-results.json
python evaluation/scorecard.py --output evaluation/publication-metrics.json
```

The matrix starts a fresh interpreter for each size/run pair. It retains samples
plus min, max, mean, median, standard deviation and nearest-rank p95. Parser and
semantic-parser initialization are measured separately. Warm stage samples use
an initialized parser. Resolution and lowering are fused; topology and finite
analysis include a deepcopy of prepared inputs. Serialization measures PNIR dict
materialization, not encoding or disk I/O.

The source workload is a chain. Isolated graph kernels also measure deep chains,
large SCCs, dense adjacency and disconnected vertices. Dense graphs exercise
generic kernels, not unbounded source fan-out. The finite checker repeatedly
measures one refund model; it does not sweep model state-space size.

The process is the replication unit for timing summaries. Samples within one
process are nested observations. One machine does not support cross-machine
claims; seven samples do not establish stable p99 latency. Timing samples and
wheel bytes may vary across reproduction. Semantic fixtures and corpus labels
must reproduce.

## Research boundaries

Hold-out workflow families, independent labeling, matched baseline capabilities,
service-side effect oracles, state-space sweeps and resource caps are specified
in the scientific evaluation guide. Proof assumptions include explicit initial
states, finite domains, transition summaries and faithful simulation of relevant
runtime behavior. Runtime contract checks do not prove an external service
correct. Compensation attempts do not establish reversal or task success.
