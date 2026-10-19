"""Stable semantic, graph, and decision diagnostics. Spans are half-open UTF-8 byte offsets."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    span: dict
    severity: str = "error"
    related_spans: tuple[dict, ...] = ()
    witness: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        result = asdict(self)
        result["related_spans"] = list(self.related_spans)
        result["witness"] = list(self.witness)
        return result

    def render(self, source: str) -> str:
        prefix = source.encode("utf-8")[: self.span["start"]].decode("utf-8")
        line = prefix.count("\n") + 1
        column = len(prefix.rsplit("\n", 1)[-1]) + 1
        rendered = f"{self.span['file']}:{line}:{column}: {self.code}: {self.message}"
        if self.witness:
            rendered += "\n  cycle: " + " -> ".join(self.witness)
        for span in self.related_spans:
            rendered += (
                f"\n  related: {span['file']} bytes {span['start']}..{span['end']}"
            )
        return rendered
