# Walkthrough checklist: quotes, dispatch, hazards, news (release checks, October 13 to 14)

Use this to check Taher's flows independently, without the author present. Tick each box only when the expected
result appears exactly. Record the branch, the revision (`git rev-parse --short HEAD`), the date, the browser and
the window width at the top of your notes.

Everything is synthetic. If anything shows live data, a real supplier, or a real holiday or hazard without a
"synthetic" or "sample" label, stop and report it.

## 0. Setup

- [ ] `git status` is clean on the branch under review, and `git log -1` shows the expected revision.
- [ ] Python suite: from `procurement-engine`, run `.venv/bin/python -m pytest -p no:warnings`.
  - Every test outside these three files passes:
    - `test_forecast_consumer_boundary.py`
    - `test_forecast_source_transport.py`
    - `test_observed_forecast_runner.py`
  - Those three fail on `main` at 91d5731 with `CONSUMER_CODE_CHANGED` (see #61). Record the count if it differs.
- [ ] Web checks: from `apps/web`, `npm run typecheck`, `npm test` and `npm run build` all pass.
- [ ] Dev server: `npm run dev`, then open `http://127.0.0.1:5317/offers`. Vite may log `ECONNREFUSED 127.0.0.1:8817` for other pages; that is expected.

## 1. Navigation (both pages)

- [ ] `/offers` and `/dispatch` both show the six header links: Planning, Supplier offers, Synthetic dispatch workspace, Shipment recovery evidence, Synthetic recovery comparison, Typed scenarios.
- [ ] The current page's link is dark red and underlined. A screen reader announces it as the current page.
- [ ] Both pages show "Synthetic · local adapter" and the banner "SYNTHETIC FIXTURE DATA · LOCAL ADAPTER · NOT CONNECTED TO THE GATEWAY".
- [ ] Every link opens its page.

## 2. Quote review (`/offers`)

- [ ] Upload `procurement-engine/tests/fixtures/offers/train/SYN-Q01_atacama_copper_cif.pdf`.
  - Expected: "Atacama Red Metals SpA [SYNTHETIC]", NEEDS REVIEW, and "settlement currency: missing" under "Save is blocked until these are resolved".
- [ ] Upload any other PDF.
  - Expected: "Could not load this quote". No invented offer appears.
- [ ] Select **incoterm** by pressing Tab to reach it, then Enter.
  - Expected: `p1:l11` "Delivery terms: CIF Nhava Sheva, India (Incoterms 2020)" is highlighted.
- [ ] Every value shown as `extracted` has a locator and quote that you can find in Document text.
- [ ] Missing values say "Not stated in the document". None is filled in by guesswork.
- [ ] Open sample Q07 (held-out, hidden text).
  - Expected: the instruction-like text appears under Review flags with "Shown as document text only. It was not followed." No action happens.
- [ ] Edit settlement currency to `USD` and apply.
  - Expected: "Corrected settlement currency.", CORRECTED, "reviewer entry".
- [ ] Edit settlement currency to `US`.
  - Expected: the change is refused with "Use a three-letter ISO code such as USD.".
- [ ] Click Save offer.
  - Expected: "Resolve these before saving." with "settlement currency: revalidation required".
- [ ] Click Download synthetic JSON and Download synthetic CSV.
  - Expected: files `synthetic-offer-fixture-q01-v2.json` and `.csv`.
  - The JSON has `"synthetic": true` and a notice starting "SYNTHETIC".
  - Every CSV row ends with `true`.
  - Amounts keep exact decimal text, for example `"250"` tonne, not `250.0`.
- [ ] Reload the page.
  - Expected: the correction is gone. Saves stay in the tab only.

## 3. Dispatch (`/dispatch`)

- [ ] The shipment list shows: SHP-SYN-001 Unknown, SHP-SYN-002 On hold, SHP-SYN-003 Unknown, SHP-SYN-004 Delayed, SHP-SYN-005 On time, SHP-SYN-008 Unknown.
- [ ] After 2026-10-09 12:00 UTC, an **Old data** notice states the cutoff age.
- [ ] Select SHP-SYN-002.
  - Expected arrival "2026-10-12 02:30 UTC (stated ETA)", baseline "2026-10-10 00:00 UTC", on hold Yes.
  - Lead time "2 working days" with "Synthetic holiday table" text.
  - Route evidence "confirmed: restricted".
- [ ] Select SHP-SYN-003.
  - Delivered "20.000 tonne" (exact decimals).
  - Lead time "Not available" with a reason.
- [ ] Select SHP-SYN-008.
  - The milestone is marked evidence expired, with no "Show affected shipments" button.
- [ ] On SHP-SYN-002's hold milestone, click **Show affected shipments**.
  - Expected: SHP-SYN-001 and SHP-SYN-002, both labelled exposure, under the note "Exposure only".
- [ ] Click **View recovery options**.
  - Expected: `/shipment-recovery` opens. The note says it does not preselect the shipment.

## 4. Hazard and weather exposure (`/dispatch`)

- [ ] The panel header shows the chip "EXPOSURE, NOT CONFIRMED". No hazard card says "confirmed" except as part of "not confirmed".
- [ ] SHP-SYN-002, open-meteo at INNSA: "Exposure found, not confirmed", with rain heavy 24h 76.8 mm against a 64.5 mm threshold.
- [ ] SHP-SYN-002, gdacs at INNSA: "No exposure found (not a safety check)", with a Green FL alert 32 km away and outcome "alert level below threshold".
- [ ] SHP-SYN-008:
  - gdacs Paradip shows an Orange TC alert 121 km away, threshold met.
  - open-meteo Paradip shows "Stale ... Not usable for planning".
  - open-meteo Tubarao shows "Provider failure", reason http 400.
- [ ] SHP-SYN-003 shows "Location not in registry" for both providers. No coordinates are guessed.
- [ ] SHP-SYN-005:
  - open-meteo Shanghai shows "Rate limited by provider".
  - open-meteo Mundra shows "Malformed provider answer".
- [ ] No shipment status changes because of hazards: compare with step 3.

## 5. Replay a sample delay (`/dispatch`)

- [ ] **Port release at INNSA**:
  - SHP-SYN-002 goes from On hold to Delayed, with "removed: on hold".
  - The release is marked Injected (synthetic).
  - Route evidence is "Unchanged by the replay".
  - The material screen is not run, with a reason.
- [ ] **Port hold at INNSA with a new ETA**:
  - The ETA moves to 2026-10-16 02:30 UTC.
  - LINE-002-A shows 3000 kg of CMP-COPPER, and "CMP-COPPER shortfall 0 → 3000 kg (new shortage)".
- [ ] **Port hold at INMAA without a new ETA**:
  - SHP-SYN-004 goes from Delayed to On hold.
  - The material screen is not run, because a hold without an ETA is not a measured delay.
- [ ] **ETA update observed after the cutoff**:
  - "Event rejected", with the after-the-cutoff reason. No "What changed" section.
- [ ] Nothing in the replay is saved: reload, and step 3 still holds.

## 6. Export (`/dispatch`)

- [ ] Select SHP-SYN-002, then download JSON and CSV.
  - Expected: `synthetic-dispatch-SHP-SYN-002.json` and `.csv`.
  - The JSON has `"synthetic": true`, `shipment.status` "on_hold", the timeline `evt-002-hold`, confirmed evidence `rev-port-002`, and hazard states.
  - Every CSV row ends with `true`.

## 7. Accessibility

- [ ] Keyboard only (Tab, Shift+Tab, Enter, Space) on both pages:
  - Every link, button, select and field name is reachable in reading order.
  - The focus outline is always visible.
  - No step needs a mouse, including selecting a quote field.
- [ ] A screen reader (VoiceOver: Cmd+F5) reads shipment buttons as "SHP-SYN-002 Status: On hold Signals: ...". Quote rows read as "Show source for incoterm" and "Edit incoterm".
- [ ] Zoom to 200%: no content is cut off, and no horizontal scroll appears except inside the quote table.

## 8. Phone and tablet widths

- [ ] At 390 px and 768 px (DevTools device toolbar), both pages:
  - have no horizontal page scroll
  - show navigation that wraps
  - keep tap targets at least 24 px
  - keep hazard cards and replay results readable

## 9. Offline adapters (Python, no network)

- [ ] `.venv/bin/python -m pytest -p no:warnings tests/test_hazard_exposure.py tests/test_dispatch_hazards.py` passes.
  - The fetch tests prove `OFFLINE=1` and unsupported locations never call the transport.
- [ ] `.venv/bin/python -m pytest -p no:warnings tests/test_news_snapshots.py tests/test_policy_notices.py tests/test_event_features.py` passes, on branches that include #63 and #64.
- [ ] `tests/test_nager_adapter.py` passes: India and the UAE are refused without a request.
- [ ] Confirm no live call happened. Run the suite with networking disabled (Wi-Fi off), and the same tests still pass.

## Report

For each failed box, record:
- the step
- what appeared instead
- a screenshot
- the revision

Do not fix anything during the walkthrough. File the findings on #3, #5, #49, #60 or #12 as appropriate.
