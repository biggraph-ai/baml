"""Public source-to-checked-PNIR pipeline."""

from pathlib import Path

from .parser import PaniniParser
from .resolver import Resolver
from .verifier import verify_program
from .ir import CheckResult


def compile_source(
    source: str, path: str | Path = "<stdin>", *, adapters: dict | None = None
) -> CheckResult:
    """Syntax errors raise Lark UnexpectedInput; semantic failures are diagnostics."""
    return verify_program(
        Resolver(adapters).resolve(PaniniParser.create().semantic_ast(source, path))
    )
