"""Strict LALR parser and normalized representation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from functools import lru_cache
from pathlib import Path
from typing import Any

from lark import Lark, Token, Tree

from .lexer import LexToken, serialize_token


def _offsets(source: str) -> list[int]:
    offsets = [0]
    for character in source:
        offsets.append(offsets[-1] + len(character.encode("utf-8")))
    return offsets


def _span(node: Tree[Any], offsets: list[int], path: str | Path) -> dict[str, object]:
    if node.meta.empty:
        return {"file": str(path), "start": 0, "end": offsets[-1]}
    return {
        "file": str(path),
        "start": offsets[node.meta.start_pos],
        "end": offsets[node.meta.end_pos],
    }


def _normalize(value: Tree[Any] | Token, offsets: list[int], path: str | Path) -> Any:
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
        "span": _span(value, offsets, path),
        "children": [_normalize(child, offsets, path) for child in value.children],
    }


@lru_cache(maxsize=1)
def _semantic_parser() -> Lark:
    grammar = (
        files("panini_compiler").joinpath("panini.lark").read_text(encoding="utf-8")
    )
    return Lark(
        grammar,
        parser="lalr",
        lexer="contextual",
        strict=True,
        propagate_positions=True,
        maybe_placeholders=False,
        keep_all_tokens=True,
    )


@dataclass
class PaniniParser:
    """Executable definition of the Python reference frontend."""

    parser: Lark

    @classmethod
    def create(cls) -> "PaniniParser":
        grammar = (
            files("panini_compiler").joinpath("panini.lark").read_text(encoding="utf-8")
        )
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
        return [
            serialize_token(token, source, path) for token in self.parser.lex(source)
        ]

    def ast(self, source: str, path: str | Path = "<stdin>") -> dict[str, object]:
        tree = self.parse(source)
        return {"schemaVersion": 1, **_normalize(tree, _offsets(source), path)}

    def semantic_ast(
        self, source: str, path: str | Path = "<stdin>"
    ) -> dict[str, object]:
        """Version 2 preserves semantic literals; the legacy AST v1 is unchanged.

        Token leaves retain spelling, including operators/modifiers. Rule nodes have
        source-order IDs and byte spans. This is a syntax representation, not proof
        that references, bodies, or graph obligations are valid.
        """
        parser = _semantic_parser()
        offsets = _offsets(source)
        next_id = 0

        def visit(value: Tree[Any] | Token) -> dict[str, object]:
            nonlocal next_id
            item_id = f"syntax:{next_id}"
            next_id += 1
            if isinstance(value, Token):
                return {
                    "kind": "Token",
                    "tokenType": value.type,
                    "text": str(value),
                    "id": item_id,
                    "span": {
                        "file": str(path),
                        "start": offsets[value.start_pos],
                        "end": offsets[value.end_pos],
                    },
                }
            rule = str(value.data)
            kind = "".join(part.title() for part in rule.split("_"))
            children = [visit(child) for child in value.children]
            if rule == "node_contract":
                kind = {
                    "max_visits": "MaxVisitsContract",
                    "timeout": "TimeoutContract",
                    "compensate": "CompensateContract",
                    "writes": "WritesContract",
                    "on_error": "OnErrorContract",
                }[str(value.children[0])]
            span = (
                {"file": str(path), "start": 0, "end": len(source.encode("utf-8"))}
                if value.meta.empty
                else _span(value, offsets, path)
            )
            result = {"kind": kind, "id": item_id, "span": span, "children": children}
            if kind in {"Binary", "Unary"}:
                result["operator"] = next(
                    c["text"] for c in children if c["kind"] == "Token"
                )
            if kind == "Modifier":
                result["value"] = children[0]["text"]
            if kind in {
                "ClassDecl",
                "AgentDecl",
                "InterfaceDecl",
                "EnumDecl",
                "GraphDecl",
                "MemberDecl",
            }:
                result["name"] = next(
                    c["text"]
                    for c in children
                    if c["kind"] == "Token" and c["tokenType"] == "NAME"
                )
            return result

        return {"schemaVersion": 2, **visit(parser.parse(source))}
