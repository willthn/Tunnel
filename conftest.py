"""Configuration pytest globale : aucun test ne doit appeler l'API Claude par accident.

L'agent lit AGENT_LLM : « off » force le repli sans LLM (utile aussi pour répéter la démo hors ligne).
Les tests de la boucle LLM injectent leur propre faux client.
"""
import os

os.environ["AGENT_LLM"] = "off"
