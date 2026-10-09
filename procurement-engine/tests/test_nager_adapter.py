"""Nager.Date adapter against the public PublicHolidays response shape. MOCKED responses only:
these tests never make a network request, and the adapter has no default transport."""
from __future__ import annotations

import json
from datetime import date

import pytest

from procure.calendars import NagerDateProvider, working_days

# Shape of GET /api/v3/PublicHolidays/{year}/{countryCode}. Values are invented for the test.
MOCK_HOLIDAYS = [
    {"date": "2026-10-05", "localName": "Test Day One", "name": "Test Day One", "countryCode": "ZZ",
     "fixed": False, "global": True, "counties": None, "launchYear": None, "types": ["Public"]},
    {"date": "2026-10-06", "localName": "Test Regional Day", "name": "Test Regional Day", "countryCode": "ZZ",
     "fixed": False, "global": False, "counties": ["ZZ-01"], "launchYear": None, "types": ["Public"]},
    {"date": "2026-10-07", "localName": "Test Bank Day", "name": "Test Bank Day", "countryCode": "ZZ",
     "fixed": False, "global": True, "counties": None, "launchYear": None, "types": ["Bank"]},
]
WEEKENDS = {"ZZ": ["saturday", "sunday"], "SG": ["saturday", "sunday"]}


class MockFetch:
    def __init__(self, status: int, body: object = None, raw: bytes | None = None, error: Exception | None = None):
        self.status, self.body, self.raw, self.error, self.urls = status, body, raw, error, []

    def __call__(self, url: str):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.status, self.raw if self.raw is not None else json.dumps(self.body).encode()


def provider(fetch, weekends=WEEKENDS):
    return NagerDateProvider(fetch=fetch, weekends=weekends)


def test_there_is_no_default_transport():
    with pytest.raises(TypeError):
        NagerDateProvider(weekends=WEEKENDS)  # type: ignore[call-arg]


def test_public_holidays_are_read_from_the_documented_shape():
    fetch = MockFetch(200, MOCK_HOLIDAYS)
    r = provider(fetch).holidays("ZZ", 2026)
    assert fetch.urls == ["https://date.nager.at/api/v3/PublicHolidays/2026/ZZ"]
    assert (r["status"], r["synthetic"]) == ("ok", False)
    assert r["holidays"] == [{"date": "2026-10-05", "name": "Test Day One"}]
    assert r["regional_holidays"] == [{"date": "2026-10-06", "name": "Test Regional Day", "counties": ["ZZ-01"]}]
    assert r["other_types"] == [{"date": "2026-10-07", "name": "Test Bank Day", "types": ["Bank"]}]
    assert r["weekend"] == ["saturday", "sunday"]
    assert r["source"]["provider_id"] == "nager.date"


def test_regional_holidays_are_reported_not_applied():
    fetch = MockFetch(200, MOCK_HOLIDAYS)
    r = working_days(provider(fetch), "ZZ", date(2026, 10, 2), date(2026, 10, 9))
    assert r["status"] == "ok"
    assert r["working_days"] == 4  # 5 weekdays minus the one national public holiday
    assert r["holidays"] == [{"date": "2026-10-05", "name": "Test Day One"}]
    assert r["not_applied"] == [{"date": "2026-10-06", "name": "Test Regional Day", "reason": "regional_holiday"},
                                {"date": "2026-10-07", "name": "Test Bank Day", "reason": "not_a_public_holiday"}]


def test_india_and_uae_are_not_requested():
    fetch = MockFetch(200, MOCK_HOLIDAYS)
    for country in ("IN", "AE"):
        r = provider(fetch, {**WEEKENDS, country: ["saturday", "sunday"]}).holidays(country, 2026)
        assert (r["status"], r["reason"]) == ("unsupported_country", "not_in_nager_catalog")
    assert fetch.urls == []


def test_country_without_a_configured_weekend_is_unsupported():
    fetch = MockFetch(200, MOCK_HOLIDAYS)
    r = provider(fetch).holidays("FR", 2026)
    assert (r["status"], r["reason"]) == ("unsupported_country", "weekend_not_configured")
    assert fetch.urls == []


@pytest.mark.parametrize("status,status_out,reason", [
    (404, "unsupported_country", "unknown_country_code"),
    (400, "unsupported_year", "rejected_by_provider"),
    (204, "unsupported_year", "no_holidays_returned"),
    (500, "source_unavailable", "http_500"),
    (429, "source_unavailable", "http_429"),
])
def test_http_statuses_map_to_explicit_states(status, status_out, reason):
    r = provider(MockFetch(status, raw=b"")).holidays("ZZ", 2026)
    assert (r["status"], r["reason"]) == (status_out, reason)
    assert r["holidays"] == []


def test_empty_list_is_not_read_as_a_year_without_holidays():
    r = provider(MockFetch(200, [])).holidays("ZZ", 2026)
    assert (r["status"], r["reason"]) == ("unsupported_year", "no_holidays_returned")


@pytest.mark.parametrize("fetch", [
    MockFetch(200, raw=b"not json"),
    MockFetch(200, {"date": "2026-10-05"}),
    MockFetch(200, [{"date": "2026-13-01", "name": "x", "global": True, "types": ["Public"]}]),
    MockFetch(200, [{"date": "2027-01-01", "name": "Wrong year", "global": True, "types": ["Public"]}]),
    MockFetch(200, [{"date": "2026-10-05", "global": True, "types": ["Public"]}]),
    MockFetch(0, error=OSError("network down")),
])
def test_bad_responses_and_transport_errors_are_source_unavailable(fetch):
    r = provider(fetch).holidays("ZZ", 2026)
    assert r["status"] == "source_unavailable" and r["holidays"] == []


@pytest.mark.parametrize("country_code", ["SG", None])
def test_response_for_another_country_or_without_country_is_unavailable(country_code):
    row = {**MOCK_HOLIDAYS[0], "countryCode": country_code}
    result = working_days(provider(MockFetch(200, [row])), "ZZ", date(2026, 10, 2), date(2026, 10, 9))
    assert (result["status"], result["reason"], result["working_days"]) == (
        "source_unavailable", "invalid_response", None)


def test_invalid_requests_are_not_sent():
    fetch = MockFetch(200, MOCK_HOLIDAYS)
    assert provider(fetch).holidays("zz/../x", 2026)["status"] == "unsupported_country"
    assert provider(fetch).holidays("ZZ", 1700)["status"] == "unsupported_year"
    assert fetch.urls == []
