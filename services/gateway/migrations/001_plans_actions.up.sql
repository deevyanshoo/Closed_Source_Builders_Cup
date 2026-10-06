-- Restore an archived v1 schema when rolling forward. No customer data is deleted.
DO $$ BEGIN
  IF to_regnamespace('cs_plan_actions') IS NULL AND to_regnamespace('cs_plan_actions_archived_v1') IS NOT NULL THEN
    ALTER SCHEMA cs_plan_actions_archived_v1 RENAME TO cs_plan_actions;
  END IF;
END $$;
CREATE SCHEMA IF NOT EXISTS cs_plan_actions;
CREATE TABLE IF NOT EXISTS cs_plan_actions.schema_migrations (
  version integer PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS cs_plan_actions.plans (
  saved_plan_id text PRIMARY KEY,
  workspace_id text NOT NULL CHECK (length(workspace_id)>0),
  current_revision integer NOT NULL CHECK (current_revision>0),
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id,saved_plan_id)
);
CREATE TABLE IF NOT EXISTS cs_plan_actions.plan_revisions (
  workspace_id text NOT NULL,
  saved_plan_id text NOT NULL,
  revision integer NOT NULL CHECK (revision>0),
  plan_version text NOT NULL,
  snapshot jsonb NOT NULL CHECK (snapshot->>'status'='ready'),
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (saved_plan_id,revision),
  UNIQUE (workspace_id,saved_plan_id,revision,plan_version),
  FOREIGN KEY (workspace_id,saved_plan_id) REFERENCES cs_plan_actions.plans(workspace_id,saved_plan_id),
  CHECK (snapshot #>> '{request,workspace_id}'=workspace_id),
  CHECK (snapshot #>> '{plan,plan_version}'=plan_version)
);
CREATE TABLE IF NOT EXISTS cs_plan_actions.approvals (
  approval_id text PRIMARY KEY,
  workspace_id text NOT NULL,
  saved_plan_id text NOT NULL,
  revision integer NOT NULL,
  plan_version text NOT NULL,
  action_kind text NOT NULL CHECK (action_kind='prepare_rfq'),
  arguments jsonb NOT NULL,
  approved_by text NOT NULL,
  status text NOT NULL DEFAULT 'confirmed' CHECK (status IN ('confirmed','executed')),
  receipt_id text,
  created_at timestamptz NOT NULL DEFAULT now(),
  executed_at timestamptz,
  UNIQUE (workspace_id,approval_id,saved_plan_id,revision,plan_version),
  FOREIGN KEY (workspace_id,saved_plan_id,revision,plan_version) REFERENCES cs_plan_actions.plan_revisions(workspace_id,saved_plan_id,revision,plan_version),
  CHECK ((status='confirmed' AND receipt_id IS NULL AND executed_at IS NULL) OR (status='executed' AND receipt_id IS NOT NULL AND executed_at IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS cs_plan_actions.action_receipts (
  receipt_id text PRIMARY KEY,
  workspace_id text NOT NULL,
  saved_plan_id text NOT NULL,
  revision integer NOT NULL,
  plan_version text NOT NULL,
  approval_id text NOT NULL UNIQUE,
  action_kind text NOT NULL CHECK (action_kind='prepare_rfq'),
  idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 128),
  request_hash text NOT NULL,
  prepared_by text NOT NULL,
  receipt jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id,idempotency_key),
  FOREIGN KEY (workspace_id,approval_id,saved_plan_id,revision,plan_version) REFERENCES cs_plan_actions.approvals(workspace_id,approval_id,saved_plan_id,revision,plan_version),
  CHECK (receipt->>'status'='draft_prepared'),
  CHECK (receipt->>'supplier_communication'='false'),
  CHECK (receipt->>'workspace_id'=workspace_id)
);
CREATE TABLE IF NOT EXISTS cs_plan_actions.audit_events (
  audit_id text PRIMARY KEY,
  workspace_id text NOT NULL,
  saved_plan_id text NOT NULL,
  revision integer NOT NULL,
  event_type text NOT NULL CHECK (event_type IN ('plan_saved','plan_revised','action_confirmed','rfq_prepared')),
  principal_id text NOT NULL,
  receipt_id text REFERENCES cs_plan_actions.action_receipts(receipt_id),
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (workspace_id,saved_plan_id) REFERENCES cs_plan_actions.plans(workspace_id,saved_plan_id),
  UNIQUE (receipt_id,event_type)
);
CREATE INDEX IF NOT EXISTS plans_workspace_updated ON cs_plan_actions.plans(workspace_id,updated_at DESC);
CREATE INDEX IF NOT EXISTS audit_plan_created ON cs_plan_actions.audit_events(workspace_id,saved_plan_id,created_at);
INSERT INTO cs_plan_actions.schema_migrations(version) VALUES(1) ON CONFLICT(version) DO NOTHING;
