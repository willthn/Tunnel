# Déploiement du backend

Le backend est une application FastAPI gérée par `uv` (Python 3.12 ou plus). Rien d'autre à installer.

## Commandes

| Étape | Commande |
|---|---|
| Installer | `uv sync --frozen` |
| Lancer | `uv run fastapi run app/main.py --host 0.0.0.0 --port ${PORT:-8000}` |
| Vérifier | `curl <url>/health` |
| Test de fumée | `uv run python scripts/smoke_agent.py --url <url> --require-llm` |

Un seul processus (le défaut de `fastapi run`) : en `DATA_MODE=fake`, le plan enregistré vit en mémoire, plusieurs workers auraient chacun le leur. Après chaque redémarrage du serveur, refaire `POST /reset`.

## Variables d'environnement (à saisir dans l'hébergeur, jamais dans Git)

| Variable | Valeur | Obligatoire |
|---|---|---|
| `DATA_MODE` | `fake` (par défaut, fonctionne sans base) ou `aiven` | non |
| `PG_URI` | URI Aiven PostgreSQL | si `aiven` |
| `ANTHROPIC_API_KEY` | clé Anthropic | pour l'agent LLM (sinon repli sans LLM) |
| `ANTHROPIC_WORKSPACE_ID` | `wrkspc_...` | si la clé n'est pas rattachée à un workspace |
| `AGENT_LLM` | `off` pour forcer le repli sans LLM | non |
| `CORS_ORIGINS` | domaine du front publié, séparés par des virgules | si le front n'est pas en `*.lovable.app` |

`*.lovable.app`, `*.lovableproject.com`, `*.lovable.dev` et `localhost` sont déjà autorisés.

## À savoir

- **Délai d'attente** : `POST /incident/cold-snap` dure 14 à 18 s avec Claude. Le proxy de l'hébergeur doit accepter au moins 60 s.
- **Le front** appelle `GET /plan`, `POST /incident/cold-snap`, `POST /reset`, `GET /metrics/mape` (voir README, section 9).
- **`/health`** indique `data_mode` et `llm` : à regarder en premier si l'agent semble « bête » (c'est le repli).
- **Repli d'hébergement** : si Aiven Runtime bloque plus de 15 min, passer sur Modal ou lancer en local (voir README, section Ressources).
