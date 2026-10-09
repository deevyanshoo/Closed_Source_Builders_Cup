# Holiday calendars (offline)

Working-day lead time between two dates for one country, from a holiday provider. Nothing in this
package makes a network request.

## Pieces

- `provider.py`: the `HolidayProvider` interface (`holidays(country, year)`) and
  `working_days(provider, country, start, end)`. Working days are the days after `start` up to and
  including `end`, less weekend days and national public holidays.
- `synthetic.py`: `SyntheticHolidayProvider`, the bundled table in
  `tests/fixtures/calendars/synthetic_holidays.json`. **Every date and name in it is invented.** The
  Saturday and Sunday weekend is a fixture assumption. It covers the fixture countries (IN, CN, CL,
  AE, BR, ZA) for 2026 only, so the dispatch workspace can show a lead time offline. It is not a
  source of real holidays.
- `nager.py`: `NagerDateProvider`, written against the public Nager.Date
  `GET /api/v3/PublicHolidays/{year}/{countryCode}` response shape and tested only with mocked
  responses (`tests/test_nager_adapter.py`). It has no default transport, so it cannot make a request
  until someone passes one in. No live request has been made.

## States

Every result has a `status`. A number is given only when it is `ok`.

| Status | Meaning |
| --- | --- |
| `ok` | The provider states the country, year and weekend |
| `unsupported_country` | Not covered, no weekend configured, or no country code |
| `unsupported_year` | Not covered for that year. For Nager, an empty list or HTTP 204 also lands here: it is never read as a year without holidays |
| `source_unavailable` | Missing, unreadable, unlabelled or malformed table, a transport error, or an unexpected HTTP status |

Nothing is guessed:
- **Regional holidays** (Nager `global: false`) are listed under `not_applied` and never removed from working days, because the facility's region is not known.
- **Other observances** (bank or school days, for example) are treated the same way.

## India and Nager.Date

`docs/API_Integration_Guide.md` records that the live Nager.Date catalog omitted India (IN) and the
UAE (AE). The adapter therefore refuses both with `not_in_nager_catalog` and never requests them.
**IN stays unsupported for real holidays until another source is approved.** The IN rows in the
synthetic table are invented and must not be read as Indian public holidays.

## Known limits

- Dates are UTC calendar dates, so a local date can differ by one day.
- Plant shifts, port cutoffs and facility calendars are separate inputs and are not modelled.
- Using Nager.Date live needs approval for the transport, the hosted service's terms, and the countries and years to cover.
