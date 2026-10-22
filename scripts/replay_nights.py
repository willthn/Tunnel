"""Rejoue les nuits d'apprentissage : le modèle repart de l'a priori et apprend nuit après nuit.

Usage : uv run python scripts/replay_nights.py   (DATA_MODE=aiven pour écrire les runs dans Aiven)
Chaque nuit enregistre un run (data.save_model_run) : c'est la courbe d'erreur de prévision.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import model, truth  # noqa: E402

t = truth.load_truth()
print("nuit        MAPE    a (kWh/km/°C)")
for night in sorted({o["night_date"] for o in model._observations()}):
    r = model.record_outcome(night)
    print(f"{night}  {r['mape']:5.1f}%  {r['coef']['a']:.4f}  (vérité {t['a_cold']})")
