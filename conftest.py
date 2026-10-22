"""Configuration pytest globale : aucun test ne doit appeler l'API Claude par accident.

L'agent lit AGENT_LLM : « off » force le repli sans LLM (utile aussi pour répéter la démo hors ligne).
DATA_MODE est forcé à « fake » : un .env en mode aiven ne doit pas faire écrire les tests dans la base partagée.
Les tests de la boucle LLM injectent leur propre faux client.
"""
import os

os.environ["AGENT_LLM"] = "off"
# Les tests n'écrivent jamais dans la vraie base Aiven partagée, même si le .env local est en mode aiven.
os.environ["DATA_MODE"] = "fake"


import pytest


@pytest.fixture(autouse=True)
def _fresh_fake_plans():
    """Les plans « enregistrés » du mode fake vivent en mémoire : on repart de zéro à chaque test."""
    from app import agent, data

    getattr(data, "_fake_plans", []).clear()
    agent._refresh_cache.clear()  # cache météo de l'agent : chaque test part sans
    yield
    getattr(data, "_fake_plans", []).clear()
    agent._refresh_cache.clear()
