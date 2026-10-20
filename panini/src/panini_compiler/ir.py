"""Versioned, deterministic PNIR for the sequential effects profile.

Expressions remain structured semantic AST nodes, with resolved type annotations.
Verification reports distinguish structural checks from deferred runtime/effect obligations.
"""

from dataclasses import dataclass, field
from copy import deepcopy


PROFILE = "sequential-effects-v1"


@dataclass
class CheckResult:
    graphs: list[dict] = field(default_factory=list)
    symbols: list[dict] = field(default_factory=list)
    diagnostics: list = field(default_factory=list)
    checks: list[str] = field(
        default_factory=lambda: [
            "name-resolution",
            "graph-types",
            "contract-types",
            "decision-types",
            "effect-contracts",
            "runtime-policy-types",
        ]
    )
    deferred: list[str] = field(
        default_factory=lambda: [
            "graph-topology",
            "dispatch-budget-verification",
            "runtime-enforcement",
            "method-body-verification",
            "arbitrary-host-effect-inference",
            "external-effect-atomicity",
            "static-invariant-proof",
            "temporal-liveness",
        ]
    )

    analysis: dict | None = None

    def to_dict(self) -> dict:
        return deepcopy(
            {
                "schemaVersion": 3,
                "kind": "Program",
                "profile": PROFILE,
                "symbols": self.symbols,
                "graphs": self.graphs,
                "checks": self.checks,
                "deferred": self.deferred,
                "diagnostics": [d.to_dict() for d in self.diagnostics],
                "analysis": self.analysis,
            }
        )
