"""Normalisation with Decimal: numbers, dates, units, dated FX and expiry. No guessing."""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import pytest

from procure.offers import fx_rates
from procure.offers import normalize as nz
from procure.offers.normalize import (expiry_status, normalize_pricing, normalize_quantity, parse_decimal,
                                      unit_problem)


# ---------- numbers ----------

@pytest.mark.parametrize("text,expected", [
    ("USD 9,412.50 per tonne", "9412.50"),
    ("2.598,00", "2598.00"),
    ("50,000", "50000"),
    ("9,15", "9.15"),
    ("1.234.567,89", "1234567.89"),
    ("1,23,456.00", "123456.00"),  # Indian lakh grouping
    ("104.20", "104.20"),
    ("0.45359237", "0.45359237"),
])
def test_parse_decimal_reads_common_number_styles(text, expected):
    value = parse_decimal(text)
    assert isinstance(value, Decimal) and value == Decimal(expected)


def test_decimal_comma_documents_read_a_lone_comma_as_decimal():
    assert parse_decimal("1,000", decimal_comma=True) == Decimal("1.000")
    assert parse_decimal("1,000") == Decimal("1000")


def test_signed_source_numbers_keep_sign_with_or_without_currency():
    for text in ("-50 tonne", "Available -5 tonne", "premium -USD 95 per tonne", "premium USD -95 per tonne",
                 "premium -USD95/pertonne"):
        assert all(number < 0 for number in nz.numbers_in(text))
    assert parse_decimal("-50 tonne") == Decimal("-50")
    assert parse_decimal("+50 tonne") == Decimal("50")
    assert nz.numbers_in("-2,598", decimal_comma=False) == {Decimal("-2598")}
    assert nz.ambiguous_number_in("-2,598") is True
    assert nz.numbers_in("Available −5 tonne") == {Decimal("-5")}


def test_spaced_signs_bind_to_source_amounts():
    for text in ("Available - 5 tonne", "Available − 5 tonne", "premium - USD 95 per tonne",
                 "premium USD - 95 per tonne"):
        assert nz.numbers_in(text) == {Decimal("-5") if "5 tonne" in text else Decimal("-95")}
    assert nz.numbers_in("Available + 5 tonne") == {Decimal("5")}
    assert nz.numbers_in("Available 5 tonne") == {Decimal("5")}


@pytest.mark.parametrize("text", ["25.10.2026", "1,2345,678", "1,2,3", "no number here", "9.15 and 10.20"])
def test_unreadable_or_multiple_numbers_are_not_guessed(text):
    assert parse_decimal(text) is None


def test_document_number_style_is_detected():
    assert nz.uses_decimal_comma(["Price (USD/t)", "2.598,00"]) is True
    assert nz.uses_decimal_comma(["USD 9,412.50", "50,000"]) is False


# ---------- dates ----------

@pytest.mark.parametrize("text,iso", [
    ("Offer valid until: 31 October 2026", "2026-10-31"),
    ("Valid until: 25.10.2026", "2026-10-25"),
    ("28/10/2026", "2026-10-28"),
    ("Nov 5, 2026", "2026-11-05"),
    ("18 Oct 2026", "2026-10-18"),
    ("2026-11-15", "2026-11-15"),
    ("30 Sept 2026", "2026-09-30"),
])
def test_dates_in_common_formats(text, iso):
    assert nz.dates_in(text) == {iso}


def test_day_month_order_that_cannot_be_told_apart_is_not_read():
    assert nz.dates_in("05/06/2026") == set()


# ---------- units ----------

@pytest.mark.parametrize("value,kg", [
    ({"amount": "25", "unit": "tonne"}, "25000"),
    ({"amount": "12000", "unit": "kg"}, "12000"),
    ({"amount": "100", "unit": "lb"}, "45.359237"),
])
def test_quantities_convert_exactly_to_kg(value, kg):
    out = normalize_quantity(value)
    assert out["status"] == "ok"
    assert Decimal(out["kg"]) == Decimal(kg)
    assert out["original"] == value


def test_unknown_quantity_unit_is_unsupported_not_converted():
    out = normalize_quantity({"amount": "5", "unit": "dmt"})
    assert out["status"] == "unsupported_unit" and out["kg"] is None


@pytest.mark.parametrize("value", [None, {}, {"amount": "NaN", "unit": "tonne"},
                                         {"amount": "Infinity", "unit": "tonne"},
                                         {"amount": "-5", "unit": "tonne"},
                                         {"amount": "1e1000000", "unit": "tonne"},
                                         {"amount": "1" * 31, "unit": "tonne"}])
def test_invalid_quantity_is_bounded_and_unconverted(value):
    out = normalize_quantity(value)
    assert out["status"] == "invalid_amount" and out["kg"] is None


def test_thirty_digit_tonne_conversion_is_exact_not_context_rounded():
    amount = "123456789012345678901234567890"
    out = normalize_quantity({"amount": amount, "unit": "tonne"})
    assert out["status"] == "ok"
    assert out["kg"] == "123456789012345678901234567890000"


def test_exact_quantity_scale_boundary_and_malformed_unit():
    assert normalize_quantity({"amount": "0.1234567890", "unit": "tonne"})["kg"] == "123.456789"
    assert normalize_quantity({"amount": "0.12345678901", "unit": "tonne"})["status"] == "invalid_amount"
    assert normalize_quantity({"amount": "5", "unit": []})["status"] == "unsupported_unit"


@pytest.mark.parametrize("text,problem", [("price per ton", "ambiguous_unit"), ("USD 99 per dmt", "unsupported_unit"),
                                          ("per metric ton", None), ("per tonne", None)])
def test_ambiguous_and_unsupported_unit_words(text, problem):
    assert unit_problem(text) == problem


# ---------- FX ----------

class FakeResponse:
    def __init__(self, payload):
        self.text = json.dumps(payload)

    def json(self):
        return json.loads(self.text)


def test_same_currency_needs_no_rate():
    rate = fx_rates.get_rate("USD", "USD", dt.date(2026, 10, 7))
    assert (rate.status, rate.rate) == ("not_needed", Decimal("1"))


def test_live_rate_is_exact_decimal_with_date_and_source(monkeypatch):
    calls = []

    def fake_get(url, params=None, **_):
        calls.append((url, params))
        return FakeResponse({"amount": 1.0, "base": "EUR", "date": "2026-10-06", "rates": {"USD": 1.0893}})

    monkeypatch.setattr(fx_rates, "http_get", fake_get)
    rate = fx_rates.get_rate("EUR", "USD", dt.date(2026, 10, 7))
    assert rate.status == "live"
    assert rate.rate == Decimal("1.0893") and isinstance(rate.rate, Decimal)
    assert rate.rate_date == "2026-10-06"
    assert rate.source == "frankfurter_ecb"
    assert rate.retrieved_at
    assert calls[0][0].endswith("/2026-10-07") and calls[0][1] == {"base": "EUR", "symbols": "USD"}


def test_cached_rate_is_reused_offline(monkeypatch):
    monkeypatch.setattr(fx_rates, "http_get", lambda *a, **k: FakeResponse(
        {"amount": 1.0, "base": "CNY", "date": "2026-10-06", "rates": {"USD": 0.1387}}))
    fx_rates.get_rate("CNY", "USD", dt.date(2026, 10, 6))

    def offline(*_a, **_k):
        raise fx_rates.OfflineError("offline")

    monkeypatch.setattr(fx_rates, "http_get", offline)
    rate = fx_rates.get_rate("CNY", "USD", dt.date(2026, 10, 6))
    assert (rate.status, rate.rate, rate.rate_date) == ("cache", Decimal("0.1387"), "2026-10-06")


def test_no_rate_is_unavailable_never_synthetic(monkeypatch):
    def offline(*_a, **_k):
        raise fx_rates.OfflineError("offline")

    monkeypatch.setattr(fx_rates, "http_get", offline)
    rate = fx_rates.get_rate("ZAR", "INR", dt.date(2026, 1, 2))
    assert rate.status == "unavailable" and rate.rate is None
    assert "synthetic" not in json.dumps(rate.to_dict())


def test_response_without_the_requested_currency_is_unavailable(monkeypatch):
    monkeypatch.setattr(fx_rates, "http_get", lambda *a, **k: FakeResponse(
        {"amount": 1.0, "base": "EUR", "date": "2026-10-06", "rates": {"GBP": 0.84}}))
    rate = fx_rates.get_rate("EUR", "XAU", dt.date(2026, 10, 3))
    assert rate.status == "unavailable" and rate.rate is None


@pytest.mark.parametrize("payload", [
    {"amount": 1, "base": "GBP", "date": "2026-10-06", "rates": {"USD": 2}},
    {"amount": 1, "base": "EUR", "date": "2026-10-08", "rates": {"USD": 2}},
    {"amount": 1, "base": "EUR", "date": "tomorrow", "rates": {"USD": 2}},
    {"amount": 1, "base": "EUR", "date": "2026-10-06", "rates": {"USD": float("inf")}},
    {"amount": 2, "base": "EUR", "date": "2026-10-06", "rates": {"USD": 2}},
    {"amount": 1, "base": "EUR", "date": "2026-10-06", "rates": {"USD": -2}},
])
def test_invalid_live_fx_payload_is_unavailable_and_never_cached(monkeypatch, payload):
    monkeypatch.setattr(fx_rates, "http_get", lambda *a, **k: FakeResponse(payload))
    monkeypatch.setattr(fx_rates, "cache_latest", lambda *a, **k: None)
    writes = []
    monkeypatch.setattr(fx_rates, "cache_write", lambda *a, **k: writes.append((a, k)))
    rate = fx_rates.get_rate("EUR", "USD", dt.date(2026, 10, 7))
    assert rate.status == "unavailable" and rate.rate is None and not writes


def test_invalid_cached_fx_payload_is_unavailable(monkeypatch, tmp_path):
    cache = tmp_path / "bad-fx.json"
    cache.write_text(json.dumps({"amount": 1, "base": "GBP", "date": "2026-10-06",
                                 "rates": {"USD": 2}}))
    monkeypatch.setattr(fx_rates, "http_get", lambda *a, **k: (_ for _ in ()).throw(fx_rates.OfflineError("offline")))
    monkeypatch.setattr(fx_rates, "cache_latest", lambda *a, **k: cache)
    rate = fx_rates.get_rate("EUR", "USD", dt.date(2026, 10, 7))
    assert rate.status == "unavailable" and rate.rate is None


def test_valid_live_fx_survives_cache_write_failure(monkeypatch):
    payload = {"amount": 1, "base": "EUR", "date": "2026-10-06", "rates": {"USD": 1.2}}
    monkeypatch.setattr(fx_rates, "http_get", lambda *a, **k: FakeResponse(payload))
    monkeypatch.setattr(fx_rates, "cache_latest", lambda *a, **k: None)
    monkeypatch.setattr(fx_rates, "cache_write", lambda *a, **k: (_ for _ in ()).throw(OSError("private/path/secret")))
    rate = fx_rates.get_rate("EUR", "USD", dt.date(2026, 10, 7))
    assert (rate.status, rate.rate, rate.rate_date) == ("live", Decimal("1.2"), "2026-10-06")
    assert rate.source == fx_rates.SOURCE and rate.retrieved_at
    assert "cache_write_failed" in rate.message
    assert "private/path/secret" not in rate.message


@pytest.mark.parametrize("code", ["usd", "US", "EURO", ""])
def test_malformed_currency_codes_are_rejected(code):
    assert fx_rates.get_rate(code, "USD", dt.date(2026, 10, 7)).status == "invalid_currency"


# ---------- pricing ----------

def fixed(amount, currency, per_unit):
    return {"type": "fixed", "unit_price": {"amount": amount, "currency": currency}, "per_unit": per_unit}


def usd_rate(rate="1.0893", date="2026-10-06"):
    return lambda base, quote, on: fx_rates.FxRate(base, quote, Decimal(rate), date, "frankfurter_ecb",
                                                    "2026-10-07T00:00:00+00:00", "live", "")


def test_fixed_price_per_lb_becomes_exact_per_kg():
    out = normalize_pricing(fixed("1", "USD", "lb"), reporting_currency="USD", as_of=dt.date(2026, 10, 7))
    assert out["per_kg"]["amount"] == "2.2046226218"
    assert out["per_kg"]["currency"] == "USD"
    assert out["reporting"]["status"] == "not_needed"
    assert out["reporting"]["per_kg"] == {"amount": "2.2046226218", "currency": "USD"}


def test_converted_price_records_rate_and_date():
    out = normalize_pricing(fixed("9.15", "EUR", "kg"), reporting_currency="USD", as_of=dt.date(2026, 10, 7),
                            rate_lookup=usd_rate())
    assert out["reporting"]["per_kg"] == {"amount": "9.967095", "currency": "USD"}
    assert out["reporting"]["fx"]["rate"] == "1.0893" and out["reporting"]["fx"]["rate_date"] == "2026-10-06"
    assert out["original"] == fixed("9.15", "EUR", "kg")


def test_missing_rate_leaves_reporting_value_unavailable():
    def no_rate(base, quote, on):
        return fx_rates.FxRate(base, quote, None, None, "frankfurter_ecb", None, "unavailable", "offline, no cache")

    out = normalize_pricing(fixed("71800.00", "CNY", "tonne"), reporting_currency="USD",
                            as_of=dt.date(2026, 10, 7), rate_lookup=no_rate)
    assert out["per_kg"] == {"amount": "71.8", "currency": "CNY"}
    assert out["reporting"]["status"] == "unavailable" and out["reporting"]["per_kg"] is None


def test_index_linked_price_stays_index_plus_premium():
    value = {"type": "index_linked", "index": "LME Copper Grade A official cash settlement",
             "averaging_period": "monthly average, M+1", "premium": {"amount": "95.00", "currency": "USD"},
             "per_unit": "tonne"}
    out = normalize_pricing(value, reporting_currency="USD", as_of=dt.date(2026, 10, 7))
    assert out["type"] == "index_linked"
    assert out["index"] == value["index"]
    assert out["premium_per_kg"] == {"amount": "0.095", "currency": "USD"}
    assert "per_kg" not in out and "unit_price" not in out


@pytest.mark.parametrize("value", [None, {}, fixed("-5", "USD", "kg"), fixed("NaN", "USD", "kg"),
                                         {"type": "fixed", "per_unit": "kg", "unit_price": {}},
                                         fixed("1e1000000", "USD", "kg")])
def test_invalid_fixed_price_fails_closed_before_arithmetic(value):
    out = normalize_pricing(value, reporting_currency="USD", as_of=dt.date(2026, 10, 7))
    assert out["status"] == "invalid_amount" and "per_kg" not in out


def test_typed_negative_index_premium_keeps_sign():
    value = {"type": "index_linked", "index": "LME Copper", "averaging_period": "monthly average",
             "premium": {"amount": "-95", "currency": "USD"}, "per_unit": "tonne"}
    out = normalize_pricing(value, reporting_currency="USD", as_of=dt.date(2026, 10, 7))
    assert out["status"] == "ok" and out["premium_per_kg"] == {"amount": "-0.095", "currency": "USD"}


def test_malformed_currency_and_unit_shapes_fail_before_price_arithmetic():
    bad_currency = fixed("5", [], "kg")
    bad_unit = fixed("5", "USD", [])
    assert normalize_pricing(bad_currency, reporting_currency="USD", as_of=dt.date(2026, 10, 7))["status"] == "unsupported_currency"
    assert normalize_pricing(bad_unit, reporting_currency="USD", as_of=dt.date(2026, 10, 7))["status"] == "unsupported_unit"


@pytest.mark.parametrize("bad_rate", [
    fx_rates.FxRate("GBP", "USD", Decimal("2"), "2026-10-06", "mock", "now", "live", ""),
    fx_rates.FxRate("EUR", "USD", Decimal("Infinity"), "2026-10-06", "mock", "now", "live", ""),
    fx_rates.FxRate("EUR", "USD", Decimal("2"), "2026-10-08", "mock", "now", "live", ""),
])
def test_injected_fx_lookup_cannot_bypass_identity_finiteness_or_date(bad_rate):
    out = normalize_pricing(fixed("9.15", "EUR", "kg"), reporting_currency="USD", as_of=dt.date(2026, 10, 7),
                            rate_lookup=lambda *_: bad_rate)
    assert out["reporting"]["status"] == "unavailable"
    assert out["reporting"]["per_kg"] is None


@pytest.mark.parametrize("source,retrieved_at", [
    (None, "2026-10-07T00:00:00+00:00"),
    ("", "2026-10-07T00:00:00+00:00"),
    ("x" * 65, "2026-10-07T00:00:00+00:00"),
    ("mock", None),
    ("mock", "2026-10-07T00:00:00"),
    ("mock", "not-a-time"),
])
def test_injected_rate_without_bounded_source_and_aware_retrieval_cannot_convert(source, retrieved_at):
    rate = fx_rates.FxRate("EUR", "USD", Decimal("1.2"), "2026-10-06", source, retrieved_at, "live", "")
    out = normalize_pricing(fixed("9.15", "EUR", "kg"), reporting_currency="USD", as_of=dt.date(2026, 10, 7),
                            rate_lookup=lambda *_: rate)
    assert out["reporting"]["status"] == "unavailable"
    assert out["reporting"]["per_kg"] is None


def test_injected_rate_with_complete_provenance_converts():
    rate = fx_rates.FxRate("EUR", "USD", Decimal("1.2"), "2026-10-06", "mock",
                           "2026-10-07T00:00:00+00:00", "live", "")
    out = normalize_pricing(fixed("9.15", "EUR", "kg"), reporting_currency="USD", as_of=dt.date(2026, 10, 7),
                            rate_lookup=lambda *_: rate)
    assert out["reporting"]["per_kg"] == {"amount": "10.98", "currency": "USD"}
    assert out["reporting"]["fx"]["source"] == "mock"


# ---------- expiry ----------

@pytest.mark.parametrize("valid_until,status", [("2026-09-30", "expired"), ("2026-10-07", "valid"),
                                                ("2026-10-31", "valid"), (None, "unknown")])
def test_expiry_against_as_of_date(valid_until, status):
    assert expiry_status(valid_until, dt.date(2026, 10, 7)) == status


@pytest.mark.parametrize("value", ["not-a-date", "2026-02-30", 20261007, [], {}])
def test_malformed_expiry_is_invalid_not_valid_or_exception(value):
    assert expiry_status(value, dt.date(2026, 10, 7)) == "invalid"
