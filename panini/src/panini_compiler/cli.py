"""Command-line interface for inspectable compiler stages."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from lark.exceptions import UnexpectedInput

from .parser import PaniniParser
from .resolver import Resolver
from .verifier import verify_program
from .diagnostics import Diagnostic


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="panini", description="Panini reference compiler"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    lex = commands.add_parser("lex", help="emit the token stream as JSON")
    lex.add_argument("file", type=Path)
    for name in ("parse", "check"):
        command = commands.add_parser(name, help=f"{name} a Panini source file")
        command.add_argument("file", type=Path)
        command.add_argument(
            "--emit",
            choices=("cst", "ast", "semantic-ast", "pnir")
            if name == "check"
            else ("cst", "ast", "semantic-ast"),
            default="pnir" if name == "check" else "ast",
        )
        if name == "check":
            command.add_argument(
                "--adapters",
                type=Path,
                help="JSON adapter identity/version manifest (no credentials)",
            )
            command.add_argument(
                "--effects", type=Path, help="Capability/receipt contract manifest"
            )
            command.add_argument(
                "--model", type=Path, help="Restricted finite-state safety model"
            )
            command.add_argument("--max-states", type=int, default=10000)
            command.add_argument(
                "--diagnostics", choices=("text", "json"), default="text"
            )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    source = args.file.read_text(encoding="utf-8")
    frontend = PaniniParser.create()
    try:
        if args.command == "lex":
            output = [token.to_dict() for token in frontend.lex(source, args.file)]
        elif args.command == "check":
            semantic = frontend.semantic_ast(source, args.file)
            adapters = {}
            if args.adapters:
                try:
                    adapters = json.loads(args.adapters.read_text(encoding="utf-8"))
                    if not isinstance(adapters, dict):
                        raise ValueError("Manifest must be an object")
                except (OSError, ValueError):
                    diagnostic = Diagnostic(
                        "E2504",
                        "Cannot load adapter manifest: expected a readable JSON object",
                        {"file": str(args.adapters), "start": 0, "end": 0},
                    )
                    print(
                        json.dumps([diagnostic.to_dict()])
                        if args.diagnostics == "json"
                        else diagnostic.render(""),
                        file=sys.stderr,
                    )
                    return 1
            metadata = {}
            for name, code in [("effects", "E2401"), ("model", "E2703")]:
                path = getattr(args, name)
                if path:
                    try:
                        value = json.loads(path.read_text(encoding="utf-8"))
                        if not isinstance(value, dict):
                            raise ValueError("Expected an object")
                        metadata[name] = value
                    except (OSError, ValueError):
                        diagnostic = Diagnostic(
                            code,
                            f"Cannot load {name}: expected a readable JSON object",
                            {"file": str(path), "start": 0, "end": 0},
                        )
                        print(
                            json.dumps([diagnostic.to_dict()])
                            if args.diagnostics == "json"
                            else diagnostic.render(""),
                            file=sys.stderr,
                        )
                        return 1
            result = verify_program(
                Resolver(adapters, metadata.get("effects")).resolve(semantic)
            )
            if "model" in metadata and not result.diagnostics:
                from .modelcheck import analyze_program

                analyze_program(result, metadata["model"], max_states=args.max_states)
            if result.diagnostics:
                if result.analysis is not None:
                    print(
                        json.dumps(
                            {"kind": "FiniteVerificationReport", **result.analysis},
                            indent=2,
                            sort_keys=True,
                        )
                    )
                if args.diagnostics == "json":
                    print(
                        json.dumps(
                            [d.to_dict() for d in result.diagnostics],
                            indent=2,
                            sort_keys=True,
                        ),
                        file=sys.stderr,
                    )
                else:
                    for diagnostic in result.diagnostics:
                        print(diagnostic.render(source), file=sys.stderr)
                return 1
            if args.emit == "cst":
                print(frontend.parse(source).pretty(), end="")
                return 0
            output = (
                frontend.ast(source, args.file)
                if args.emit == "ast"
                else semantic
                if args.emit == "semantic-ast"
                else result.to_dict()
            )
        elif args.emit == "semantic-ast":
            output = frontend.semantic_ast(source, args.file)
        elif args.emit == "cst":
            print(frontend.parse(source).pretty(), end="")
            return 0
        else:
            output = frontend.ast(source, args.file)
    except UnexpectedInput as error:
        if args.command == "check" and args.diagnostics == "json":
            position = getattr(error, "pos_in_stream", len(source))
            position = len(source) if position is None or position < 0 else position
            start = len(source[:position].encode("utf-8"))
            print(
                json.dumps(
                    [
                        {
                            "code": "E1001",
                            "message": "invalid Panini syntax",
                            "severity": "error",
                            "span": {
                                "file": str(args.file),
                                "start": start,
                                "end": start,
                            },
                        }
                    ],
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
        else:
            print(
                f"{args.file}:{error.line}:{error.column}: E1001: invalid Panini syntax",
                file=sys.stderr,
            )
            print(error.get_context(source), file=sys.stderr, end="")
        return 1
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0
