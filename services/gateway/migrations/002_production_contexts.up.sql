-- Local synthetic production context storage, separate from legacy saved plans.
-- Migration001/PR28 backup coverage is deliberately unchanged.
BEGIN;
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname='cs_production_contexts')
 AND EXISTS (SELECT 1 FROM pg_namespace WHERE nspname='cs_production_contexts_archived_v1') THEN
  ALTER SCHEMA cs_production_contexts_archived_v1 RENAME TO cs_production_contexts;
 END IF;
END $$;
CREATE SCHEMA IF NOT EXISTS cs_production_contexts;
CREATE TABLE IF NOT EXISTS cs_production_contexts.schema_migrations (
 version integer PRIMARY KEY CHECK (version=1), applied_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS cs_production_contexts.contexts (
 context_id text PRIMARY KEY CHECK (context_id ~ '^[A-Za-z0-9_-]{32}$'),
 workspace_id text NOT NULL CHECK (length(workspace_id) BETWEEN 1 AND 256),
 revision integer NOT NULL CHECK (revision>0),
 snapshot jsonb NOT NULL,
 metadata jsonb NOT NULL DEFAULT '{"synthetic":true,"source":"caller_supplied_synthetic","syntactic_validation":"accepted","numerical_acceptance":"not_claimed"}'::jsonb,
 created_by text NOT NULL CHECK (length(created_by)>0),
 updated_by text NOT NULL CHECK (length(updated_by)>0),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 CHECK (jsonb_typeof(snapshot)='object'),
 CHECK ((snapshot->>'workspace_id'=workspace_id AND jsonb_typeof(snapshot->'synthetic')='boolean' AND snapshot->>'synthetic'='true') IS TRUE),
 CHECK ((snapshot->>'snapshot_id'='production-'||context_id||'-r'||revision::text) IS TRUE),
 CHECK ((snapshot->>'plan_version'='production-plan-'||context_id||'-r'||revision::text) IS TRUE),
 CHECK (metadata='{"synthetic":true,"source":"caller_supplied_synthetic","syntactic_validation":"accepted","numerical_acceptance":"not_claimed"}'::jsonb)
);
CREATE INDEX IF NOT EXISTS production_context_workspace_updated ON cs_production_contexts.contexts(workspace_id,updated_at DESC,context_id);
INSERT INTO cs_production_contexts.schema_migrations(version) VALUES(1) ON CONFLICT DO NOTHING;
COMMIT;
