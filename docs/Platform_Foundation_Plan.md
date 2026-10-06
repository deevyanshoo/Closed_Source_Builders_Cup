# Local platform foundation implementation plan

Spec: approved issue-2 package in platform-kickoff.md, Product_Design/Work_Plan revision 3. Base f4a8ee9, feature/platform-foundation. Coordinator owns contracts and integration; consumers receive frozen schemas before writing. This package made no numerical or domain code changes and kept PR #9 separate during development. PR #9 and PR #10 were subsequently integrated into `main` after combined checks under the user's standing merge authorization.

## Design and contract decisions

Use a local-only, loopback Go compatibility gateway and a React Router/Vite workspace. Decimal strings remain strings through browser/Go; legacy numeric JSON is read as numeric tokens and rewritten as strings, never decoded through float64. The legacy Python engine still computes with floats and rounded outputs. Reject unsupported precision and currency/unit contexts explicitly. A snapshot version identifies content; it does not enforce state, permissions or repeated actions. Actions remain disabled, and offer/voice/shipment examples are proposed synthetic contracts.

Design direction: industrial UI skill, Swiss print light palette, flat divided workspace, concise form and recommendation/evidence sections. No fake operational modules or implementation details in primary product copy. Show actionable unavailable/error/empty states and a clear inventory-feasibility limitation on results.

## Task 1: Coordinator contracts

- [x] Publish OpenAPI, examples, generation/check scripts and precision/coverage limitations locally.
- [x] Generate TypeScript/Go wire types; validate fixtures; freeze consumer interface.

## Task 2: Go consumer

Files: services/gateway only, excluding coordinator-generated internal/contract/types.go.
Consume generated structs and OpenAPI. Standard-library HTTP server, configurable Python URL, loopback defaults, bounded timeouts/body limits, strict validation. No browser-supplied upstream URL. Compute-only adapter plus catalog/fixture reads; no state/actions or arbitrary Python proxy.
Tests first: mismatch units/budget currency, unknown/unsupported context, malformed 200 upstream, unavailable service, oversized/unknown JSON, exact fixture numeric strings, and real request adaptation against synthetic httptest. Run go test ./... and go vet ./... with local Go/cache settings. Worker does not start shared servers or commit.

## Task 3: React consumer

Files: apps/web only. Consume generated types from contracts/generated/types.ts. Build React Router/Vite SPA, relative /v2 fetch through configurable dev proxy, all monetary/quantity input values retained as strings. Dynamic material catalog; buyer/destination, currency/unit and demand/stock form; recommendation comparison, evidence dates and warnings; accessible loading/validation/unavailable/empty/retry states. Clear old results when context changes or recomputation fails. No mocked operational modules.
Write focused request serialization/response state tests before implementation; prove exact strings survive JSON. Run typecheck/tests/build. Official registry dependencies may be installed only here. No shared server or browser session mutations by worker.

## Task 4: Integration and delivery

Coordinator verifies one gateway/frontend server on unused loopback ports 8816/5316, reuses healthy baseline Python at 8016, inspects actual browser workflow and screenshots success/errors/mobile. Distinguish mocked browser states from connected Python evidence. Validate exact fixtures through Go and browser serialization. Obtain one bounded review, address blocking findings, verify author, commit/push/create draft PR. Full issue 2 remains open: this package excludes actions/state/authentication/cloud/voice.

## Review focus

Semantic 200 errors and invalid source responses; decimal loss or unsafe legacy precision; mismatched currencies/units and uncapped unsupported codes; stale displayed results after edits/failure; accurate provenance and remaining numerical limitations. User supplies approval to continue this bounded design; no additional design approval gate.
