"""Versioned, deterministic PNIR for the local sequential M1 profile.

Expressions remain structured semantic AST nodes, with resolved type annotations.
This artifact does not certify topology, execution bounds, policies, or effects.
"""

from dataclasses import dataclass, field


PROFILE = "local-static-state-v1"


@dataclass
class CheckResult:
    graphs: list[dict] = field(default_factory=list)
    symbols: list[dict] = field(default_factory=list)
    diagnostics: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "schemaVersion": 1,
            "kind": "Program",
            "profile": PROFILE,
            "symbols": self.symbols,
            "graphs": self.graphs,
            "checks": ["name-resolution", "graph-types", "contract-types"],
            "deferred": [
                "graph-topology",
                "dispatch-budget-verification",
                "runtime-enforcement",
                "method-body-verification",
                "effects",
                "policy/invariant-verification",
            ],
            "diagnostics": [d.to_dict() for d in self.diagnostics],
        }
