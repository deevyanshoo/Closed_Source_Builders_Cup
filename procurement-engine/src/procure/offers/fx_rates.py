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


def _parse(text: str, quote: str) -> tuple[Decimal, str]:
    payload = json.loads(text, parse_float=Decimal)
    rate = Decimal(str(payload["rates"][quote]))
    if rate <= 0:
        raise ValueError(f"non-positive rate {rate}")
    return rate, str(payload["date"])


def get_rate(base: str, quote: str, on: dt.date | None = None) -> FxRate:
    if not (_CODE.match(base or "") and _CODE.match(quote or "")):
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
            rate, rate_date = _parse(resp.text, quote)
            cache_write(prefix, ".json", resp.text.encode())
            return FxRate(base, quote, rate, rate_date, SOURCE, now_iso(), "live", url.split("/{")[0])
        except OfflineError as exc:
            errors.append(str(exc))
            break
        except Exception as exc:  # noqa: BLE001 - provider or payload failure, try the next source
            errors.append(f"{url.split('/{')[0]}: {exc}")
            log.warning("FX %s/%s on %s failed at %s: %s", base, quote, day, url, exc)

    cached = cache_latest(prefix, ".json")
    if cached is not None:
        try:
            rate, rate_date = _parse(cached.read_text(), quote)
            return FxRate(base, quote, rate, rate_date, SOURCE, cache_time(cached).isoformat(), "cache",
                          f"Live unavailable; cached response {cached.name}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"cache: {exc}")
    return FxRate(base, quote, None, None, SOURCE, None, "unavailable",
                  "No live or cached reference rate. " + "; ".join(errors)[:300])
