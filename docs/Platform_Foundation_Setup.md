# Run the local recommendation workspace

This package adds a loopback React/Go workspace on top of the existing Python API. It does not start or replace that API, deploy cloud resources, or implement authentication/actions. Keep these services local; do not expose them to untrusted networks.

## Prerequisites

- Node 22.12+ and npm. The implementation was prepared with Node 22.23.1.
- Go 1.27.1 for this workspace. A checksum-verified official archive was installed under ignored `.local-tools/go` during this task; that installation is not committed. A normal official Go installation can be used by setting PATH instead.
- Existing Python API endpoint, configured through `PYTHON_SERVICE_URL`. The reference API is `http://127.0.0.1:8016`; verify it is healthy before starting the gateway. Do not start a duplicate baseline server.

## Gateway

From `services/gateway`:

```sh
export PATH="$PWD/../../.local-tools/go/bin:$PATH"
export GOTOOLCHAIN=local
export GOCACHE="$PWD/../../.local-tools/go-cache"
export GOPATH="$PWD/../../.local-tools/go-path"
go test ./...
go vet ./...
PYTHON_SERVICE_URL=http://127.0.0.1:8016 \
GATEWAY_ADDR=127.0.0.1:8816 \
CONTRACT_EXAMPLES_DIR="$PWD/../../contracts/examples" \
go run .
```

The gateway only calls the configured Python service. Its health endpoint proves gateway liveness; recommendation/catalog results provide the separate connectivity evidence. A legacy recommendation call may write Python's existing recommendation audit record, but cannot approve or send an RFQ. Snapshot versions have no state-application semantics.

## Web

From `apps/web`:

```sh
npm ci
npm run typecheck
npm test
npm run build
GATEWAY_URL=http://127.0.0.1:8816 npm run dev -- --host 127.0.0.1 --port 5316 --strictPort
```

Open `http://127.0.0.1:5316/planning`. Vite proxies relative `/v2` requests to the configured gateway. Browser code receives no Python credentials or provider keys. Changing server URLs is a development configuration operation, not a product input.

## Contracts and fixtures

Run `python3 contracts/generate.py` to regenerate wire types, or add `--check` to detect drift. `contracts/check.py` requires Python `jsonschema` and validates all seven fixtures against OpenAPI; the existing Python virtualenv already supplies it. Use that environment read-only with `PYTHONDONTWRITEBYTECODE=1`, or use a separate local validation environment.

Run `contracts/check_gateway.py` with the same validation environment against the local gateway for HTTP fixtures and semantic errors. Add `--connected` to validate one actual legacy catalog/recommendation; this can create the existing Python audit record.

Consumer examples are synthetic/proposed. The exact high-precision values in `precision.json` are suitable for browser/Go text round-trip checks, but the legacy numerical adapter must reject them. Successful legacy recommendations explicitly disclose rounding limitations and unverified inventory feasibility. Purchase rows must sum to the request quantity and 100% within 0.05 kg and 0.05 percentage points per row, reflecting upstream one-decimal rounding. This structural check does not prove inventory feasibility.

## Boundaries and dependencies

The schema permits arbitrary ISO-shaped currency/country codes; compatibility coverage currently supports IN buyer destination, USD commodity quote, INR settlement/reporting and kg quantities. Unsupported contexts stay visible and unavailable. Unknown material/provider failures must not yield fake recommendations.

PR #9 remains unmerged at the task base. Its full-horizon inventory and backtest fixes were not copied here. Plan snapshots are advisory legacy comparisons, not verified feasible allocations or executable supplier quotes. Source observation dates differ from publication dates; unavailable dates must remain explicit. Google model/speech/provider calls are not verified by this foundation.

`/v2/actions` is not implemented and returns a semantic HTTP 501 error. No stale-plan, idempotency, permission, persistent-state or cloud claim is made. These remain later issue-2 packages. This draft should not close the complete issue.
