# Local saved-state export and restore

The default operator export uses format **v2**: one custom archive covers both
`cs_plan_actions` (six workflow tables) and `cs_production_contexts` (two saved
synthetic context tables). Restore writes only to a **different empty local
database**. It preserves complete saved snapshots, exact decimal strings,
ownership/revisions, context metadata, approvals, RFQ draft receipts and audit
rows. Restore does not recompute a recommendation or execute an action. The CLI
has no browser upload or remote restore surface.

This is local PostgreSQL17 evidence. Cloud SQL activation remains **HOLD**;
PostgreSQL18, hosted recovery, provider identity, online cutover and paid
continuity are unverified. The exhausted model pilot is not rerun here. Root
owns any future provider validation and the separately costed December4
continuity decision; these scripts do not activate a trial or upgrade billing.

## Private inputs and artifacts

Use the installed `/opt/homebrew/opt/postgresql@17/bin` tools. Connection files
contain one PostgreSQL URL with explicit loopback host, port, user and non-system
database. Each connection file must be an owner-controlled regular file with
mode0600 in a private mode0700 directory; unsafe symlinks are rejected. Prepare
credentials through a private editor/secret mechanism, never a shell argument,
chat, tracked file or public receipt. The CLI rejects libpq URL options and
inherited connection overrides. Passwords are passed to PostgreSQL tools using
a temporary private pgpass file, not subprocess arguments.

Archives contain application data. Keep the bundle and any failed destination
private; do not commit, upload or attach raw archives or application rows. The
bundle directory is mode0700 and `archive.dump`/`manifest.json` are mode0600.
Operator stdout is sanitized JSON. A failed command returns nonzero and must
not be interpreted as a completed backup or verified restore.

A checksum proves integrity, **not authenticity**. Use only a host-created
trusted archive and an independently retained manifest SHA256. Someone who can
replace both archive and trusted hash controls the restore input. Do not accept
untrusted SQL archives or derive the expected hash from a received bundle and
call that authentication.

## Export, restore, verify, then switch

1. Retain the original database, its configuration and the exact source commit.
   Stop writers for the rehearsal/cutover. Export is transactionally consistent:
   the custom dump and all eight table counts/full-row digests share one exported
   repeatable-read read-only snapshot. This does not prove a live online switch
   is safe while writers continue.
2. Export to a **new** bundle directory; completed output cannot be overwritten:

   ```sh
   python3 scripts/local_state_backup.py export \
     --source-connection-file /private/operator/source.url \
     --output-dir /private/operator/new-bundle \
     --source-revision FULL_40_CHARACTER_GIT_SHA
   ```

   Retain the reported manifest SHA256 through a trusted operator channel. The
   v2 manifest records both schema versions and accepted migration001/002 hashes,
   source Git revision, actual source cluster/database identity, exact tool/server
   versions, archive hash and all eight table counts/full-row digests. It contains
   no URLs, passwords or raw rows. Both schemas must have exactly migration1 and
   the accepted table set; missing/extra schemas/tables or unsupported archive
   objects are rejected. Quiesce DDL as well as application writers.
3. Have the operator create a distinct empty local target. The restore CLI does
   not create/drop a database, rename schemas, clean objects or offer `--force`.
   It checks actual cluster system identifier/databaseOID, so changing
   `127.0.0.1` to `localhost` does not bypass same-database rejection. A target
   containing non-system objects or custom schemas is rejected before writes.

   ```sh
   python3 scripts/local_state_backup.py restore \
     --source-connection-file /private/operator/source.url \
     --target-connection-file /private/operator/new-target.url \
     --bundle-dir /private/operator/new-bundle \
     --expected-manifest-sha256 TRUSTED_64_CHARACTER_HASH \
     --expected-source-revision FULL_40_CHARACTER_GIT_SHA \
     --local-test-restore
   ```

   Restore checks private artifact files, format/schema/migration/source/tool
   identity and archive integrity first. It then uses a single transaction with
   exit-on-error, no owner/ACL replay and no destructive flags. Verification
   checks the schema, table counts and full-row digests. A failed target remains
   quarantined for inspection: do not automatically delete it, retry into a
   different database or point a gateway at it.
4. Verify restored HTTP workflow before changing gateway configuration. Confirm
   snapshots/revisions/approval states/receipts/audit equal the original, an
   executed-key retry returns the original receipt without a new effect, and
   conflict/stale/foreign/viewer requests still fail. Only after success should
   an operator point a **task-owned** gateway's `DATABASE_URL_FILE` to the target
   and restart it. `/health` alone is insufficient proof of persistence.
5. Retain the original database and private recovery reference. If the verified
   target is rejected at cutover, stop task writers, restore the original
   `DATABASE_URL_FILE`, restart the task gateway and reverify. This is a
   configuration rollback, not a merge of writes made independently to two
   databases. Do not silently discard post-cutover writes.

Migration001 rollback renames the schema to `cs_plan_actions_archived_v1`. That
in-cluster archive is not a backup, does not survive losing the cluster, and
must not replace a separately verified export/restore artifact.

## Archive compatibility

A v2 manifest has exactly `format_version=2`, `source_revision`,
`archive_sha256`, `source_identity`, `server_version`, `tools`, and `schemas`.
`schemas` has exactly `cs_plan_actions` and `cs_production_contexts`; each has
`schema_version=1`, `migration_sha256`, and `tables` with `{count,sha256}` for
its exact accepted tables. The custom dump and all eight digests use the same
exported snapshot. The archive TOC is checked before target writes.

Historical **v1** remains readable with the existing restore arguments, source
identity gate and trusted manifest hash. It retains its original single-schema
manifest and covers **only** six `cs_plan_actions` tables. Restore reports
`format_version:1` and `coverage:legacy_plan_actions_only`; it does not fabricate
or initialize production contexts. A v1 archive cannot recover a complete
current gateway database. The old source revision is checked against the trusted
manifest; unchanged accepted migration hashes must match the installed checkout.

New exports default to v2 and fail when either required schema is absent.
Explicit `export --format-version 1` is available only for a legacy source with
no context schema. It refuses even an empty `cs_production_contexts`, preventing
silent omission of known saved-context state. V2 reports
`coverage:plan_actions_and_production_contexts`. Changing the format version or
coverage declaration cannot upgrade an old archive. Retain original private
bundles and their externally trusted hashes; never rewrite them in place.

Migration002 rollback archives `cs_production_contexts` inside the same cluster,
which is not an independent backup. The v2 export covers active schemas only;
unexpected custom/archived schemas require an explicit operator decision and
are rejected, never silently included or discarded.

## Run the isolated two-schema rehearsal

Build the unchanged gateway from the isolated checkout with the existing Go
installation/cache; no provider/dependency installation is required:

```sh
cd services/gateway
go build -o ../../.local-tools/state-recovery-gateway .
cd ../..
python3 -m unittest discover -s scripts/tests -p test_local_state_backup.py -v
python3 scripts/check_local_context_state_restore.py \
  --run-dir "$PWD/.local-tools/state-recovery-context-new-run" \
  --postgres-port 55432 \
  --receipt /private/operator/new-sanitized-receipt.json
```

The opt-in harness requires a **new** `.local-tools/state-recovery-*` directory
and verifies task port55432 is free before creating its own SCRAM-authenticated
PostgreSQL17 cluster and synthetic databases. Reference55416, restore55428,
saved-context55430 and all prior archives are preserved. It starts only a task
gateway, a labelled recommendation HTTP fixture and the fixed local Python
comparison bridge. Tokens/passwords/connection files stay private, outside argv
and public receipts. Provider settings are not inherited.

The recommendation precision fixture supplies
`42123456.123456789012345678`; it is storage/transport evidence, not a forecast or
model result. Complete caller-supplied synthetic contexts include
`1000000000000000000.000000000000000001`. Saving them claims accepted syntax,
not numerical feasibility. After export→distinct empty restore→actual PostgreSQL
and gateway restart, all eight complete table contents, saved plan/approval/
receipt/audit HTTP state and complete context snapshot/reference/revision/
metadata must match. Eight concurrent executed-key retries return the original
receipt without a new effect. Changed-content/consumed/stale/viewer/foreign
requests must fail. A restored saved-context comparison is checked against the
entire fixed public Python result; subsequent replacement rejects stale
reference creation, session start and repeated result polls, while cancel/close
remain available. A further gateway restart retains the new context revision.

The rehearsal also exercises real same-database alias, populated/custom-schema/
system-object target, trusted manifest mutation and archive corruption gates;
missing/extra source tables reject export. A separate populated legacy source
exports/restores v1 and compares all six full tables with explicit limited scope.
The accepted `check_local_state_restore.py` remains a legacy-only rehearsal and
now requests `--format-version 1`; its broader offline Python checks are not
rerun by this increment.

The harness stops only its own processes/cluster and removes synthetic credential
files. Private cluster/log/archive artifacts remain for inspection. Public
receipts contain hashes/counts/version/check labels, not rows or credentials.
Unit tests with mocked tools remain distinct from actual local PostgreSQL/HTTP
checks. Cleanup failure is a failed rehearsal, never a passing backup receipt.

**Live original source identity remains required during restore.** This is not a
catastrophic-source-loss recovery path, hosted/online-cutover proof, retention
policy or CloudSQL18 compatibility. Successful restore into an isolated target
is a prerequisite; an operator must still verify the application before any
configuration switch and retain the original source.
