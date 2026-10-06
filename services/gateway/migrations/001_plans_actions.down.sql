-- Stop writers first. Archive all v1 tables together; never DROP or TRUNCATE user records.
-- Reapplying the up migration restores the archive and its existing records.
DO $$ BEGIN
  IF to_regnamespace('cs_plan_actions') IS NOT NULL THEN
    IF to_regnamespace('cs_plan_actions_archived_v1') IS NOT NULL THEN
      RAISE EXCEPTION 'Archive already exists; manual reconciliation required';
    END IF;
    ALTER SCHEMA cs_plan_actions RENAME TO cs_plan_actions_archived_v1;
  END IF;
END $$;
