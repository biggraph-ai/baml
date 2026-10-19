"""Validate a checkout/archive with a fresh hash-pinned environment and wheel.

Usage: python evaluation/validate.py --revision HASH --output /tmp/validation.json
Requires uv on PATH. No existing project environment is used.
"""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--revision", required=True)
    cli.add_argument("--output", type=Path, required=True)
    cli.add_argument("--matrix-output", type=Path)
    cli.add_argument("--archive", type=Path)
    args = cli.parse_args()
    root = Path(__file__).resolve().parents[1]
    report = {
        "revision": args.revision,
        "python": sys.version,
        "platform": platform.platform(),
        "commands": [],
        "archiveSha256": sha(args.archive.read_bytes()) if args.archive else None,
        "inputs": {
            str(path.relative_to(root)): sha(path.read_bytes())
            for directory in ("src", "tests", "examples", "evaluation")
            for path in sorted((root / directory).rglob("*"))
            if path.is_file()
            and path.suffix in {".py", ".panini", ".lark", ".json", ".in"}
            and "__pycache__" not in path.parts
            and "results" not in path.name
        },
        "requirementsSha256": sha((root / "requirements-validation.txt").read_bytes()),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def run(command, *, check_json=False, code=None):
        result = subprocess.run(
            [str(item) for item in command], cwd=root, capture_output=True, text=True
        )
        row = {
            "command": [str(item) for item in command],
            "exitCode": result.returncode,
            "stdoutSha256": sha(result.stdout.encode()),
            "stdoutBytes": len(result.stdout.encode()),
            "stderr": result.stderr,
        }
        if code == "tests":
            row["testResult"] = result.stdout
        report["commands"].append(row)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        if result.returncode:
            raise RuntimeError(
                f"Validation failed: {command}\n{result.stdout}\n{result.stderr}"
            )
        if check_json:
            return json.loads(result.stdout)
        return result.stdout

    with tempfile.TemporaryDirectory(prefix="panini-validation-") as folder:
        work = Path(folder)
        env = work / "venv"
        run(["uv", "--version"])
        run(["uv", "venv", "--python", sys.executable, env])
        python = env / "bin/python"
        panini = env / "bin/panini"
        run(
            [
                "uv",
                "pip",
                "sync",
                "--python",
                python,
                "--require-hashes",
                "requirements-validation.txt",
            ]
        )
        run([python, "-m", "build", "--no-isolation", "--outdir", work / "dist"])
        wheel = next((work / "dist").glob("*.whl"))
        report["wheelSha256"] = sha(wheel.read_bytes())
        run(["uv", "pip", "install", "--python", python, "--no-deps", wheel])
        run(
            [
                python,
                "-c",
                "import panini_compiler; assert 'site-packages' in panini_compiler.__file__; print(panini_compiler.__file__)",
            ]
        )
        run([python, "-m", "pytest", "tests"], code="tests")
        run([panini, "--help"])
        run([panini, "lex", "examples/audit.panini"], check_json=True)
        run([panini, "parse", "examples/audit.panini", "--emit", "cst"])
        run(
            [panini, "parse", "examples/audit.panini", "--emit", "ast"], check_json=True
        )
        run(
            [panini, "parse", "examples/retry.panini", "--emit", "semantic-ast"],
            check_json=True,
        )
        run([panini, "check", "examples/retry.panini"], check_json=True)
        run(
            [panini, "check", "examples/retry.panini", "--diagnostics", "json"],
            check_json=True,
        )
        run(
            [
                panini,
                "check",
                "examples/decision.panini",
                "--adapters",
                "examples/decision-adapters.json",
            ],
            check_json=True,
        )
        proof = run(
            [
                panini,
                "check",
                "examples/refund.panini",
                "--effects",
                "examples/refund-effects.json",
                "--model",
                "examples/refund-model.json",
            ],
            check_json=True,
        )
        assert proof["analysis"]["status"] == "proved"
        for example in ("run_decision.py", "run_refund.py"):
            run([python, "examples/" + example])
        for example in ("retry", "decision", "refund"):
            target = work / (example + ".pnir")
            command = [panini, "build", "examples/" + example + ".panini", "-o", target]
            if example == "decision":
                command += ["--adapters", "examples/decision-adapters.json"]
            if example == "refund":
                command += [
                    "--effects",
                    "examples/refund-effects.json",
                    "--model",
                    "examples/refund-model.json",
                ]
            run(command)
            run([panini, "inspect", target], check_json=True)
        run([panini, "capabilities"], check_json=True)
        # API round-trip, tampering, budgets, source maps, policy/effect behavior
        # are assertions in the full installed-wheel test suite above.
        if args.matrix_output:
            matrix = run(
                [
                    python,
                    "evaluation/matrix.py",
                    "--revision",
                    args.revision,
                    "--sizes",
                    "25",
                    "100",
                    "400",
                    "--runs",
                    "3",
                    "--repeats",
                    "7",
                ],
                check_json=True,
            )
            args.matrix_output.parent.mkdir(parents=True, exist_ok=True)
            args.matrix_output.write_text(json.dumps(matrix, indent=2) + "\n")
            report["matrixSha256"] = sha(args.matrix_output.read_bytes())
        report["status"] = "passed"
        report["notRun"] = [
            "Windows/macOS execution",
            "second machine",
            "external provider/framework comparison",
        ]
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Validation passed: {args.output}")


if __name__ == "__main__":
    main()
