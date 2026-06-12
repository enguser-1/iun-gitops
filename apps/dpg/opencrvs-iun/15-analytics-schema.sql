-- Schema analytics requis par countryconfig farajaland v1.9 (boot sync).
-- Source : opencrvs-countryconfig v1.9.0 infrastructure/postgres/setup-analytics.sh
-- Adaptation IUN : user 'app' proprietaire de la DB events -> pas de role dedie ni GRANT.
-- Application : voir commande oc exec (base64 | psql) du 2026-06-12.
CREATE SCHEMA IF NOT EXISTS analytics;

CREATE TABLE IF NOT EXISTS analytics.locations (
  id TEXT PRIMARY KEY,
  name text NOT NULL,
  parent_id TEXT REFERENCES analytics.locations(id),
  location_type TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analytics.event_actions (
  event_type text NOT NULL,
  action_type TEXT NOT NULL,
  annotation jsonb,
  assigned_to text,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  created_at_location TEXT,
  created_by text NOT NULL,
  created_by_role text NOT NULL,
  created_by_signature text,
  created_by_user_type TEXT NOT NULL,
  declared_at timestamp with time zone,
  registered_at timestamp with time zone,
  declaration jsonb NOT NULL DEFAULT '{}'::jsonb,
  event_id uuid NOT NULL,
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  original_action_id uuid,
  registration_number text UNIQUE,
  request_id text,
  status TEXT NOT NULL,
  transaction_id text NOT NULL,
  content jsonb,
  UNIQUE (id, event_id)
);

CREATE TABLE IF NOT EXISTS analytics.location_levels (
  id text PRIMARY KEY,
  level int NOT NULL,
  name text NOT NULL
);

CREATE TABLE IF NOT EXISTS analytics.location_statistics (
  name text,
  reference_id text NOT NULL,
  year int NOT NULL,
  crude_birth_rate NUMERIC(4,1) NOT NULL,
  male_population int NOT NULL,
  female_population int NOT NULL,
  total_population int NOT NULL,
  UNIQUE (reference_id, year)
);
