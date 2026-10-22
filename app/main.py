"""Routes FastAPI appelées par le front Lovable (B : Agent).

Les routes et leurs réponses font partie du contrat : le front code contre elles.
Lancer : uv run fastapi dev app/main.py
"""
import logging
import os
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import metrics
from app.agent import MODEL, llm_enabled, run_agent
from app.contract import DATA_MODE, DEFAULT_NIGHT
from app.planner import run_planner

app = FastAPI(title="WattShift")
app.add_middleware(
    CORSMiddleware,
    # Front publié sur Vercel (*.vercel.app) ; Lovable : *.lovable.app, prévisualisations *.lovableproject.com / *.lovable.dev.
    allow_origin_regex=r"https://[a-z0-9.-]+\.(vercel\.app|lovable\.app|lovableproject\.com|lovable\.dev)|http://localhost:\d+",
    # Domaine du front publié (ex. https://wattshift.lovable.app ou un domaine perso), séparés par des virgules.
    allow_origins=[o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)


log = logging.getLogger("uvicorn.error")


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    """Toute erreur inattendue : traceback dans les logs du serveur, type et message (sans secret) au client."""
    log.error("Erreur non gérée sur %s %s", request.method, request.url.path, exc_info=exc)
    msg = re.sub(r"postgres(?:ql)?://\S+", "postgres://<masqué>", f"{type(exc).__name__}: {exc}")
    return JSONResponse(status_code=500, content={"detail": msg[:300]})


class ColdSnap(BaseModel):
    night_date: str = DEFAULT_NIGHT
    temp_c: float = -5.0


class Reset(BaseModel):
    night_date: str = DEFAULT_NIGHT


@app.get("/health")
def health() -> dict:
    """Sert aussi de contrôle avant démo : mode des données et agent branché sur Claude ou non."""
    return {"ok": True, "data_mode": DATA_MODE, "llm": llm_enabled(), "model": MODEL}


@app.get("/plan")
def get_plan(night_date: str = DEFAULT_NIGHT, strategy: str = "agent") -> dict:
    """Plan de la nuit, format Plan du contrat.

    Stratégie « agent » : renvoie le plan enregistré tant que la météo ne change pas de plus de 3°C ;
    sinon (ou s'il n'y en a pas) l'agent en produit un nouveau, ce qui prend une dizaine de secondes.
    """
    if strategy not in ("naive", "agent"):
        raise HTTPException(status_code=422, detail="strategy doit valoir 'naive' ou 'agent'")
    constraints = {"night_date": night_date, "strategy": strategy}
    return run_agent(constraints) if strategy == "agent" else run_planner(constraints)


@app.post("/incident/cold-snap")
def cold_snap(body: ColdSnap) -> dict:
    """Bouton « Vague de froid » : renvoie {naive, agent, comparison}.

    Les deux plans sont faits et jugés à la même température (`temp_c`) : le plan naïf l'ignore
    pour décider, mais `compare_plans` le juge sur la consommation réelle à cette température.
    """
    naive = run_planner({"night_date": body.night_date, "strategy": "naive", "temp_override_c": body.temp_c})
    agent = run_agent({"night_date": body.night_date, "strategy": "agent", "temp_override_c": body.temp_c})
    return {"naive": naive, "agent": agent, "comparison": metrics.compare_plans(naive, agent)}


@app.post("/reset")
def reset(body: Reset | None = None) -> dict:
    """Remet la nuit normale avant une répétition : replanifie sans incident et enregistre ce plan.

    Rapide (repli sans LLM, aucun appel API) : à lancer avant chaque démo.
    """
    night = (body or Reset()).night_date
    return run_agent({"night_date": night, "strategy": "agent"}, use_llm=False, force=True)


@app.get("/metrics/mape")
def mape() -> list:
    return metrics.mape_history()


# Front Tunnel (build Vite, dossier static/) servi par le même service : même origine, aucun CORS à régler.
# Monté en dernier : les routes de l'API ci-dessus restent prioritaires.
_FRONT = Path(__file__).resolve().parent.parent / "static"
if _FRONT.is_dir():
    app.mount("/", StaticFiles(directory=_FRONT, html=True), name="front")
