# Run the local saved-plan and RFQ-draft workspace

This increment adds PostgreSQL persistence and a local draft receipt. It sends no supplier message and places no order. The existing temporary recommendation API remains available. Follow `Platform_Foundation_Setup.md` for its coverage and numerical precision limits.

## Development configuration

Use Node22.12+, Go1.27.1 and PostgreSQL17. Keep all three new listeners on loopback. The task uses gateway8817, web5317 and PostgreSQL55416 to avoid the reference services. Python8016 is an existing service: verify it, do not start another reference server. A source checkout revision does not prove the revision loaded by an older running Python process.

The gateway requires these private configuration files, outside tracked source:

- `DATABASE_URL_FILE`: one PostgreSQL connection URL. For the task, `.local-tools/database-url`.
- `DEV_IDENTITIES_FILE`: JSON `{"identities":[{"token":"<random-secret>","principal_id":"local-editor","workspace_id":"local-workspace","role":"editor"}]}`. Roles are `editor` or `viewer`. Use strong temporary tokens and file mode0600; do not put real credentials in examples or reports.
- `DEV_ALLOWED_ORIGIN`: the exact web Origin, for example `http://127.0.0.1:5317`.
- `PYTHON_SERVICE_URL`, `GATEWAY_ADDR` and `CONTRACT_EXAMPLES_DIR`: service and fixture locations.

Vite reads the selected development token privately through `GATEWAY_DEV_TOKEN_FILE` and adds it on its server-side proxy. Never use a `VITE_` variable for credentials, put tokens in client storage, or pass a token in a URL. These identities prove the local authorization boundary only; they are not production Google/OAuth authentication. Local editor authority is configured by the developer running the loopback process. An untrusted browser Origin cannot authorize state changes.

## Task-owned database and migration

The task cluster lives under ignored `.local-tools/pgdata`, runs PostgreSQL17.11, and has database `plan_actions` on `127.0.0.1:55416`. It is not a macOS service. Do not initialize over this directory or touch unrelated databases. Its bootstrap role is a development role, not a production privilege configuration. The task configuration persists `listen_addresses=127.0.0.1`, `port=55416` and its private socket directory in `postgresql.conf`; restart must retain these settings rather than relying on one-time launcher flags.

For an already initialized task cluster, start only if it is stopped:

```sh
/opt/homebrew/opt/postgresql@17/bin/pg_ctl -D "$PWD/.local-tools/pgdata" status
/opt/homebrew/opt/postgresql@17/bin/pg_ctl -D "$PWD/.local-tools/pgdata" -l "$PWD/.local-tools/postgres.log" start
```

Apply or safely reapply migration001 from the repository root using the private password file:

```sh
PGPASSFILE="$PWD/.local-tools/pgpass" /opt/homebrew/opt/postgresql@17/bin/psql \
  -h127.0.0.1 -p55416 -Uplan_actions_dev -dplan_actions \
  -v ON_ERROR_STOP=1 -1 -f services/gateway/migrations/001_plans_actions.up.sql
```

For rollback, stop gateway writers first and apply `001_plans_actions.down.sql` with the same transactional command. It renames the schema to `cs_plan_actions_archived_v1`, retaining records; it refuses to overwrite an existing archive. Apply the up migration to restore that archive. Do not delete application records as a rollback method. Readiness reports unavailable while the active schema is absent. Future migration changes need their own reversible path; migration001 is not a general backup strategy.

## Gateway and web

From `services/gateway`, use the verified local Go toolchain or an official system installation. In this task the toolchain is in the sibling foundation worktree; it is read only.

```sh
export PATH="$PWD/../../../Closed_Source_platform_foundation/.local-tools/go/bin:$PATH"
export GOTOOLCHAIN=local
export GOCACHE="$PWD/.cache/go-build"
export GOPATH="$PWD/.cache/gopath"
export GOMODCACHE="$GOPATH/pkg/mod"
DATABASE_URL_FILE="$PWD/../../.local-tools/database-url" \
DEV_IDENTITIES_FILE="$PWD/../../.local-tools/dev-identities.json" \
DEV_ALLOWED_ORIGIN=http://127.0.0.1:5317 \
PYTHON_SERVICE_URL=http://127.0.0.1:8016 GATEWAY_ADDR=127.0.0.1:8817 \
CONTRACT_EXAMPLES_DIR="$PWD/../../contracts/examples" go run .
```

From `apps/web`:

```sh
npm ci --cache .cache/npm --no-audit --no-fund
GATEWAY_URL=http://127.0.0.1:8817 \
GATEWAY_DEV_TOKEN_FILE="$PWD/../../.local-tools/browser-token" \
npm run dev -- --host 127.0.0.1 --port 5317 --strictPort
```

Open `http://127.0.0.1:5317/planning`. Compute a temporary preview or save a freshly computed server snapshot. Select a saved plan, review its revision and RFQ quantity/unit/currencies/destination, confirm, then prepare the local draft. The receipt remains a draft even after successful execution. The confirmation applies to one snapshot revision and exact arguments; revise/reload and confirm again after a stale conflict. Retry an uncertain execution with the same idempotency key. A committed identical retry returns the original receipt even after a later revision. A changed request with that key fails.

`/health` proves process liveness. `/ready` separately checks database/schema and configured development identity; provider status remains `not_verified`. A healthy process/database is not evidence of a Google model or speech call.

## Checks and evidence

Run Go tests with `PLAN_ACTIONS_TEST_DATABASE_URL_FILE` pointing to the private URL file. Transaction tests create isolated schemas in the task database. An unset variable skips real-database tests, so a plain passing Go command alone is insufficient persistence evidence. Run `go vet ./...` and the race suite with the real database configured. Run web tests, typecheck and build; validate contract fixtures and generation drift.

`contracts/check_gateway.py --url http://127.0.0.1:8817 --connected` checks preserved preview compatibility. `contracts/check_plan_actions.py create` checks actual HTTP/Python/PostgreSQL writes, eight concurrent retries, conflict/stale/denied states and one-effect audit. Pass `--identities-file .local-tools/dev-identities.json --checkpoint .local-tools/restart-checkpoint.json`. Restart only the task gateway/database and run the same script with `verify` to check persisted snapshots and replay. This script creates synthetic development records and can write the preserved Python recommendation audit record. Keep raw server logs and credentials private.

Browser screenshots and the exact tested revision/DB/ports belong in the task report. Mocked numerical responses, actual Python connectivity and real provider results are distinct. Saved snapshots remain advisory; soft shortage scoring does not certify inventory feasibility. Initial coverage is India destination, USD commodity quote, INR settlement/reporting and kg; other contexts return explicit unavailability. Decimal strings survive the state boundary, while the existing Python adapter has documented binary-float/rounding limits.

No cloud resources are created here. The approved later hosting target is Mumbai (`asia-south1`), with co-located app/database/storage where supported; exact free-trial and model/speech regional eligibility must be verified before any provisioning. A US/global endpoint is not evidence of Mumbai hosting.

For operator-only export, empty-target restore and rollback rehearsal, see [Local State Recovery](Local_State_Recovery.md). Cloud SQL activation remains held.
