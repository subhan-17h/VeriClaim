"""Turn what a cell displays into a value a database can hold.

Every conversion here has a wrong answer that looks entirely right, which is why each one
is its own rule rather than a single call to ``float()``:

* ``"1,250"`` read as text sorts before ``"9"``, and no aggregate over the column works.
* ``"82%"`` stored as ``82`` is a hundredfold out against the next column, where the same
  figure was entered as a percentage and Excel stored ``0.82`` for it.
* ``"(4,500)"`` is accounting notation for a negative. Read as positive it flips the sign
  on a recovery, and the total comes out too high by twice the amount.
* ``"N/A"`` coerced to the string ``"N/A"`` drags a numeric column down to text for every
  row in it -- the failure the reference implementation's type inference had.

The rule throughout: a value that cannot be read as its column's kind becomes NULL **and
says so**, rather than being forced into something plausible. The distinction between
"the analyst wrote N/A" and "this did not parse" is kept, because the first is information
and the second is a defect.

Money is NUMERIC, never double precision. A double loses paisa, and a total out by a
rounding error is precisely what the observer's sum invariant would report as a
contradiction in the data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from vericlaim.sheets.profiler import NULL_SENTINELS

# Currency symbols and codes that decorate an amount without being part of it.
CURRENCY_RE = re.compile(r"(pkr|rs\.?|usd|inr|[$£€₨¥])", re.IGNORECASE)
BRACKETED_RE = re.compile(r"^\((?P<inner>.+)\)$")
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y")

POSTGRES_TYPES = {
    "text": "TEXT",
    "number": "NUMERIC",
    "currency": "NUMERIC",
    "percent": "NUMERIC",
    "date": "DATE",
    "boolean": "BOOLEAN",
    "empty": "TEXT",
}

HUNDRED = Decimal(100)


@dataclass(frozen=True, slots=True)
class Coerced:
    """One converted cell, and whether the conversion succeeded.

    ``ok`` is False only for a value that *should* have parsed and did not. An empty cell
    or a sentinel is a successful conversion to NULL: the analyst said there is no number
    there, which is information rather than a defect.
    """

    value: Any
    ok: bool = True
    raw: str = ""


def postgres_type(kind: str) -> str:
    """The column type that holds values of this kind without losing them."""
    return POSTGRES_TYPES.get(kind, "TEXT")


def coerce(value: Any, kind: str) -> Coerced:
    """Convert one cell to its column's kind."""
    if value is None:
        return Coerced(None, True, "")
    if isinstance(value, str):
        raw = value.strip()
        if not raw or raw.lower() in NULL_SENTINELS:
            return Coerced(None, True, raw)
    else:
        raw = str(value)

    if kind == "text":
        return Coerced(raw, True, raw)
    if kind in {"number", "currency"}:
        return _decimal(value, raw)
    if kind == "percent":
        return _percent(value, raw)
    if kind == "date":
        return _date(value, raw)
    if kind == "boolean":
        return _boolean(value, raw)
    return Coerced(raw, True, raw)


def _decimal(value: Any, raw: str) -> Coerced:
    if isinstance(value, bool):
        return Coerced(None, False, raw)
    if isinstance(value, (int, float, Decimal)):
        return Coerced(Decimal(str(value)), True, raw)

    text = CURRENCY_RE.sub("", raw).strip()
    negative = False
    bracketed = BRACKETED_RE.match(text)
    if bracketed:
        # Accounting notation: (4,500) is minus four and a half thousand.
        negative = True
        text = bracketed.group("inner").strip()
    text = text.replace(",", "").replace(" ", "")
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return Coerced(None, False, raw)
    return Coerced(-number if negative else number, True, raw)


def _percent(value: Any, raw: str) -> Coerced:
    """Read a percentage as the fraction it represents.

    A cell formatted as a percentage is already stored as a fraction by Excel, so a
    numeric value is taken as-is. Only text carrying a `%` is divided -- dividing both
    would halve every percentage that was entered properly.
    """
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return Coerced(Decimal(str(value)), True, raw)
    if "%" not in raw:
        return _decimal(value, raw)
    coerced = _decimal(value, raw.replace("%", ""))
    if not coerced.ok or coerced.value is None:
        return Coerced(None, False, raw)
    return Coerced(coerced.value / HUNDRED, True, raw)


def _date(value: Any, raw: str) -> Coerced:
    if isinstance(value, datetime):
        return Coerced(value.date(), True, raw)
    if isinstance(value, date):
        return Coerced(value, True, raw)
    for pattern in DATE_FORMATS:
        try:
            return Coerced(datetime.strptime(raw, pattern).date(), True, raw)
        except ValueError:
            continue
    return Coerced(None, False, raw)


def _boolean(value: Any, raw: str) -> Coerced:
    if isinstance(value, bool):
        return Coerced(value, True, raw)
    lowered = raw.casefold()
    if lowered in {"true", "yes", "y", "1"}:
        return Coerced(True, True, raw)
    if lowered in {"false", "no", "n", "0"}:
        return Coerced(False, True, raw)
    return Coerced(None, False, raw)
