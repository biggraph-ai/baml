"""Canonical token serialization for the reference frontend."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from lark import Token


@dataclass(frozen=True)
class SourceSpan:
    file: str
    start: int
    end: int
    start_line: int
    start_column: int
    end_line: int
    end_column: int


@dataclass(frozen=True)
class LexToken:
    kind: str
    exact_text: str
    span: SourceSpan
    mode: str = "code"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def byte_offsets(source: str) -> list[int]:
    """Map every Unicode character boundary to its UTF-8 byte offset."""
    offsets = [0]
    total = 0
    for character in source:
        total += len(character.encode("utf-8"))
        offsets.append(total)
    return offsets


def serialize_token(token: Token, source: str, path: str | Path) -> LexToken:
    offsets = byte_offsets(source)
    start = token.start_pos
    end = token.end_pos
    return LexToken(
        kind=token.type,
        exact_text=source[start:end],
        mode="instruction" if token.type == "INSTRUCTION_LITERAL" else "code",
        span=SourceSpan(
            file=str(path),
            start=offsets[start],
            end=offsets[end],
            start_line=token.line,
            start_column=token.column,
            end_line=token.end_line,
            end_column=token.end_column,
        ),
    )
