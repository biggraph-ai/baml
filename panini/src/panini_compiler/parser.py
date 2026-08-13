"""Strict LALR parser and normalized representation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from lark import Lark, Token, Tree

from .lexer import LexToken, serialize_token


def _span(node: Tree[Any], source: str, path: str | Path) -> dict[str, object]:
    offsets = [0]
    for character in source:
        offsets.append(offsets[-1] + len(character.encode("utf-8")))
    return {"file": str(path), "start": offsets[node.meta.start_pos], "end": offsets[node.meta.end_pos]}


def _normalize(value: Tree[Any] | Token, source: str, path: str | Path) -> Any:
    if isinstance(value, Token):
        if value.type == "INT":
            return {"kind": "IntegerLiteral", "value": int(value)}
        if value.type == "FLOAT":
            return {"kind": "FloatLiteral", "value": float(value)}
        if value.type == "STRING":
            return {"kind": "StringLiteral", "value": value[1:-1]}
        if value.type == "INSTRUCTION_LITERAL":
            return {"kind": "InstructionLiteral", "value": value[2:-2]}
        return str(value)

    return {
        "kind": "".join(part.title() for part in value.data.split("_")),
        "span": _span(value, source, path),
        "children": [_normalize(child, source, path) for child in value.children],
    }


@dataclass
class PaniniParser:
    """Executable definition of the Python reference frontend."""

    parser: Lark

    @classmethod
    def create(cls) -> "PaniniParser":
        grammar = files("panini_compiler").joinpath("panini.lark").read_text(encoding="utf-8")
        return cls(
            Lark(
                grammar,
                parser="lalr",
                lexer="contextual",
                propagate_positions=True,
                maybe_placeholders=False,
                strict=True,
            )
        )

    def parse(self, source: str) -> Tree[Any]:
        return self.parser.parse(source)

    def lex(self, source: str, path: str | Path = "<stdin>") -> list[LexToken]:
        return [serialize_token(token, source, path) for token in self.parser.lex(source)]

    def ast(self, source: str, path: str | Path = "<stdin>") -> dict[str, object]:
        tree = self.parse(source)
        return {"schemaVersion": 1, **_normalize(tree, source, path)}
