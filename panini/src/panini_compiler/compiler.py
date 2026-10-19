"""Public source-to-checked-PNIR pipeline."""

from pathlib import Path

from .parser import PaniniParser
from .resolver import Resolver
from .verifier import verify_program
from .ir import CheckResult


def compile_source(
    source: str,
    path: str | Path = "<stdin>",
    *,
    adapters: dict | None = None,
    effects: dict | None = None,
    model: dict | None = None,
    max_states: int = 10000,
) -> CheckResult:
    """Syntax errors raise Lark UnexpectedInput; semantic failures are diagnostics."""
    result = verify_program(
        Resolver(adapters, effects).resolve(
            PaniniParser.create().semantic_ast(source, path)
        )
    )
    if model is not None and not result.diagnostics:
        from .modelcheck import analyze_program

        analyze_program(result, model, max_states=max_states)
    return result
