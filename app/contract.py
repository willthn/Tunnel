"""Contrat partagé entre A, B et C (README section 9).

Ce fichier n'appartient à personne : il ne change qu'après accord des trois.
Les types décrivent les dict échangés entre modules et le JSON renvoyé au front.
"""
import os
from typing import Literal, NotRequired, TypedDict

from dotenv import load_dotenv

load_dotenv()

# "fake" : chaque module renvoie des valeurs plausibles en dur. "aiven" : vraies données.
DATA_MODE: Literal["fake", "aiven"] = os.getenv("DATA_MODE", "fake")  # type: ignore[assignment]

DEFAULT_NIGHT = "2026-01-14"  # nuit douce (5,4°C réel à Dijon), base de la démo
COLD_NIGHT = "2026-01-05"     # vraie nuit froide de Dijon (-6,7°C), backtest et « vague de froid »


# --- Référentiel (app/data.py, A) ---------------------------------------------

class Bus(TypedDict):
    bus_id: str
    battery_kwh: float
    soh: float              # 0.80 à 1.00
    soc_evening: float      # 0 à 1, charge au retour au dépôt
    base_kwh_per_km: float  # consommation de référence à 15°C


class Service(TypedDict):
    service_id: str
    line: str
    start_time: str  # ISO "2026-01-14T04:58:00"
    end_time: str
    distance_km: float


class Charger(TypedDict):
    charger_id: str
    power_kw: float
    status: Literal["ok", "down"]


class Depot(TypedDict):
    max_power_kw: float  # inférieure à la somme des chargeurs (README section 7)


class WeatherPoint(TypedDict):
    ts: str
    temp_c: float


class PricePoint(TypedDict):
    ts: str
    eur_kwh: float
    tranche: Literal["creuse", "pleine", "pointe"]


class ConsumptionObs(TypedDict):
    night_date: str
    bus_id: str
    service_id: str
    line: str
    temp_c: float
    km: float
    kwh: float


class ModelRun(TypedDict):
    run_id: str
    night_date: str
    mape: float
    coef_json: dict


# --- Plan (app/planner.py et app/agent.py, B) -----------------------------------

class Constraints(TypedDict):
    night_date: str
    strategy: Literal["naive", "agent"]
    temp_override_c: NotRequired[float]   # ex. -5.0 pour le bouton « vague de froid »
    margin_pct: NotRequired[float]        # marge de sécurité, 15 par défaut
    advance_charging: NotRequired[bool]   # recharger plus tôt dans la nuit
    charge_start: NotRequired[str]        # "HH:MM" la veille au soir, 21:00 par défaut (19:00 si advance_charging)
    exclude_buses: NotRequired[list[str]] # bus à ne pas utiliser (panne, maintenance)
    use_reserve: NotRequired[bool]        # autoriser les bus de réserve B31+ (vrai par défaut)


class Assignment(TypedDict):
    bus_id: str
    service_id: str
    predicted_kwh: float
    available_kwh: float
    margin_pct: float


class Charging(TypedDict):
    bus_id: str
    charger_id: str
    start: str
    end: str
    kwh: float
    cost_eur: float


class Kpis(TypedDict):
    services_uncovered: int
    charging_cost_eur: float
    replan_seconds: float


class Plan(TypedDict):
    plan_id: str
    night_date: str
    strategy: Literal["naive", "agent"]
    forecast_min_temp_c: float
    assignments: list[Assignment]
    charging: list[Charging]
    uncovered_services: list[str]
    kpis: Kpis
    explanation: str


class PlanCheck(TypedDict):
    ok: bool
    uncovered_services: list[str]
    low_margin_buses: list[str]   # marge sous le seuil demandé
    peak_power_kw: float          # pic de puissance du dépôt dans le plan
    max_power_kw: float           # limite du dépôt
    issues: list[str]             # messages lisibles, réutilisables par l'agent


# --- Métriques (app/metrics.py, C) ---------------------------------------------

class PlanEvaluation(TypedDict):
    strategy: str
    services_uncovered_planned: int   # ce que le plan annonce
    services_uncovered_real: int      # jugé sur la consommation réelle simulée
    services_failing_en_route: list   # [{service_id, bus_id, missing_kwh}]
    charging_cost_eur: float
    replan_seconds: float | None


class Comparison(TypedDict):
    temp_c: float
    naive: PlanEvaluation
    agent: PlanEvaluation
    delta: dict                       # {services_uncovered_real, charging_cost_eur}
    mape_history: list                # [MapePoint]


class MapePoint(TypedDict):
    night_date: str
    mape: float
