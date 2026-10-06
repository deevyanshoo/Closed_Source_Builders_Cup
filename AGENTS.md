# Closed Source agent workflow

Follow the owner's global agent policy when installed and all higher-priority user/system instructions. This file adds project-specific boundaries; it does not change the global policy.

Read `docs/Product_Design.md`, `docs/Work_Plan.md` and the assigned issue before editing. Start from the existing checkpoint and current revision. Preserve Taher's baseline, user changes and commit history. Use feature branches and disjoint file ownership. One coordinator owns shared contracts, migrations, provider mutations and integration.

Use deterministic tools first. Delegate bounded independent implementation and checks to smaller capable models; reserve the coordinator for architecture, integration and risk decisions. Preferred available tiers include gpt-6-luna for mechanical inventory and gpt-6-sol for routine coding. Escalate on demonstrated failure, not by default. Do not build recursive teams or duplicate investigations.

Use Jev only when a bounded structured decision replaces useful model work. Use the existing private credential mechanism; never copy or expose keys. Public/synthetic, non-sensitive normalized inputs only unless specific transmission authorization covers other data. Validate schema and cache reusable results. Jev is advisory and does not replace tests or approve actions.

Give workers one outcome, exact files, contracts, tests and a stop condition. Inspect their diff and evidence. Add failing regression tests before substantive behavior repairs, then verify the fixed behavior and relevant existing suite. Record changes, evidence, unresolved risks and exact artifact paths.

No hard-coded country/currency caps. Keep decimal money, units and source provenance explicit. Keep synthetic fixtures, observed data, forecasts and provider-unavailable states separate. Voice and UI share validated action boundaries; repeated events/approvals cannot duplicate effects.

Never commit secrets or private documents. Do not send messages, create paid plans, change billing/security, expose the repository publicly or make the final hackathon submission without explicit authorization for that action. The reviewed team plan is approved for implementation and publication in this repository.
