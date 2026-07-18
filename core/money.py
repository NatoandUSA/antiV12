#!/usr/bin/env python3
"""core.money — the ONE canonical Decimal money authority for the toolkit.

Phase 7.0 confirmed the repository had NO Decimal money convention (the Phase 5/6 engines and the
economics FEASIBILITY gate use float/pandas, which is unsafe for money). Phase 7.1M introduces this
single authority so PPC economics never touches a binary float.

Rules (enforced, not advisory):
  * money/rate values enter at public production boundaries as CANONICAL DECIMAL STRINGS;
  * binary ``float`` inputs are REJECTED (a float can never represent 0.1 exactly);
  * ``bool`` is REJECTED (it is an ``int`` subclass, never a money value);
  * NaN / Infinity / blank / scientific-exponent forms are REJECTED;
  * MISSING (``None``) stays MISSING — it is never coerced to 0;
  * arithmetic keeps full ``Decimal`` precision; quantization happens ONLY at declared boundaries;
  * currency output quantizes to 0.01 with ROUND_HALF_UP; a ``Decimal`` is serialized as a plain
    string, never as a float;
  * currency codes are carried separately from the amount.

There is no fallback, no clamp, and no default here — defaults (e.g. the economics $8 minimum-profit
floor) live in the consuming engine, which records their source. This module only parses, validates,
computes-safe, and serializes.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

# canonical quantums
CENTS = Decimal("0.01")          # USD-style currency output
RATE_QUANTUM = Decimal("0.000001")   # 6 dp for serialized rates (internal keeps full precision)


class MoneyError(ValueError):
    """A money/rate value violated the Decimal contract (bad type, blank, NaN/Inf, exponent, range)."""


def _reject_bad_scalar(value, field):
    label = f" for {field}" if field else ""
    if isinstance(value, bool):
        raise MoneyError(f"boolean is not a money value{label}: {value!r}")
    if isinstance(value, float):
        raise MoneyError(f"binary float rejected{label} (pass a canonical decimal string): {value!r}")


def parse_decimal_string(value, *, field=None, allow_missing=True):
    """Parse a canonical decimal string (or exact int/Decimal) into a ``Decimal``.

    ``None`` -> ``None`` when ``allow_missing`` (MISSING stays MISSING); otherwise raises.
    Rejects float, bool, blank, NaN, Infinity, and scientific-exponent forms.
    """
    if value is None:
        if allow_missing:
            return None
        raise MoneyError(f"required decimal missing{(' for ' + field) if field else ''}")
    _reject_bad_scalar(value, field)
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, int):
        d = Decimal(value)
    elif isinstance(value, str):
        s = value.strip()
        if s == "":
            raise MoneyError(f"blank decimal rejected{(' for ' + field) if field else ''}")
        low = s.lower()
        if "nan" in low or "inf" in low:
            raise MoneyError(f"NaN/Infinity rejected{(' for ' + field) if field else ''}: {value!r}")
        if "e" in low:
            raise MoneyError(f"scientific-exponent form rejected{(' for ' + field) if field else ''}: {value!r}")
        try:
            d = Decimal(s)
        except InvalidOperation as e:
            raise MoneyError(f"not a valid decimal{(' for ' + field) if field else ''}: {value!r}") from e
    else:
        raise MoneyError(f"unsupported money type{(' for ' + field) if field else ''}: {type(value).__name__}")
    if d.is_nan() or d.is_infinite():
        raise MoneyError(f"NaN/Infinity rejected{(' for ' + field) if field else ''}: {value!r}")
    return d


# public alias — parsing IS the serialization boundary's inverse.
def parse_money_decimal(value, *, field=None, allow_missing=True):
    return parse_decimal_string(value, field=field, allow_missing=allow_missing)


def parse_nonnegative_decimal(value, *, field=None, allow_missing=True):
    """A cost/reserve: >= 0. Negative values are rejected (costs are never negative)."""
    d = parse_decimal_string(value, field=field, allow_missing=allow_missing)
    if d is not None and d < 0:
        raise MoneyError(f"negative value rejected{(' for ' + field) if field else ''}: {value!r}")
    return d


def parse_positive_decimal(value, *, field=None, allow_missing=True):
    """A price/positive amount: > 0."""
    d = parse_decimal_string(value, field=field, allow_missing=allow_missing)
    if d is not None and d <= 0:
        raise MoneyError(f"non-positive value rejected{(' for ' + field) if field else ''}: {value!r}")
    return d


def parse_rate_decimal(value, *, field=None, allow_missing=True, lo=Decimal("0"), hi=Decimal("1"),
                       lo_inclusive=False, hi_inclusive=True):
    """A rate/ratio (e.g. conversion rate) constrained to (lo, hi]. Validated separately from money."""
    d = parse_decimal_string(value, field=field, allow_missing=allow_missing)
    if d is None:
        return None
    below = (d < lo) or (d == lo and not lo_inclusive)
    above = (d > hi) or (d == hi and not hi_inclusive)
    if below or above:
        raise MoneyError(f"rate out of range{(' for ' + field) if field else ''}: {value!r} "
                         f"(expected {'[' if lo_inclusive else '('}{lo}, {hi}{']' if hi_inclusive else ')'})")
    return d


def parse_positive_int(value, *, field=None, allow_missing=True):
    """A positive whole count (capacity, clicks, days). Rejects float and non-positive."""
    if value is None:
        if allow_missing:
            return None
        raise MoneyError(f"required count missing{(' for ' + field) if field else ''}")
    if isinstance(value, bool):
        raise MoneyError(f"boolean is not a count{(' for ' + field) if field else ''}: {value!r}")
    if isinstance(value, float):
        raise MoneyError(f"float rejected for count{(' for ' + field) if field else ''}: {value!r}")
    try:
        iv = int(value) if not isinstance(value, str) else int(value.strip())
    except (ValueError, TypeError) as e:
        raise MoneyError(f"not a valid integer{(' for ' + field) if field else ''}: {value!r}") from e
    if iv <= 0:
        raise MoneyError(f"non-positive count rejected{(' for ' + field) if field else ''}: {value!r}")
    return iv


def parse_nonnegative_int(value, *, field=None, allow_missing=True):
    """A non-negative whole count (e.g. current backlog may be 0)."""
    if value is None:
        if allow_missing:
            return None
        raise MoneyError(f"required count missing{(' for ' + field) if field else ''}")
    if isinstance(value, bool):
        raise MoneyError(f"boolean is not a count{(' for ' + field) if field else ''}: {value!r}")
    if isinstance(value, float):
        raise MoneyError(f"float rejected for count{(' for ' + field) if field else ''}: {value!r}")
    try:
        iv = int(value) if not isinstance(value, str) else int(value.strip())
    except (ValueError, TypeError) as e:
        raise MoneyError(f"not a valid integer{(' for ' + field) if field else ''}: {value!r}") from e
    if iv < 0:
        raise MoneyError(f"negative count rejected{(' for ' + field) if field else ''}: {value!r}")
    return iv


def quantize_currency(d, quantum=CENTS):
    """Quantize a money Decimal to the currency quantum (default 0.01) with ROUND_HALF_UP."""
    if d is None:
        return None
    return d.quantize(quantum, rounding=ROUND_HALF_UP)


def quantize_rate(d, quantum=RATE_QUANTUM):
    if d is None:
        return None
    return d.quantize(quantum, rounding=ROUND_HALF_UP)


def _plain(d):
    """A plain (non-scientific) decimal string. ``format(d, 'f')`` never emits an exponent."""
    return format(d, "f")


def decimal_to_canonical_string(d):
    """Serialize a Decimal as a plain string (never a float, never scientific notation).
    MISSING stays MISSING (``None``)."""
    if d is None:
        return None
    if isinstance(d, (float, bool)):
        raise MoneyError(f"refusing to serialize a non-Decimal money value: {d!r}")
    if not isinstance(d, Decimal):
        d = parse_decimal_string(d)
    return _plain(d)


def serialize_currency(d, quantum=CENTS):
    """Canonical currency string: quantized to the quantum, plain (e.g. '12.99')."""
    return decimal_to_canonical_string(quantize_currency(d, quantum))


def serialize_rate(d, quantum=RATE_QUANTUM):
    return decimal_to_canonical_string(quantize_rate(d, quantum))


def sum_required_decimals(values, *, field=None):
    """Sum a sequence of already-parsed non-missing Decimals. A missing (``None``) member blocks the
    sum — the caller must have proven every required input present first (never sum over a MISSING)."""
    total = Decimal("0")
    for i, v in enumerate(values):
        if v is None:
            raise MoneyError(f"cannot sum a MISSING value{(' in ' + field) if field else ''} at index {i}")
        if not isinstance(v, Decimal):
            raise MoneyError(f"sum member is not a Decimal{(' in ' + field) if field else ''}: {v!r}")
        total += v
    return total


def safe_divide(numerator, denominator):
    """Decimal division that returns None on a non-positive/None denominator (never raises, never inf)."""
    if numerator is None or denominator is None:
        return None
    if denominator <= 0:
        return None
    with localcontext() as ctx:
        ctx.prec = 50
        return numerator / denominator
