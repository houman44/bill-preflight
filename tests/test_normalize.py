from datetime import date
from decimal import Decimal

import pytest

from billcheck.normalize import invoice_tokens, normalize_invoice_number, parse_date, to_decimal

SAME = [
    ("INV-0001", "inv1"),
    ("INV-00417", "Inv 417"),
    ("INV-00417", "inv#417"),
    ("INV-00417", "  inv. 0417 "),
    ("INV-00417", "Invoice No. 417"),
    ("INV-00417", "417"),
    ("FLI-2026-0881", "fli 2026/881"),
    ("FLI-2026-0881", "FLI_2026_00881"),
    ("ＩＮＶ－４１７", "INV-417"),  # full-width characters (NFKC)
    ("AB-12-C", "ab12c"),  # letter/digit boundaries split tokens
]

DIFFERENT = [
    ("INV-417", "INV-4170"),
    ("FLI-2026-0881", "FLI-2026881"),  # token boundary is kept
    ("FLI-2026-0881", "FLI-2026-0882"),
    ("INV-417-A", "INV-417-B"),
    ("A417", "B417"),
]


@pytest.mark.parametrize("a,b", SAME)
def test_equal_after_normalisation(a, b):
    assert normalize_invoice_number(a) == normalize_invoice_number(b)


@pytest.mark.parametrize("a,b", DIFFERENT)
def test_not_equal_after_normalisation(a, b):
    assert normalize_invoice_number(a) != normalize_invoice_number(b)


def test_exact_outputs():
    assert normalize_invoice_number("INV-00417") == "417"
    assert normalize_invoice_number("FLI-2026-0881") == "fli-2026-881"
    assert normalize_invoice_number("000") == "0"
    assert normalize_invoice_number("INV") == "inv"  # prefix kept when it is the only token
    assert normalize_invoice_number(None) == ""
    assert normalize_invoice_number(" -/# ") == ""


def test_prefixes_are_configurable():
    assert normalize_invoice_number("INV-417", ignore_prefixes=[]) == "inv-417"
    assert normalize_invoice_number("INV-417", ignore_prefixes=[]) != normalize_invoice_number("417", ignore_prefixes=[])
    assert normalize_invoice_number("BILL-417", ignore_prefixes=["bill"]) == "417"


def test_tokens():
    assert invoice_tokens("Inv#0001/b") == ["1", "b"]


def test_to_decimal():
    assert to_decimal("1,234.50") == Decimal("1234.50")
    assert to_decimal(3) == Decimal(3)
    assert to_decimal(Decimal("0.10")) == Decimal("0.10")
    assert to_decimal(None) is None and to_decimal("") is None
    with pytest.raises(ValueError):
        to_decimal("12a")
    with pytest.raises(ValueError):
        to_decimal(True)


def test_parse_date():
    assert parse_date("2026-10-14T00:00:00Z") == date(2026, 10, 14)
    assert parse_date("2026-10-14") == date(2026, 10, 14)
    assert parse_date(None) is None
    with pytest.raises(ValueError):
        parse_date("14/10/2026")
