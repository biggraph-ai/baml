from copy import deepcopy

import pytest

from panini_compiler.compiler import compile_source
from panini_compiler.runtime import Executor, RuntimeFailure


SOURCE = """
class S { int count; bool approved; string text; }
class T { public static S step(S s); }
graph G(s:S) {
 invariant s.count <= 1;
 node a using T.step max_visits 2;
 start -> a;
 a -> when s.count == 0 => a otherwise => end;
}
"""
MODEL = {
    "graph": "G",
    "domains": {"count": {"min": 0, "max": 2}, "approved": "bool"},
    "initial": [{"count": 0, "approved": False}],
    "summaries": {
        "T.step": {
            "cases": [
                {"when": {"count": 0}, "outputs": [{"count": 1}]},
                {"when": {"count": 1}, "outputs": [{"count": 1}]},
            ]
        }
    },
}


def test_safe_finite_model_and_runtime_summary_contract():
    result = compile_source(SOURCE, model=MODEL)
    assert not result.diagnostics
    report = result.analysis
    assert report["status"] == "proved" and report["statesExplored"] > 0
    assert {"end", "error"} <= set(report["outcomes"])
    e = Executor(
        result,
        "G",
        {"count": 0, "approved": False, "text": "opaque"},
        capabilities={"T.step": lambda s: {**s, "count": 1}},
    )
    assert e.run()["status"] == "ended"
    # A lying summary cannot silently authorize a different actual state change.
    e = Executor(
        result,
        "G",
        {"count": 0, "approved": False, "text": "opaque"},
        capabilities={"T.step": lambda s: {**s, "approved": True, "count": 1}},
    )
    assert e.run()["failure"]["code"] == "ModelContractViolation"
    with pytest.raises(RuntimeFailure, match="initial"):
        Executor(result, "G", {"count": 1, "approved": False, "text": "other"})


def test_counterexample_is_replayable_as_rejected_candidate_commit():
    model = deepcopy(MODEL)
    model["summaries"]["T.step"]["cases"][0]["outputs"] = [{"count": 2}]
    result = compile_source(SOURCE, model=model)
    assert result.analysis["status"] == "counterexample"
    assert result.diagnostics[-1].code == "E2701"
    trace = result.analysis["counterexample"]
    assert trace[0]["event"] == "entry" and trace[-1]["event"] == "candidate_commit"
    assert trace[-1]["state"]["count"] == 2
    # Replay against runtime guards, which reject rather than commit this value.
    runtime_program = compile_source(SOURCE)
    e = Executor(
        runtime_program,
        "G",
        {"count": 0, "approved": False, "text": "x"},
        capabilities={"T.step": lambda s: {**s, "count": trace[-1]["state"]["count"]}},
    )
    assert e.run()["failure"]["code"] == "InvariantViolation"
    assert e.state["count"] == 0


def test_missing_summary_and_limits_are_unknown_never_proved():
    model = deepcopy(MODEL)
    model["summaries"] = {}
    r = compile_source(SOURCE, model=model)
    assert r.analysis["status"] == "unknown" and r.diagnostics[-1].code == "E2702"
    first = compile_source(SOURCE, model=MODEL, max_states=1).analysis
    second = compile_source(SOURCE, model=MODEL, max_states=1).analysis
    assert first == second and first["status"] == "unknown"
    model = deepcopy(MODEL)
    model["maxTransitions"] = 1
    assert compile_source(SOURCE, model=model).analysis["status"] == "unknown"


def test_opaque_predicates_and_liveness_claims_are_unsupported():
    source = SOURCE.replace("s.count <= 1", 's.text == "safe"')
    result = compile_source(source, model=MODEL)
    assert (
        result.analysis["status"] == "unsupported"
        and result.diagnostics[-1].code == "E2703"
    )
    model = deepcopy(MODEL)
    model["property"] = "eventual-success"
    assert compile_source(SOURCE, model=model).analysis["status"] == "unsupported"


def test_havoc_is_an_explicit_sound_overapproximation():
    model = deepcopy(MODEL)
    model["summaries"] = {"T.step": {"havoc": True}}
    assert compile_source(SOURCE, model=model).analysis["status"] == "counterexample"
    source = SOURCE.replace("s.count <= 1", "s.count <= 2")
    assert compile_source(source, model=model).analysis["status"] == "proved"


def test_runtime_invariants_and_ordered_policy_rules():
    p = compile_source(
        SOURCE
        + ' policy P for G { rule Stop: s.approved => end; rule Reject: !s.approved => throw "approval required"; }'
    )
    assert not p.diagnostics
    with pytest.raises(RuntimeFailure) as failure:
        Executor(p, "G", {"count": 2, "approved": True, "text": "x"})
    assert failure.value.code == "InvariantViolation"
    e = Executor(p, "G", {"count": 0, "approved": True, "text": "x"})
    assert e.run()["status"] == "ended" and e.dispatches == 0
    e = Executor(p, "G", {"count": 0, "approved": False, "text": "x"})
    assert e.run()["failure"]["code"] == "PolicyViolation" and e.dispatches == 0


def test_all_decision_outcomes_and_abstention_are_modeled():
    source = """enum E { yes, no } class S { string text; Decision<E> result; bool safe; }
    agent A { public Decision<E> run(S s) { decision_model Mock; decide s.text over { yes:"Y"; no:"N"; }; } }
    class T { public static S approve(S s); public static S review(S s); }
    graph G(s:S) { invariant s.safe;
    node decide using A.run writes s.result; node yes using T.approve; node no using T.review;
    start -> decide;
    decide -> when !s.result.abstained && s.result.choice == E.yes => yes otherwise => no;
    yes -> end; no -> end; }"""
    model = {
        "graph": "G",
        "domains": {"result": "decision", "safe": "bool"},
        "initial": [{"result": {"choice": None, "abstained": True}, "safe": True}],
        "summaries": {
            "T.approve": {"cases": [{"outputs": [{}]}]},
            "T.review": {"cases": [{"outputs": [{"safe": False}]}]},
        },
    }
    r = compile_source(
        source,
        adapters={"Mock": {"output": "probabilities", "version": "v1"}},
        model=model,
    )
    assert r.analysis["status"] == "counterexample"
    assert r.analysis["counterexample"][-1]["state"]["safe"] is False


def test_recovery_paths_and_exceptional_outcomes_are_included():
    from test_effects_runtime import SOURCE as effects_source, EFFECTS

    model = {
        "graph": "G",
        "domains": {"balance": {"min": 0, "max": 2}, "approved": "bool"},
        "initial": [{"balance": 2, "approved": True}],
        "summaries": {
            "Pay.charge": {"cases": [{"outputs": [{"balance": 1}]}]},
            "Pay.done": {"cases": [{"outputs": [{}]}]},
            "Pay.undo": {"cases": [{"outputs": [{"balance": 2}]}]},
        },
    }
    r = compile_source(effects_source, effects=EFFECTS, model=model)
    assert not r.diagnostics, r.analysis
    assert r.analysis["status"] == "proved"
    assert {
        "mutation_unknown",
        "compensation_unknown_or_denied",
        "recovery_finished",
        "authorization_denied",
    } <= set(r.analysis["outcomes"])
    source = effects_source.replace("s.balance >= 0", "s.balance <= 2")
    bad = deepcopy(model)
    bad["domains"]["balance"]["max"] = 3
    bad["summaries"]["Pay.undo"]["cases"][0]["outputs"] = [{"balance": 3}]
    r = compile_source(source, effects=EFFECTS, model=bad)
    assert r.analysis["status"] == "counterexample"
    assert r.analysis["counterexample"][-1]["event"] == "candidate_compensation"


def test_unreachable_rule_warnings_are_scoped_to_the_verified_initial_contract():
    source = (
        SOURCE
        + " policy P for G { rule Stop: true => end; rule Shadowed: s.approved => end; }"
    )
    result = compile_source(source, model=MODEL)
    assert not result.diagnostics
    assert result.analysis["status"] == "proved"
    assert any(
        w["code"] == "W2602" and "Shadowed" in w["message"]
        for w in result.analysis["warnings"]
    )


def test_initial_counterexample_and_bad_summary_configuration():
    model = deepcopy(MODEL)
    model["initial"] = [{"count": 2, "approved": False}]
    result = compile_source(SOURCE, model=model)
    assert (
        result.analysis["status"] == "counterexample"
        and len(result.analysis["counterexample"]) == 1
    )
    for mutate in [
        lambda m: m["summaries"]["T.step"].update(cases=[]),
        lambda m: m["summaries"]["T.step"]["cases"][0].update(
            outputs=[{"text": "opaque"}]
        ),
        lambda m: m["initial"][0].update(count=float("nan")),
    ]:
        model = deepcopy(MODEL)
        mutate(model)
        report = compile_source(SOURCE, model=model).analysis
        assert report["status"] in {"unknown", "unsupported"}


def test_cli_reports_proofs_and_nonproofs_and_never_bypasses_strict_analysis(
    tmp_path, capsys
):
    import json
    from panini_compiler.cli import main

    source = tmp_path / "model.panini"
    source.write_text(SOURCE)
    config = tmp_path / "model.json"
    config.write_text(json.dumps(MODEL))
    assert main(["check", str(source), "--model", str(config)]) == 0
    assert json.loads(capsys.readouterr().out)["analysis"]["status"] == "proved"
    for emit in ["ast", "cst", "semantic-ast", "pnir"]:
        assert (
            main(
                [
                    "check",
                    str(source),
                    "--model",
                    str(config),
                    "--max-states",
                    "1",
                    "--emit",
                    emit,
                    "--diagnostics",
                    "json",
                ]
            )
            == 1
        )
        output = capsys.readouterr()
        assert json.loads(output.out)["status"] == "unknown"
        assert json.loads(output.err)[-1]["code"] == "E2702"
    bad = deepcopy(MODEL)
    bad["summaries"]["T.step"]["cases"][0]["outputs"] = [{"count": 2}]
    config.write_text(json.dumps(bad))
    assert (
        main(["check", str(source), "--model", str(config), "--diagnostics", "json"])
        == 1
    )
    output = capsys.readouterr()
    assert json.loads(output.out)["status"] == "counterexample"
    assert json.loads(output.err)[-1]["code"] == "E2701"


def test_policy_and_effect_configuration_errors_are_diagnostics(tmp_path, capsys):
    import json
    from panini_compiler.cli import main

    for policy in [
        "policy P { invariant true; }",
        "policy P for Missing { invariant true; }",
        "policy P for G { rule Bad: true => step(s); }",
    ]:
        assert compile_source(SOURCE + policy).diagnostics
    source = tmp_path / "test.panini"
    source.write_text(SOURCE)
    config = tmp_path / "bad.json"
    config.write_text("[]")
    for option, code in [("--effects", "E2401"), ("--model", "E2703")]:
        assert (
            main(["check", str(source), option, str(config), "--diagnostics", "json"])
            == 1
        )
        output = capsys.readouterr()
        assert json.loads(output.err)[0]["code"] == code


def test_human_scale_timeouts_and_overflow_are_validated():
    assert not compile_source(
        SOURCE.replace("max_visits 2", "max_visits 2 timeout hours(48)")
    ).diagnostics
    large = "9" * 400 + ".0"
    result = compile_source(
        SOURCE.replace("max_visits 2", f"max_visits 2 timeout hours({large})")
    )
    assert "E2004" in {d.code for d in result.diagnostics}
