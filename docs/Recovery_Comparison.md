# Local receipt recovery comparison

The `/recovery` workspace compares three supplied synthetic schedules through an authenticated gateway and the existing Python public numerical tool. It changes no plan, approval, RFQ, receipt, or customer commitment. `POST /v2/production/receipt-recovery/comparisons` is implemented and read-only. Snapshot/version identifiers are caller consistency guards, not authoritative persisted production versions. The gateway binds all three input workspaces to its server-owned authenticated principal; viewer and editor may compare.

The complete request/response schemas, exact decimal strings and examples live in `contracts/openapi.yaml` and `contracts/examples/recovery*_*.json`. Unknown fields, duplicate JSON keys, trailing JSON, non-JSON media, oversized input/output and mismatched upstream scope/order sets fail closed. Numerical unsupported input is a typed 422 response; absent upstream is generic 503. Browser credentials are never forwarded to Python. Existing private-machine identity and server token transport are reused, with only the exact numerical POST added to the service allowlist. Local development identity is distinct from production human login and observed Google IAM evidence.

The stock example includes shared component reservations, an unaffected order and overlapping maintenance. Delaying 4 kg from P1 to P2 changes orderA's material shortfall from 0 to 4 kg. Supplied A@P2 restores A's coverage but makes its modeled completion 3600 seconds late and leaves B short 3 kg. Capacity and due checks stay separate. The fractional example retains opening stock `1000000000000000000.000000000000000001` and demand `0.02` as strings through React/Go/Python. No global forecast, observed inventory or executable feasibility claim follows from these fixtures.

## Reproduce locally

Use an existing compatible Python environment and Go/Node dependencies. Keep all processes loopback-only and use free task ports; preserve reference servers. From the isolated repository root, with `PYTHONPATH=procurement-engine/src`, run:

```sh
python scripts/check_recovery_comparison_local.py --prepare-runtime .local-tools/recovery-runtime-new
```

This creates a validated synthetic startup cache and 0600 viewer identities/token files in a fresh directory. It does not run ingestion or providers. Set `PROCURE_SERVICE_MODE=local`, `PROCURE_DATA_DIR` to its `cache` directory, `PROCURE_DB_PATH` to its `cache/app.duckdb`, `OFFLINE=1`, unset provider keys, set `PORT=8020`, then start `python -m procure.service_app`. Startup retains normal cache validation; the comparison itself does not read that market cache.

Build/run the gateway with `GATEWAY_MODE=local`, `GATEWAY_ADDR=127.0.0.1:8820`, `PYTHON_SERVICE_URL=http://127.0.0.1:8020`, `DEV_IDENTITIES_FILE` pointing to its private identities file, `DEV_ALLOWED_ORIGIN=http://127.0.0.1:5320` and `CONTRACT_EXAMPLES_DIR` pointing to repository examples. No database URL is required for comparison. Run the existing Vite config with `--port 5320`, `GATEWAY_URL=http://127.0.0.1:8820` and `GATEWAY_DEV_TOKEN_FILE` pointing to its private token file. Open `/recovery`; the development proxy replaces incoming browser bearer headers from that file. Never expose this development proxy externally or commit its credentials/cache.

Run actual connected equality checks:

```sh
python scripts/check_recovery_comparison_local.py --gateway http://127.0.0.1:8820 --identities-file .local-tools/recovery-runtime-new/identities.json --receipt /private/tmp/recovery-local-receipt.json
```

Four responses must equal direct `compare_receipt_recovery` output, including unsupported and exact precision fixtures. This checks local numerical composition, not a Google/model/provider, cloud deployment, durable state, replay or approval. Synthetic injected identity/unit/race checks remain separate from connected proof. SQL trial activation remains held; no provider resources are required.

Relevant checks: `contracts/generate.py --check`, `contracts/check.py`, `contracts/check_recovery_comparison.py`, focused adapter/route tests plus production/recovery numerical tests, Go recovery/auth/transport normal and race tests, frontend typecheck/build/tests and both request-ownership harnesses. Inspect actual browser desktop/mobile success, no-op, validation, unsupported, absent-service, session/workspace change, pending cancel/edit and keyboard/table scrolling. The task report records exact evidence and screenshots.
