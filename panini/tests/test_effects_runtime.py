from copy import deepcopy
import threading
import time

import pytest


from panini_compiler.compiler import compile_source
from panini_compiler.effects import Capability, EffectResult, Reconciliation
from panini_compiler.journal import SQLiteJournal
from panini_compiler.runtime import Executor, RuntimeFailure


SOURCE = """
class S { bool approved; int balance; }
class Receipt { string token; }
class Pay {
 public static S charge(S s);
 public static S undo(S s);
 public static S done(S s);
}
graph G(s:S) {
 invariant s.balance >= 0;
 node charge using Pay.charge max_visits 1 timeout seconds(0.02) on_timeout => done on_error => done compensate using Pay.undo;
 node done using Pay.done;
 start -> charge;
 charge -> done;
 done -> end;
}
"""
EFFECTS = {
    "Pay.charge": {
        "kind": "mutation",
        "capability": "pay.charge",
        "receiptType": "Receipt",
        "idempotent": True,
        "reconcilable": True,
        "timeoutSeconds": 0.2,
    },
    "Pay.undo": {
        "kind": "recovery",
        "capability": "pay.undo",
        "receiptType": "Receipt",
        "idempotent": True,
        "reconcilable": True,
        "timeoutSeconds": 0.02,
        "maxAttempts": 2,
    },
}


def program(source=SOURCE, effects=EFFECTS):
    r = compile_source(source, effects=effects)
    assert not r.diagnostics, [d.to_dict() for d in r.diagnostics]
    return r


class Bank:
    def __init__(self):
        self.results = {}
        self.undo_results = {}
        self.calls = []
        self.undos = []

    def charge(self, ctx, s):
        self.calls.append(ctx.idempotency_key)
        result = self.results.get(ctx.idempotency_key)
        if result is None:
            result = EffectResult(
                {**s, "balance": s["balance"] - 1}, {"token": ctx.idempotency_key}
            )
            self.results[ctx.idempotency_key] = result
        return result

    def reconcile(self, ctx):
        if ctx.idempotency_key in self.results:
            return Reconciliation("completed", self.results[ctx.idempotency_key])
        return Reconciliation("not_committed")

    def undo(self, ctx, s):
        self.undos.append(ctx.idempotency_key)
        assert ctx.receipt == {"token": ctx.invocation_id.removesuffix(":compensate")}
        result = self.undo_results.get(ctx.idempotency_key)
        if result is None:
            result = EffectResult(
                {**s, "balance": ctx.original_state["balance"]},
                {"token": ctx.idempotency_key},
            )
            self.undo_results[ctx.idempotency_key] = result
        return result

    def reconcile_undo(self, ctx):
        if ctx.idempotency_key in self.undo_results:
            return Reconciliation("completed", self.undo_results[ctx.idempotency_key])
        return Reconciliation("not_committed")

    def bindings(self, done=lambda s: s):
        return {
            "Pay.charge": Capability(self.charge, self.reconcile),
            "Pay.undo": Capability(self.undo, self.reconcile_undo),
            "Pay.done": done,
        }


def test_effect_authorization_and_durable_records(tmp_path):
    p = program()
    bank = Bank()
    phases = []

    def authorize(r):
        phases.append(r.phase)
        assert r.context.fence > 0
        return r.state["approved"]

    journal = SQLiteJournal(tmp_path / "runs.db")
    e = Executor(
        p,
        "G",
        {"approved": True, "balance": 2},
        capabilities=bank.bindings(),
        authorizer=authorize,
        journal=journal,
    )
    assert e.run()["status"] == "ended"
    assert e.state["balance"] == 1 and phases == ["invoke", "commit"]
    assert len(bank.calls) == 1
    saved, revision = journal.load(e.run_id)
    assert (
        saved["revision"] == revision and saved["history"][0]["status"] == "completed"
    )
    assert {"dispatch_started", "effect_started", "effect_completed"} <= {
        event["kind"] for event in journal.events(e.run_id)
    }
    denied = Executor(
        p,
        "G",
        {"approved": False, "balance": 2},
        capabilities=bank.bindings(),
        authorizer=authorize,
        journal=journal,
    )
    assert denied.run()["failure"]["code"] == "AuthorizationDenied"
    assert len(bank.calls) == 1


def test_deadline_routes_and_stale_local_result_is_discarded(tmp_path):
    source = SOURCE.replace(
        "node charge using Pay.charge", "node charge using Pay.done"
    ).replace(" compensate using Pay.undo", "")
    p = program(source, effects={})

    def slow(s):
        time.sleep(0.15)
        return {**s, "balance": 99}

    e = Executor(
        p, "G", {"approved": True, "balance": 2}, capabilities={"Pay.done": slow}
    )
    started = time.monotonic()
    assert e.step() == "ready"
    assert time.monotonic() - started < 0.5
    assert e.state["balance"] == 2
    # The next node has no timeout, so use a fast replacement; the killed child
    # cannot publish its late value into this invocation's state.
    e._capabilities["Pay.done"] = lambda s: s
    assert e.run()["status"] == "ended"
    assert e.state["balance"] == 2


def test_unknown_mutation_blocks_until_reconciliation_and_rechecks_authority(tmp_path):
    p = program()
    bank = Bank()
    gate = threading.Event()
    started = threading.Event()

    def slow(ctx, s):
        started.set()
        gate.wait(1)
        return bank.charge(ctx, s)

    current = {"allowed": True}
    bindings = bank.bindings()
    bindings["Pay.charge"] = Capability(
        slow,
        lambda ctx: (
            Reconciliation("unknown") if not gate.is_set() else bank.reconcile(ctx)
        ),
    )
    journal = SQLiteJournal(tmp_path / "runs.db")
    e = Executor(
        p,
        "G",
        {"approved": True, "balance": 2},
        capabilities=bindings,
        authorizer=lambda r: current["allowed"],
        journal=journal,
    )
    assert e.run()["status"] == "pending"
    assert e.dispatches == 1 and e.state["balance"] == 2 and started.is_set()
    assert e.reconcile() == "pending"
    assert e.dispatches == 1
    gate.set()
    for _ in range(100):
        if bank.calls:
            break
        time.sleep(0.002)
    current["allowed"] = False
    resumed = Executor.resume(
        p,
        journal,
        e.run_id,
        capabilities=bindings,
        authorizer=lambda r: current["allowed"],
    )
    assert resumed.reconcile() == "pending"
    assert resumed.failure["code"] == "AuthorizationDenied"
    current["allowed"] = True
    assert resumed.reconcile() == "ready"
    assert resumed.run()["status"] == "ended"
    assert resumed.state["balance"] == 1 and len(bank.calls) == 1


def test_terminal_failure_compensates_and_does_not_duplicate_recovery(tmp_path):
    p = program()
    bank = Bank()

    def fail(s):
        raise ValueError("downstream")

    journal = SQLiteJournal(tmp_path / "runs.db")
    e = Executor(
        p,
        "G",
        {"approved": True, "balance": 2},
        capabilities=bank.bindings(fail),
        authorizer=lambda _: True,
        journal=journal,
    )
    result = e.run()
    assert result["status"] == "failed" and result["failure"]["code"] == "NodeFailed"
    assert e.state["balance"] == 2 and len(bank.undos) == 1
    assert e.history[0]["status"] == "compensated"
    resumed = Executor.resume(
        p,
        journal,
        e.run_id,
        capabilities=bank.bindings(fail),
        authorizer=lambda _: True,
    )
    resumed.recover()
    assert len(bank.undos) == 1


def test_commit_denial_and_invariant_failure_retain_receipt_and_recover(tmp_path):
    for mode in ["commit_denial", "invariant"]:
        p = program()
        bank = Bank()

        def charge(ctx, s):
            result = bank.charge(ctx, s)
            if mode == "invariant":
                return EffectResult({**s, "balance": -1}, result.receipt)
            return result

        bindings = bank.bindings()
        bindings["Pay.charge"] = Capability(charge, bank.reconcile)
        e = Executor(
            p,
            "G",
            {"approved": True, "balance": 2},
            capabilities=bindings,
            authorizer=lambda r: (
                not (
                    mode == "commit_denial"
                    and r.phase == "commit"
                    and r.capability == "pay.charge"
                )
            ),
            journal=SQLiteJournal(tmp_path / (mode + ".db")),
        )
        assert e.run()["status"] == "failed"
        assert e.failure["code"] == (
            "AuthorizationDenied" if mode == "commit_denial" else "InvariantViolation"
        )
        # Post-invocation denial cannot prevent an external commit: the bank
        # observed the charge, and its durable receipt drove recovery afterward.
        assert len(bank.calls) == 1 and len(bank.results) == 1
        assert e.history[0]["receipt"] == {"token": bank.calls[0]}
        assert len(bank.undos) == 1 and e.state["balance"] == 2


def test_compensation_unknown_outcome_is_not_blindly_retried(tmp_path):
    p = program()
    bank = Bank()
    gate = threading.Event()

    def slow_undo(ctx, s):
        value = bank.undo(ctx, s)
        gate.wait(1)
        return value

    bindings = bank.bindings(lambda s: (_ for _ in ()).throw(ValueError("fail")))
    bindings["Pay.undo"] = Capability(slow_undo, bank.reconcile_undo)
    journal = SQLiteJournal(tmp_path / "runs.db")
    e = Executor(
        p,
        "G",
        {"approved": True, "balance": 2},
        capabilities=bindings,
        authorizer=lambda _: True,
        journal=journal,
    )
    assert e.run()["status"] == "recovery_pending"
    assert len(bank.undos) == 1
    assert e.recover() == "failed"
    assert len(bank.undos) == 1
    assert e.history[0]["status"] == "compensated"
    gate.set()


def test_stale_executor_is_fenced_before_side_effect(tmp_path):
    p = program()
    bank = Bank()
    journal = SQLiteJournal(tmp_path / "runs.db")
    kwargs = {"capabilities": bank.bindings(), "authorizer": lambda _: True}
    e = Executor(p, "G", {"approved": True, "balance": 2}, journal=journal, **kwargs)
    other = Executor.resume(p, journal, e.run_id, **kwargs)
    assert e.step() == "ready"
    assert other.step() == "stale"
    assert len(bank.calls) == 1


def test_manifests_and_exceptional_cycles_are_checked():
    bad = deepcopy(EFFECTS)
    bad["Pay.charge"]["idempotent"] = False
    assert "E2401" in {d.code for d in compile_source(SOURCE, effects=bad).diagnostics}
    source = SOURCE.replace("max_visits 1 ", "").replace(
        "on_timeout => done", "on_timeout => charge"
    )
    assert "E2306" in {
        d.code for d in compile_source(source, effects=EFFECTS).diagnostics
    }


def test_crash_window_after_remote_commit_is_reconciled_not_reexecuted(tmp_path):
    p = program(SOURCE.replace("seconds(0.02)", "seconds(0.5)"))
    bank = Bank()
    committed = threading.Event()
    release = threading.Event()

    def hold_result(ctx, s):
        value = bank.charge(ctx, s)
        committed.set()
        release.wait(1)
        return value

    bindings = bank.bindings()
    bindings["Pay.charge"] = Capability(hold_result, bank.reconcile)
    journal = SQLiteJournal(tmp_path / "runs.db")
    e = Executor(
        p,
        "G",
        {"approved": True, "balance": 2},
        capabilities=bindings,
        authorizer=lambda _: True,
        journal=journal,
    )
    worker = threading.Thread(target=e.step)
    worker.start()
    assert committed.wait(0.5)
    resumed = Executor.resume(
        p, journal, e.run_id, capabilities=bindings, authorizer=lambda _: True
    )
    assert resumed.status == "pending"
    assert resumed.reconcile() == "ready"
    release.set()
    worker.join(1)
    assert not worker.is_alive() and e.status == "stale"
    assert resumed.run()["status"] == "ended"
    assert len(bank.calls) == 1 and resumed.state["balance"] == 1


def test_compensation_retries_are_independent_and_exhaustible(tmp_path):
    p = program()
    bank = Bank()
    attempts = []

    def fail_undo(ctx, s):
        attempts.append(ctx.idempotency_key)
        raise RuntimeError("compensator unavailable")

    bindings = bank.bindings(lambda s: (_ for _ in ()).throw(ValueError("downstream")))
    bindings["Pay.undo"] = Capability(
        fail_undo, lambda ctx: Reconciliation("not_committed")
    )
    e = Executor(
        p,
        "G",
        {"approved": True, "balance": 2},
        capabilities=bindings,
        authorizer=lambda _: True,
        journal=SQLiteJournal(tmp_path / "runs.db"),
    )
    assert e.run()["status"] == "recovery_pending"
    assert e.recover() == "recovery_pending"
    assert e.recover() == "recovery_failed"
    assert e.recover() == "recovery_failed"
    assert len(attempts) == 2 and len(set(attempts)) == 1
    assert e.history[0]["receipt"]["token"] in bank.results


def test_unknown_reconciliation_has_a_finite_attempt_budget(tmp_path):
    p = program()
    calls = []

    def unknown(ctx, s):
        raise RuntimeError("may have committed")

    def reconcile(ctx):
        calls.append(ctx.idempotency_key)
        return Reconciliation("unknown")

    bank = Bank()
    bindings = bank.bindings()
    bindings["Pay.charge"] = Capability(unknown, reconcile)
    e = Executor(
        p,
        "G",
        {"approved": True, "balance": 2},
        capabilities=bindings,
        authorizer=lambda _: True,
        journal=SQLiteJournal(tmp_path / "runs.db"),
    )
    assert e.run()["status"] == "pending"
    for _ in range(5):
        e.reconcile()
    assert len(calls) == 3 and e.failure["code"] == "ReconciliationExhausted"


def test_reverse_completion_order_and_explicit_irreversible_policy(tmp_path):
    source = SOURCE.replace(
        "node done using Pay.done;",
        "node second using Pay.charge max_visits 1 compensate using Pay.undo; node done using Pay.done;",
    ).replace("charge -> done;", "charge -> second; second -> done;")
    p = program(source)
    bank = Bank()
    e = Executor(
        p,
        "G",
        {"approved": True, "balance": 3},
        capabilities=bank.bindings(lambda s: (_ for _ in ()).throw(ValueError("fail"))),
        authorizer=lambda _: True,
        journal=SQLiteJournal(tmp_path / "ordered.db"),
    )
    assert e.run()["status"] == "failed"
    assert bank.undos == [key + ":compensate" for key in reversed(bank.calls)]
    assert e.state["balance"] == 3
    effects = deepcopy(EFFECTS)
    effects["Pay.charge"]["irreversiblePolicy"] = "human-confirmed"
    source = SOURCE.replace(" compensate using Pay.undo", "")
    p = program(source, effects)
    bank = Bank()
    policies = []

    def authorize(request):
        policies.append(request.irreversible_policy)
        return True

    e = Executor(
        p,
        "G",
        {"approved": True, "balance": 2},
        capabilities=bank.bindings(lambda s: (_ for _ in ()).throw(ValueError("fail"))),
        authorizer=authorize,
        journal=SQLiteJournal(tmp_path / "irreversible.db"),
    )
    assert e.run()["status"] == "recovery_failed"
    assert e.history[0]["recoveryIssue"].startswith("Irreversible")
    assert policies == ["human-confirmed", "human-confirmed"]


def test_runtimefailures_after_a_possible_commit_are_also_uncertain(tmp_path):
    p = program()
    bank = Bank()

    def fail_after_commit(ctx, s):
        bank.charge(ctx, s)
        raise RuntimeFailure("RemoteFailure", "response lost")

    bindings = bank.bindings()
    bindings["Pay.charge"] = Capability(fail_after_commit, bank.reconcile)
    e = Executor(
        p,
        "G",
        {"approved": True, "balance": 2},
        capabilities=bindings,
        authorizer=lambda _: True,
        journal=SQLiteJournal(tmp_path / "runs.db"),
    )
    assert e.run()["status"] == "pending"
    assert e.reconcile() == "ready"
    assert e.run()["status"] == "ended" and len(bank.calls) == 1


def test_cancellation_acknowledgement_is_not_treated_as_reconciliation(tmp_path):
    p = program()
    observed = []

    def cancelable(ctx, s):
        observed.append(ctx)
        assert ctx.cancel_requested.wait(0.5)
        ctx.cancel_acknowledged.set()
        return EffectResult(s, {"token": ctx.idempotency_key})

    bank = Bank()
    bindings = bank.bindings()
    bindings["Pay.charge"] = Capability(
        cancelable, lambda ctx: Reconciliation("not_committed")
    )
    e = Executor(
        p,
        "G",
        {"approved": True, "balance": 2},
        capabilities=bindings,
        authorizer=lambda _: True,
        journal=SQLiteJournal(tmp_path / "runs.db"),
    )
    assert e.run()["status"] == "pending"
    assert observed[0].cancel_acknowledged.wait(0.5)
    assert e.history[0]["cancellation"]["requested"] is True
    assert e.reconcile() == "ready"
    assert e.run()["status"] == "ended" and e.state["balance"] == 2


def test_bad_receipt_never_discards_an_uncertain_mutation(tmp_path):
    p = program()
    bank = Bank()

    def corrupt_receipt(ctx, s):
        result = bank.charge(ctx, s)
        return EffectResult(result.value, {"wrong": "receipt"})

    bindings = bank.bindings()
    bindings["Pay.charge"] = Capability(corrupt_receipt, bank.reconcile)
    e = Executor(
        p,
        "G",
        {"approved": True, "balance": 2},
        capabilities=bindings,
        authorizer=lambda _: True,
        journal=SQLiteJournal(tmp_path / "runs.db"),
    )
    assert e.run()["status"] == "pending"
    assert e.state["balance"] == 2
    assert e.reconcile() == "ready"
    assert e.run()["status"] == "ended" and e.state["balance"] == 1


def test_recovery_runs_after_a_crash_in_a_later_pure_node(tmp_path):
    p = program()
    bank = Bank()
    entered = threading.Event()
    release = threading.Event()

    def hold(s):
        entered.set()
        release.wait(1)
        return s

    bindings = bank.bindings(hold)
    journal = SQLiteJournal(tmp_path / "runs.db")
    e = Executor(
        p,
        "G",
        {"approved": True, "balance": 2},
        capabilities=bindings,
        authorizer=lambda _: True,
        journal=journal,
    )
    assert e.step() == "ready"
    worker = threading.Thread(target=e.step)
    worker.start()
    assert entered.wait(0.5)
    resumed = Executor.resume(
        p, journal, e.run_id, capabilities=bindings, authorizer=lambda _: True
    )
    assert resumed.reconcile() == "failed"
    assert len(bank.undos) == 1 and resumed.state["balance"] == 2
    release.set()
    worker.join(1)
    assert e.status == "stale"


def test_durable_receipt_cannot_be_erased_by_conflicting_reconciliation(tmp_path):
    class Crash(BaseException):
        pass

    class CrashOnce(SQLiteJournal):
        crashed = False

        def save(self, run_id, snapshot, expected_revision, event):
            revision = super().save(run_id, snapshot, expected_revision, event)
            if event["kind"] == "effect_completed" and not self.crashed:
                self.crashed = True
                raise Crash()
            return revision

    p = program()
    bank = Bank()
    journal = CrashOnce(tmp_path / "runs.db")
    bindings = bank.bindings()
    e = Executor(
        p,
        "G",
        {"approved": True, "balance": 2},
        capabilities=bindings,
        authorizer=lambda _: True,
        journal=journal,
    )
    with pytest.raises(Crash):
        e.step()
    bindings["Pay.charge"] = Capability(
        bank.charge, lambda ctx: Reconciliation("not_committed")
    )
    resumed = Executor.resume(
        p, journal, e.run_id, capabilities=bindings, authorizer=lambda _: True
    )
    assert resumed.reconcile() == "pending"
    assert resumed.failure["code"] == "ConflictingReconciliation"
    assert resumed.history[0]["known_completed"] is True
    resumed._capabilities = bank.bindings()
    assert resumed.reconcile() == "ready"
    assert resumed.run()["status"] == "ended" and len(bank.calls) == 1
