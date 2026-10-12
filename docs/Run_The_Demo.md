# Run the demo

How to start Closed Source on one machine and what to click on each page. Checked on main at 497b53e on
2026-10-12 in headless Chrome at 1440 px. Everything runs on 127.0.0.1 and nothing is sent to a supplier.

## Two ways to run it

| | A. Shipment hero launcher | B. Full local stack |
|---|---|---|
| What starts it | One command: Divyanshu's `scripts/run_shipment_hero_demo.py` | Four terminal tabs |
| URL | http://127.0.0.1:5344 | http://127.0.0.1:5318 |
| Pages that work | `/shipment-recovery`, `/offers`, `/dispatch` | All six pages |
| Pages that show "unavailable" | `/planning`, `/recovery`, `/scenarios` (by design: no Python service, database or voice executor) | None (the text proposal step is not built, see below) |
| Use it for | The shipment recovery story | A full walkthrough |

## Data, network and what is not built

| Item | Status |
|---|---|
| Quotes, shipments, ports, hazards, holidays, replay events, production snapshots, shipment contexts | Synthetic. Every page labels them |
| Planning prices and FX | Read from the local price store (`procurement-engine/data/app.duckdb`, not in git), filled by an earlier refresh. The Python service runs with `OFFLINE=1`, so it makes no live call and cannot fill an empty store. `/planning` was checked on a machine whose store was already filled |
| Model calls | None. The text proposal step on `/scenarios` is wired in the browser, but the gateway has no proposal source yet, so it answers "Text proposal unavailable" |
| Speech | Not built. `/scenarios` is typed only |
| Hosting | Not built. Everything is local |
| Carbon and compliance | Not built |
| Comtrade export discovery | Not built |
| Local quote service on `/offers` | Off. It needs a signing key file and a separate offers-only Python service; neither stack starts them |

## Prerequisites

| Tool | Version | Check |
|---|---|---|
| Node and npm | Node 22.12 or later | `node --version` |
| Web packages | from `package-lock.json` | `cd apps/web && npm ci` once |
| Python environment | `procurement-engine/.venv` | `cd procurement-engine && make setup` once |
| Go | 1.27 | `go version` (on a Mac: `brew install go`) |
| PostgreSQL | 17 (stack B only) | `/opt/homebrew/opt/postgresql@17/bin/postgres --version` |
| Git | any | the launcher uses `/usr/bin/git` |

Run every command below from the repository root.

### One-time setup for both stacks

This creates a private development identity for workspace `W` (the workspace used by the shipment catalog), and
fills the Go module cache once, because the launcher turns module downloads off. The token is random, stays in the
ignored `.local-tools/` folder and is never printed.

```sh
mkdir -p .local-tools && chmod 700 .local-tools
( umask 077
  T=$(openssl rand -hex 32)
  printf '{"identities":[{"token":"%s","principal_id":"demo-editor","workspace_id":"W","role":"editor"}]}\n' "$T" > .local-tools/demo-identities.json
  printf '%s' "$T" > .local-tools/demo-browser-token )
( cd services/gateway && mkdir -p .cache/go-build && \
  GOTOOLCHAIN=local GOCACHE="$PWD/.cache/go-build" GOPATH="$PWD/.cache/gopath" go mod download )
```

## A. Start the shipment hero launcher

The launcher refuses to start unless:
- the checkout is clean: no changed or untracked files, and ignored files are fine
- `apps/web` has no `.env` file
- ports 5344 and 8844 are free

It builds the gateway itself in a clean environment with Go module downloads off.

```sh
R="$PWD"
procurement-engine/.venv/bin/python scripts/run_shipment_hero_demo.py \
  --repo "$R" --python "$R/procurement-engine/.venv/bin/python" \
  --go "$(command -v go)" --node "$(command -v node)" --git /usr/bin/git \
  --go-cache "$R/services/gateway/.cache/go-build" \
  --go-mod-cache "$R/services/gateway/.cache/gopath/pkg/mod" \
  --identities-file "$R/.local-tools/demo-identities.json" \
  --proxy-token-file "$R/.local-tools/demo-browser-token" \
  --reviewed-head "$(git rev-parse HEAD)" --profile hero
```

When it prints `http://127.0.0.1:5344/shipment-recovery`, open that URL. Stop it with Ctrl+C in the same terminal.

## B. Start the full local stack

### One-time database setup

This creates a private PostgreSQL 17 cluster in `.local-tools/pgdata` on 127.0.0.1:55416 (TCP only, because the
repository path is too long for a Unix socket), then applies both gateway migrations. The password is random and
only stored in mode 600 files.

```sh
PG=/opt/homebrew/opt/postgresql@17/bin; L="$PWD/.local-tools"
( umask 077
  PW=$(openssl rand -hex 24); printf '%s\n' "$PW" > "$L/pg-bootstrap-password"
  $PG/initdb -D "$L/pgdata" -U plan_actions_dev --pwfile="$L/pg-bootstrap-password" --auth=scram-sha-256 -E UTF8 --locale=C > "$L/initdb.log"
  printf "listen_addresses = '127.0.0.1'\nport = 55416\nunix_socket_directories = ''\n" >> "$L/pgdata/postgresql.conf"
  printf '127.0.0.1:55416:*:plan_actions_dev:%s\n' "$PW" > "$L/pgpass"
  printf 'postgres://plan_actions_dev:%s@127.0.0.1:55416/plan_actions?sslmode=disable\n' "$PW" > "$L/database-url" )
$PG/pg_ctl -D "$L/pgdata" -l "$L/postgres.log" -w start
PGPASSFILE="$L/pgpass" $PG/createdb -h 127.0.0.1 -p 55416 -U plan_actions_dev plan_actions
for m in 001_plans_actions 002_production_contexts; do
  PGPASSFILE="$L/pgpass" $PG/psql -h127.0.0.1 -p55416 -Uplan_actions_dev -dplan_actions -v ON_ERROR_STOP=1 -q -1 -f services/gateway/migrations/$m.up.sql
done
```

Migration 002 prints two harmless transaction warnings.

### Start (four tabs, in this order)

| Tab | Command | Ready when |
|---|---|---|
| 1. Database | `/opt/homebrew/opt/postgresql@17/bin/pg_ctl -D .local-tools/pgdata -l .local-tools/postgres.log -w start` | "server started" |
| 2. Python service | `cd procurement-engine && OFFLINE=1 .venv/bin/python -m uvicorn procure.api:app --host 127.0.0.1 --port 8001` | "Application startup complete" |
| 3. Gateway | see below | no output; `curl -s http://127.0.0.1:8817/ready` shows `"database":"ready"` |
| 4. Web | see below | "Local: http://127.0.0.1:5318/" |

Tab 3, gateway:

```sh
R="$PWD"; cd services/gateway
GOTOOLCHAIN=local GOCACHE="$PWD/.cache/go-build" GOPATH="$PWD/.cache/gopath" \
DATABASE_URL_FILE="$R/.local-tools/database-url" \
DEV_IDENTITIES_FILE="$R/.local-tools/demo-identities.json" \
DEV_ALLOWED_ORIGIN=http://127.0.0.1:5318 \
PYTHON_SERVICE_URL=http://127.0.0.1:8001 GATEWAY_ADDR=127.0.0.1:8817 \
CONTRACT_EXAMPLES_DIR="$R/contracts/examples" \
go run . \
  -voice-local-python "$R/procurement-engine/.venv/bin/python" -voice-local-source "$R/procurement-engine/src" \
  -shipment-local-contexts "$R/scripts/demo/shipment_recovery_catalog.json" \
  -shipment-local-python "$R/procurement-engine/.venv/bin/python" -shipment-local-source "$R/procurement-engine/src"
```

Tab 4, web:

```sh
R="$PWD"; cd apps/web
GATEWAY_URL=http://127.0.0.1:8817 GATEWAY_DEV_TOKEN_FILE="$R/.local-tools/demo-browser-token" \
npx vite --host 127.0.0.1 --port 5318 --strictPort
```

Open http://127.0.0.1:5318/planning.

### Stop

Press Ctrl+C in tabs 2, 3 and 4, then:

```sh
/opt/homebrew/opt/postgresql@17/bin/pg_ctl -D .local-tools/pgdata stop
```

### Common mistakes

| You see | Cause | Fix |
|---|---|---|
| `cd: no such file or directory` | The command was run inside a subfolder | `cd` to the repository root first |
| `Could not set lock on file ... app.duckdb` | Another Python service already has the price store open | Stop the other service; only one can run |
| `Port 5318 is already in use` | An earlier web server is still running | Stop it, or use the one that is running |
| Pages say "Local session unavailable" | The gateway is not running, or the web server was started without `GATEWAY_DEV_TOKEN_FILE` | Start tab 3, restart tab 4 with the token file |

## Pages

The six header links are Planning, Supplier offers, Synthetic dispatch workspace, Shipment recovery evidence,
Synthetic recovery comparison and Typed scenarios.

| Page | What it does | Sample data | Clicks | What you should see | Stack |
|---|---|---|---|---|---|
| `/planning` | Computes a buying schedule for one material from stored prices and forecasts | Values from `contracts/examples/request.json`: buyer `synthetic-buyer`, IN, copper, kg, 50000, stock 20000, monthly 15000, need by month 3, lead time 1, USD, INR, INR, medium | Fill the fields, click **Compute recommendation** | "TEMPORARY PREVIEW · NOT SAVED", **SPLIT BUY** "Buy 40% now, 40% in 1 month and 20% in 2 months", a purchase schedule, a policy comparison, and the warning "Inventory feasibility is not verified". Prices depend on the local price store | B |
| `/offers` | Checks a supplier quote against its source lines | `procurement-engine/tests/fixtures/offers/train/SYN-Q01_atacama_copper_cif.pdf` | **Upload a quote**, pick the file. Click the **incoterm** field name. In **Decision date and payment timing**, set decision date 2026-10-10, bill of lading date 2026-11-06, compare with Q03, rate 8, **Run scenario** | "Atacama Red Metals SpA [SYNTHETIC]", **NEEDS REVIEW**, "settlement currency: missing". Line `p1:l11` highlighted. Schedule 705937.5 USD on 2026-10-10 and 1647187.5 USD on 2026-11-06, "Payment moves 98.1 days later on average", 50595.41 USD | A, B |
| `/dispatch` | Shipment statuses from evidence, hazard exposure and a delay replay | Built-in synthetic shipments | Click **SHP-SYN-002**, then **Port hold at INNSA with a new ETA** | SHP-SYN-002 **On hold**, "2 working days", route evidence "confirmed: restricted". After the replay: arrival 2026-10-12 02:30 UTC → 2026-10-16 02:30 UTC and "CMP-COPPER shortfall 0 → 3000 kg (new shortage)" | A, B |
| `/shipment-recovery` | Runs host-configured shipment recovery comparisons | The four contexts in `scripts/demo/shipment_recovery_catalog.json` | Pick the first context, click **Compare selected context** | "INDEPENDENT SHIPMENT COMPARISONS", "Eligible scenarios · 1", production result compared. The other contexts show "Eligible scenarios · 0", "Excluded events · 1" and "Numerical comparison unsupported: invalid_quantity" | A, B |
| `/recovery` | Compares original, disrupted and recovery production schedules | Example "Stock delay" | Click **Compare schedules** | "THREE SCHEDULES, ONE SET OF INPUTS" with material, capacity and due-date checks for each schedule | B |
| `/scenarios` | The same comparison through the typed voice session route | Example "Receipt arrives late" | First switch the example to "No changes" and back to "Receipt arrives late", then **Run local comparison** | The three-schedule comparison. Without the switch, the first load sends no late arrival (reported on #4) | B |

Two more notes on the pages:
- **`/dispatch` old-data notice:** from 2026-10-09 12:00 UTC on, `/dispatch` shows "Old data: Data cutoff is N hours old. Statuses describe that cutoff, not now." The fixture cutoff is 2026-10-08 12:00 UTC, so N grows every hour. It means the statuses describe what was known at the cutoff, not today.
- **Text proposal on `/scenarios`:** choose **Saved context**, **Load complete inline example into editor**, **Save new context**, select it, type a request and click **Show proposed changes**. The result is "Text proposal unavailable. Use the typed comparison form.", because no proposal source is connected yet.
