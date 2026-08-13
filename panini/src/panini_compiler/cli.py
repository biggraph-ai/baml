"""Command-line interface for inspectable compiler stages."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from lark.exceptions import UnexpectedInput

from .parser import PaniniParser


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="panini", description="Panini reference compiler")
    commands = parser.add_subparsers(dest="command", required=True)
    lex = commands.add_parser("lex", help="emit the token stream as JSON")
    lex.add_argument("file", type=Path)
    for name in ("parse", "check"):
        command = commands.add_parser(name, help=f"{name} a Panini source file")
        command.add_argument("file", type=Path)
        command.add_argument("--emit", choices=("cst", "ast"), default="ast")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    source = args.file.read_text(encoding="utf-8")
    frontend = PaniniParser.create()
    try:
        if args.command == "lex":
            output = [token.to_dict() for token in frontend.lex(source, args.file)]
        elif args.emit == "cst":
            print(frontend.parse(source).pretty(), end="")
            return 0
        else:
            output = frontend.ast(source, args.file)
    except UnexpectedInput as error:
        print(f"{args.file}:{error.line}:{error.column}: E1001: invalid Panini syntax", file=sys.stderr)
        print(error.get_context(source), file=sys.stderr, end="")
        return 1
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0
