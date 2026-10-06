# External source integration priorities

Reviewed October 6, 2026 against public-apis/public-apis commit `874e5879d20843f7c2a5822cef4c0752127b3775`: 2,037 API rows in 51 categories plus 10 MCP listings. The directory is discovery metadata, not permission to reuse every service or proof of current API health. Its MIT license does not license the providers' data.

## Integrate by decision value

| Source | Workflow use | Access and evidence boundary |
|---|---|---|
| [Frankfurter](https://frankfurter.dev/) | Keep dated reference FX for quote normalization and scenarios | Underlying source terms apply; not an executable rate. v2 USD/INR timed out in this review's network checks, so reconfirm adapter health. |
| [World Bank](https://www.worldbank.org/en/research/commodity-markets) | Preserve monthly commodity history and macro context | Pink Sheet downloads differ from the Indicators API. Version units, release dates and selected-series rights. |
| [Open-Meteo](https://open-meteo.com/en/pricing), [USGS](https://earthquake.usgs.gov/data/software/), [GDACS](https://www.gdacs.org/Documents/2025/GDACS_API_quickstart_v2.pdf) | Hazard/weather exposure → affected order → production/dispatch scenario | Weather or event proximity does not prove closure/damage. Open-Meteo free hosted API is non-commercial; commercial ensemble access requires Professional or higher. |
| [Nager.Date](https://github.com/nager/Nager.Date) plus facility calendars | Working-day lead times | Live catalog omitted IN/AE; SG 2026 returned 11 holidays. Country/year and hosted rights must be verified. Plant shifts and port cutoffs are separate inputs. |
| [GraphHopper](https://www.graphhopper.com/pricing/) or [openrouteservice](https://openrouteservice.org/dev/) | Land-leg alternatives | Select one after entitlement review. GraphHopper free is non-commercial; current openrouteservice host/quota/rights need confirmation. Neither supplies ocean freight prices or capacity. |
| [GLEIF](https://www.gleif.org/en/lei-data/gleif-api/), country registries | Supplier legal-identity evidence | LEI coverage is incomplete; identity does not establish factory quality/capacity. Do not attach real records to fictional suppliers as endorsement. |
| [UN Comtrade](https://uncomtrade.org/docs/un-comtrade-api/) | HS/reporter/partner export-market discovery | Registration/key improves free access; check display/redistribution rights. Missing is not zero, trade unit value is not a supplier quote, and trade period differs from release date. |
| [UK Carbon Intensity](https://carbon-intensity.github.io/api-definitions/), [government factors](https://www.gov.uk/government/collections/government-conversion-factors-for-company-reporting) | Cost/date/carbon comparison | GB grid scope; activity factors need explicit geography, mode, units, version and reporting boundary. Estimates are not measured emissions or certification. |

GLEIF, Comtrade and GDACS are outside this repository snapshot. Existing [GDELT](https://gdeltproject.org/about.html) news work can be retained with attribution and links; underlying article rights remain separate. Selected FRED/WTO sources are expansion candidates after access and series-rights checks, not prerequisites for the first core workflow.

## Source contract

Store provider ID, source/query, entity/series/HS version, effective/observation period, first-available timestamp when known, retrieval time, units/currency, geographic scope, license/attribution, payload hash and quality flags. Keep unknown publication time explicit.

Check response semantics, support for the selected country/date, non-empty observations, units and freshness. REST Countries v3.1 returned HTTP200 with `success:false` and deprecation, demonstrating why HTTP status alone is insufficient. Use versioned local country/time-zone metadata for basic discovery rather than depending on a drifting API.

The operational join is `event → facility/lane → shipment/PO → component → production order → due date`. Events initially create exposure candidates. A numerical delay effect stays an explicit scenario until operating evidence supports it. Retain unchanged-plan outcomes when constraints do not justify a different action.

We still create labelled sample quotes, BOMs, capacity, inventory, maintenance windows and freight/milestone records. No universal free live port-closure or container-status source was verified. APIs do not supply private quotations or prove predictive advantage; forecasts need past-only evaluation against baselines.

## Avoid hidden dependencies

Exchangerate.host now requires a key despite its catalog auth label. NewsAPI's Developer tier excludes staging/production; GNews free access has development/non-commercial restrictions. OpenCorporates proprietary use and OpenSanctions commercial screening need appropriate terms. Climatiq community/AI/ML restrictions require an intended-use agreement before embedding relevant functions. GCP credits do not pay these providers.

Aether-X creator-package version0.2.2 described two live Brazilian ports, reference seeds elsewhere and synthetic trends; newer source-by-port behavior was not verified. Do not present it as universal live congestion. No directory MCP package or vendor script was installed during this research.

Use existing Gemini extraction and the Google speech path first. Additional OCR/ML/voice providers must demonstrate a measured improvement worth their access, rights and integration cost.
