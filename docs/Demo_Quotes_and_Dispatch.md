# Demo script: quotes and dispatch (2:45 video)

This covers Taher's parts:
- quote intelligence (issue #3): the `/offers` page
- the dispatch workspace (issues #5 and #60): the `/dispatch` page

Everything shown is synthetic and runs in the browser on labelled local adapters. There is no gateway, no model
call and no network request. Every expected screen below was checked in headless Chrome on branch
`feature/release-readiness`. That branch combines #55, #61 and #62 with the release-readiness changes.

## Before recording (not timed)

1. From `apps/web`, run `npm install` once, then `npm run dev`. Open `http://127.0.0.1:5317/offers`. The port is fixed in `vite.config.ts`.
2. Keep `procurement-engine/tests/fixtures/offers/train/SYN-Q01_atacama_copper_cif.pdf` ready in the file picker.
3. Use a 1440 px wide window. Both pages also work at 390 and 768 px.
4. Ignore any Vite `ECONNREFUSED 127.0.0.1:8817` lines in the terminal. Other pages call the gateway, which is not running; these two pages never do.
5. After 2026-10-09 12:00 UTC, `/dispatch` shows an **Old data** notice with the cutoff's age. That is expected. Say "statuses describe the cutoff, not now".

## Script

| Time | Click | Expected screen | Say |
|---|---|---|---|
| 0:00 | Open `/offers` | Header links: Planning, **Supplier offers** (current, in red), Synthetic dispatch workspace, Shipment recovery evidence, Synthetic recovery comparison, Typed scenarios. Banner "SYNTHETIC FIXTURE DATA · LOCAL ADAPTER · NOT CONNECTED TO THE GATEWAY". Empty state "No quote loaded" | "Closed Source checks supplier quotes and shipments against evidence. Everything here is synthetic." |
| 0:08 | **Upload a quote**, then choose `SYN-Q01_atacama_copper_cif.pdf` | "Atacama Red Metals SpA [SYNTHETIC]" with **NEEDS REVIEW**. Red box "Save is blocked until these are resolved: settlement currency: missing". Field table, and "Document text" on the right | "Every value is extracted or missing; nothing is guessed. The quote never states the invoice currency, so save is blocked." |
| 0:22 | Click the **incoterm** field name | "CIF Nhava Sheva, India", `extracted`. Line `p1:l11` "Delivery terms: CIF Nhava Sheva, India (Incoterms 2020)" is highlighted in Document text | "Each value points to the exact line it came from." |
| 0:32 | On **settlement currency** click **Edit**. Type `USD` in "ISO currency" and click **Apply correction** | "Corrected settlement currency." Status **CORRECTED**. The row shows USD, `corrected`, "reviewer entry" | "A reviewer fills the gap. It is recorded as a reviewer entry, not as document evidence." |
| 0:42 | Click **Save offer →** | "Resolve these before saving." Blocking list: "settlement currency: revalidation required" and "Item 1 · pricing: normalization unavailable" | "The browser copy will not save a changed economic value until the Python service re-checks it. Nothing is sent to the supplier." |
| 0:52 | Click **Download synthetic CSV** | The browser saves `synthetic-offer-fixture-q01-v2.csv`. Every row ends in `true` under `synthetic` | "The reviewed offer exports with its evidence, labelled synthetic in every row." |
| 1:00 | Click **Synthetic dispatch workspace** in the header | List: SHP-SYN-001 Unknown, SHP-SYN-002 **On hold**, SHP-SYN-003 Unknown, SHP-SYN-004 **Delayed**, SHP-SYN-005 On time, SHP-SYN-008 Unknown | "Every status needs evidence. Unknown means there is no plan baseline to compare with." |
| 1:08 | Click **SHP-SYN-002** | Expected arrival "2026-10-12 02:30 UTC (stated ETA)". Plan baseline "2026-10-10 00:00 UTC". On hold "Yes". **2 working days**, "Synthetic holiday table: invented dates, not real public holidays". Route evidence **CONFIRMED: RESTRICTED** from a synthetic port authority notice | "A port hold, a later ETA, and an operating notice that confirms restricted operations." |
| 1:20 | Scroll to **Hazard and weather exposure** | Chip "EXPOSURE, NOT CONFIRMED". open-meteo at Nhava Sheva (JNPT): **Exposure found, not confirmed**, with rain heavy 24h 76.8 mm against a threshold of 64.5 mm. gdacs at the same port: **No exposure found (not a safety check)**, Green FL alert 32 km away | "Weather and hazard feeds can only raise exposure. Below a threshold we say no exposure found, never safe." |
| 1:34 | Click **SHP-SYN-008** and scroll to the hazards | gdacs Paradip (INPRT): Orange TC alert 121 km away, threshold met. open-meteo Paradip: "Stale: retrieved too long before the cutoff. Not usable for planning." open-meteo Tubarao: **Provider failure**, reason http 400 | "Stale answers stay visible but unusable, and failures are explicit." |
| 1:48 | Scroll to **Replay a sample delay** and click **Port release at INNSA** | "Synthetic replay event, not observed data: port released for SHP-SYN-002". **What changed**: ON HOLD → DELAYED, "Signals added: none · removed: on hold", "On hold: yes → no". The release is marked **Injected (synthetic)**. Neighbours sharing INNSA are labelled exposure. "Unchanged by the replay" | "Inject one synthetic event and see what changes, and why. Releasing the hold leaves it delayed, because the ETA is still after plan." |
| 2:08 | Click **Port hold at INNSA with a new ETA** | Expected arrival 2026-10-12 02:30 UTC → 2026-10-16 02:30 UTC. Material screen: "LINE-002-A 3000 kg of CMP-COPPER: plan 2026-10-10 00:00 UTC → 2026-10-16 02:30 UTC", with "CMP-COPPER shortfall 0 → 3000 kg (new shortage)" for COMMIT-SYN-1 | "A stated new ETA flows into the existing material screen: the copper now arrives after it is needed." |
| 2:22 | Click **ETA update observed after the cutoff** | "Event rejected": "Rejected: the event happened, was observed or had evidence retrieved after the data cutoff, so it was not known then." | "Nothing after the cutoff leaks into the view." |
| 2:30 | Scroll up to **Export** and click **Download synthetic JSON** | The browser saves `synthetic-dispatch-SHP-SYN-008.json`, or the file for whichever shipment is selected | "The dispatch view exports with its status, timeline, evidence and hazards." |
| 2:36 | Scroll to the replay result and click **View recovery options** | Opens `/shipment-recovery`, "Shipment recovery evidence." | "Recovery options live on the shipment recovery page. Dispatch links to it and never computes them." |
| 2:45 | End | | |

If time is short, drop the 2:22 rejection and the 2:30 export: the script then runs about 2:30.

## What not to claim

- **Synthetic data:** quotes, shipments, ports, hazards, holidays and replay events are all synthetic. No live tracking, port, weather, hazard, holiday or news data is used.
- **Live providers:** the Open-Meteo, GDACS, Nager.Date, GDELT and RSS adapters exist but have only ever run on hand-written payloads.
- **Gateway and saves:** neither page is connected to the gateway, and saves in `/offers` stay in the browser tab.
- **Recovery link:** it does not preselect the shipment yet.
- **Coordinates:** the location registry's coordinates are hand-entered and unverified.
- **Holidays:** India has no approved real holiday source.
- **Extraction accuracy:** the figures are on synthetic quotes only (`procurement-engine/evaluation/offers/README.md`).
- **News and policy notices:** the snapshots (#63, #64) have no page yet.

## Reset between takes

Reload `/offers`; corrections and saves live only in that tab. `/dispatch` is read only. Delete the downloaded files.
