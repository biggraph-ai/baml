"""Run a successful and compensated refund against an in-memory fake ledger.

No payment API, external service, credentials, or real financial action is used.
SQLite records the reference scheduler's invocations and recovery transitions.
"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from panini_compiler import (
    Capability,
    EffectResult,
    Executor,
    Reconciliation,
    SQLiteJournal,
    compile_source,
)


class MockLedger:
    def __init__(self):
        self.forward = {}
        self.recovery = {}

    def reserve(self, context, state):
        if context.idempotency_key not in self.forward:
            self.forward[context.idempotency_key] = EffectResult(
                {**state, "balance": state["balance"] - 1},
                {"id": context.idempotency_key},
            )
        return self.forward[context.idempotency_key]

    def release(self, context, state):
        if context.idempotency_key not in self.recovery:
            self.recovery[context.idempotency_key] = EffectResult(
                {**state, "balance": context.original_state["balance"]},
                {"id": context.idempotency_key},
            )
        return self.recovery[context.idempotency_key]

    @staticmethod
    def reconcile(records, context):
        if context.idempotency_key in records:
            return Reconciliation("completed", records[context.idempotency_key])
        # Safe for this mock: there are no delayed background ledger commits.
        return Reconciliation("not_committed")


def main():
    root = Path(__file__).parent
    program = compile_source(
        (root / "refund.panini").read_text(encoding="utf-8"),
        "refund.panini",
        effects=json.loads((root / "refund-effects.json").read_text()),
        model=json.loads((root / "refund-model.json").read_text()),
    )
    if program.diagnostics:
        raise SystemExit([d.to_dict() for d in program.diagnostics])
    print(
        json.dumps(
            {
                "verification": program.analysis["status"],
                "scope": program.analysis["property"],
            }
        )
    )
    with TemporaryDirectory(prefix="panini-refund-") as directory:
        journal = SQLiteJournal(Path(directory) / "runs.db")
        for fail_notification in [False, True]:
            ledger = MockLedger()

            def notify(state):
                if fail_notification:
                    raise RuntimeError("injected notification failure")
                return state

            executor = Executor(
                program,
                "Refund",
                {"approved": True, "balance": 2},
                journal=journal,
                authorizer=lambda request: request.state["approved"],
                capabilities={
                    "Payments.reserve": Capability(
                        ledger.reserve, lambda c: ledger.reconcile(ledger.forward, c)
                    ),
                    "Payments.release": Capability(
                        ledger.release, lambda c: ledger.reconcile(ledger.recovery, c)
                    ),
                    "Payments.notify": notify,
                },
            )
            result = executor.run()
            print(
                json.dumps(
                    {
                        **result,
                        "effectStatus": executor.history[0]["status"],
                        "journalEvents": len(journal.events(executor.run_id)),
                    },
                    sort_keys=True,
                )
            )


if __name__ == "__main__":
    main()
