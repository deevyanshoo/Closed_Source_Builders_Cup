# Demo script: quotes and dispatch (about 80 seconds)

This covers Taher's parts:
- quote intelligence (issue #3): the `/offers` page
- the dispatch workspace (issue #5): the `/dispatch` page

Everything shown is synthetic and runs in the browser on labelled local adapters. There is no gateway, no
model call and no network request. The steps and expected screens were checked in headless Chrome against
branch `feature/dispatch-replay`.

## Before you start (not timed)

1. Start the web app from `apps/web`: `npm install` once, then `npm run dev`. Open `http://127.0.0.1:5173/offers`.
2. Keep `procurement-engine/tests/fixtures/offers/train/SYN-Q01_atacama_copper_cif.pdf` ready in a file picker.
3. Use a window at least 1280 px wide. Both pages also work at phone width.
4. If you present more than a day after the dispatch cutoff (2026-10-08 12:00 UTC), `/dispatch` shows an
   **Old data** notice with the cutoff's age. That is expected: say "statuses describe the cutoff, not now".
   `/offers` shows its old-data line only after 7 days.

## Script

| Time | Click | Expected screen | Say |
|---|---|---|---|
| 0:00 | Open `/offers` | Heading "Supplier quotes, checked against their source". Red-edged banner "SYNTHETIC FIXTURE DATA · LOCAL ADAPTER · NOT CONNECTED TO THE GATEWAY". Empty state "No quote loaded" | "A supplier quote, checked against its own text." |
| 0:05 | **Upload a quote** → choose `SYN-Q01_atacama_copper_cif.pdf` | Header "Atacama Red Metals SpA [SYNTHETIC]" with status **NEEDS REVIEW**. Red box "Save is blocked until these are resolved: settlement currency: missing". Field table on the left, "Document text" on the right | "Every value is extracted or missing. Nothing is guessed. This quote never says which currency it will be invoiced in, so that is missing and blocks save." |
| 0:20 | Click the **Incoterm** row | Value "CIF Nhava Sheva, India", status `extracted`. Source `p1:l11` is highlighted in Document text: "Delivery terms: CIF Nhava Sheva, India (Incoterms 2020)" | "Each value points to the exact line it came from." |
| 0:28 | On the **Settlement currency** row click **Edit**. Type `USD` in "ISO currency" and click **Apply correction** | Message "Corrected settlement currency." Status **CORRECTED**. The row shows USD, `corrected`, "reviewer entry", "Entered by reviewer" | "A reviewer fills the gap, and it is recorded as a reviewer entry, not as document evidence." |
| 0:36 | Click **Save offer →** | "Resolve these before saving." The blocking list now reads "settlement currency: revalidation required" and "Item 1 · pricing: normalization unavailable" | "The browser copy refuses to save a changed economic value until the Python service re-checks it. The service's `/offers` save route does that. Nothing is sent to the supplier." |
| 0:45 | Go to `/dispatch` | Banner "SYNTHETIC FIXTURE DATA · LOCAL ADAPTER". Shipment list: SHP-SYN-001 Unknown, SHP-SYN-002 **On hold**, SHP-SYN-003 Unknown, SHP-SYN-004 **Delayed**, SHP-SYN-005 On time, SHP-SYN-008 Unknown | "Every status needs evidence. Unknown means there is no plan baseline to compare with." |
| 0:52 | Click **SHP-SYN-002** | Expected arrival "2026-10-12 02:30 UTC (stated ETA)". Plan baseline "2026-10-10 00:00 UTC". On hold "Yes". **Working-day lead time: 2 working days**, with the note "Synthetic holiday table: invented dates, not real public holidays". Route evidence shows **confirmed: restricted** from a synthetic port authority notice | "A port hold, a later ETA, and an operating notice that confirms restricted operations." |
| 1:02 | Scroll to **Replay a sample delay** and click **Port release at INNSA** | "Synthetic replay event, not observed data". **What changed**: SHP-SYN-002 **On hold → Delayed**, "Signals added: none · removed: on hold", "On hold: yes → no". The milestones list shows the release marked **Injected (synthetic)**. Shipments sharing INNSA are labelled **exposure**. Route evidence is "Unchanged by the replay". Material screen: "This event states no new arrival time, so no arrival change is screened" | "Inject one synthetic event and see what changes, and why. Releasing the hold leaves it delayed: the ETA is still after plan. Neighbours are exposure, not confirmed delays." |
| 1:14 | Optional: click **Port hold at INNSA with a new ETA** | Expected arrival moves from 2026-10-12 02:30 UTC to 2026-10-16 02:30 UTC. Material screen: "LINE-002-A 3000 kg of CMP-COPPER: plan 2026-10-10 00:00 UTC → 2026-10-16 02:30 UTC" and, for COMMIT-SYN-1 needed 2026-10-14, "CMP-COPPER shortfall 0 → 3000 kg (new shortage)" | "A stated new ETA flows into the existing material screen: the copper now arrives after it is needed." |
| 1:18 | Click **View recovery options** at the end of the replay result | Opens `/shipment-recovery` with the heading "Shipment recovery evidence." | "Recovery options live on the shipment recovery page. Dispatch links to them and does not compute them." |

If time is short, skip the optional step: the script then runs about 70 seconds.

## What not to claim

- Quotes, shipments, ports, hazards and holidays are synthetic. No live tracking, port, weather or holiday
  data is used. India has no approved real holiday source yet.
- The pages are not connected to the gateway. Saves in `/offers` stay in the browser tab.
- The recovery link does not preselect the shipment yet.
- Extraction accuracy figures are on synthetic quotes only (see `procurement-engine/evaluation/offers/README.md`).

## Reset between runs

Reload `/offers`; corrections and saves stay in that tab only. `/dispatch` is read only.
