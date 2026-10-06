# Working together

The [daily plan](docs/Work_Plan.md) defines domain ownership and daily acceptance. Select a bounded issue, record intended files and dependencies, then branch from the latest `main` with `feature/<topic>` or `fix/<topic>`.

Shared contracts and migrations have one integration owner. Coordinate changes before implementation; use agreed examples to develop independently. Never force-push shared branches. Preserve the original prototype and its commit history.

Each pull request describes the user-visible change, relevant tests, source/data assumptions and remaining limitations. Add meaningful regression tests for substantive behavior changes. Verify numerical constraints with hand-solvable cases and interfaces with actual workflow checks. Keep provider-mocked tests separate from successful real-provider calls.

Use synthetic fixtures for quotes, suppliers, plants and shipments until authorized customer data exists. Label them in the UI and exports. Never commit credentials, private documents, local databases, raw user logs or machine-specific paths.

Do not send supplier communications, place orders, create paid subscriptions, change billing, make the repository public or submit to the competition through a feature task. These actions need their own authorization. Routine implementation, focused tests and reviewable branches proceed within the approved plan.

For a work update, record the branch/revision, checks performed, remaining dependency and next action in the relevant issue or pull request. Completion means verified behavior, not the presence of files or a successful mock.
