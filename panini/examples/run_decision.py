"""Execute the example with local fake callbacks; no vendor or payment action."""

import json
from pathlib import Path

from panini_compiler.compiler import compile_source
from panini_compiler.runtime import Executor


def classifier(reason, options):
    probability = 0.55 if "uncertain" in reason.lower() else 0.9
    return {
        "probabilities": {"approve": probability, "review": 1 - probability},
        "modelVersion": "deterministic-mock-v1",
    }


def route(state, label):
    return {**state, "route": label}


def main():
    root = Path(__file__).parent
    program = compile_source(
        (root / "decision.panini").read_text(encoding="utf-8"),
        "decision.panini",
        adapters=json.loads(
            (root / "decision-adapters.json").read_text(encoding="utf-8")
        ),
    )
    if program.diagnostics:
        raise SystemExit([d.to_dict() for d in program.diagnostics])
    for reason in ["Clear duplicate charge", "Uncertain request"]:
        # Non-nullable state fields must be initialized. The initial result is
        # explicitly abstained; it never authorizes an action.
        state = {
            "reason": reason,
            "route": "pending",
            "triage": {
                "choice": None,
                "abstained": True,
                "probabilities": {"approve": 0.5, "review": 0.5},
                "model": "initial",
                "modelVersion": "initial",
            },
        }
        execution = Executor(
            program,
            "Route",
            state,
            capabilities={
                "Routes.approve": lambda s: route(s, "approve"),
                "Routes.review": lambda s: route(s, "review"),
            },
            adapters={"MockClassifier": classifier},
        )
        print(json.dumps(execution.run(), sort_keys=True))


if __name__ == "__main__":
    main()
