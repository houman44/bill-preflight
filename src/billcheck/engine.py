"""Run the rules over a batch and derive a verdict per bill.

Verdict per bill: BLOCK if any rule FAILs, REVIEW if any rule WARNs,
PASS otherwise.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from billcheck.loader import Inputs
from billcheck.normalize import mstr, parse_date, to_decimal
from billcheck.rules import (
    FAIL,
    RULES,
    WARN,
    Context,
    RuleResult,
    bill_po_ids,
    bill_total,
    currency_name,
    norm_inv,
    vendor_id,
)

BLOCK, REVIEW, OK = "BLOCK", "REVIEW", "PASS"
VERDICT_ORDER = {BLOCK: 0, REVIEW: 1, OK: 2}

IDEMPOTENCY_KEY_VERSION = "v1"


def idempotency_key(bill: dict, config: dict) -> str:
    """Deterministic key for one bill: SHA-256 over vendor id, normalised
    invoice number and invoice total (2 dp). The same invoice from the same
    vendor for the same amount always gets the same key, on any machine, on
    any retry."""
    total = bill_total(bill)
    material = "|".join(
        [
            f"billcheck-idem-{IDEMPOTENCY_KEY_VERSION}",
            f"vendor={vendor_id(bill)}",
            f"invoice={norm_inv(bill, config)}",
            f"amount={mstr(total) if total is not None else ''}",
        ]
    )
    return "BPF-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


@dataclass
class BillVerdict:
    bill: dict
    verdict: str
    results: list[RuleResult]
    idempotency_key: str
    normalised_invoice_number: str

    @property
    def bill_id(self) -> int:
        return self.bill.get("id")

    def failed(self) -> list[RuleResult]:
        return [r for r in self.results if r.status == FAIL]

    def warned(self) -> list[RuleResult]:
        return [r for r in self.results if r.status == WARN]

    def to_dict(self) -> dict:
        b = self.bill
        v = b.get("vendor") or {}
        return {
            "bill_id": b.get("id"),
            "invoice_number": b.get("invoice_number"),
            "invoice_number_normalised": self.normalised_invoice_number,
            "vendor": {"id": vendor_id(b), "name": v.get("name") if isinstance(v, dict) else None},
            "po_ids": bill_po_ids(b),
            "invoice_date": str(parse_date(b.get("invoice_date"))) if b.get("invoice_date") else None,
            "invoice_total": mstr(bill_total(b)),
            "currency": currency_name(b.get("currency")),
            "verdict": self.verdict,
            "idempotency_key": self.idempotency_key,
            "rules": [r.to_dict() for r in self.results],
        }


def verdict_for(results: list[RuleResult]) -> str:
    statuses = {r.status for r in results}
    if FAIL in statuses:
        return BLOCK
    if WARN in statuses:
        return REVIEW
    return OK


def build_context(inputs: Inputs, as_of: date) -> Context:
    pos_by_id: dict[int, dict] = {}
    line_by_id: dict[int, tuple[dict, dict]] = {}
    for po in inputs.pos:
        pid = po.get("procurify_PO")
        if pid is None:
            raise ValueError("purchase order without procurify_PO id")
        pos_by_id[pid] = po
        for line in po.get("order_items") or []:
            line_by_id[line["id"]] = (po, line)
    received: dict[int, Decimal] = {}
    rows: dict[int, list[dict]] = {}
    for r in inputs.receipts:
        lid = r.get("order_item")
        rows.setdefault(lid, []).append(r)
        received[lid] = received.get(lid, Decimal(0)) + (to_decimal(r.get("pass_quantity"), "pass_quantity") or Decimal(0))
    vendors = {v["id"]: v for v in inputs.vendors} if inputs.vendors is not None else None
    bills_sorted = sorted(inputs.bills, key=lambda b: b.get("id"))
    ids = [b.get("id") for b in bills_sorted]
    if None in ids or len(set(ids)) != len(ids):
        raise ValueError("every bill needs a unique integer 'id'")
    return Context(
        config=inputs.config,
        as_of=as_of,
        tolerance=to_decimal(inputs.config["amount_tolerance"], "amount_tolerance"),
        pos_by_id=pos_by_id,
        line_by_id=line_by_id,
        received_by_line=received,
        receipts_by_line=rows,
        vendors_by_id=vendors,
        all_bills=bills_sorted,
    )


def resolve_as_of(config: dict, override: str | None = None) -> date:
    if override:
        return parse_date(override)
    if config.get("as_of_date"):
        return parse_date(config["as_of_date"])
    return date.today()


def check_bill(bill: dict, ctx: Context) -> BillVerdict:
    results = [fn(bill, ctx) for _, fn in RULES]
    return BillVerdict(
        bill=bill,
        verdict=verdict_for(results),
        results=results,
        idempotency_key=idempotency_key(bill, ctx.config),
        normalised_invoice_number=norm_inv(bill, ctx.config),
    )


def run_checks(inputs: Inputs, as_of: date) -> tuple[Context, list[BillVerdict]]:
    ctx = build_context(inputs, as_of)
    return ctx, [check_bill(b, ctx) for b in ctx.all_bills]
