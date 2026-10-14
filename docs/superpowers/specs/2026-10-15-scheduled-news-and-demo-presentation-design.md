# Scheduled news and natural demo presentation

Date: 2026-10-15
Status: Approved by the user on 2026-10-15 for implementation, testing and deployment using parallel agents in Antigravity, subject to the additional free-tier-only constraint below.

## Approved free-tier-only constraint (supersedes conflicting initial settings)

User clarification on 2026-10-15: **Existing trial credits are okay; no out-of-pocket charges.** This supersedes the original strict free-allowance-only hold below. Activation is allowed while the billing account is verified to remain an unupgraded active Free Trial with available credit; no paid-account upgrade is authorized. Continue to use the bounded eligible-region configuration to conserve credit. Google's Free Trial auto-closes rather than charging unless manually upgraded: https://cloud.google.com/signup-faqs.

The user explicitly requested no new cost from scheduling. Trial credits are not free-tier usage. Prepare and test the complete feature, but activate recurring infrastructure only after its free allowance headroom and bounded usage are verified. If that cannot be established, leave scheduling inactive and report the exact blocker; the independent natural-wording release may proceed under existing deployment authorization.

Use `us-central1` for the news job and dedicated Standard Storage bucket, because Mumbai Storage has no Always Free allowance. Keep the existing portal in Mumbai. Limit the worker to 120 seconds, one task, 1 vCPU, 512 MiB, zero job retries and zero Scheduler retries, with no automated Gemini calls. At 744 hourly executions in a 31-day month the configured execution upper bound is 89,280 vCPU-seconds and 44,640 GiB-seconds before any account-shared usage; retries, manual invocations and regional pricing discounts must be assessed separately.

Storage Always Free currently includes 5 GB-months, 5,000 Class A operations, 50,000 Class B operations and 100 GB of eligible North American data transfer. Cap snapshot payloads at 2 MiB and retain seven days. Use one deterministic hourly object name with generation-zero conditional creation so the first valid publication wins atomically; use bounded object reads rather than listing on every portal request. Pause/inhibit deployment activation on unknown shared quota headroom. Google does not provide a universal hard zero-spend cap; forecasts or budget alerts alone are not a zero-cost guarantee.

References: https://cloud.google.com/storage/pricing, https://cloud.google.com/run/pricing, https://cloud.google.com/scheduler/pricing.

## Intended outcome

Refresh relevant public news for all ten company profiles several times a day, even when the portal is closed. Keep the existing useful procurement answers, analytics, confidence and recovery recommendations, while removing repetitive fixture terminology from normal conversation. Preserve a clear demo disclosure, accurate provenance on request, and explicit simulated/unsent action outcomes. Keep Git and deployment evidence reconciled during every release.

The requested cadence is multiple times per day. This proposal chooses hourly refresh, on the hour in UTC, to cover global news without creating ten independent schedules. The existing portal remains the destination.

## Verified starting point

- Google Cloud trial is active; authenticated console displayed INR 28,426.33 credit and 65 days remaining. No signup or billing upgrade is needed for this proposal.
- Cloud Run service `closed-source-quote-demo` in `asia-south1` serves revision `closed-source-quote-demo-00022-wgm` at 100% traffic.
- Deployed application source is `c3b86d2f5668903f137e2c981eee6357738bb67d`, preserved remotely as annotated tag `deploy/demo-20261015-orbs`.
- Feature branch documentation head before this proposal is `9956bf99758fd3ba7b08500766231d8af8e846a4`. Draft PR #106 preserves the hosted feature; it is not merged into main.
- `NewsService` stores public-feed and company-query results in memory with a 15-minute TTL. Monitoring runs while the workspace is open. Cache state does not survive process replacement.
- The Cloud Scheduler API is disabled in this project. Existing jobs elsewhere in the billing account have not been inventoried; free quota availability is not assumed.
- Local main was fast-forwarded to remote main `497b53e14bcf7790606c173cb1c184e7e81d89df`; deleted upstream tracking references were pruned. No remote branches or history were deleted.

## Approaches considered

1. **Recommended: Scheduler -> Cloud Run Job -> private Cloud Storage snapshots.** One bounded batch refresh uses the existing feed adapters and company mapping, then publishes durable snapshots for the existing portal. Separates background ingestion from visitor sessions and keeps permissions narrow.
2. **Authenticated HTTP refresh on the existing service.** Less deployment plumbing, but a refresh route inside the publicly invoked service introduces endpoint authentication and long request handling concerns. Without durable storage it also loses cache results after scaling or redeployment.
3. **Always-on timer inside the web service.** Fewest new resources, but requires allocated background CPU/minimum instances and careful single-writer coordination. It couples scheduling to application process lifetime and costs more continuously.

## Selected design

### Refresh worker and schedule

- New Cloud Run Job `closed-source-news-refresh`, same project and region, one task, parallelism one, 1 vCPU and 512 MiB initially, maximum execution duration ten minutes, no automatic job retries initially.
- New Cloud Scheduler job `closed-source-news-hourly`, hourly UTC schedule `0 * * * *`, one retry for a transient trigger failure. The target is the Cloud Run Jobs v2 `:run` API with OAuth using a dedicated scheduler identity; it is not a public application endpoint.
- Fetch the global feed/FX context once and each company's specific public queries once per run. Reuse the existing relevance, deduplication, source-health and implication contracts. Explicitly test the current `force`/TTL behavior so iteration over ten companies does not refetch global feeds ten times or skip a requested background refresh.
- Persist only public headline metadata, source links, timestamps, FX observations and deterministic company mapping. Do not persist visitor sessions, uploaded quotes, emails, credentials, voice data or private business data.
- Automated collection does not invoke Gemini for every headline. Existing on-demand AI explanations remain available with their source and confidence details. Do not downgrade existing analytical quality or invent public observations when sources fail.

### Durable snapshots

- One new regional private Cloud Storage bucket dedicated to news; uniform bucket-level access and public access prevention. Seven-day object lifecycle retention.
- Immutable versioned JSON bundles under `snapshots/`, ordered by scheduled UTC slot and completion timestamp. Each bundle contains schema version, run ID, started/completed timestamps, catalog version, all ten company IDs, provider health and each company's news/FX view.
- Keep last attempted refresh separate from last successful provider observation, with separate news and FX success timestamps for each company/provider. Preserve item publication, first-seen and original source-check timestamps when carrying old observations forward.
- Worker loads the previous valid bundle to preserve usable observations during partial failures. A failed company refresh can retain prior data with stale status; a missing prior view is explicitly unavailable. An empty successful response is distinguished from a failed provider.
- Publish an attempt record for failures. Publish a complete validated bundle only when every company has a valid fresh, stale or unavailable view and at least one news provider returned a successful current response. FX success alone cannot count as news success. All-news-provider failure must not advance any news-success timestamp; attempt records still report independent FX health. Overall collection status cannot conceal unavailable or stale company views.
- Duplicate/overlapping executions may publish separate immutable bundles; readers select the newest scheduled slot, then the earliest successfully created valid complete object within that slot using Storage's server creation timestamp (object name breaks ties). The first valid published bundle wins for a slot; a later weaker duplicate cannot replace it. A slower older slot cannot replace a newer slot. No mutable latest pointer or delete/overwrite permission is required.

### Portal integration

- Existing news endpoints, company context guards and risk/agent consumers use a shared snapshot reader. Cache storage lookups briefly and bound list/read work; never load unbounded history.
- Use the newest schema-compatible, catalog-compatible complete bundle. If corrupt or missing, fall back to the previous valid bundle. If no stored bundle is usable, retain the existing live adapter with truthful health state.
- News workspace shows per-company successful news collection, separate FX success, last attempt, source failures and freshness. Mark a company/provider's data stale when its last successful source check is more than two hours old, even if newer scheduled attempts ran on time. Bundle completion time never makes carried-forward observations fresh. Retain the current public source age limits for article relevance independently.
- Keep the existing user-triggered refresh route and rate limits. Manual refresh can fetch live results for the visitor, but it does not imply that the scheduled portfolio bundle was updated. Its timestamps remain distinct.
- Background ingestion continues without an open browser. Personalized proactive risk explanations still depend on the active workspace/session; this change does not promise emails or closed-browser notifications.

### Scoped infrastructure permissions

- Enable the Cloud Scheduler API.
- Worker identity `closed-source-news-worker`: object creator and object viewer on only the dedicated news bucket. No Vertex prediction, mail, private file or project-wide storage permissions.
- Scheduler identity `closed-source-news-scheduler`: Cloud Run Invoker on only the new job; token generation uses Google's normal Scheduler service agent integration.
- Existing app identity receives object viewer on only the news bucket. Preserve its existing model permissions.
- Bucket remains private; no anonymous access, service-account keys or new unauthenticated maintenance endpoints.
- Actual globally unique bucket name is checked before creation and recorded in the release receipt. Resource creation is idempotent and checks existing state before retrying.

### Natural bot presentation

- Default text and voice responses speak directly about the selected workspace: quantities, suppliers, inventory cover, delays, confidence and recommended actions. Remove repeated `Source: fictional company profile` suffixes and spoken `[SYNTHETIC]` tokens.
- Keep one clear product-level demo disclosure explaining that company operational records are sample data and public news comes from connected public sources, with freshness shown and stale/unavailable states possible. If asked where records came from, answer accurately. Do not claim suppliers or inventory were verified from a real company's systems.
- Introduce a presentation-only supplier label formatter shared by relevant UI surfaces and assistant-facing display context. Preserve canonical supplier names, IDs, quote records, recipient validation and tool arguments internally.
- Preserve provenance fields and evidence links in the underlying response. Real public news and sample operational inputs remain distinguishable in source details.
- Draft message content can read like an ordinary business draft. The review card, outbox and action result must still say draft/unsent or simulated delivery, as appropriate. Never state that an email/RFQ was actually sent when it was only simulated.
- Keep all numerical calculations, confidence levels, currencies, units, material coverage, recovery logic and company-session isolation unchanged.

### Git and release hygiene

- One coordinator owns integration, external changes and deployment. Workers have disjoint file ownership or isolated worktrees.
- Before and after each task, record active branch, source commit, changed files, remote divergence, PR state and live revision. Keep secrets and bulky browser evidence outside tracked source.
- Commit coherent tested increments; update PR #106 with the final scope and validation rather than creating duplicate integration PRs. Do not label unmerged work as integrated into main.
- Preserve reviewed contributors' work. Inventory historical branches/PRs before proposing archive/delete actions; do not mass-delete or blindly merge them.
- Every deployment records build ID, immutable image digest, source commit, traffic revision, smoke evidence and rollback revision. Git tags identify deployed source independently of documentation-only commits.

## Acceptance criteria

1. With all browser tabs closed, a scheduled execution collects and publishes valid views for all ten companies. Prove one manual trigger and one naturally scheduled trigger with distinct execution IDs and saved timestamps.
2. A cold/restarted web process serves the saved snapshot, maintaining company isolation and original source timestamps.
3. Partial provider failure retains prior observations as stale; complete news failure does not claim news success even if FX succeeds. On-time attempts carrying old observations do not reset freshness. Corrupt, incompatible and delayed bundles cannot replace newer valid data; a later weaker same-slot duplicate cannot displace the first valid winner.
4. News and proactive-risk views explain freshness and show evidence links; mapping, confidence and existing procurement workflows pass their substantive regression checks.
5. Ordinary typed and live voice answers avoid repetitive fixture qualifiers. Explicit data-origin questions receive truthful answers. Supplier actions still validate canonical IDs; RFQ/mail simulations remain explicitly unsent/simulated.
6. Test all ten company contexts and representative cross-company switching, numerical inventory questions, delay recovery and RFQ proposals. Browser-check desktop/mobile UI and real live voice responses with the existing state/orb indicators.
7. Review scoped IAM and bucket access, check no private/session content appears in snapshots, run required project gates, then deploy the existing hosted service and verify the actual destination.

## Cost and rollback

Cloud Scheduler includes three jobs per billing account; additional jobs cost USD 0.10 per 31 days, not per execution. Cloud Run execution, network and storage charges are separate and subject to their own free allowances. Do not promise zero total cost or treat trial credit as a spending cap. Capture actual run duration and storage volume before estimating monthly usage. No paid-account upgrade is part of this scope.

Pause the new scheduler to stop automatic triggers, disable the snapshot reader to restore the current live feed path, and restore web traffic to revision `closed-source-quote-demo-00022-wgm` if needed. Keep snapshots private during rollback; no destruction of existing services or datasets is required.

## Official references

- [Scheduled Cloud Run Jobs and OAuth invocation](https://docs.cloud.google.com/run/docs/execute/jobs-on-schedule)
- [Cloud Scheduler pricing](https://cloud.google.com/scheduler/pricing)
- [Cloud Run pricing](https://cloud.google.com/run/pricing)

## Self-review

The proposal distinguishes current verified state from future work, chooses a concrete cadence, bounds automatic spend, preserves source truth and simulated-action boundaries, and defines failure/rollback behavior. A separate read-only review identified FX/news success conflation, freshness reset on failed attempts and same-slot duplicate regression; all three are addressed above. No source code, infrastructure or bot behavior has been changed for this proposal. Implementation planning begins after written design review.
