# Local saved synthetic production contexts

The local typed scenario page can retain a complete `ProductionSnapshot` in the authenticated workspace and open a read-only comparison using its current server reference. A legacy saved procurement recommendation does not contain this production graph and is not converted. No missing inventory, BOM, routing, receipt or capacity is inferred.

The editor supplies complete synthetic JSON. Decimal quantities remain strings through the browser, Go and JSONB. The gateway performs strict shape validation, overwrites snapshot/plan identifiers, assigns an opaque context ID and revision, and records the authenticated actor. Stored metadata says `caller_supplied_synthetic`, syntactic validation `accepted`, and numerical acceptance `not_claimed`. Saving unsupported numerical inputs does not produce a feasible plan; the separate fixed Python public tool returns its actual comparison or unsupported result.

## Interfaces and permissions

The authoritative additive version-0.2.0 definitions and examples are in `contracts/openapi.yaml` and `contracts/examples/production_context_*.json`. Generated Go and TypeScript wire types and strict validators follow that source.

| Route | Behaviour |
| --- | --- |
| `GET /v2/workspaces/{workspace_id}/production-contexts` | Authenticated workspace summaries, newest 100, explicit `has_more`. |
| `POST /v2/workspaces/{workspace_id}/production-contexts` | Editor creates a full synthetic context; no numerical acceptance. |
| `GET /v2/production-contexts/{context_id}` | Viewer/editor reads full content in their workspace; unknown/foreign IDs are unavailable. |
| `PUT /v2/production-contexts/{context_id}` | Editor atomically replaces the complete input using the expected revision. |
| `POST /v2/voice/saved-context-sessions` | Viewer/editor opens a read-only ephemeral session using only context ID and expected revision. No snapshot override. |

Existing inline sessions remain available. Model/speech/provider integration, final plan application and durable voice sessions are absent. These context operations persist actual local state; comparison only reads supplied schedules. No action permission or idempotent final application is claimed.

Stale revision is a semantic conflict, foreign contexts are unavailable, and invalid shape/role/database availability has a separate error state. Concurrent replacements with the same expected revision cannot both succeed. Database acquisition has a bounded timeout outside the voice mutex; a short row-share guard surrounds lifecycle acceptance, then releases before response output and numerical execution. Cancellation/close remains possible after revision changes.

The browser re-reads selected content before create/start and before rendering a ready result, validates the returned reference and identity, and uses its existing generation/cleanup guards. Replacement, selection, identity change and unmount retire known sessions. Failed cleanup requires explicit retry; POST requests are never automatically replayed. Revision checks apply at acceptance/read boundaries, not as instantaneous cross-client DOM synchronization. Reload reads persistent contexts but begins a fresh ephemeral session.

## Local setup and acceptance harness

Use a new task-owned loopback PostgreSQL17 cluster and private mode-0700 working directory. Apply migrations `001_plan_actions.up.sql` and `002_production_contexts.up.sql` to that task database. Configure the gateway with `DATABASE_URL_FILE`, `DEV_IDENTITIES_FILE`, loopback `GATEWAY_ADDR`, and explicit `-voice-local-python`/`-voice-local-source` flags. Supply the current source root and an existing local Python environment. Vite uses a private `GATEWAY_DEV_TOKEN_FILE` and same-origin proxy; browser code receives no bearer token.

`scripts/check_voice_saved_context.py verify --gateway http://127.0.0.1:<task-port> --identities-file <private-file> --private-dir <private-directory> --python <absolute-python>` exercises actual authenticated Go/PostgreSQL/Python hand, no-change, precision and unsupported cases against the full public Python result. The synthetic identities file must include `editor`, `viewer` and a `foreign` principal in a separate workspace. The harness writes sanitized hashes/check receipts; it does not automate the browser or call a provider. Its `proxy` mode independently compares mounted browser ready results and records bounded local request events. A private `control.json` can set `delay_ready:true` to delay only response delivery, explicitly a timing fixture, while preserving real numerical execution.

Focused contracts, gateway normal/race tests, frontend type/build/controller tests and actual mounted browser acceptance remain distinct evidence. A mock transport proves only its exercised lifecycle. No local test demonstrates cloud delivery, real-world source rights, model behaviour, speech latency or production operational truth.

## Storage and recovery limits

Migration002 creates the independent `cs_production_contexts` schema. It does not alter the accepted six-table `cs_plan_actions` operator backup schema. **The existing local state export/restore rehearsal does not cover these new context records.** Export coverage for them requires a separately approved increment. Restart persistence within this task database is tested independently; it is not a backup claim.

The down migration archives the context schema rather than deleting it and refuses to overwrite an existing archive; up can restore it. No production migration or provider activation is performed by this package. Local synthetic role tokens are a development mechanism, not end-user production authentication.
