# Démo en 3 minutes

Tous les chiffres ci-dessous sont ceux de `scripts/smoke_agent.py` sur les données actuelles. Ils sont calculés en direct : annoncez ce que l'écran affiche, pas cette page.

## Avant de monter sur scène (5 min)

1. `POST /reset` : remet la nuit normale (instantané, sans LLM). À refaire avant chaque répétition.
2. `uv run python scripts/smoke_agent.py --url <backend> --require-llm` : tout doit être OK. Sinon, voir « Plan B ».
3. `GET /health` doit afficher `"llm": true`. Si `false`, la clé Anthropic n'est pas lue sur le serveur.
4. Premier clic « Vague de froid » fait une fois en coulisses : plus rien de froid ensuite, mais refaire `POST /reset`.

## Déroulé

| Temps | Écran | Ce que vous dites |
|---|---|---|
| 0:00 | Titre | « Chaque soir, un responsable de dépôt de bus électriques décide quel bus part sur quel service et quand le recharger. À la main, 30 minutes, avec de grandes marges de sécurité. » |
| 0:20 | Plan de la nuit (agent) | « Voici la nuit de demain à Dijon : 5,4°C, 30 services, 40 bus dont 10 de réserve. Tous les services sont couverts pour environ 266 € de recharge. » |
| 0:40 | Plan naïf à côté | « Un plan simple, qui suppose 15°C partout, donne la même chose par temps doux. » |
| 1:00 | **Clic « Vague de froid -5°C »** | « Imprévu : une vague de froid. L'agent le détecte, et c'est lui qui décide quoi faire. » |
| 1:05 | Timeline `agent_trace` (≈ 15 s) | Lisez les étapes à mesure : « Il rafraîchit la météo, constate que 29 ou 30 services du plan actuel ne tiennent plus, relance le planificateur, vérifie la puissance du dépôt, retient le meilleur plan. » |
| 1:30 | Explication de l'agent | Lisez 2 phrases : quels bus changent, recharge avancée, surcoût. |
| 1:50 | Comparaison naïf contre agent | « Jugés sur la consommation réelle à -5°C : le plan naïf laisse **30 services** en panne en ligne, l'agent **0**. » |
| 2:20 | Courbe de MAPE | « Le modèle apprend de chaque nuit : l'erreur de prévision passe d'environ 19 % à 3 à 5 % en trois nuits. » |
| 2:40 | Roadmap | Télémétrie réelle d'un opérateur, pannes de chargeurs, solveur d'optimisation, prix spot. |

## Les points à ne pas rater

- **Ce n'est pas un script** : l'agent appelle des outils (météo, réévaluation, planificateur, vérification) et décide de relancer ou non. Le LLM n'invente aucun chiffre, tout vient du code.
- **Honnêteté** : si l'agent laisse un service non couvert, il le dit. C'est un atout, ne le cachez pas.
- **Les données** : météo réelle de Dijon, horaires réels Divia (GTFS), flotte et consommation simulées. Dites-le.

## Plan B (par ordre de préférence)

| Problème | Action |
|---|---|
| API Claude lente ou en panne | Rien à faire : l'agent bascule seul sur le repli (même parcours, sans LLM, instantané). Le champ `fallback: true` le signale. À présenter comme une garantie : « s'il n'y a plus d'IA, le dépôt a quand même un plan ». |
| Backend déployé injoignable | Lancer en local : `uv run fastapi dev app/main.py`, pointer le front dessus (`VITE_API_URL`). |
| Front Lovable cassé | Montrer la réponse JSON de `POST /incident/cold-snap` (ou la vidéo de secours). |
| Plus rien ne marche | Vidéo de secours enregistrée pendant la répétition. |

Chiffres de référence (nuit normale 5,4°C, vague de froid -5°C, 40 bus) : naïf 30 services en échec réel, agent 0. Latence de l'incident : 14 à 18 s avec Claude.
