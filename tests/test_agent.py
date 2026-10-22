"""Tests de la boucle agent (B). Faux client Claude : aucun réseau, aucune clé."""
from types import SimpleNamespace

import pytest

from app import agent, contract, data, planner
from app.planner import diff_plans, reassess_plan, run_planner

NIGHT = contract.DEFAULT_NIGHT
COLD = {"night_date": NIGHT, "strategy": "agent", "temp_override_c": -5.0}


# --- faux client ----------------------------------------------------------------

def tool(name, **inp):
    return SimpleNamespace(type="tool_use", id=f"tu_{name}_{len(str(inp))}", name=name, input=inp)


def text(t):
    return SimpleNamespace(type="text", text=t)


def reply(*blocks, stop="tool_use"):
    return SimpleNamespace(stop_reason=stop, content=list(blocks))


class FakeClient:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})  # instantané : la liste est mutée ensuite
        if not self.replies:
            raise AssertionError("le faux client n'a plus de réponse scriptée")
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def saved(monkeypatch):
    out = []
    monkeypatch.setattr(data, "save_plan", lambda plan, explanation: out.append((plan, explanation)))
    return out


def happy_script():
    return FakeClient(
        reply(tool("get_forecast"), tool("assess_current_plan"), tool("run_planner")),
        reply(tool("check_plan", plan_id="p1")),
        reply(tool("finalize_plan", plan_id="p1")),
        reply(text("Le froid rend plusieurs services infaisables. Le plan est refait."), stop="end_turn"),
    )


# --- boucle LLM ----------------------------------------------------------------

def test_llm_happy_path_uses_the_models_explanation_and_traces_steps(saved):
    client = happy_script()
    plan = agent.run_agent(COLD, client=client)
    assert plan["fallback"] is False
    assert plan["explanation"] == "Le froid rend plusieurs services infaisables. Le plan est refait."
    assert [s["tool"] for s in plan["agent_trace"]] == [
        "get_forecast", "assess_current_plan", "run_planner", "check_plan", "finalize_plan",
    ]
    assert all(s["summary"] and s["seconds"] >= 0 for s in plan["agent_trace"])
    assert plan["plan_id"] == f"{NIGHT}-agent" and plan["strategy"] == "agent"
    assert saved and saved[0][1] == plan["explanation"]
    # modèle demandé, pas de thinking forcé ni de tool_choice forcé (400 sur Sonnet 5.5)
    kw = client.calls[0]
    assert kw["model"] == "claude-sonnet-5-5"
    assert "tool_choice" not in kw and "temperature" not in kw


def test_tool_results_are_returned_in_a_single_user_message(saved):
    client = happy_script()
    agent.run_agent(COLD, client=client)
    second = client.calls[1]["messages"]
    results = second[-1]["content"]
    assert second[-1]["role"] == "user"
    assert [r["type"] for r in results] == ["tool_result"] * 3


def test_llm_can_harden_and_relaunch(saved):
    client = FakeClient(
        reply(tool("run_planner")),
        reply(tool("run_planner", advance_charging=True, margin_pct=20)),
        reply(tool("check_plan", plan_id="p2"), tool("finalize_plan", plan_id="p2")),
        reply(text("Recharge avancée."), stop="end_turn"),
    )
    plan = agent.run_agent(COLD, client=client)
    assert plan["fallback"] is False
    assert [s["tool"] for s in plan["agent_trace"]].count("run_planner") == 2


def test_tool_inputs_are_clamped_and_unknown_buses_dropped(saved, monkeypatch):
    seen = []
    real = planner.run_planner
    monkeypatch.setattr(planner, "run_planner", lambda c: seen.append(dict(c)) or real(c))
    client = FakeClient(
        reply(tool("run_planner", margin_pct=99, exclude_buses=["B01", "ZZZ"], bogus=1)),
        reply(tool("finalize_plan", plan_id="p1")),
        reply(text("ok"), stop="end_turn"),
    )
    agent.run_agent(COLD, client=client)
    assert seen[0]["margin_pct"] == 30.0
    assert seen[0]["exclude_buses"] == ["B01"]
    assert "bogus" not in seen[0]


def test_unknown_plan_handle_is_a_tool_error_not_a_crash(saved):
    client = FakeClient(
        reply(tool("check_plan", plan_id="p9")),
        reply(tool("run_planner")),
        reply(tool("finalize_plan", plan_id="p1")),
        reply(text("ok"), stop="end_turn"),
    )
    plan = agent.run_agent(COLD, client=client)
    first_results = client.calls[1]["messages"][-1]["content"]
    assert first_results[0]["is_error"] is True
    assert plan["fallback"] is False


# --- repli ---------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    RuntimeError("api down"),
    reply(text("Je ne peux pas."), stop="refusal"),
    reply(text("tron"), stop="max_tokens"),
    reply(text("Rien à faire."), stop="end_turn"),  # termine sans avoir planifié
])
def test_any_llm_failure_falls_back_to_deterministic_plan(saved, bad):
    plan = agent.run_agent(COLD, client=FakeClient(bad))
    assert plan["fallback"] is True
    assert plan["explanation"]
    assert plan["agent_trace"][-1]["tool"] == "finalize_plan"
    assert any(s["tool"] == "fallback" for s in plan["agent_trace"])
    assert saved


def test_endless_tool_calls_stop_after_max_rounds(saved):
    client = FakeClient(*[reply(tool("get_forecast")) for _ in range(agent.MAX_TOOL_ROUNDS + 3)])
    plan = agent.run_agent(COLD, client=client)
    assert plan["fallback"] is True
    assert len(client.calls) <= agent.MAX_TOOL_ROUNDS + 1


def test_llm_off_uses_fallback_without_client(saved):
    plan = agent.run_agent(COLD)  # AGENT_LLM=off (conftest.py)
    assert plan["fallback"] is True
    assert plan["kpis"]["services_uncovered"] == len(plan["uncovered_services"])


def test_fallback_hardens_until_the_plan_checks_out(saved):
    plan = agent.run_agent(COLD)
    tools = [s["tool"] for s in plan["agent_trace"]]
    assert tools[:3] == ["get_forecast", "assess_current_plan", "run_planner"]
    cold = run_planner({**COLD, "strategy": "agent"})
    assert plan["kpis"]["services_uncovered"] <= cold["kpis"]["services_uncovered"]


def test_naive_strategy_bypasses_the_agent(saved):
    plan = agent.run_agent({"night_date": NIGHT, "strategy": "naive"})
    assert plan["strategy"] == "naive" and "agent_trace" not in plan
    assert not saved


def test_replan_seconds_covers_the_whole_run(saved):
    plan = agent.run_agent(COLD)
    assert plan["kpis"]["replan_seconds"] >= sum(s["seconds"] for s in plan["agent_trace"]) - 0.01


# --- détection et diff ---------------------------------------------------------

def test_reassess_finds_services_that_no_longer_hold_in_the_cold():
    base = run_planner({"night_date": NIGHT, "strategy": "agent", "temp_override_c": 3.0})
    same = reassess_plan(base, 3.0)
    cold = reassess_plan(base, -5.0)
    assert same["infeasible"] == []
    assert cold["infeasible"], "le froid doit casser au moins un service du plan doux"
    assert {"service_id", "bus_id", "missing_kwh"} <= cold["infeasible"][0].keys()


def test_diff_plans_is_computed_from_the_plans():
    old = run_planner({"night_date": NIGHT, "strategy": "agent", "temp_override_c": 3.0})
    new = run_planner({**COLD, "advance_charging": True})
    d = diff_plans(old, new)
    assert d["cost_delta_eur"] == pytest.approx(new["kpis"]["charging_cost_eur"] - old["kpis"]["charging_cost_eur"], abs=0.11)
    assert d["uncovered_after"] == new["uncovered_services"]
    for r in d["reassigned"]:
        assert r["from_bus"] != r["to_bus"]
    for a in d["charging_advanced"]:
        assert a["hours_earlier"] >= 0.5
