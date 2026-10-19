"""Repeated fresh-process runs on this machine, retaining every timing sample."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

from run import evaluate_corpus


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--revision")
    parser.add_argument("--sizes", nargs="+", type=int, default=[25, 100, 400])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=7)
    args = parser.parse_args()
    if (
        not 1 <= args.runs <= 20
        or not 1 <= args.repeats <= 100
        or any(not 2 <= size <= 2000 for size in args.sizes)
    ):
        parser.error("runs 1..20, repeats 1..100, sizes 2..2000 required")
    root = Path(__file__).resolve().parent
    records = []
    for size in args.sizes:
        for run in range(args.runs):
            completed = subprocess.run(
                [
                    sys.executable,
                    str(root / "run.py"),
                    "--nodes",
                    str(size),
                    "--repeats",
                    str(args.repeats),
                    "--skip-corpus",
                ]
                + (["--revision", args.revision] if args.revision else []),
                check=True,
                capture_output=True,
                text=True,
            )
            records.append({"run": run + 1, **json.loads(completed.stdout)})
    print(
        json.dumps(
            {
                "schemaVersion": 1,
                "runs": records,
                "smokeCorpus": evaluate_corpus(),
                "regressionCorpus": evaluate_corpus(root / "regression-corpus.json"),
                "limitations": [
                    "One machine; fresh interpreter processes, not fresh machines.",
                    "Hand-authored and specification-derived cases by one implementation session; no independent author study.",
                    "Timing variation is expected; dependency/input/output reproducibility is separate from timing equality.",
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
