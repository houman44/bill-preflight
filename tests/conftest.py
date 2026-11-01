"""Small builders for test inputs (same shapes as the demo data)."""

from __future__ import annotations

import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from billcheck.engine import run_checks  # noqa: E402
from billcheck.loader import Inputs, load_config_obj  # noqa: E402
from billcheck.normalize import parse_date  # noqa: E402

DEMO = ROOT / "demo"
TAX_RATES = {2: Decimal("5"), 3: Decimal("12"), 7: Decimal("13")}


def m(x) -> Decimal:
    return Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def make_po(pid=1, vendor=10, date="2026-10-01T12:00:00Z", lines=((100, 10, "5.00", "6100-00"),), currency="CAD", vendor_active=True):
    return {
        "procurify_PO": pid,
        "number": str(pid),
        "date": date,
        "pref_vendor": {"id": vendor, "name": f"Vendor {vendor}", "active": vendor_active},
        "purchased_currency": {"id": 1, "name": currency},
        "order_items": [
            {"id": oid, "PO_line_num": i + 1, "name": f"Item {oid}", "quantity": qty, "price": price, "accountCode": acct}
            for i, (oid, qty, price, acct) in enumerate(lines)
        ],
    }


def make_bill(bid=1, vendor=10, inv="A-1", date="2026-10-10", lines=((100, 10, "5.00", 2, 1),), costs=(), total=None,
              currency="CAD", subtotal=None, tax=None, account_code=None):
    """lines: (order item id, qty, unit cost, tax id, po id)."""
    items, sub, tx = [], Decimal(0), Decimal(0)
    for n, (oid, qty, unit, tax_id, po) in enumerate(lines):
        line_total = m(Decimal(qty) * Decimal(unit))
        rate = TAX_RATES.get(tax_id, Decimal(0))
        t = m(line_total * rate / 100)
        sub += line_total
        tx += t
        oi = {"id": oid}
        if account_code is not None:
            oi["accountCode"] = account_code
        items.append({
            "id": bid * 100 + n, "orderitem": oi if oid is not None else None,
            "po": {"id": po, "number": str(po)} if po is not None else None,
            "quantity": str(qty), "unit_cost": Decimal(unit), "total_cost": line_total,
            "tax": tax_id, "tax_name": f"tax {tax_id}", "tax_percentage": str(rate), "tax_amount": t,
            "tax_is_inclusive": False,
        })
    other = sum((Decimal(a) for _, a in costs), Decimal(0))
    sub_v = Decimal(subtotal) if subtotal is not None else sub
    tax_v = Decimal(tax) if tax is not None else tx
    return {
        "id": bid,
        "vendor": {"id": vendor, "name": f"Vendor {vendor}"},
        "currency": {"id": 1, "name": currency},
        "invoice_number": inv,
        "invoice_date": f"{date}T00:00:00Z" if date else None,
        "subtotal_cost": sub_v,
        "tax_amount": tax_v,
        "costs": [{"name": n, "amount": a} for n, a in costs],
        "invoice_total": Decimal(total) if total is not None else sub_v + tax_v + other,
        "items": items,
    }


def receipt(oid, qty, fail=0):
    return {"id": oid * 10, "order_item": oid, "pass_quantity": str(qty), "fail_quantity": str(fail)}


BASE_CONFIG = {
    "rules_config_name": "test",
    "as_of_date": "2026-10-31",
    "closed_periods": [{"name": "Q3", "start": "2026-07-01", "end": "2026-09-30"}],
    "erp_mappings": {
        "tax_codes": {"2": {"rate": "5", "erp_tax_code": "GST5"}, "3": {"rate": "12", "erp_tax_code": "GSTPST12"}},
        "account_codes": {"6100-00": {"erp_account": "6100 Office", "active": True},
                          "6999-00": {"erp_account": "6999 Old", "active": False}},
    },
}


def make_inputs(bills, pos=None, receipts=None, config=None, vendors=None) -> Inputs:
    cfg = load_config_obj({**BASE_CONFIG, **(config or {})})
    return Inputs(bills=bills, pos=pos if pos is not None else [make_po()],
                  receipts=receipts if receipts is not None else [receipt(100, 10)],
                  vendors=vendors, config=cfg, files=[])


def check(bills, **kw):
    inputs = make_inputs(bills, **kw)
    _, verdicts = run_checks(inputs, parse_date(inputs.config["as_of_date"]))
    return {v.bill_id: v for v in verdicts}


def rule(verdict, rid):
    return next(r for r in verdict.results if r.rule_id == rid)


def codes(verdict, rid=None):
    return sorted(f.code for r in verdict.results if rid in (None, r.rule_id) for f in r.findings)


@pytest.fixture
def demo_paths():
    return {
        "bills": DEMO / "bills.json",
        "pos": DEMO / "pos.json",
        "receipts": DEMO / "receipts.json",
        "config": DEMO / "rules.json",
        "vendors": DEMO / "vendors.json",
    }
