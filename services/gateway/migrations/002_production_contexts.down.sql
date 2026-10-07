-- Recoverable in-cluster archive, not an independent backup; never drop rows.
BEGIN;
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname='cs_production_contexts') THEN
  IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname='cs_production_contexts_archived_v1') THEN
   RAISE EXCEPTION 'Production context archive already exists; refusing overwrite';
  END IF;
  ALTER SCHEMA cs_production_contexts RENAME TO cs_production_contexts_archived_v1;
 END IF;
END $$;
COMMIT;
