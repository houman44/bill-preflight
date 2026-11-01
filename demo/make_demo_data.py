"""Generate the synthetic demo data (bills, POs, receipts, vendors).

All vendors, people, numbers and amounts are fictional. Field names follow
Procurify's public API reference (developer.procurify.com); only a subset of
each object's fields is populated. Run: python demo/make_demo_data.py
"""

from __future__ import annotations

import json
import re
import uuid
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

HERE = Path(__file__).parent
NS = uuid.UUID("6f1d2b1e-0000-4000-8000-000000000000")
CAD = {"id": 1, "name": "CAD"}


def uid(s: str) -> str:
    return str(uuid.uuid5(NS, s))


def m(x) -> Decimal:
    return Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


VENDORS = [
    (301, "Larkspur Office Supply Co.", "Vancouver", "BC"),
    (302, "Fernhill Lab Instruments Ltd.", "Burnaby", "BC"),
    (303, "Quarry Lane Facilities Services", "Richmond", "BC"),
    (304, "Bluewren IT Hardware Inc.", "Surrey", "BC"),
    (305, "Tidewater Packaging Ltd.", "Mississauga", "ON"),
]
VNAME = {v[0]: v[1] for v in VENDORS}

TAXES = {
    2: ("GST 5%", Decimal("5")),
    3: ("GST + PST (BC) 12%", Decimal("12")),
    7: ("HST (ON) 13%", Decimal("13")),
}

# PO id, vendor id, PO date, lines: (order item id, line no, name, sku, qty, price, account code)
POS = [
    (4101, 301, "2026-09-15T17:20:00Z", [(9101, 1, "Copy paper, letter, 10-ream case", "LOS-CP-10", 20, "48.50", "6100-00"),
                                         (9102, 2, "Toner cartridge TN-660", "LOS-TN660", 6, "89.00", "6100-00")]),
    (4102, 301, "2026-10-02T16:05:00Z", [(9103, 1, "Ergonomic task chair", "LOS-CH-ERG", 4, "412.00", "6100-00")]),
    (4103, 302, "2026-09-20T18:40:00Z", [(9104, 1, "Nitrile gloves, box of 100", "FLI-GLV-M", 50, "14.20", "6200-00"),
                                         (9105, 2, "Pipette tips 200uL, rack of 96", "FLI-PT200", 30, "22.00", "6200-00")]),
    (4104, 303, "2026-10-01T15:00:00Z", [(9106, 1, "HVAC quarterly maintenance visit", "QL-HVAC-Q", 1, "1840.00", "6300-00")]),
    (4105, 304, "2026-10-05T19:10:00Z", [(9107, 1, "Laptop 14in, 32GB", "BW-LT14-32", 10, "1650.00", "1520-00"),
                                         (9108, 2, "USB-C docking station", "BW-DOCK-C", 10, "189.00", "1520-00")]),
    (4106, 305, "2026-09-25T14:30:00Z", [(9109, 1, "Corrugated box 18x12x12, bundle of 25", "TP-BX181212", 40, "31.75", "5100-00")]),
    (4107, 302, "2026-10-10T17:55:00Z", [(9110, 1, "Centrifuge tubes 50mL, case of 500", "FLI-CT50", 12, "64.00", "6200-00")]),
    (4108, 301, "2026-10-12T16:45:00Z", [(9111, 1, "Whiteboard markers, box of 12", "LOS-WBM-12", 15, "18.40", "6100-00")]),
    (4109, 301, "2026-10-18T20:15:00Z", [(9112, 1, "Desk organizer, mesh", "LOS-ORG-M", 10, "24.00", "6100-00")]),
    (4110, 303, "2026-10-03T15:25:00Z", [(9113, 1, "Window cleaning, October", "QL-WIN-OCT", 1, "620.00", "6300-00")]),
    (4111, 305, "2026-10-06T13:50:00Z", [(9114, 1, "Corrugated box 18x12x12, bundle of 25 (standing order)", "TP-BX181212", 80, "31.75", "5100-00")]),
]

# Receipts: (receipt id, order item id, pass qty, fail qty, received date)
RECEIPTS = [
    (8001, 9101, 20, 0, "2026-09-29"), (8002, 9102, 6, 0, "2026-09-29"),
    (8003, 9103, 4, 0, "2026-10-09"),
    (8004, 9104, 50, 0, "2026-10-01"), (8005, 9105, 30, 0, "2026-10-01"),
    (8006, 9106, 1, 0, "2026-10-17"),
    (8007, 9107, 8, 1, "2026-10-20"), (8008, 9108, 10, 0, "2026-10-20"),  # 9 laptops arrived, 1 damaged, 1 backordered
    (8009, 9109, 40, 0, "2026-10-03"),
    (8010, 9110, 12, 0, "2026-10-17"),
    (8011, 9111, 15, 0, "2026-10-20"),
    (8012, 9112, 10, 0, "2026-10-22"),
    (8013, 9113, 1, 0, "2026-10-24"),
    (8014, 9114, 40, 0, "2026-10-13"), (8015, 9114, 40, 0, "2026-10-17"),
]

LINE = {li[0]: (po[0], li) for po in POS for li in po[3]}

# Bills: (bill id, vendor, invoice number, invoice date, lines [(order item, qty, unit cost, tax id)],
#         other costs [(name, amount)], stated-total override, note)
BILLS = [
    (7001, 301, "INV-00417", "2026-10-14", [(9101, 20, "48.50", 2), (9102, 6, "89.00", 2)], [], None),
    (7002, 301, "INV-00431", "2026-10-16", [(9103, 4, "412.00", 2)], [("Freight", "45.00")], None),
    (7003, 302, "FLI-2026-0881", "2026-10-09", [(9104, 50, "14.20", 3), (9105, 30, "22.00", 3)], [], None),
    # BLOCK: total misread (1932.00 printed; drafted as 1982.00)
    (7004, 303, "QL-5521", "2026-10-20", [(9106, 1, "1840.00", 2)], [], "1982.00"),
    # BLOCK: bills 10 laptops; 8 received in good condition
    (7005, 304, "BW-INV-77812", "2026-10-22", [(9107, 10, "1650.00", 3), (9108, 10, "189.00", 3)], [], None),
    # BLOCK: HST tax not mapped to an ERP tax code
    (7006, 305, "TP-10093", "2026-10-08", [(9109, 40, "31.75", 7)], [], None),
    # REVIEW: unit price 2.5% above PO
    (7007, 302, "FLI-2026-0912", "2026-10-21", [(9110, 12, "65.60", 3)], [], None),
    (7008, 301, "INV-00452", "2026-10-23", [(9111, 15, "18.40", 2)], [], None),
    (7009, 303, "QL-5530", "2026-10-27", [(9113, 1, "620.00", 2)], [], None),
    # BLOCK: same vendor + same invoice number as 7001 after normalisation, different PO
    (7010, 301, "Inv 417", "2026-10-24", [(9112, 10, "24.00", 2)], [], None),
    (7011, 305, "TP-10101", "2026-10-15", [(9114, 40, "31.75", 2)], [], None),
    # REVIEW: same vendor, same total as 7011, four days apart, different invoice number
    (7012, 305, "TP-10107", "2026-10-19", [(9114, 40, "31.75", 2)], [], None),
]


def build():
    vendors = [
        {
            "id": vid, "name": name, "active": True, "city": city, "state_province": prov, "country": "Canada",
            "external_id": f"NS-VEND-{vid:04d}", "currency": 1,
        }
        for vid, name, city, prov in VENDORS
    ]
    vmap = {v["id"]: v for v in vendors}

    pos = []
    for pid, vid, date, lines in POS:
        items, subtotal = [], Decimal(0)
        for oid, ln, name, sku, qty, price, acct in lines:
            total = m(Decimal(qty) * Decimal(price))
            subtotal += total
            items.append({
                "id": oid, "PO_line_num": ln, "name": name, "sku": sku, "unit": "ea",
                "quantity": qty, "price": price, "total_cost": str(total), "accountCode": acct,
                "currency": CAD, "purchase_order": pid, "purchase_order_number": str(pid),
            })
        pos.append({
            "procurify_PO": pid, "number": str(pid), "uuid": uid(f"po-{pid}"), "date": date,
            "pref_vendor": {k: vmap[vid][k] for k in ("id", "name", "active", "external_id")},
            "vendor_name": vmap[vid]["name"], "purchased_currency": {**CAD, "rate": "1.00000000", "base": True},
            "subtotal": str(subtotal), "total": str(subtotal), "fulfilment_status": "full",
            "active": True, "version": 1, "order_items": items,
        })

    receipts = [
        {"id": rid, "order_item": oid, "pass_quantity": f"{p}.00", "fail_quantity": f"{fq}.00",
         "received_at": d, "created_at": f"{d}T21:00:00Z", "is_period_closed": False}
        for rid, oid, p, fq, d in RECEIPTS
    ]

    bills = []
    line_seq = 70000
    for bid, vid, inv, d, lines, costs, total_override in BILLS:
        items, subtotal, tax = [], Decimal(0), Decimal(0)
        for oid, qty, unit, tax_id in lines:
            line_seq += 1
            pid, li = LINE[oid]
            total = m(Decimal(qty) * Decimal(unit))
            tname, tpct = TAXES[tax_id]
            tamt = m(total * tpct / 100)
            subtotal += total
            tax += tamt
            items.append({
                "id": line_seq,
                "orderitem": {"id": oid, "accountCode": li[6]},
                "po": {"number": str(pid), "id": pid, "uuid": uid(f"po-{pid}")},
                "sku": li[3], "description": li[2], "unit": "ea",
                "quantity": f"{qty}.00", "unit_cost": Decimal(unit), "total_cost": total,
                "tax": tax_id, "tax_name": tname, "tax_percentage": f"{tpct}.000",
                "tax_amount": tamt, "tax_is_inclusive": False,
            })
        other = sum((Decimal(a) for _, a in costs), Decimal(0))
        invoice_total = Decimal(total_override) if total_override else subtotal + tax + other
        bills.append({
            "id": bid, "uuid": uid(f"bill-{bid}"), "status": 0, "type": 0, "invoice_created": True,
            "vendor": {"id": vid, "name": VNAME[vid]}, "currency": CAD,
            "invoice_number": inv, "invoice_date": f"{d}T00:00:00Z", "due_date": None, "gl_post_date": None,
            "subtotal_cost": subtotal, "tax_amount": tax,
            "costs": [{"name": n, "amount": a} for n, a in costs],
            "invoice_total": invoice_total,
            "items": items,
        })
    return bills, pos, receipts, vendors


def dump(obj, name):
    # Decimals are written as JSON numbers with exactly their decimal digits
    # (e.g. 1579.20), never via float.
    text = json.dumps(obj, indent=2, default=lambda o: f"__DEC__{o}__")
    text = re.sub(r'"__DEC__(-?[0-9.]+)__"', r"\1", text)
    (HERE / name).write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    bills, pos, receipts, vendors = build()
    dump({"data": bills}, "bills.json")
    dump({"data": pos}, "pos.json")
    dump({"data": receipts}, "receipts.json")
    dump({"data": vendors}, "vendors.json")
    print("wrote bills.json, pos.json, receipts.json, vendors.json")
