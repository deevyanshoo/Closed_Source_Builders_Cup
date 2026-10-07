"""Dated reference FX rates for offer comparison, from Frankfurter (ECB reference rates).

Reuses the existing adapter's HTTP layer (timeouts, retries, OFFLINE handling) and raw cache.
Any pair of three-letter codes can be requested; there is no fixed currency list. The rate is
read as an exact Decimal and returned with the date Frankfurter reports for it, which can be an
earlier business day than the date asked for.

Order: live, then the cached response for the same pair and date. There is no synthetic
fallback here: without a real rate the result is `unavailable` and no conversion happens.
A reference rate is not an executable bank rate.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
from dataclasses import dataclass
from decimal import Decimal

from ..db import now_iso
from ..ingest.http import OfflineError, cache_latest, cache_time, cache_write, http_get
from .normalize import _bounded_decimal

log = logging.getLogger(__name__)

SOURCE = "frankfurter_ecb"
ENDPOINTS = [  # same providers as ingest/fx.py, single-date form
    ("https://api.frankfurter.dev/v1/{day}", lambda b, q: {"base": b, "symbols": q}),
    ("https://api.frankfurter.app/{day}", lambda b, q: {"from": b, "to": q}),
]
_CODE = re.compile(r"^[A-Z]{3}$")


@dataclass(frozen=True)
class FxRate:
    base: str
    quote: str
    rate: Decimal | None  # units of `quote` per 1 `base`
    rate_date: str | None
    source: str | None
    retrieved_at: str | None
    status: str  # not_needed | live | cache | unavailable | invalid_currency
    message: str

    def to_dict(self) -> dict:
        return {"base": self.base, "quote": self.quote, "rate": None if self.rate is None else str(self.rate),
                "rate_date": self.rate_date, "source": self.source, "retrieved_at": self.retrieved_at,
                "status": self.status, "message": self.message}


def _parse(text: str, base: str, quote: str, on: dt.date | None) -> tuple[Decimal, str]:
    payload = json.loads(text, parse_float=Decimal)
    if not isinstance(payload, dict) or payload.get("base") != base:
        raise ValueError("FX response base does not match request")
    date_text = payload.get("date")
    if not isinstance(date_text, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_text):
        raise ValueError("FX response date is invalid")
    try:
        rate_date = dt.date.fromisoformat(date_text)
    except ValueError as exc:
        raise ValueError("FX response date is invalid") from exc
    if rate_date > (on or dt.date.today()):
        raise ValueError("FX response date is after requested date")
    amount = payload.get("amount", 1)
    if isinstance(amount, bool) or _bounded_decimal(amount, allow_negative=False) != Decimal("1"):
        raise ValueError("FX response does not price one base unit")
    rates = payload.get("rates")
    if not isinstance(rates, dict) or quote not in rates:
        raise ValueError("FX response quote does not match request")
    rate = _bounded_decimal(rates[quote], allow_negative=False)
    if rate is None or rate <= 0:
        raise ValueError("FX response rate is not finite and positive")
    return rate, date_text


def get_rate(base: str, quote: str, on: dt.date | None = None) -> FxRate:
    if not (isinstance(base, str) and isinstance(quote, str) and _CODE.fullmatch(base)
            and _CODE.fullmatch(quote)):
        return FxRate(base, quote, None, None, None, None, "invalid_currency",
                      "Currency codes must be three upper-case letters")
    if base == quote:
        return FxRate(base, quote, Decimal("1"), None, None, None, "not_needed", "Same currency")

    day = on.isoformat() if on else "latest"
    prefix = f"frankfurter_rate_{base}_{quote}_{day}"
    errors = []
    for url, params in ENDPOINTS:
        try:
            resp = http_get(url.format(day=day), params=params(base, quote), retries=2)
            rate, rate_date = _parse(resp.text, base, quote, on)
        except OfflineError as exc:
            errors.append(str(exc))
            break
        except Exception as exc:  # noqa: BLE001 - provider or payload failure, try the next source
            errors.append(f"{url.split('/{')[0]}: {exc}")
            log.warning("FX %s/%s on %s failed at %s: %s", base, quote, day, url, exc)
            continue
        message = url.split("/{")[0]
        try:
            cache_write(prefix, ".json", resp.text.encode())
        except Exception:  # cache persistence cannot invalidate a checked live response
            message += "; cache_write_failed"
            log.warning("FX cache_write_failed for validated %s/%s on %s", base, quote, day)
        return FxRate(base, quote, rate, rate_date, SOURCE, now_iso(), "live", message)

    cached = cache_latest(prefix, ".json")
    if cached is not None:
        try:
            rate, rate_date = _parse(cached.read_text(), base, quote, on)
            return FxRate(base, quote, rate, rate_date, SOURCE, cache_time(cached).isoformat(), "cache",
                          f"Live unavailable; cached response {cached.name}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"cache: {exc}")
    return FxRate(base, quote, None, None, SOURCE, None, "unavailable",
                  "No live or cached reference rate. " + "; ".join(errors)[:300])
