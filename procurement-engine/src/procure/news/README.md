# News snapshots (issue #49, offline)

This module turns GDELT and Google News RSS headlines into dated, deduplicated, rights-aware snapshots. Its rows
are exactly the shape #14's point-in-time event gate accepts (`procure.event_features.project_events`). It does not
change `ingest/news.py`, `news_signal.py` or the gate.

## Pieces

- **`fetch.py`:** reuses `ingest.http.http_get`, which refuses under `OFFLINE=1`. It uses the `GDELT_URL` and `RSS_URL`
  endpoints and the per-material queries from `config/materials.yaml`, with the same parameters as `ingest/news.py`.
  - Fetch states: `ok`, `empty`, `offline`, `rate_limited` (HTTP 429), `provider_failure`, `malformed_payload`.
  - `recorded_fetches` replays recorded responses through the same parsers.
- **`snapshots.py`:** `build_snapshots(fetches, as_of=...)` and `gate_rows(result)`.
- **`config/news_sources_v1.json`:** provider terms, attribution and permitted uses, plus the tagging rules.

## Snapshot fields

| Field | Meaning |
|---|---|
| `providers` | Providers that returned the item |
| `source.source_id` | Publisher domain |
| `story_id` | Canonical article URL if stated, else feed GUID; RSS adopts a URL story with the same publisher and headline |
| `revision_id` | One exact headline of the story |
| `url` | Canonical URL: no `www`, no fragment, no `utm_*` |
| `title` | Headline. This is untrusted text |
| `first_seen_at` | Our earliest retrieval |
| `retrieved_at` | All retrievals of this snapshot |
| `provider_first_seen_at` | GDELT `seendate` |
| `published_at` | When the feed states it, with `published_at_status` set to `stated` or `not_stated` |
| `content_sha256` | Hash of the headline identity |
| `language` | As the provider states it, or `unknown` |
| `rights` | Per provider: status, permitted uses, attribution and terms note |
| `synthetic` and `sample` | Labels for synthetic or sample items |

Only headlines, links and metadata are stored, never article text.

## Rules

- **Entities:** tagged only by word-boundary phrase matches on the headline.
  - Material, port and route each come from their own phrase lists.
  - Country comes from the ISO 3166 names and aliases.
  - Event type comes from the strike, closure, export restriction, weather and sanctions phrases. It is set only when exactly one type matches; otherwise it stays `unresolved` or `ambiguous`.
  - Unmatched dimensions are listed in `entities.unresolved`.
- **Instruction-like headlines:** they are flagged `embedded_instruction` and get no gate row until reviewed. Nothing in a headline is followed.
- **Deduplication:**
  - The same publisher and headline seen via two providers is one snapshot.
  - A repeated fetch of identical content keeps the earliest retrieval.
  - A changed headline for the same story is a new revision, so the gate picks the revision known at its origin.
- **Corroboration:** counts distinct publishers with the same normalised headline (wire copies). The same publisher via two providers counts once.
- **Cutoff:** a fetch retrieved after `as_of` is rejected whole.
- **Gate rows:**
  - `source_kind` is `news`.
  - `effect_class` maps from the event type: delay, export restriction or policy. It is never price.
  - `confidence` is 0, because no source states one and none is inferred.
  - Permissions, corroboration and scope are snapshots dated at first sight.
- **Output:** candidate evidence only, never a confirmed operating status or a price effect.

## Rights

Both providers are `unreviewed` and grant no use, so the gate currently excludes every row with `permission_denied`.
That is the intended state until a rights review records permitted uses.
- **GDELT:** states its data is open with attribution.
- **Google News RSS:** for personal, non-commercial reading under Google's terms.

Confirm both on the providers' pages before any live run. Headlines and links belong to their publishers.

## Tests

`tests/test_news_snapshots.py` runs offline from `tests/fixtures/news`: hand-written, synthetic GDELT JSON and RSS XML
with `.test` domains. It covers:
- a malformed feed
- cross-provider duplicates
- a later revision
- an item retrieved after `as_of`
- GDELT 429
- `OFFLINE=1`
- an instruction-like headline
- acceptance by the real gate

## Official policy notices (issue #12, saved HTML only)

`policy.py` parses saved listing pages from four sources: DGFT trade notices, CBIC customs notifications, PIB press
releases and JNPA port circulars. It produces the same item shape, so `build_snapshots` makes the same snapshots.

- **Snapshot fields:**
  - `source.kind` is `official`.
  - The stated listing date is kept as `published_date`, with `published_at_status` set to `date_only`. No time of day is invented.
- **No fetching.**
  - There is no fetch function, and `fetch_allowed` is false for every source in `config/policy_sources_v1.json`.
  - robots.txt and terms are recorded as **not checked**, each with the exact thing to read before any fetch.
- **Parser:**
  - Uses the standard library `html.parser`, so it adds no dependency.
  - [Scrapling](https://github.com/D4Vinci/Scrapling) is a proposed swap that needs approval as a new dependency.
- **Layouts:** each source has a versioned layout (container, header labels, row shape).
  - A page without them is `layout_changed`, never an empty listing.
  - Rows with an invalid date or shape are rejected individually.
- **Page states:** `ok`, `empty`, `layout_changed`, `malformed_payload`, `provider_failure`, `rate_limited`.
- **Deduplication against news:** a notice and a news item with the same canonical URL, publisher and headline are one snapshot. One publisher seen twice is not corroboration.
- **Fixtures:** the saved pages in `tests/fixtures/policy` are hand-written in the general style of each site, not copies of real pages, and are labelled synthetic.
