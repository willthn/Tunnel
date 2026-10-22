# Tunnel (nom provisoire)

Agent de planification nocturne pour dépôts de bus électriques.

> Nous aidons les responsables de dépôts de bus électriques à décider chaque nuit quel bus part sur quel service et quand le recharger, en tenant compte de la météo, du prix de l'électricité et de l'état réel de chaque batterie. Résultat : plus aucun service abandonné par grand froid, une facture de recharge réduite, et un plan refait en quelques secondes au lieu de 30 minutes à la main.

---

## 1. Problème

| Constat | Détail |
|---|---|
| Les flottes de bus s'électrifient | Les opérateurs passent progressivement en électrique, avec des dépôts mixtes thermique et électrique |
| L'affectation devient un casse-tête | Chaque bus a une autonomie différente (état de batterie, charge restante), chaque service une longueur différente |
| La météo change tout | Par grand froid, le chauffage et la chimie de la batterie peuvent réduire l'autonomie de 20 à 40 % (ordre de grandeur) |
| La recharge coûte cher et est limitée | Puissance maximale du dépôt, nombre de chargeurs, prix de l'électricité variable selon l'heure |
| Le besoin est explicite côté opérateurs | Un grand opérateur affiche dans sa feuille de route 2030 : « affecter le bon véhicule au bon service en tenant compte de l'ensemble des contraintes », puis étendre à la recharge, la maintenance, les prix de l'énergie et les flux du dépôt |

## 2. Utilisateur

| Qui | Responsable de dépôt ou régulateur de nuit d'un réseau de bus urbain |
|---|---|
| Quand | Chaque soir (plan du lendemain) et en cas d'imprévu pendant la nuit |
| Ce qu'il fait aujourd'hui | Affectation manuelle ou semi-automatique, règles empiriques, marges de sécurité larges |
| Ce qu'il risque | Bus en panne sèche en ligne, service non assuré, surcoût de recharge, bus thermiques de réserve mobilisés |

## 3. Solution

| Brique | Rôle |
|---|---|
| Ingestion data | Météo horaire, prix de l'électricité, services, état de la flotte |
| Modèle de consommation | Prévoit les kWh nécessaires par bus et par service selon la température, et s'améliore à chaque nuit |
| Planificateur | Affecte les bus aux services et planifie la recharge sous contraintes |
| Agent | Détecte l'événement (vague de froid), relance prévision et planification, vérifie le plan, explique les arbitrages au responsable |
| Mémoire | Enregistre consommation réelle et décisions passées pour affiner les prévisions suivantes |

## 4. Scénario de démo

| Étape | Ce qu'on montre |
|---|---|
| 1 | Nuit normale : plan de l'agent, bus affectés, créneaux de recharge |
| 2 | Clic sur « Vague de froid -5°C » |
| 3 | L'agent détecte que X services ne seraient plus couverts avec le plan actuel |
| 4 | Il replanifie seul et explique ses choix (quel bus change de service, pourquoi, recharge avancée) |
| 5 | Écran de résultats : plan naïf vs plan agent, et courbe d'erreur de prévision qui baisse sur 3 nuits |

## 5. Réponse aux critères du jury

| Critère | Notre réponse |
|---|---|
| 01 Problème et valeur | Utilisateur précis (responsable de dépôt), besoin formulé par les opérateurs eux-mêmes |
| 02 Capacité agent | Boucle multi-étapes autonome : détecter, prévoir, planifier, vérifier, expliquer, apprendre |
| 03 Data au cœur | Fraîcheur (météo, prix), ancrage (vrais horaires GTFS), mémoire (consommation réelle par bus et température). Sans data, aucune décision possible |
| 04 Preuve | Backtest naïf vs agent sur une vraie nuit froide, avec métriques chiffrées |
| 05 Exécution et pitch | Démo scriptée en 3 minutes, différenciation claire (gestion de l'imprévu en combinant météo, énergie et flotte), route vers les opérateurs de transport |

---

## 6. Architecture

```
Open-Meteo / CSV prix / GTFS / flotte simulée
                |
                v
              Aiven PostgreSQL (référentiel, séries temporelles, plans)
                |
                v
              Backend FastAPI (Aiven Runtime)
              |- predict_consumption()
              |- run_planner()
              |- check_plan()
              |- agent (Claude, appel d'outils)
                |
                v  API REST (JSON section 9)
              Front Lovable (écran dépôt, bouton incident, métriques)
```

| Composant | Choix |
|---|---|
| Hébergement | Backend sur Aiven Runtime, front publié par Lovable |
| Base | Aiven PostgreSQL seul : toute la data passe par Aiven, aucune base locale. À cette échelle (40 bus, 30 services, 3 nuits), ClickHouse n'apporte rien et coûte une deuxième connexion et un deuxième schéma |
| Backend | Python, FastAPI |
| Environnement Python | uv (dépendances, venv, lancement des scripts) : pas de pip ni de venv à la main |
| Agent | Claude via API, avec appel d'outils |
| Front | Lovable (génère du React + Vite + Tailwind), branché sur l'API FastAPI |
| Bonus si temps | Aiven for ClickHouse (séries temporelles, si on veut le montrer), Aiven for OpenSearch (historique des incidents), Aiven for Valkey (état live), OR-Tools (solveur) |

### Environnement Python avec uv

Tout le Python du projet (backend, scripts de chargement, modèle, agent) est géré par [uv](https://docs.astral.sh/uv/). Les dépendances sont déclarées dans `pyproject.toml`, figées dans `uv.lock`, et les deux fichiers sont commités.

| Action | Commande |
|---|---|
| Installer uv (une fois) | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Installer l'environnement | `uv sync` |
| Ajouter une dépendance | `uv add fastapi` (`uv add --dev pytest` pour le dev) |
| Lancer le backend | `uv run fastapi dev app/main.py` |
| Lancer un script | `uv run python scripts/load_data.py` |
| Lancer les tests | `uv run pytest` |

Dépendances de base : `fastapi`, `uvicorn`, `psycopg[binary]` (Postgres), `anthropic`, `httpx` (Open-Meteo), `pandas`.

### Data sur Aiven

| Service Aiven | Contenu |
|---|---|
| Aiven for PostgreSQL | Tout : référentiel (bus, services, chargeurs), séries temporelles (météo, prix, consommation observée, runs du modèle) et plans générés |
| Aiven for ClickHouse (bonus) | Copie des séries temporelles, seulement si tout le reste marche |

| Règle | Détail |
|---|---|
| Une seule source de vérité | Le backend et l'agent lisent et écrivent uniquement dans Aiven. Les CSV ne servent qu'à l'import initial |
| Connexion par variable d'environnement | `PG_URI`, copiée depuis la console Aiven dans un `.env` non commité (`.env.example` commité) |
| TLS obligatoire | Connexion en `sslmode=require`, comme l'impose Aiven |
| Chargement | `uv run python scripts/load_data.py` crée les tables (section 8) et charge CSV, météo et flotte simulée dans Aiven |

### Front avec Lovable

| Règle | Détail |
|---|---|
| Dépôt dédié | Lovable synchronise un dépôt GitHub entier : connecter le projet Lovable à un dépôt séparé (ex. `WattShift-front`), pas à celui-ci |
| Pas de Supabase côté Lovable | Lovable propose Supabase par défaut : refuser. Le front ne parle qu'à l'API FastAPI, la data reste dans Aiven |
| Contrat | Donner à Lovable le format JSON de la section 9 et l'URL de l'API (variable `VITE_API_URL`). Prévoir des données factices au même format pour avancer avant que l'API soit en ligne |
| CORS | Autoriser le domaine Lovable (`*.lovable.app`) dans FastAPI |
| Écrans à générer | Plan de la nuit (bus, services, créneaux de recharge), bouton « Vague de froid -5°C », explication de l'agent, comparaison naïf vs agent et courbe de MAPE |

### Terrain de démo : Dijon

Le réseau est celui de Divia Mobilités (agglomération de Dijon : 2 lignes de tram, 7 Lianes, lignes de bus, navettes). Les horaires viennent du GTFS ouvert, la météo d'Open-Meteo pour Dijon (climat continental, vraies nuits froides en hiver). Le tram est exclu du périmètre (pas de batterie). Le plan du réseau sert de support visuel pour le pitch. La flotte de 40 bus et le dépôt restent fictifs : aucune donnée interne de l'opérateur n'est utilisée, et le projet n'est pas affilié à Divia.

### Ressources et crédits

Crédits fournis à chaque participant par l'organisation, et ce qu'on en fait.

| Ressource | Usage prévu |
|---|---|
| Aiven | Base PostgreSQL (ClickHouse en bonus), hébergement du backend (Aiven Runtime) |
| Lovable | Tout le front (voir ci-dessus) |
| Anthropic ($100) | Claude pour la boucle agent et les explications |
| Lightdash Cloud Pro (1 mois, par équipe) | Optionnel : tableau de bord des métriques naïf vs agent branché sur Aiven PostgreSQL, en secours si l'écran Lovable des métriques n'est pas prêt |
| Miro Enterprise (1 mois) | Préparation du pitch et schéma d'architecture |
| Modal ($100) | Non prévu. Solution de repli si Aiven Runtime bloque plus de 15 min pour le backend |
| Keenable | Non prévu pour l'instant |
| Vercel AI Gateway ($120), ElevenLabs (110k), Supabase ($25), Stripe ($500), n8n Cloud Pro, OpenAI ($50) | Non utilisés : hors périmètre de la démo |

## 7. Données

| Donnée | Source | Statut |
|---|---|---|
| Services (lignes, horaires, distance) | GTFS du réseau Divia Mobilités (Dijon, transport.data.gouv.fr), 30 services extraits et figés en CSV | Réel |
| Météo horaire | Open-Meteo (API historique, gratuite, sans clé), une vraie nuit froide de l'hiver dernier | Réel |
| Prix de l'électricité | 3 tranches : heures creuses, pleines, pointe | Simplifié |
| Flotte (40 bus : 30 titulaires + 10 de réserve) | Générée en Python, avec des états de santé de batterie (SoH) variés d'un bus à l'autre | Simulé |
| Consommation observée | Générée à partir d'un modèle « vrai » caché + bruit, pour que l'agent ait quelque chose à apprendre | Simulé |

Toutes ces données sont chargées dans Aiven PostgreSQL (voir section 8) avant la démo. Rien n'est lu directement depuis un fichier pendant l'exécution.

**Paramètres réalistes de la flotte (ordres de grandeur)**

| Paramètre | Valeur |
|---|---|
| Bus standard 12 m | Batterie 350 à 450 kWh |
| État de santé batterie (SoH) | 80 à 100 %, différent selon les bus (certains vieux bus à 80 % rendent des services longs infaisables par grand froid) |
| Bus de réserve | 10 bus en plus des 30 titulaires (40 au total pour 30 services). Ils permettent à l'agent de remplacer un bus devenu insuffisant par grand froid |
| Consommation par temps doux | 1,1 à 1,4 kWh/km |
| Consommation par grand froid | 1,6 à 2,0 kWh/km |
| Chargeurs dépôt | 10 chargeurs de 100 à 150 kW |
| Puissance max du dépôt | Inférieure à la somme des chargeurs (contrainte réelle) |

## 8. Schéma des tables

### PostgreSQL (référentiel et plans)

```sql
CREATE TABLE buses (
  bus_id            TEXT PRIMARY KEY,
  battery_kwh       NUMERIC,      -- capacité nominale
  soh               NUMERIC,      -- état de santé, 0.80 à 1.00
  soc_evening       NUMERIC,      -- charge au retour au dépôt, 0 à 1
  base_kwh_per_km   NUMERIC       -- consommation de référence à 15°C
);

CREATE TABLE services (
  service_id   TEXT PRIMARY KEY,
  line         TEXT,
  start_time   TIMESTAMP,
  end_time     TIMESTAMP,
  distance_km  NUMERIC
);

CREATE TABLE chargers (
  charger_id  TEXT PRIMARY KEY,
  power_kw    NUMERIC,
  status      TEXT              -- 'ok' ou 'down'
);

CREATE TABLE depot (
  depot_id      TEXT PRIMARY KEY, -- une seule ligne pour la démo
  max_power_kw  NUMERIC           -- puissance max du dépôt, inférieure à la somme des chargeurs
);

CREATE TABLE plans (
  plan_id     TEXT PRIMARY KEY,
  night_date  DATE,
  strategy    TEXT,             -- 'naive' ou 'agent'
  created_at  TIMESTAMP DEFAULT now(),
  payload     JSONB,            -- plan complet (format section 9)
  explanation TEXT              -- explication générée par l'agent
);
```

### PostgreSQL (séries temporelles et mémoire)

Mêmes tables qu'on aurait mises dans ClickHouse, dans la même base. Si on ajoute ClickHouse en bonus, on y recopie ces quatre tables sans changer leurs colonnes.

```sql
CREATE TABLE weather_hourly (
  ts      TIMESTAMP PRIMARY KEY,
  temp_c  REAL
);

CREATE TABLE energy_price (
  ts       TIMESTAMP PRIMARY KEY,
  eur_kwh  REAL,
  tranche  TEXT                 -- 'creuse', 'pleine', 'pointe'
);

CREATE TABLE consumption_obs (
  night_date  DATE,
  bus_id      TEXT,
  service_id  TEXT,
  line        TEXT,
  temp_c      REAL,
  km          REAL,
  kwh         REAL,
  PRIMARY KEY (night_date, bus_id, service_id)
);

CREATE TABLE model_runs (
  run_id      TEXT PRIMARY KEY,
  night_date  DATE,
  mape        REAL,             -- erreur de prévision moyenne
  coef_json   JSONB             -- coefficients appris
);
```

## 9. Contrat entre les 3 personnes : format JSON du plan et signatures Python

```json
{
  "plan_id": "2026-01-14-agent",
  "night_date": "2026-01-14",
  "strategy": "agent",
  "forecast_min_temp_c": -5.0,
  "assignments": [
    {
      "bus_id": "B07",
      "service_id": "S12",
      "predicted_kwh": 312.4,
      "available_kwh": 368.0,
      "margin_pct": 17.8
    }
  ],
  "charging": [
    {
      "bus_id": "B07",
      "charger_id": "C03",
      "start": "2026-01-13T23:00:00",
      "end": "2026-01-14T03:30:00",
      "kwh": 210.0,
      "cost_eur": 27.3
    }
  ],
  "uncovered_services": ["S29"],
  "kpis": {
    "services_uncovered": 1,
    "charging_cost_eur": 948.2,
    "replan_seconds": 4.1
  },
  "explanation": "B07 passe de S03 à S12 car..."
}
```

### Contrat en fonctions Python

En plus du JSON, les signatures ci-dessous sont figées à 0:20. Chaque module est d'abord livré en version factice (valeurs plausibles en dur, mêmes types), pour que l'Agent code contre lui dès le début. On bascule sur la vraie version sans changer les appels, avec `DATA_MODE=fake` ou `DATA_MODE=aiven` dans le `.env`.

| Fichier | Responsable | Fonctions |
|---|---|---|
| `app/data.py` | Data 1 | `get_buses() -> list[dict]`, `get_services() -> list[dict]`, `get_chargers() -> list[dict]`, `get_depot() -> dict` (`{"max_power_kw": 806}` au minimum : `data/depot.json` en mode fake, table `depot` en mode aiven), `get_forecast(night: date) -> list[dict]` (`ts`, `temp_c`), `get_prices(night: date) -> list[dict]`, `save_plan(plan: dict, explanation: str) -> None`, `get_plan(night_date: str, strategy: str) -> dict \| None` (dernier plan sauvegardé, avec son `explanation`) |
| `app/model.py` | Data 2 | `predict_consumption(bus_id: str, service_id: str, temp_c: float) -> float` (kWh), `available_kwh(bus_id: str, temp_c: float) -> float`, `record_outcome(night: date) -> dict` (nouvelle MAPE et coefficients) |
| `app/metrics.py` | Data 2 | `compare_plans(naive: dict, agent: dict) -> dict` (KPIs section 11), `mape_history() -> list[dict]` |
| `app/planner.py`, `app/agent.py` | Agent | `run_planner(constraints: dict) -> dict` (plan section 9), `check_plan(plan: dict) -> dict`, boucle agent |
| `app/main.py` | Agent | Routes FastAPI appelées par le front |

Un fichier n'a qu'un responsable : on évite ainsi les conflits Git.

## 10. Logique métier

### Modèle de consommation

| Élément | Formule |
|---|---|
| Consommation au km | `kwh_per_km = base_bus + a * max(0, 5 - temp_c) + b_ligne` (le chauffage et la chimie de la batterie ne pèsent que sous 5°C : un plan naïf tient par temps doux et ne casse qu'avec le froid) |
| Énergie disponible | `available_kwh = battery_kwh * soh * soc_matin * (1 - c * max(0, 5 - temp_c))` |
| Énergie nécessaire | `predicted_kwh = kwh_per_km * distance_km` |
| Apprentissage | Après chaque nuit, régression linéaire sur `consumption_obs`, nouveaux coefficients dans `model_runs`, calcul de la MAPE |

### Plan naïf (référence)

Même planificateur, mais consommation fixe à 15°C, sans météo ni apprentissage. C'est ce que ferait une règle simple.

### Heuristique de planification

| Étape | Règle |
|---|---|
| 1 | Trier les services par énergie nécessaire, du plus gourmand au moins gourmand |
| 2 | Trier les bus par énergie disponible |
| 3 | Affecter chaque service au bus disponible le moins chargé qui le couvre avec 15 % de marge |
| 4 | Services sans bus valide : `uncovered_services`, alerte |
| 5 | Recharge : remplir les besoins en priorité sur les tranches les moins chères, sans dépasser la puissance max du dépôt |

### Boucle agent

| Outil | Rôle |
|---|---|
| `get_forecast(night)` | Lit la météo de la nuit dans Postgres |
| `predict_consumption(bus, service, temp)` | Applique le modèle courant |
| `run_planner(constraints)` | Lance l'heuristique, renvoie un plan JSON |
| `check_plan(plan)` | Vérifie marges, services non couverts, puissance dépôt |
| `save_plan(plan, explanation)` | Écrit dans Postgres |
| `record_outcome(night)` | Ajoute la consommation observée et réentraîne le modèle |

Le LLM ne calcule pas le plan : il décide quoi relancer, avec quelles contraintes (marge plus forte par grand froid, recharge avancée), puis explique le résultat. Les calculs restent dans le code.

## 11. Preuve que ça marche (métriques)

| Métrique | Plan naïf | Plan agent |
|---|---|---|
| Services non couverts par grand froid | à calculer | à calculer |
| Coût de recharge de la nuit (€) | à calculer | à calculer |
| Temps de replanification | estimation manuelle : 30 min | mesuré (secondes) |
| Erreur de prévision (MAPE) | n/a | nuit 1, 2, 3 |

Toutes les valeurs sont calculées en live sur les données. Aucun chiffre en dur.

## 12. Périmètre

| Réel (tourne en démo) | Simulé (données générées) | Maquette (visible, non fonctionnel) |
|---|---|---|
| Base Aiven PostgreSQL | Télémétrie et état des bus | Onglet maintenance |
| Météo réelle | Consommation observée | Multi-dépôts |
| Modèle qui apprend | Tarifs en 3 tranches | Chargeur en panne (bonus si temps) |
| Heuristique et boucle agent | | Connexion à un outil d'exploitation opérateur |
| Métriques naïf vs agent | | |

À ne jamais faker : les métriques, la replanification live, la courbe d'apprentissage.

---

## 13. Équipe et planning (4h30 de build + 1h30 de marge)

La data est le chemin critique (la courbe d'apprentissage et la comparaison naïf vs agent sont la preuve pour le jury). Avec Lovable, le front prend environ 1 h : C passe donc la première moitié du build sur la data, en binôme avec A, sur une ligne de coupe sans fichier commun.

| Personne | Rôle |
|---|---|
| A : Data 1, infra et ingestion | Services Aiven, tables (section 8), `scripts/load_data.py`, GTFS, Open-Meteo, prix, `app/data.py` |
| B : Agent | Heuristique, plan naïf, boucle agent, explications, routes FastAPI |
| C : Data 2 puis Produit | Flotte simulée, modèle « vrai » caché + bruit, régression, MAPE, rejeu sur 3 nuits, `app/model.py` et `app/metrics.py`. Puis front Lovable, déploiement sur Aiven Runtime, scénario de démo, pitch |

| Heure | A : Data 1 | B : Agent | C : Data 2 puis Produit |
|---|---|---|---|
| 0:00 à 0:20 | Ensemble : valider schéma des tables (section 8), format JSON (section 9) et signatures Python (section 9) | | |
| 0:20 à 0:50 | Versions factices de `app/data.py`, connexion Aiven | Heuristique sur `app/data.py` factice | Projet Lovable lancé sur données factices, versions factices de `app/model.py` |
| 0:50 à 1:30 | Tables créées et remplies dans Aiven | Heuristique et plan naïf | Flotte simulée et consommation observée, en pandas en local |
| **1:30** | **Checkpoint : données en base, un plan sort, le modèle tourne en local, une page Lovable en ligne** | | |
| 1:30 à 2:30 | `app/data.py` en mode `aiven`, écriture des plans | Boucle agent complète | Régression, MAPE, rejeu sur 3 nuits, écriture dans Aiven, `app/metrics.py` |
| 2:30 à 3:00 | Backend déployé sur Aiven Runtime | Routes FastAPI pour le front | Retour au front : écran dépôt et bouton « vague de froid » branchés sur l'API |
| **3:00** | **Checkpoint : intégration de bout en bout, courbe d'apprentissage visible** | | |
| 3:00 à 4:00 | Métriques dans le front, aide à l'intégration | Fiabiliser le scénario | Slides de pitch |
| 4:00 à 4:30 | Ensemble : gel du code, déploiement final, vidéo de secours | | |
| 4:30 à 6:00 | Marge : bugs, répétitions, bonus (ClickHouse, Lightdash) | | |

### Tâches par personne

Chacun prend une lettre (A, B ou C) et coche ses cases dans ce fichier en commitant (`[x]`). Les fichiers appartiennent à une seule personne (section 9).

**A : Data 1, infra et ingestion**

- [x] GTFS de Dijon récupéré, 30 services extraits dans `data/services.csv` (`scripts/extract_services.py`)
- [x] (fait par C, `scripts/simulate_data.py`) Générer la flotte à 40 bus (30 titulaires + 10 de réserve) avec des SoH variés (de 0,80 à 1,00) pour que le grand froid rende certaines paires bus/service infaisables, puis la charger dans `buses`
- [ ] Créer le service Aiven PostgreSQL, partager `PG_URI` hors GitHub, ajouter `.env.example`
- [ ] Initialiser le projet : `uv init`, `pyproject.toml`, `uv.lock` (dépendances de la section 6)
- [ ] Créer les tables de la section 8 (`scripts/create_tables.py` ou SQL)
- [ ] `app/data.py` en mode `fake` (signatures section 9), puis mode `aiven`
- [x] Météo Open-Meteo pour Dijon, hiver 2025-2026 figé en `data/weather.csv` (`scripts/fetch_weather.py`) : nuit douce 2026-01-14 (5,4°C), vraie nuit froide 2026-01-05 (-6,7°C)
- [ ] Prix de l'électricité en 3 tranches dans `energy_price`
- [ ] `scripts/load_data.py` : charge services, météo, prix, flotte et chargeurs dans Aiven
- [ ] Déployer le backend sur Aiven Runtime (tester d'abord un « hello world » FastAPI + uv)

**B : Agent**

- [ ] `app/planner.py` : heuristique de la section 10 sur `app/data.py` factice
- [ ] Plan naïf (consommation fixe à 15°C) et `check_plan()`
- [ ] Boucle agent Claude avec les 6 outils (`app/agent.py`)
- [ ] Explications du plan en français
- [ ] Routes FastAPI (`app/main.py`) : plan naïf, plan agent, bouton « vague de froid », métriques
- [ ] Fiabiliser le scénario de démo de bout en bout

**C : Data 2 puis Produit**

- [x] Flotte simulée : 30 bus (`buses`) et 10 chargeurs (`chargers`) selon les paramètres de la section 7, dans `data/buses.csv` et `data/chargers.csv`
- [x] Modèle « vrai » caché + bruit, consommation observée sur 3 nuits (`consumption_obs`)
- [x] `app/model.py` : `predict_consumption`, `available_kwh`, `record_outcome` (régression, MAPE)
- [x] Rejeu de 3 nuits, écriture dans `model_runs`, `app/metrics.py` (`compare_plans`, `mape_history`)
- [ ] Projet Lovable (dépôt séparé), publié sur données factices
- [ ] Écran dépôt, bouton « vague de froid », comparaison naïf vs agent, courbe de MAPE, branchés sur l'API
- [ ] Slides de pitch et vidéo de secours

**Ensemble**

- [ ] 0:00 à 0:20 : valider schéma (section 8), JSON et signatures (section 9)
- [ ] Gel du code à 4:30, déploiement final

### À faire avant le jour J (si le règlement l'autorise)

Rien de tout ça n'est du code produit, mais chaque point retire un risque du build.

| Tâche | Pourquoi |
|---|---|
| Créer le service Aiven PostgreSQL et partager `PG_URI` (hors GitHub) | Le provisionnement prend plusieurs minutes |
| Télécharger le GTFS de Dijon (Divia) et lancer `uv run scripts/extract_services.py <dossier_gtfs>` : produit `data/services.csv` (30 vacations de 110 à 175 km, fait) | Choisir les lignes prend du temps. Privilégier des lignes à fort kilométrage (Lianes, tram exclu : il n'est pas à batterie) et prendre la longueur de service dans `stop_times` + `shapes` |
| Récupérer la nuit froide sur Open-Meteo et la figer en CSV | Repli prêt si l'API bloque |
| Vérifier qu'Aiven Runtime accepte une app FastAPI + uv (un « hello world ») | C'est le principal risque inconnu de la stack |
| Installer uv sur les 3 machines, accès au dépôt GitHub pour les 3 | Ne pas perdre les 20 premières minutes |

### Travail avec Git

Les trois personnes sont ajoutées comme collaborateurs du dépôt (Settings > Collaborators), donc avec accès en écriture : chacun le **clone**, personne ne le forke (un fork obligerait à passer par des pull requests entre dépôts).

| Étape | Commande |
|---|---|
| Une fois | `git clone https://github.com/willthn/WattShift.git` |
| Avant de commencer et avant chaque push | `git pull --rebase` |
| Après chaque étape qui marche | `git add …`, `git commit`, `git push` |

| Règle | Détail |
|---|---|
| Tout le monde sur `main`, petits commits fréquents | Pas le temps de relire des PR en 4h30 |
| Un fichier, un responsable (voir section 9) | Pratiquement aucun conflit |
| `uv.lock` : en cas de conflit, résoudre `pyproject.toml` puis régénérer avec `uv lock` | Le fichier est généré, il ne se fusionne pas à la main |
| Le front Lovable vit dans son propre dépôt | Lovable gère seul sa synchro GitHub |
| Même règles avec Claude Code ou VS Code | L'outil ne change rien au workflow Git |

### Règles d'équipe

| Règle | Pourquoi |
|---|---|
| Le contrat JSON et les signatures Python ne changent pas sans prévenir les deux autres | Permet de travailler en parallèle |
| Si une source réelle bloque plus de 15 min, passer en CSV figé | Le temps est la contrainte n°1 |
| Déployer sur Aiven dès la première heure | Éviter la mauvaise surprise de fin |
| Python uniquement via uv (`uv add`, `uv run`), `uv.lock` commité | Même environnement sur les 3 machines et sur Aiven Runtime |
| Data uniquement dans Aiven, identifiants dans `.env` jamais commité | Une seule source de vérité, pas de secret sur GitHub |
| À chaque checkpoint, garder la version la plus simple qui marche | Mieux vaut une boucle complète qu'une brique parfaite |
| Gel du code à 4:30 | La dernière heure sert à la démo, pas au code |

## 14. Roadmap (slide de fin)

| Aujourd'hui | Prochaine étape |
|---|---|
| 1 dépôt, flotte simulée | Branchement sur la télémétrie réelle d'un opérateur |
| Vague de froid | Pannes de chargeurs, maintenance, ordre de sortie des bus garés en file |
| Heuristique | Solveur d'optimisation (OR-Tools) |
| Prix en 3 tranches | Prix spot de l'électricité en temps réel |
| Mémoire de consommation | Historique des incidents et des décisions recherchable (OpenSearch) |

## 15. Route vers les premiers utilisateurs

| Cible | Approche |
|---|---|
| Grands opérateurs urbains | Le besoin figure déjà dans leurs feuilles de route : proposer un pilote sur un dépôt |
| Réseaux de taille moyenne | Moins outillés, cycle de décision plus court |
| Autorités organisatrices des mobilités | Intérêt direct pour la fiabilité du service et le coût énergétique |
