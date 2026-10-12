# Run the offers local quote service

How to run `/offers` in **Local quote service** mode (#84, #88), so quotes are uploaded, corrected and saved through the
gateway into the offers-only Python service instead of staying in the browser tab. Checked on main at 497b53e on
2026-10-12: synthetic Q01 was uploaded, corrected and saved through the gateway.

Everything stays on 127.0.0.1. The offers-only service forces `OFFLINE=1`, turns `.env` loading off and removes
`*_API_KEY` variables from its own environment, so it makes no provider or model call.

## What runs

| Part | Address | Role |
|---|---|---|
| Offers-only service | 127.0.0.1:8018 | `procurement-engine/serve_offers_local.py`: rules extraction, review and save, with its own DuckDB file. Refuses any request without a valid signature (HTTP 403) |
| Gateway | 127.0.0.1:8817 | Signs each `/v2/offers` request for the logged-in development identity and forwards it to the service |
| Web | 127.0.0.1:5318 | `/offers`; the browser never sees the signing key or a bearer token |

The shipment hero launcher (`scripts/run_shipment_hero_demo.py`) does not start this mode.

## The signing key

The gateway and the service share one random signing key. It is a local synthetic secret for this machine, not a
provider key. Keep it **outside the repository**; the service also needs a config folder that contains it.

| Rule | Why |
|---|---|
| Folder outside the repo, for example `$HOME/.closed-source-offers-local` | It can never be committed |
| Key file `config/quote-hop.key`, 32 random bytes, mode 600, one link | The gateway refuses any other file mode, size or a symbolic link; the service requires the key inside its config folder |
| `data/` holds `offers-only.duckdb` | The service requires exactly this database path |

## One-time setup

From the repository root:

```sh
OL="$HOME/.closed-source-offers-local"
( umask 077
  mkdir -p "$OL/config" "$OL/data"
  cp procurement-engine/config/* "$OL/config/"
  head -c 32 /dev/urandom > "$OL/config/quote-hop.key"
  chmod 600 "$OL/config/quote-hop.key" )
```

The config copy holds the repository's synthetic configuration (materials, locations, thresholds), which extraction
reads. The development identity files in `.local-tools/` are the ones from [Run_The_Demo.md](Run_The_Demo.md) if that
guide is merged; otherwise create them as below (workspace `W`, never printed):

```sh
mkdir -p .local-tools && chmod 700 .local-tools
( umask 077
  T=$(openssl rand -hex 32)
  printf '{"identities":[{"token":"%s","principal_id":"demo-editor","workspace_id":"W","role":"editor"}]}\n' "$T" > .local-tools/demo-identities.json
  printf '%s' "$T" > .local-tools/demo-browser-token )
```

## Start (three tabs, from the repository root)

Tab 1, offers-only service:

```sh
OL="$HOME/.closed-source-offers-local"; cd procurement-engine
.venv/bin/python serve_offers_local.py --quote-key-file "$OL/config/quote-hop.key" \
  --offers-db-path "$OL/data/offers-only.duckdb" --data-dir "$OL/data" --config-dir "$OL/config" \
  --host 127.0.0.1 --port 8018
```

Ready when it prints "Application startup complete". `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8018/offers`
prints `403`, which is correct: unsigned requests are refused.

Tab 2, gateway with the two quote flags:

```sh
R="$PWD"; OL="$HOME/.closed-source-offers-local"; cd services/gateway
GOTOOLCHAIN=local GOCACHE="$PWD/.cache/go-build" GOPATH="$PWD/.cache/gopath" \
DEV_IDENTITIES_FILE="$R/.local-tools/demo-identities.json" \
DEV_ALLOWED_ORIGIN=http://127.0.0.1:5318 \
GATEWAY_ADDR=127.0.0.1:8817 CONTRACT_EXAMPLES_DIR="$R/contracts/examples" \
go run . -quote-local-key-file "$OL/config/quote-hop.key" -quote-local-upstream http://127.0.0.1:8018
```

The gateway exits with "invalid local quote configuration" if only one of the two flags is given. This minimal
gateway serves `/v2/offers`; its `/ready` reports 503 because no database is configured, which is expected here. To
use the other pages too, add the database, Python service, voice and shipment settings from stack B in
Run_The_Demo.md to the same command; both the minimal and the combined command were checked.

Tab 3, web:

```sh
R="$PWD"; cd apps/web
GATEWAY_URL=http://127.0.0.1:8817 GATEWAY_DEV_TOKEN_FILE="$R/.local-tools/demo-browser-token" \
npx vite --host 127.0.0.1 --port 5318 --strictPort
```

## Use it

| Step | Click | You should see |
|---|---|---|
| 1 | Open http://127.0.0.1:5318/offers and click **Local quote service** | "LOCAL QUOTE SERVICE · DEVELOPMENT ONLY", "editor · workspace W", "No service offers in this workspace." |
| 2 | Tick **I confirm this is synthetic test material.** | The upload field is enabled |
| 3 | Upload `procurement-engine/tests/fixtures/offers/train/SYN-Q01_atacama_copper_cif.pdf` | "Synthetic quote uploaded for review.", "Atacama Red Metals SpA [SYNTHETIC]", **NEEDS REVIEW**, "Save is blocked until these are resolved: settlement currency: missing". **Save offer** is disabled |
| 4 | Click **Edit** on settlement currency, type `USD` in **ISO currency**, click **Apply correction** | The field shows USD as a reviewer entry; **Save offer** is enabled |
| 5 | Click **Save offer →** | "Offer saved in the local service.", status **SAVED**, version 3; the workspace list shows "saved · version 3" |

Unlike the default **Synthetic fixtures** mode, the save here is checked by the service, so a corrected currency does
not leave "revalidation required" behind. Saved offers stay in `$OL/data/offers-only.duckdb` across restarts.

## Stop and reset

Press Ctrl+C in each tab. To start again with no offers, stop the service and delete `$OL/data/offers-only.duckdb`.
To replace the key, stop the service and the gateway, then write a new `quote-hop.key` the same way. To remove
everything, delete `$HOME/.closed-source-offers-local`.

## What this does not establish

- **Synthetic only:** synthetic quotes only. The upload asks you to confirm synthetic material.
- **Not production auth:** development identities from a local file, not production sign-in.
- **No hosting:** the gateway route is a local development connector and is off unless both flags are passed.
