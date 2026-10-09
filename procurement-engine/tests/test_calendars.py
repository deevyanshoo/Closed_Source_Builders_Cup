"""Working-day lead time over a holiday provider. Offline: the bundled table is synthetic."""
from __future__ import annotations

import json
from datetime import date

import pytest

from procure.calendars import SyntheticHolidayProvider, working_days
from procure.calendars import synthetic as synthetic_module


@pytest.fixture(scope="module")
def provider():
    return SyntheticHolidayProvider()


def test_table_is_labelled_synthetic(provider):
    result = provider.holidays("IN", 2026)
    assert (result["status"], result["synthetic"]) == ("ok", True)
    assert result["source"]["provider_id"] == "synthetic-holiday-table"
    assert "SYNTHETIC" in result["source"]["notice"]
    assert [h["date"] for h in result["holidays"]] == ["2026-09-29", "2026-10-10", "2026-10-14"]
    assert result["weekend"] == ["saturday", "sunday"]


def test_lead_time_excludes_weekends_and_holidays(provider):
    r = working_days(provider, "IN", date(2026, 10, 8), date(2026, 10, 15))
    assert r["status"] == "ok"
    assert (r["working_days"], r["calendar_days"], r["weekend_days"]) == (4, 7, 2)
    assert r["holidays"] == [{"date": "2026-10-14", "name": "Synthetic closure day IN-A"}]
    assert r["synthetic"] is True
    assert "after the start date" in r["convention"]


def test_holiday_on_a_weekend_is_not_counted_twice(provider):
    r = working_days(provider, "IN", date(2026, 10, 8), date(2026, 10, 12))
    assert (r["working_days"], r["weekend_days"], r["holidays"]) == (2, 2, [])


def test_same_day_is_zero_and_reversed_range_is_invalid(provider):
    assert working_days(provider, "IN", date(2026, 10, 8), date(2026, 10, 8))["working_days"] == 0
    r = working_days(provider, "IN", date(2026, 10, 9), date(2026, 10, 8))
    assert (r["status"], r["working_days"]) == ("invalid_range", None)


def test_unsupported_country_and_year_are_explicit(provider):
    r = working_days(provider, "SG", date(2026, 10, 1), date(2026, 10, 5))
    assert (r["status"], r["country"], r["working_days"]) == ("unsupported_country", "SG", None)
    r = working_days(provider, "IN", date(2026, 12, 30), date(2027, 1, 4))
    assert (r["status"], r["year"], r["working_days"]) == ("unsupported_year", 2027, None)
    assert working_days(provider, "india", date(2026, 10, 1), date(2026, 10, 5))["status"] == "unsupported_country"
    assert working_days(provider, None, date(2026, 10, 1), date(2026, 10, 5))["status"] == "unsupported_country"


def test_missing_or_unlabelled_table_is_source_unavailable(tmp_path, monkeypatch):
    missing = SyntheticHolidayProvider(tmp_path / "absent.json")
    assert missing.holidays("IN", 2026)["status"] == "source_unavailable"
    assert working_days(missing, "IN", date(2026, 10, 8), date(2026, 10, 9))["status"] == "source_unavailable"
    real = json.loads(synthetic_module.TABLE.read_text())
    real["synthetic"] = False
    path = tmp_path / "unlabelled.json"
    path.write_text(json.dumps(real))
    assert SyntheticHolidayProvider(path).holidays("IN", 2026)["status"] == "source_unavailable"


def test_invalid_table_rows_make_the_source_unavailable(tmp_path):
    bad = json.loads(synthetic_module.TABLE.read_text())
    bad["countries"]["IN"]["holidays"].append({"date": "2026-02-30", "name": "Bad date"})
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad))
    assert SyntheticHolidayProvider(path).holidays("IN", 2026)["status"] == "source_unavailable"


def test_holiday_outside_the_requested_year_is_not_used(provider):
    assert all(h["date"].startswith("2026-") for h in provider.holidays("CN", 2026)["holidays"])
