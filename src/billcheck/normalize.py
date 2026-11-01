"""Normalisation helpers: invoice numbers, money, quantities, dates.

Everything here is pure and deterministic. Money and quantities are handled
as ``decimal.Decimal`` end to end; floats never enter a comparison.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# Leading tokens dropped from an invoice number, as long as at least one
# token remains after dropping. Configurable via rules config
# ("duplicate_check.ignore_prefixes").
DEFAULT_IGNORE_PREFIXES: tuple[str, ...] = ("invoice", "inv", "no")

# A token is a run of letters or a run of digits. Everything else
# (spaces, '-', '/', '.', '#', '_', etc.) is a separator and is discarded.
_TOKEN_RE = re.compile(r"[^\W\d_]+|\d+")

CENT = Decimal("0.01")


def invoice_tokens(raw: object, ignore_prefixes: tuple[str, ...] | list[str] = DEFAULT_IGNORE_PREFIXES) -> list[str]:
    """Return the normalised token list for an invoice number.

    Exact rule, applied in order:
      1. Unicode NFKC normalisation (full-width digits become ASCII digits),
         then case-folding ("INV" -> "inv").
      2. Split into tokens: maximal runs of letters, or maximal runs of digits.
         A letter/digit boundary also splits ("inv1" -> "inv", "1").
         All other characters are separators and are dropped.
      3. In each digit token, strip leading zeros ("00417" -> "417";
         "000" -> "0").
      4. While the first token is in ``ignore_prefixes`` and more than one
         token remains, drop it ("inv", "417" -> "417").
    """
    if raw is None:
        return []
    text = unicodedata.normalize("NFKC", str(raw)).casefold()
    tokens = _TOKEN_RE.findall(text)
    out: list[str] = []
    for tok in tokens:
        if tok.isdigit():
            tok = tok.lstrip("0") or "0"
        out.append(tok)
    prefixes = {p.casefold() for p in ignore_prefixes}
    while len(out) > 1 and out[0] in prefixes:
        out.pop(0)
    return out


def normalize_invoice_number(raw: object, ignore_prefixes: tuple[str, ...] | list[str] = DEFAULT_IGNORE_PREFIXES) -> str:
    """Normalised invoice number: tokens from :func:`invoice_tokens` joined by '-'.

    Examples (default prefixes)::

        "INV-00417"  -> "417"
        "Inv 417"    -> "417"
        "inv#417"    -> "417"
        "INV-0001"   -> "1"
        "inv1"       -> "1"
        "FLI-2026-0881" -> "fli-2026-881"
        "FLI 2026/881"  -> "fli-2026-881"
        "FLI-2026881"   -> "fli-2026881"   (token boundary kept: not equal)
    """
    return "-".join(invoice_tokens(raw, ignore_prefixes))


def to_decimal(value: object, field: str = "value") -> Decimal | None:
    """Convert JSON number/string to Decimal. ``None`` and "" give ``None``."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field}: boolean is not a number")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        # Only reachable if a caller bypassed the Decimal-parsing loader.
        return Decimal(repr(value))
    if isinstance(value, str):
        s = value.strip().replace(",", "")
        if s == "":
            return None
        try:
            return Decimal(s)
        except InvalidOperation as exc:
            raise ValueError(f"{field}: not a number: {value!r}") from exc
    raise ValueError(f"{field}: unsupported type {type(value).__name__}")


def money(value: Decimal) -> Decimal:
    """Round half-up to cents."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def fmt_money(value: Decimal | None) -> str:
    if value is None:
        return "-"
    return f"{money(value):,.2f}"


def dstr(value: Decimal | None) -> str | None:
    """Canonical string for a Decimal in evidence (no exponent, trailing zeros trimmed to 2dp min for money)."""
    if value is None:
        return None
    if value == value.to_integral_value():
        return str(value.quantize(Decimal(1)))
    return format(value.normalize(), "f")


def mstr(value: Decimal | None) -> str | None:
    """Money as a fixed 2-decimal string."""
    if value is None:
        return None
    return str(money(value))


def parse_date(value: object) -> date | None:
    """Parse 'YYYY-MM-DD' or an ISO date-time; only the calendar date is kept.

    Time zone is ignored on purpose: the date as written is the date used.
    """
    if value is None or value == "":
        return None
    s = str(value).strip()
    try:
        return date.fromisoformat(s[:10])
    except ValueError as exc:
        raise ValueError(f"not a date: {value!r}") from exc
