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
   ClickHouse (séries temporelles)   PostgreSQL (référentiel et plans)
                \                         /
                 v                       v
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
| Bases | Aiven ClickHouse et Aiven PostgreSQL : toute la data passe par Aiven, aucune base locale |
| Backend | Python, FastAPI |
| Environnement Python | uv (dépendances, venv, lancement des scripts) : pas de pip ni de venv à la main |
| Agent | Claude via API, avec appel d'outils |
| Front | Lovable (génère du React + Vite + Tailwind), branché sur l'API FastAPI |
| Bonus si temps | Aiven for OpenSearch (historique des incidents), Aiven for Valkey (état live), OR-Tools (solveur) |

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

Dépendances de base : `fastapi`, `uvicorn`, `psycopg[binary]` (Postgres), `clickhouse-connect` (ClickHouse), `anthropic`, `httpx` (Open-Meteo), `pandas`.

### Data sur Aiven

| Service Aiven | Contenu |
|---|---|
| Aiven for PostgreSQL | Référentiel (bus, services, chargeurs) et plans générés |
| Aiven for ClickHouse | Séries temporelles : météo, prix, consommation observée, runs du modèle |

| Règle | Détail |
|---|---|
| Une seule source de vérité | Le backend et l'agent lisent et écrivent uniquement dans Aiven. Les CSV ne servent qu'à l'import initial |
| Connexion par variables d'environnement | `PG_URI` et `CLICKHOUSE_URL`, `CLICKHOUSE_USER`, `CLICKHOUSE_PASSWORD`, copiés depuis la console Aiven dans un `.env` non commité (`.env.example` commité) |
| TLS obligatoire | Connexions en `sslmode=require` (Postgres) et HTTPS (ClickHouse), comme l'impose Aiven |
| Chargement | `uv run python scripts/load_data.py` crée les tables (section 8) et charge CSV, météo et flotte simulée dans Aiven |

### Front avec Lovable

| Règle | Détail |
|---|---|
| Dépôt dédié | Lovable synchronise un dépôt GitHub entier : connecter le projet Lovable à un dépôt séparé (ex. `WattShift-front`), pas à celui-ci |
| Pas de Supabase côté Lovable | Lovable propose Supabase par défaut : refuser. Le front ne parle qu'à l'API FastAPI, la data reste dans Aiven |
| Contrat | Donner à Lovable le format JSON de la section 9 et l'URL de l'API (variable `VITE_API_URL`). Prévoir des données factices au même format pour avancer avant que l'API soit en ligne |
| CORS | Autoriser le domaine Lovable (`*.lovable.app`) dans FastAPI |
| Écrans à générer | Plan de la nuit (bus, services, créneaux de recharge), bouton « Vague de froid -5°C », explication de l'agent, comparaison naïf vs agent et courbe de MAPE |

### Ressources et crédits

Crédits fournis à chaque participant par l'organisation, et ce qu'on en fait.

| Ressource | Usage prévu |
|---|---|
| Aiven | Bases PostgreSQL et ClickHouse, hébergement du backend (Aiven Runtime) |
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
| Services (lignes, horaires, distance) | GTFS d'un réseau de taille moyenne (transport.data.gouv.fr), 30 services extraits et figés en CSV | Réel |
| Météo horaire | Open-Meteo (API historique, gratuite, sans clé), une vraie nuit froide de l'hiver dernier | Réel |
| Prix de l'électricité | 3 tranches : heures creuses, pleines, pointe | Simplifié |
| Flotte (30 bus) | Générée en Python | Simulé |
| Consommation observée | Générée à partir d'un modèle « vrai » caché + bruit, pour que l'agent ait quelque chose à apprendre | Simulé |

Toutes ces données sont chargées dans Aiven (PostgreSQL ou ClickHouse, voir section 8) avant la démo. Rien n'est lu directement depuis un fichier pendant l'exécution.

**Paramètres réalistes de la flotte (ordres de grandeur)**

| Paramètre | Valeur |
|---|---|
| Bus standard 12 m | Batterie 350 à 450 kWh |
| État de santé batterie (SoH) | 80 à 100 % |
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

CREATE TABLE plans (
  plan_id     TEXT PRIMARY KEY,
  night_date  DATE,
  strategy    TEXT,             -- 'naive' ou 'agent'
  created_at  TIMESTAMP DEFAULT now(),
  payload     JSONB,            -- plan complet (format section 9)
  explanation TEXT              -- explication générée par l'agent
);
```

### ClickHouse (séries temporelles et mémoire)

```sql
CREATE TABLE weather_hourly (
  ts      DateTime,
  temp_c  Float32
) ENGINE = MergeTree ORDER BY ts;

CREATE TABLE energy_price (
  ts       DateTime,
  eur_kwh  Float32,
  tranche  String               -- 'creuse', 'pleine', 'pointe'
) ENGINE = MergeTree ORDER BY ts;

CREATE TABLE consumption_obs (
  night_date  Date,
  bus_id      String,
  service_id  String,
  line        String,
  temp_c      Float32,
  km          Float32,
  kwh         Float32
) ENGINE = MergeTree ORDER BY (night_date, bus_id);

CREATE TABLE model_runs (
  run_id      String,
  night_date  Date,
  mape        Float32,          -- erreur de prévision moyenne
  coef_json   String            -- coefficients appris
) ENGINE = MergeTree ORDER BY night_date;
```

## 9. Format JSON du plan (contrat entre les 3 personnes)

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

## 10. Logique métier

### Modèle de consommation

| Élément | Formule |
|---|---|
| Consommation au km | `kwh_per_km = base_bus + a * max(0, 15 - temp_c) + b_ligne` |
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
| `get_forecast(night)` | Lit la météo de la nuit dans ClickHouse |
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
| Bases Aiven ClickHouse et Postgres | Télémétrie et état des bus | Onglet maintenance |
| Météo réelle | Consommation observée | Multi-dépôts |
| Modèle qui apprend | Tarifs en 3 tranches | Chargeur en panne (bonus si temps) |
| Heuristique et boucle agent | | Connexion à un outil d'exploitation opérateur |
| Métriques naïf vs agent | | |

À ne jamais faker : les métriques, la replanification live, la courbe d'apprentissage.

---

## 13. Équipe et planning (4h30 de build + 1h30 de marge)

| Personne | Rôle |
|---|---|
| A : Data | Aiven, chargement CSV, météo, flotte simulée, modèle de consommation, métriques |
| B : Agent | Heuristique, plan naïf, boucle agent, explications |
| C : Produit | Front Lovable, déploiement sur Aiven Runtime, écran dépôt, scénario de démo, pitch |

| Heure | A : Data | B : Agent | C : Produit |
|---|---|---|---|
| 0:00 à 0:20 | Ensemble : valider schéma des tables (section 8) et format JSON (section 9) | | |
| 0:20 à 1:30 | Aiven en place, tables remplies | Heuristique et plan naïf sur données factices | Projet Lovable publié sur données factices, backend vide déployé |
| **1:30** | **Checkpoint : données en base, un plan sort, une page en ligne** | | |
| 1:30 à 3:00 | Modèle de consommation, rejeu de 3 nuits | Boucle agent complète | Écran dépôt et bouton « vague de froid » |
| **3:00** | **Checkpoint : intégration de bout en bout** | | |
| 3:00 à 4:00 | Métriques | Fiabiliser le scénario | Slides de pitch |
| 4:00 à 4:30 | Ensemble : gel du code, déploiement final, vidéo de secours | | |
| 4:30 à 6:00 | Marge : bugs, répétitions, bonus | | |

### Règles d'équipe

| Règle | Pourquoi |
|---|---|
| Le contrat JSON ne change pas sans prévenir les deux autres | Permet de travailler en parallèle |
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
