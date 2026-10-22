# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy"]
# ///
"""Rejoue les nuits d'apprentissage : le modèle repart de l'a priori et apprend nuit après nuit.

Usage : uv run scripts/replay_nights.py
Écrit data/model_state.json et data/model_runs.csv (la courbe d'erreur de prévision).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import model, truth  # noqa: E402

model.reset()
nights = list(truth.load_truth()["history_nights"])
print("nuit        MAPE    a (kWh/km/°C)")
for n in nights:
    r = model.record_outcome(n)
    print(f"{n}  {r['mape']:5.1f}%  {r['coef']['a']:.4f}  (vérité {truth.load_truth()['a_cold']})")
