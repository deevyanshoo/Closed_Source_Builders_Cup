# Demo script: quotes and dispatch (2:45 video)

This covers Taher's parts:
- quote intelligence (issue #3): the `/offers` page
- the dispatch workspace (issues #5 and #60): the `/dispatch` page

Everything shown is synthetic and runs in the browser on labelled local adapters. There is no gateway call, no model
call and no network request on these two pages. Every expected screen below was checked in headless Chrome at 1440 px
on main at 497b53e on 2026-10-12, which includes #73, #74 and #83 to #92.

## Before recording (not timed)

1. Start either stack in [Run_The_Demo.md](Run_The_Demo.md) and open `/offers` on its URL. These two pages also work
   without a gateway: from `apps/web`, run `npm ci` once, then `npm run dev`, and open `http://127.0.0.1:5317/offers`.
2. On `/offers`, leave **Synthetic fixtures** selected (the default). The **Local quote service** mode is not part of
   this script.
3. Keep `procurement-engine/tests/fixtures/offers/train/SYN-Q01_atacama_copper_cif.pdf` ready in the file picker.
4. Use a 1440 px wide window. Both pages also work at 390 and 768 px.
5. With plain `npm run dev`, ignore any Vite `ECONNREFUSED 127.0.0.1:8817` lines. Other pages call the gateway; these two pages never do.
6. `/dispatch` will show **Old data: Data cutoff is N hours old. Statuses describe that cutoff, not now.** The
   synthetic shipments have a fixed cutoff of 2026-10-08 12:00 UTC, and the notice appears once the cutoff is more
   than a day old. N is the hours since the cutoff (91 on 2026-10-12 around 07:00 UTC), so it is larger on recording
   day. It means every status describes what was known at the cutoff, not today. Do not change the fixture dates;
   say "statuses describe the cutoff, not now".

## Script

| Time | Click | Expected screen | Say |
|---|---|---|---|
| 0:00 | Open `/offers` | Header links: Planning, **Supplier offers** (current, in red), Synthetic dispatch workspace, Shipment recovery evidence, Synthetic recovery comparison, Typed scenarios. **Synthetic fixtures** selected. Banner "SYNTHETIC FIXTURE DATA · LOCAL ADAPTER · NOT CONNECTED TO THE GATEWAY". Empty state "No quote loaded" | "Closed Source checks supplier quotes and shipments against evidence. Everything here is synthetic." |
| 0:08 | **Upload a quote**, then choose `SYN-Q01_atacama_copper_cif.pdf` | "Atacama Red Metals SpA [SYNTHETIC]" with **NEEDS REVIEW**. Red box "Save is blocked until these are resolved: settlement currency: missing". Field table, and "Document text" on the right | "Every value is extracted or missing; nothing is guessed. The quote never states the invoice currency, so save is blocked." |
| 0:20 | Click the **incoterm** field name | "CIF Nhava Sheva, India", `extracted`. Line `p1:l11` "Delivery terms: CIF Nhava Sheva, India (Incoterms 2020)" is highlighted in Document text | "Each value points to the exact line it came from." |
| 0:28 | In **Decision date and payment timing**, set decision date 2026-10-10, bill of lading date 2026-11-06, compare with the payment terms of Q03, rate `8`. Click **Run scenario** | "Open: valid until 2026-10-31, 21 days left". Payment terms `p1:l15`, **parsed**. Schedule: 30% telegraphic transfer 705937.5 USD on 2026-10-10, 70% 1647187.5 USD on 2026-11-06. "Payment moves 98.1 days later on average", worth **50595.41 USD** at 8% | "Payment terms become dated cash. The rate is ours to enter; nothing is assumed." |
| 0:44 | On **settlement currency** click **Edit**. Type `USD` in "ISO currency" and click **Apply correction** | "Corrected settlement currency." Status **CORRECTED**. The row shows USD, `corrected`, "reviewer entry" | "A reviewer fills the gap. It is recorded as a reviewer entry, not as document evidence." |
| 0:52 | Click **Save offer →** | "Resolve these before saving." Blocking list: "settlement currency: revalidation required" and "Item 1 · pricing: normalization unavailable" | "The browser copy will not save a changed economic value until the Python service re-checks it. Nothing is sent to the supplier." |
| 1:00 | Click **Download synthetic CSV** | The browser saves `synthetic-offer-fixture-q01-v2.csv`. Every row ends in `true` under `synthetic` | "The reviewed offer exports with its evidence, labelled synthetic in every row." |
| 1:06 | Click **Synthetic dispatch workspace** in the header | List: SHP-SYN-001 Unknown, SHP-SYN-002 **On hold**, SHP-SYN-003 Unknown, SHP-SYN-004 **Delayed**, SHP-SYN-005 On time, SHP-SYN-008 Unknown. The **Old data** notice | "Every status needs evidence. Unknown means there is no plan baseline to compare with." |
| 1:12 | Click **SHP-SYN-002** | Expected arrival "2026-10-12 02:30 UTC (stated ETA)". Plan baseline "2026-10-10 00:00 UTC". On hold "Yes". **2 working days**, "Synthetic holiday table: invented dates, not real public holidays". Route evidence **CONFIRMED: RESTRICTED** from a synthetic port authority notice | "A port hold, a later ETA, and an operating notice that confirms restricted operations." |
| 1:22 | Scroll to **Hazard and weather exposure** | Chip "EXPOSURE, NOT CONFIRMED". open-meteo at Nhava Sheva (JNPT): **Exposure found, not confirmed**, with rain heavy 24h 76.8 mm against a threshold of 64.5 mm. gdacs at the same port: **No exposure found (not a safety check)**, Green FL alert 32 km away (33 km once #72 is merged) | "Weather and hazard feeds can only raise exposure. Below a threshold we say no exposure found, never safe." |
| 1:34 | Click **SHP-SYN-008** and scroll to the hazards | gdacs Paradip (INPRT): Orange TC alert 121 km away (130 km once #72 is merged), threshold met. open-meteo Paradip: "Stale: retrieved too long before the cutoff. Not usable for planning." open-meteo Tubarao: **Provider failure**, reason http 400 | "Stale answers stay visible but unusable, and failures are explicit." |
| 1:46 | Scroll to **Replay a sample delay** and click **Port release at INNSA** | "Synthetic replay event, not observed data: port released for SHP-SYN-002". **What changed**: ON HOLD → DELAYED, "Signals added: none · removed: on hold", "On hold: yes → no". The release is marked **Injected (synthetic)**. Neighbours sharing INNSA are labelled exposure. "Unchanged by the replay" | "Inject one synthetic event and see what changes, and why. Releasing the hold leaves it delayed, because the ETA is still after plan." |
| 2:04 | Click **Port hold at INNSA with a new ETA** | Expected arrival 2026-10-12 02:30 UTC → 2026-10-16 02:30 UTC. Material screen: "LINE-002-A 3000 kg of CMP-COPPER: plan 2026-10-10 00:00 UTC → 2026-10-16 02:30 UTC", with "CMP-COPPER shortfall 0 → 3000 kg (new shortage)" for COMMIT-SYN-1 | "A stated new ETA flows into the existing material screen: the copper now arrives after it is needed." |
| 2:18 | Click **ETA update observed after the cutoff** | "Event rejected": "Rejected: the event happened, was observed or had evidence retrieved after the data cutoff, so it was not known then." | "Nothing after the cutoff leaks into the view." |
| 2:26 | Scroll up to **Export** and click **Download synthetic JSON** | The browser saves `synthetic-dispatch-<selected shipment>.json` | "The dispatch view exports with its status, timeline, evidence and hazards." |
| 2:32 | Click **View recovery options** | Opens `/shipment-recovery`, "Shipment recovery evidence." | "Recovery options live on the shipment recovery page. Dispatch links to it and never computes them." |
| 2:40 | End | | |

If time is short, drop the 2:18 rejection and the 2:26 export: the script then runs about 2:25. Run the 0:28
scenario before the 0:44 correction: a corrected currency clears the price check in the browser, and the scenario
then shows "Order value not available".

## What not to claim

- **Synthetic data:** quotes, shipments, ports, hazards, holidays and replay events are all synthetic. No live tracking, port, weather, hazard, holiday or news data is used.
- **Live providers:** the Open-Meteo, GDACS, Nager.Date, GDELT and RSS adapters exist but have only ever run on hand-written payloads.
- **Gateway and saves:** in the default **Synthetic fixtures** mode neither page calls the gateway, and saves in `/offers` stay in the browser tab. The **Local quote service** mode (#84, #88) is off in the demo stacks; it needs a signing key file and a separate offers-only service.
- **Payment scenario:** amounts are quantity times unit price in the quote currency, with no FX, tax or freight. Event dates and the rate are user inputs, not forecasts. It runs in the browser; there is no service route for it.
- **Sourcing handoff:** the sourcing candidates read model (#74) and the quote to shipment handoff (#86) are not shown on these pages.
- **Recovery link:** it does not preselect the shipment yet.
- **Coordinates:** on main the location registry's coordinates are still hand-entered. #72 (open) cites sources for six ports; Mundra and Jebel Ali stay unverified.
- **Holidays:** India has no approved real holiday source.
- **Extraction accuracy:** 88.5% of fields on 8 held-out synthetic quotes, rules only (`docs/Extraction_Eval_Refresh_2026-10-10.md`). Synthetic quotes written by the same author as the rules, so it says nothing about real supplier documents.
- **News and policy notices:** the snapshots (#63, #64) have no page yet.

## Reset between takes

Reload `/offers`; corrections and saves live only in that tab. `/dispatch` is read only. Delete the downloaded files.
