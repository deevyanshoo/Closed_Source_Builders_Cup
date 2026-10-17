# One workspace disclosure — hosted observations

Observed on `closed-source-quote-demo-00074-sjn`, application source `b3797c2`, after the user requested that synthetic/proxy context appear once at the top rather than in every answer.

The restored Solara workspace displays exactly one notice above its Supply tabs: “Demo workspace — Company records, supplier quotes and buyer inputs are illustrative. News draws on public sources; emissions use published averages and may use product proxy factors.” Existing baseline and reviewed shipment suppliers/dates survived reload. The notice begins 70 pixels from the top and uses the existing light/dark palette.

| View | Observation | Screenshot |
| --- | --- | --- |
| Desktop 1440 × 1000, Light | One readable notice; document width 1440, no horizontal overflow | [Light desktop](hosted-disclosure-light-desktop-r74.jpg) |
| Desktop 1440 × 1000, Dark | One readable notice with consistent dark colours; layout fits the viewport | [Dark desktop](hosted-disclosure-dark-desktop-r74.jpg) |
| Mobile 390 × 844, Dark | Notice wraps within 354 pixels; document width 390, no horizontal overflow | [Dark mobile](hosted-disclosure-dark-mobile-r74.jpg) |
| Mobile 390 × 844, Light | Notice wraps readably within the viewport; navigation and Talk/Type remain usable | [Light mobile](hosted-disclosure-light-mobile-r74.jpg) |

The exact typed regression prompt, “Summarize the current planned shipment and the reviewed recovery. Do not change anything,” returns the correct original Europa / 24 October and reviewed Swift / 28 October context, 8,000 kg purchase and 5 November need-by date. The new answer no longer repeats the generic “not a confirmed order” and “not live tracking” disclaimers. It remains a planning read with one deterministic specialist step; the visible shipment/recovery comparison stays unchanged and no new action card appears. [Current operator answer](hosted-operator-natural-summary-r74.jpg).

The browser was restored to its normal viewport in Light mode. Existing conversation history was retained; old recorded responses were not rewritten. These observations cover the shared notice on one restored company workspace in four viewport/theme states, not forty new all-company transactions. Earlier all-company workflow and controlled-speech evidence is separately labelled with its original revision. This is not a physical microphone or independent generated-output waveform test.
