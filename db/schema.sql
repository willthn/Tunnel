-- Schéma Aiven PostgreSQL (README section 8). Idempotent : peut être relancé sans risque.

-- Référentiel et plans
CREATE TABLE IF NOT EXISTS buses (
  bus_id            TEXT PRIMARY KEY,
  battery_kwh       NUMERIC,      -- capacité nominale
  soh               NUMERIC,      -- état de santé, 0.80 à 1.00
  soc_evening       NUMERIC,      -- charge au retour au dépôt, 0 à 1
  base_kwh_per_km   NUMERIC       -- consommation de référence à 15°C
);

CREATE TABLE IF NOT EXISTS services (
  service_id   TEXT PRIMARY KEY,
  line         TEXT,
  start_time   TIMESTAMP,
  end_time     TIMESTAMP,
  distance_km  NUMERIC
);

CREATE TABLE IF NOT EXISTS chargers (
  charger_id  TEXT PRIMARY KEY,
  power_kw    NUMERIC,
  status      TEXT              -- 'ok' ou 'down'
);

CREATE TABLE IF NOT EXISTS depot (
  depot_id      TEXT PRIMARY KEY, -- une seule ligne pour la démo
  max_power_kw  NUMERIC           -- puissance max du dépôt, inférieure à la somme des chargeurs
);

CREATE TABLE IF NOT EXISTS plans (
  plan_id     TEXT PRIMARY KEY,
  night_date  DATE,
  strategy    TEXT,             -- 'naive' ou 'agent'
  created_at  TIMESTAMP DEFAULT now(),
  payload     JSONB,            -- plan complet (format section 9), trace de l'agent comprise
  explanation TEXT              -- explication générée par l'agent
);

-- Séries temporelles et mémoire
CREATE TABLE IF NOT EXISTS weather_hourly (   -- température retenue pour planifier
  ts      TIMESTAMP PRIMARY KEY,
  temp_c  REAL
);

CREATE TABLE IF NOT EXISTS weather_obs (      -- toutes les sources, avec leur provenance
  ts          TIMESTAMP,                      -- heure locale Europe/Paris
  source      TEXT,                           -- 'meteofrance-arome', 'ecmwf-ifs', 'gfs', 'copernicus-era5', 'noaa-isd-07280', 'meteofrance-21473001'
  kind        TEXT,                           -- 'forecast', 'reanalysis' ou 'observed'
  temp_c      REAL,
  fetched_at  TIMESTAMP DEFAULT now(),        -- fraîcheur : quand la donnée a été récupérée
  PRIMARY KEY (ts, source, kind)
);

CREATE TABLE IF NOT EXISTS energy_price (
  ts       TIMESTAMP PRIMARY KEY,    -- heure locale Europe/Paris
  eur_kwh  REAL,                     -- prix spot day-ahead France, €/kWh de gros
  tranche  TEXT                      -- 'creuse' (8 h les moins chères du jour), 'pleine', 'pointe' (3 h les plus chères)
);
ALTER TABLE energy_price ADD COLUMN IF NOT EXISTS source TEXT;  -- 'energy-charts-fr-day-ahead' ou 'tarif-3-tranches' (repli)

CREATE TABLE IF NOT EXISTS consumption_obs (
  night_date  DATE,
  bus_id      TEXT,
  service_id  TEXT,
  line        TEXT,
  temp_c      REAL,
  km          REAL,
  kwh         REAL,
  PRIMARY KEY (night_date, bus_id, service_id)
);

CREATE TABLE IF NOT EXISTS model_runs (
  run_id      TEXT PRIMARY KEY,
  night_date  DATE,
  mape        REAL,             -- erreur de prévision moyenne
  coef_json   JSONB             -- coefficients appris
);
