"""Routes FastAPI appelées par le front Lovable (B : Agent).

Les routes et leurs réponses font partie du contrat : le front code contre elles.
Lancer : uv run fastapi dev app/main.py
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app import metrics
from app.agent import run_agent
from app.contract import DATA_MODE, DEFAULT_NIGHT
from app.planner import run_planner

app = FastAPI(title="WattShift")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https://.*\.lovable\.app|http://localhost:\d+",
    allow_methods=["*"],
    allow_headers=["*"],
)


class ColdSnap(BaseModel):
    night_date: str = DEFAULT_NIGHT
    temp_c: float = -5.0


@app.get("/health")
def health() -> dict:
    return {"ok": True, "data_mode": DATA_MODE}


@app.get("/plan")
def get_plan(night_date: str = DEFAULT_NIGHT, strategy: str = "agent") -> dict:
    """Plan de la nuit, format Plan du contrat."""
    constraints = {"night_date": night_date, "strategy": strategy}
    return run_agent(constraints) if strategy == "agent" else run_planner(constraints)


@app.post("/incident/cold-snap")
def cold_snap(body: ColdSnap) -> dict:
    """Bouton « Vague de froid » : renvoie {naive, agent, comparison}."""
    naive = run_planner({"night_date": body.night_date, "strategy": "naive"})
    agent = run_agent(
        {"night_date": body.night_date, "strategy": "agent", "temp_override_c": body.temp_c}
    )
    return {"naive": naive, "agent": agent, "comparison": metrics.compare_plans(naive, agent)}


@app.get("/metrics/mape")
def mape() -> list:
    return metrics.mape_history()
