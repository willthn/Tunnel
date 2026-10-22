"""Métriques naïf vs agent (C : Data 2). VERSION FACTICE, à remplacer par C."""
from app.contract import Comparison, MapePoint


def compare_plans(naive: dict, agent: dict) -> Comparison:
    """KPIs du README section 11, côte à côte."""
    keys = ("services_uncovered", "charging_cost_eur", "replan_seconds")
    return {k: {"naive": naive["kpis"][k], "agent": agent["kpis"][k]} for k in keys}  # type: ignore[return-value]


def mape_history() -> list[MapePoint]:
    """Une MAPE par nuit rejouée, dans l'ordre chronologique."""
    return [
        {"night_date": "2026-01-12", "mape": 18.5},
        {"night_date": "2026-01-13", "mape": 11.2},
        {"night_date": "2026-01-14", "mape": 6.4},
    ]
