# Closed Source

An AI manufacturing and global trade control room connecting procurement, market risk, production, shipment recovery, export opportunities and sustainability around the same orders and evidence.

The project starts from Taher Afsar's procurement prototype. Its history is preserved at tag `baseline-2026-10-06` (commit `223f99b7efa3164843eed1c46530ed83c5412ba3`). New modules build on that work.

## Team plan

- [Daily work plan and domain ownership](docs/Work_Plan.md)
- [Product design and acceptance gates](docs/Product_Design.md)
- [Voice architecture research](docs/Voice_Architecture.md)
- [Public API integration priorities](docs/API_Integration_Guide.md)
- [Implementation tickets](https://github.com/deevyanshoo/Closed_Source_Builders_Cup/issues)
- [Existing engine setup and reference interface](procurement-engine/README.md)

Divyanshu leads platform, markets, planning, voice engineering, operational propagation and cloud integration. Taher leads quote intelligence, shipment ingestion and dispatch workflows, and route evidence. Both own complete features, tests, interfaces and cross-review.

## Current status

The team roadmap is approved for implementation. The existing Python engine and Streamlit reference interface are the baseline. React/TypeScript, the Go workflow gateway and the expanded operational modules are planned work; their presence in the roadmap does not mean they are implemented.

Use explicit source dates and distinguish observations, forecasts and synthetic operational examples. Do not claim customer savings, global source completeness, supplier transactions or regulatory certification from generated scenarios.

## Contributing

Start from the latest `main`, create a feature branch, and follow [CONTRIBUTING.md](CONTRIBUTING.md). Keep shared contracts and migrations under one integration owner; use pull requests for feature changes. Do not overwrite another contributor's branch or force-push shared history.
