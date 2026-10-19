import json

import pytest

from panini_compiler.compiler import compile_source
from panini_compiler.runtime import Executor, RuntimeFailure


RETRY = """
class S { bool retry; int count; }
class T { public static S init(S s); public static S attempt(S s); }
graph G(s: S) {
 node init using T.init;
 node retry using T.attempt max_visits 3;
 start -> init;
 init -> retry;
 retry -> when s.retry => retry otherwise => end;
}
"""


@pytest.fixture(scope="module")
def retry_program():
    result = compile_source(RETRY)
    assert not result.diagnostics
    return result


def attempt(s):
    s["count"] += 1
    return s


BINDINGS = {"T.init": lambda s: s, "T.attempt": attempt}


def test_budget_exhaustion_is_failure_not_success(retry_program):
    executor = Executor(
        retry_program, "G", {"retry": True, "count": 0}, capabilities=BINDINGS
    )
    result = executor.run()
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "BudgetExceeded"
    assert result["dispatches"] == 4
    assert result["state"]["count"] == 3
    assert executor.step() == "failed"
    assert executor.dispatches == 4


def test_real_success_and_mutation_cannot_reset_counters(retry_program):
    def done(s):
        s["count"] += 1
        s["retry"] = s["count"] < 2
        return s

    executor = Executor(
        retry_program,
        "G",
        {"retry": True, "count": 0},
        capabilities={**BINDINGS, "T.attempt": done},
    )
    assert executor.run()["status"] == "ended"
    assert executor.dispatches == 3
    # Resetting a workflow's own count does not replenish runtime dispatch budgets.
    executor = Executor(
        retry_program,
        "G",
        {"retry": True, "count": 0},
        capabilities={**BINDINGS, "T.attempt": lambda s: {"retry": True, "count": 0}},
    )
    assert executor.run()["failure"]["code"] == "BudgetExceeded"
    assert executor.dispatches == 4


def test_resume_preserves_global_budgets(retry_program):
    executor = Executor(
        retry_program, "G", {"retry": True, "count": 0}, capabilities=BINDINGS
    )
    executor.step()
    executor.step()
    checkpoint = json.loads(json.dumps(executor.checkpoint()))
    resumed = Executor.restore(retry_program, checkpoint, capabilities=BINDINGS)
    assert resumed.dispatches == 2
    assert resumed.run()["failure"]["code"] == "BudgetExceeded"
    assert resumed.dispatches == 4
    # A caller editing a returned state/checkpoint does not mutate a live executor.
    checkpoint["counts"].clear()
    copy = resumed.state
    copy["count"] = -100
    assert resumed.state["count"] == 3
    with pytest.raises(RuntimeFailure, match="Checkpoint"):
        Executor.restore(retry_program, checkpoint, capabilities=BINDINGS)
    ended = Executor(
        retry_program, "G", {"retry": False, "count": 0}, capabilities=BINDINGS
    )
    ended.run()
    assert (
        Executor.restore(
            retry_program, ended.checkpoint(), capabilities=BINDINGS
        ).status
        == "ended"
    )


def test_callback_failure_counts_and_does_not_commit_state(retry_program):
    def fail(s):
        s["count"] = 999
        raise RuntimeError("external failure")

    executor = Executor(
        retry_program,
        "G",
        {"retry": True, "count": 0},
        capabilities={**BINDINGS, "T.attempt": fail},
    )
    result = executor.run()
    assert result["failure"]["code"] == "NodeFailed"
    assert result["state"]["count"] == 0
    assert result["dispatches"] == 2
    resumed = Executor.restore(
        retry_program, executor.checkpoint(), capabilities=BINDINGS
    )
    assert resumed.run()["dispatches"] == 2
    assert resumed.status == "failed"


def test_state_validation_and_incompatible_checkpoints(retry_program):
    for state in [
        {"retry": 1, "count": 0},
        {"retry": True, "count": "0"},
        {"retry": True, "count": 0, "counts": {}},
    ]:
        with pytest.raises(RuntimeFailure):
            Executor(retry_program, "G", state, capabilities=BINDINGS)
    executor = Executor(
        retry_program, "G", {"retry": True, "count": 0}, capabilities=BINDINGS
    )
    for mutate in [
        lambda c: c.update(program="wrong"),
        lambda c: c.update(dispatches=99),
        lambda c: c["counts"].update({next(iter(c["counts"])): -1}),
    ]:
        saved = executor.checkpoint()
        mutate(saved)
        with pytest.raises(RuntimeFailure):
            Executor.restore(retry_program, saved, capabilities=BINDINGS)

    def bad_result(s):
        return {"retry": True, "count": "bad"}

    executor = Executor(
        retry_program,
        "G",
        {"retry": True, "count": 0},
        capabilities={**BINDINGS, "T.attempt": bad_result},
    )
    assert executor.run()["failure"]["code"] == "InvalidState"
    assert executor.state["count"] == 0


def test_runtime_does_not_trust_topology_marker_or_ignore_m4_contracts(retry_program):
    forged = retry_program.to_dict()
    forged["graphs"][0]["transitions"].clear()
    with pytest.raises(RuntimeFailure):
        Executor(forged, "G", {"retry": True, "count": 0})
    for contract in ["compensate using T.attempt"]:
        program = compile_source(
            RETRY.replace("max_visits 3", "max_visits 3 " + contract)
        )
        assert not program.diagnostics
        with pytest.raises(RuntimeFailure) as failure:
            Executor(program, "G", {"retry": True, "count": 0})
        assert failure.value.code == "UnsupportedContract"


DECISION_SOURCE = """
enum Intent { approve, review }
class S { string reason; Decision<Intent> result; int count; }
agent Triage {
 public Decision<Intent> classify(S s) {
  decision_model Local;
  decide s.reason over { approve: "Eligible"; review: "Uncertain"; } abstain_below 0.8;
 }
}
class T { public static S approve(S s); public static S review(S s); }
graph G(s:S) {
 node triage using Triage.classify writes s.result max_visits 1;
 node yes using T.approve;
 node no using T.review;
 start -> triage;
 triage -> when s.result.abstained == false && s.result.choice == Intent.approve => yes otherwise => no;
 yes -> end;
 no -> end;
}
"""
MANIFEST = {"Local": {"output": "probabilities", "version": "mock-v1"}}
INITIAL = {
    "reason": "test",
    "count": 0,
    "result": {
        "choice": None,
        "abstained": True,
        "probabilities": {"approve": 0.5, "review": 0.5},
        "model": "initial",
        "modelVersion": "initial",
    },
}


@pytest.fixture(scope="module")
def decision_program():
    program = compile_source(DECISION_SOURCE, adapters=MANIFEST)
    assert not program.diagnostics
    return program


@pytest.mark.parametrize(
    "approve,expected,abstained", [(0.8, 1, False), (0.79, 2, True), (0.1, 2, False)]
)
def test_mocked_decision_routes_and_writes_only_its_field(
    decision_program, approve, expected, abstained
):
    calls = []

    def model(prompt, options):
        calls.append((prompt, [o["name"] for o in options]))
        # Changing this copy cannot mutate compiled options.
        options.clear()
        return {
            "probabilities": {"approve": approve, "review": 1 - approve},
            "modelVersion": "mock-v1",
        }

    def mark(s, n):
        s["count"] = n
        return s

    executor = Executor(
        decision_program,
        "G",
        INITIAL,
        capabilities={
            "T.approve": lambda s: mark(s, 1),
            "T.review": lambda s: mark(s, 2),
        },
        adapters={"Local": model},
    )
    assert executor.step() == "ready"
    assert executor.state["reason"] == "test" and executor.state["count"] == 0
    assert executor.state["result"]["abstained"] is abstained
    if abstained:
        assert executor.state["result"]["choice"] is None
    assert executor.run()["status"] == "ended"
    assert executor.state["count"] == expected
    assert calls == [("test", ["approve", "review"])]
    assert executor.dispatches == 2


def test_malformed_model_result_is_failure_not_fallback(decision_program):
    for response in [
        None,
        {
            "probabilities": {"approve": float("nan"), "review": 0.5},
            "modelVersion": "mock-v1",
        },
        {"probabilities": {"approve": 1, "review": 0}, "modelVersion": "wrong"},
    ]:
        executor = Executor(
            decision_program, "G", INITIAL, adapters={"Local": lambda *_: response}
        )
        assert executor.run()["status"] == "failed"
        assert executor.dispatches == 1
        assert executor.state == INITIAL
    executor = Executor(decision_program, "G", INITIAL)
    assert executor.run()["failure"]["code"] == "MissingAdapter"


def test_callbacks_cannot_dispatch_or_checkpoint_mid_invocation(retry_program):
    for action, code in [
        ("dispatch", "ReentrantDispatch"),
        ("checkpoint", "InvalidCheckpoint"),
    ]:
        executor = None

        def callback(s):
            if action == "dispatch":
                executor.step()
            else:
                executor.checkpoint()
            return s

        executor = Executor(
            retry_program,
            "G",
            {"retry": True, "count": 0},
            capabilities={"T.init": callback},
        )
        assert executor.run()["failure"]["code"] == code
        assert executor.dispatches == 1


def test_numeric_guard_limits_are_checked_before_execution():
    for literal in [str(2**63), "9" * 5000, "9" * 400 + ".0"]:
        source = RETRY.replace("s.retry =>", f"s.count < {literal} =>")
        result = compile_source(source)
        assert "E2003" in {d.code for d in result.diagnostics}
    result = compile_source(RETRY.replace("s.retry =>", f"s.count == {-(2**63)} =>"))
    assert not result.diagnostics
    executor = Executor(
        result, "G", {"retry": False, "count": 0}, capabilities=BINDINGS
    )
    assert executor.run()["status"] == "ended"


def test_unsupported_isolation_backend_is_rejected_before_dispatch(monkeypatch):
    program = compile_source(
        RETRY.replace("max_visits 3", "max_visits 3 timeout seconds(1)")
    )
    assert not program.diagnostics
    monkeypatch.setattr(
        "panini_compiler.runtime.multiprocessing.get_all_start_methods",
        lambda: ["spawn"],
    )
    with pytest.raises(RuntimeFailure) as error:
        Executor(program, "G", {"retry": True, "count": 0}, capabilities=BINDINGS)
    assert error.value.code == "UnsupportedIsolation"
