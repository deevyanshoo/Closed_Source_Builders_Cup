# ZOORQ — A product by ClosedSource

Understand which supplier delays and world events could affect your next purchase, then prepare a recovery plan in the same company workspace. ZOORQ connects procurement, inventory, news, routes and emissions with a text and voice operator.

**[Open the hosted demo](https://zoorq-963802031195.asia-south1.run.app/demo)** — no signup required. Start in the filled Solara Cables workspace or choose from ten company profiles.

Gemini interprets questions and news; deterministic tools calculate inventory, sourcing, recovery and emissions. Vertex AI provides the model runtime, Cloud Run hosts the app, Firestore persists workspaces and chats, GCS stores hourly news snapshots, and Google Maps displays supported route corridors.

The project starts from Taher Afsar's procurement prototype. Its history is preserved at tag `baseline-2026-10-06` (commit `223f99b7efa3164843eed1c46530ed83c5412ba3`). New modules build on that work.

## Team plan

- [Daily work plan and domain ownership](docs/Work_Plan.md)
- [Product design and acceptance gates](docs/Product_Design.md)
- [Voice architecture research](docs/Voice_Architecture.md)
- [Local typed synthetic scenario workspace](docs/Voice_Local_Browser.md)
- [Public API integration priorities](docs/API_Integration_Guide.md)
- [Implementation tickets](https://github.com/deevyanshoo/Closed_Source_Builders_Cup/issues)
- [Existing engine setup and reference interface](procurement-engine/README.md)

Divyanshu leads platform, markets, planning, voice engineering, operational propagation and cloud integration. Taher leads quote intelligence, shipment ingestion and dispatch workflows, and route evidence. Both own complete features, tests, interfaces and cross-review.

## Current status

The hosted product uses the React/TypeScript workspace and Python numerical/Google AI service. All ten company profiles have reviewed quote, procurement-plan, delay-recovery and unsent RFQ workflows. The operator can read connected company data, calculate scenarios and prepare work; external research requires reviewed consent, and consequential changes retain their action review.

See the [release QA report](docs/ZOORQ_QA_REPAIR_20261017.md) for exact revisions, tested paths and remaining acceptance limits, and the [deployment/operator guide](deploy/judge-demo/README.md) for runtime boundaries. The Go gateway and broader production, trade and maintenance roadmap are separate from the hosted release; roadmap items are not implementation claims.

Company operations and quote inputs are illustrative demo records. Public news is dated, route displays include schematic itineraries, and emissions depend on available factor coverage. RFQs are unsent; email actions only record a simulation and do not contact suppliers.

Use explicit source dates and distinguish observations, forecasts and synthetic operational examples. Do not claim customer savings, global source completeness, supplier transactions or regulatory certification from generated scenarios.

## Contributing

Start from the latest `main`, create a feature branch, and follow [CONTRIBUTING.md](CONTRIBUTING.md). Keep shared contracts and migrations under one integration owner; use pull requests for feature changes. Do not overwrite another contributor's branch or force-push shared history.
