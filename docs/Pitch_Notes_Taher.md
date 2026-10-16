# Pitch notes: Taher's parts of ZOORQ

Short talking points for the hosted demo (<https://closed-source-quote-demo-ryoeyuqvta-el.a.run.app/demo>). "Live" means seen working on that URL on 16 October 2026; "waiting" means built and tested on an open pull request, to be integrated and deployed by Divyanshu.

## One line

ZOORQ connects world signals to a company's supply exposure and helps a buyer make the next decision with evidence, while every number says where it comes from and what it does not prove.

## My features

| Feature | State | What it does | Proof points |
|---|---|---|---|
| Quote engine and evidence trail | Live | Reads a supplier quote, shows every field with the line it came from, blocks acceptance while a critical field is missing, then compares accepted quotes | Live check: two copper quotes read with rules extraction and accepted; comparison picked Europa Copper SA at 82,000 USD for 8,000 kg, Swift at 86,000 USD |
| Carbon components and coverage | Waiting (PR #114) | Material production and entered freight as separate components, combined only when the factor ends at the production gate | 52 catalogue lines: 2 exact factors, 5 labelled proxies, 3 need a stated mass, 42 unavailable with a reason. Copper cathode uses the International Copper Association figure, 3,965 kg CO2e per tonne |
| Programme discovery | Waiting (PR #115) | Official programmes a company might check, with the reason each matched | 8 entries from official pages (India x4, EU CBAM, Ireland EU ETS, Kenya green finance taxonomy, South Africa carbon tax). Amounts only where official rules allow: an issued-certificate value, a first-year interest difference from an offered rate, or a CBAM sensitivity for a published quarter |
| Market reference | Waiting (PR #116) | One world benchmark per material, kept apart from synthetic supplier quotes, in the company's currencies | World Bank Pink Sheet (CC BY 4.0); live ECB rates or a dated BIS snapshot; 6 of 10 companies have no exact benchmark and say so |
| Price and FX outlook | Waiting (PR #125) | Shows P10 to P90 only where a model beat the naive baseline on 24 untouched months | 23 series (7 commodities, 16 currencies), 69 series-horizon rows: PREDICTION 0, RANGE_ONLY 31, NO_RELIABLE_FORECAST 38. Naive was chosen for 15 of 23 series on validation; no challenger passed the test-split gate |
| Synthetic dispatch | Waiting (PR #117) | Shipments, holds, hazard exposure and delay replay, linked to the selected company's own orders and quotes | Fixtures re-exported at 2026-10-16T12:00:00Z with statuses, distances and replay results unchanged |

Carbon, programme and market answers inside the operator chat and voice are Divyanshu's integration task; my functions return the sourced data his tools read.

## What to say about the outlook

"We tested four models, including gradient boosting and a ridge, on 23 public series and held back the last two years. None beat simply carrying the last value forward by enough to call it a prediction, so ZOORQ shows a volatility range or says there is no reliable forecast. It never turns a projection into a buy or wait instruction."

## What not to claim

| Do not say | Say instead |
|---|---|
| "Complete carbon footprint" or "certified" | "Estimate from published average factors; material and freight shown separately" |
| "You are eligible" or "you will save" | "Possibly eligible, check conditions"; "first-year interest difference, not an offer" |
| "Country prices" | "A global benchmark converted into your currencies, beside synthetic supplier quotes" |
| "Our model predicts copper" | "No model beat the naive baseline on held-out data" |
| "Live shipment tracking" | "Synthetic shipments, labelled as fixture data" |
| Anything about real suppliers being contacted | "Drafts are unsent; simulated sending never leaves the demo" |
