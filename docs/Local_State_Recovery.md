# Local saved-state export and restore

This operator-only rehearsal exports the existing `cs_plan_actions` schema and
restores it into a **different empty local database**. It preserves saved
snapshots, exact decimal strings, ownership, revisions, approvals, RFQ draft
receipts and audit rows. Restore never recomputes a recommendation or executes
an action. The CLI has no browser upload or remote restore surface.

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
   the custom dump and all six table counts/full-row digests share one exported
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
   manifest records schema revision, migration hash, source revision, actual
   source cluster/database identity, exact tool versions, archive hash and all
   six table counts/digests; it does not contain URLs, passwords or raw rows.
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

## Run the isolated rehearsal

Run from an isolated checkout. Build the unchanged gateway using the existing
Go toolchain and module cache; no dependency/provider installation is required:

```sh
cd services/gateway
go build -o ../../.local-tools/state-recovery-gateway .
cd ../..
python3 -m unittest discover -s scripts/tests -p test_local_state_backup.py -v
python3 scripts/check_local_state_restore.py \
  --run-dir "$PWD/.local-tools/state-recovery-new-run" \
  --postgres-port 55428 \
  --receipt /private/operator/new-sanitized-receipt.json
```

The harness requires a **new** `.local-tools/state-recovery-*` directory and a
free loopback port other than the protected55416 cluster. It starts a new
SCRAM-authenticated PostgreSQL cluster, five synthetic task databases, a task
gateway and the existing Python runtime read-only from the baseline checkout's
`.venv/bin/python`. Python code/config comes from the current checkout, with
`OFFLINE=1`, an empty model key and private data/audit paths. Existing clusters
and reference services are untouched.

One plan is saved through actual offline Python. A separate explicitly labelled
legacy HTTP fixture supplies the hand-constructed exact value
`42123456.123456789012345678`; that fixture proves storage/transport precision,
not numerical/model correctness. Both use real gateway HTTP/PostgreSQL. The
harness exports, rejects alias/populated/custom-schema/corrupt restores, restores into the
empty target, restarts only task services, compares every row and HTTP history,
and exercises eight concurrent receipt replays plus semantic denial cases.

The harness stops only its own child processes/cluster and removes its
synthetic credential files. It retains private clusters/logs/bundles for
inspection. Its public receipt reports counts, digests, versions and check
labels; deterministic mocked tooling tests are distinct from this real local
rehearsal. A successful local run does not prove Cloud SQL backups, PostgreSQL18
compatibility, disaster recovery retention policy or provider permissions.
