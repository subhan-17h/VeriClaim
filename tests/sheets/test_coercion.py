"""Turning what a cell displays into a value a database can hold.

Every one of these conversions has a wrong answer that looks right. `"1,250"` read as text
sorts before `"9"`. `"82%"` stored as 82 is wrong by two orders of magnitude against a
sibling column where Excel stored 0.82 for the same thing. `"(4,500)"` read as a positive
number flips the sign on a recovery. And `"N/A"` coerced to the string "N/A" turns a
numeric column into a text one for every row in it.

The rule throughout: a value that cannot be read as its column's kind becomes NULL and
says so, rather than being forced into something plausible.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from vericlaim.sheets.coercion import coerce, postgres_type


def value(raw, kind: str):
    return coerce(raw, kind).value


# ------------------------------------------------------------------ numbers


def test_a_thousands_separator_is_not_part_of_the_number() -> None:
    assert value("1,250", "number") == Decimal("1250")


def test_a_number_already_stored_as_one_is_left_alone() -> None:
    assert value(1250, "number") == Decimal("1250")


def test_a_decimal_survives_the_trip() -> None:
    assert value("1,250.75", "number") == Decimal("1250.75")


def test_a_word_in_a_number_column_becomes_null_and_says_so() -> None:
    coerced = coerce("twelve", "number")

    assert coerced.value is None
    assert coerced.ok is False


# ------------------------------------------------------------------ money


def test_a_currency_symbol_is_not_part_of_the_amount() -> None:
    assert value("PKR 250,000", "currency") == Decimal("250000")


def test_a_trailing_currency_code_is_stripped_too() -> None:
    assert value("250,000 PKR", "currency") == Decimal("250000")


@pytest.mark.parametrize(
    "raw",
    [
        "PKR 250,000",
        "250,000 PKR",
        "Rs. 250,000",
        "Rs 250,000",
        "₨250,000",
        "$250,000",
        "USD 250,000",
        "€250,000",
        "£250,000",
        "250,000",
    ],
)
def test_the_amount_reads_the_same_whatever_marks_the_currency(raw: str) -> None:
    """The corpus is PKR and this code does not know that.

    The unit is declared once per column in the reviewed contexts, and nothing in the
    parsing branches on it. A reader that understood only PKR would quietly fail every
    other mark and drag the whole column to text, and a second currency would then be a
    code change instead of a context-file one.
    """
    assert value(raw, "currency") == Decimal("250000")


def test_an_amount_in_brackets_is_negative() -> None:
    """Accounting notation. Read as positive it flips the sign on a recovery, and the
    total comes out too high by twice the amount."""
    assert value("(4,500)", "currency") == Decimal("-4500")


def test_a_bare_amount_needs_no_symbol() -> None:
    assert value(250000, "currency") == Decimal("250000")


# ------------------------------------------------------------------ percentages


def test_a_percentage_typed_as_text_becomes_a_fraction() -> None:
    """Stored as 82 it is a hundredfold out against the next column, where the same
    figure was entered as a percentage and Excel stored 0.82."""
    assert value("82%", "percent") == Decimal("0.82")


def test_a_percentage_excel_already_stored_as_a_fraction_is_not_divided_again() -> None:
    assert value(0.82, "percent") == Decimal("0.82")


def test_a_whole_number_percentage_is_still_read_as_typed() -> None:
    assert value("100%", "percent") == Decimal("1")


def test_a_fractional_percentage_keeps_its_precision() -> None:
    assert value("61.5%", "percent") == Decimal("0.615")


# ------------------------------------------------------------------ dates


def test_a_date_excel_stored_as_one_comes_through() -> None:
    assert value(datetime(2026, 3, 1), "date") == date(2026, 3, 1)


def test_a_date_typed_as_text_is_read() -> None:
    assert value("2026-03-01", "date") == date(2026, 3, 1)


def test_text_that_is_not_a_date_becomes_null() -> None:
    assert coerce("sometime in March", "date").ok is False


# ------------------------------------------------------------------ absence


@pytest.mark.parametrize("sentinel", ["N/A", "n/a", "-", "--", "TBD", "nil"])
def test_the_marks_people_use_for_missing_become_null(sentinel: str) -> None:
    """Not a failure: the analyst said there is no number, which is information."""
    coerced = coerce(sentinel, "number")

    assert coerced.value is None
    assert coerced.ok is True


def test_an_empty_cell_is_null() -> None:
    assert coerce(None, "number").value is None


def test_whitespace_alone_is_null() -> None:
    assert coerce("   ", "text").value is None


def test_a_sentinel_in_a_text_column_is_still_absence() -> None:
    assert coerce("N/A", "text").value is None


# ------------------------------------------------------------------ text


def test_text_is_trimmed_but_otherwise_untouched() -> None:
    assert value("  Lahore  ", "text") == "Lahore"


def test_a_number_in_a_text_column_keeps_its_written_form() -> None:
    assert value(12, "text") == "12"


# ------------------------------------------------------------------ the schema


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("text", "TEXT"),
        ("number", "NUMERIC"),
        ("currency", "NUMERIC"),
        ("percent", "NUMERIC"),
        ("date", "DATE"),
        ("boolean", "BOOLEAN"),
        ("empty", "TEXT"),
    ],
)
def test_each_kind_has_a_column_type(kind: str, expected: str) -> None:
    assert postgres_type(kind) == expected


def test_money_is_numeric_rather_than_floating_point() -> None:
    """A double precision column loses paisa, and a total that is out by a rounding error
    is exactly what the observer's sum invariant would then report as a contradiction."""
    assert postgres_type("currency") == "NUMERIC"
    assert isinstance(value("250,000.25", "currency"), Decimal)
