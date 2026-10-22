"""Boucle agent Claude (B : Agent). VERSION FACTICE, à remplacer par B.

Pas d'appel à Claude ici : relance le planificateur et colle une explication fixe.
"""
from app import data
from app.contract import Constraints, Plan
from app.planner import check_plan, run_planner


def run_agent(constraints: Constraints) -> Plan:
    """Produit, vérifie, explique et enregistre le plan de la nuit."""
    plan = run_planner(constraints)
    check = check_plan(plan)
    plan["explanation"] = (
        "Plan factice : "
        + (", ".join(check["issues"]) if check["issues"] else "tous les services sont couverts.")
    )
    data.save_plan(plan, plan["explanation"])
    return plan
