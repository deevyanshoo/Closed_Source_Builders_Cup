"""Bounded exact decimal and instant operations for the local production screen."""
from __future__ import annotations

from datetime import datetime, timezone
from fractions import Fraction
import re

_AMOUNT = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_INSTANT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$")
_INPUT_DIGITS = 1024
_FRACTION_LIMIT = 10 ** 4096
_OUTPUT_LIMIT = 10 ** 2048
_CHUNK = 128


class ScreenError(Exception):
    def __init__(self, reason: str, *details: str):
        self.reason = reason
        self.details = tuple(sorted(set(details)))


def _parse_integer(text: str) -> int:
    value = 0
    for start in range(0, len(text), _CHUNK):
        part = text[start:start + _CHUNK]
        value = value * 10 ** len(part) + int(part)
    return value


def _integer_text(value: int) -> str:
    if value < 0:
        return "-" + _integer_text(-value)
    base = 10 ** _CHUNK
    parts = []
    while value >= base:
        value, tail = divmod(value, base)
        parts.append(tail)
    return str(value) + "".join(f"{part:0{_CHUNK}d}" for part in reversed(parts))


def bounded(value: Fraction) -> Fraction:
    if abs(value.numerator) >= _FRACTION_LIMIT or value.denominator >= _FRACTION_LIMIT:
        raise ScreenError("invalid_quantity")
    return value


def amount(raw: object, *, positive: bool = False) -> Fraction:
    if not isinstance(raw, str) or not _AMOUNT.fullmatch(raw):
        raise ScreenError("invalid_quantity")
    whole, separator, fraction = raw.partition(".")
    digits = whole + fraction
    if len(digits) > _INPUT_DIGITS:
        raise ScreenError("invalid_quantity")
    value = bounded(Fraction(_parse_integer(digits), 10 ** len(fraction) if separator else 1))
    if positive and value <= 0:
        raise ScreenError("invalid_quantity")
    return value


def add(left: Fraction, right: Fraction) -> Fraction:
    return bounded(left + right)


def subtract(left: Fraction, right: Fraction) -> Fraction:
    return bounded(left - right)


def multiply(left: Fraction, right: Fraction) -> Fraction:
    return bounded(left * right)


def text(value: Fraction) -> str:
    bounded(value)
    if value == 0:
        return "0"
    denominator = value.denominator
    twos = fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        raise ScreenError("invalid_quantity")
    scale = max(twos, fives)
    if scale > 2048:
        raise ScreenError("invalid_quantity")
    coefficient = abs(value.numerator) * 2 ** (scale - twos) * 5 ** (scale - fives)
    if coefficient >= _OUTPUT_LIMIT:
        raise ScreenError("invalid_quantity")
    digits = _integer_text(coefficient)
    if scale:
        digits = digits.rjust(scale + 1, "0")
        whole, fractional = digits[:-scale], digits[-scale:]
        digits = whole + "." + fractional.rstrip("0") if fractional.rstrip("0") else whole
    return "-" + digits if value < 0 else digits


def instant(raw: object) -> datetime:
    if not isinstance(raw, str) or not _INSTANT.fullmatch(raw):
        raise ScreenError("invalid_timestamp")
    if not raw.endswith("Z") and (int(raw[-5:-3]) > 23 or int(raw[-2:]) > 59):
        raise ScreenError("invalid_timestamp")
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if value.tzinfo is None or value.utcoffset() is None:
            raise ScreenError("invalid_timestamp")
        return value.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ScreenError("invalid_timestamp") from exc


def iso(value: datetime) -> str:
    return value.isoformat()


def seconds(start: datetime, end: datetime) -> Fraction:
    delta = end - start
    micros = ((delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds)
    return bounded(Fraction(micros, 1_000_000))
