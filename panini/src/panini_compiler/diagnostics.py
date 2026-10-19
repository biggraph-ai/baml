"""Stable M1 diagnostics. Spans are half-open UTF-8 byte offsets."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    span: dict
    severity: str = "error"

    def to_dict(self) -> dict:
        return asdict(self)

    def render(self, source: str) -> str:
        prefix = source.encode("utf-8")[: self.span["start"]].decode("utf-8")
        line = prefix.count("\n") + 1
        column = len(prefix.rsplit("\n", 1)[-1]) + 1
        return f"{self.span['file']}:{line}:{column}: {self.code}: {self.message}"
