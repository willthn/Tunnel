"""Configuration pytest globale : aucun test ne doit appeler l'API Claude par accident.

L'agent lit AGENT_LLM : « off » force le repli sans LLM (utile aussi pour répéter la démo hors ligne).
Les tests de la boucle LLM injectent leur propre faux client.
"""
import os

os.environ["AGENT_LLM"] = "off"


import pytest


@pytest.fixture(autouse=True)
def _fresh_fake_plans():
    """Les plans « enregistrés » du mode fake vivent en mémoire : on repart de zéro à chaque test."""
    from app import data

    getattr(data, "_fake_plans", []).clear()
    yield
    getattr(data, "_fake_plans", []).clear()
