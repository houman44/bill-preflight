"""The eight deterministic pre-sync rules.

Each rule takes one bill plus a read-only :class:`Context` and returns a
:class:`RuleResult` with status PASS, WARN or FAIL and the evidence behind it.
No rule reads the clock, the network, or a random source; given the same
inputs and config, every rule returns the same result.

Field names on bills, purchase orders, receipts and vendors follow Procurify's
public API reference. Where the reference does not define a field the rules
need, the README says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Callable

from billcheck.normalize import (
    dstr,
    money,
    mstr,
    normalize_invoice_number,
    parse_date,
    to_decimal,
)

RULES_VERSION = "1.0.0"

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
_RANK = {PASS: 0, WARN: 1, FAIL: 2}

# Default severity of every finding code. Overridable per code via
# config["severity_overrides"], e.g. {"BEFORE_PO_DATE": "FAIL"}.
DEFAULT_SEVERITY: dict[str, str] = {
    # R1 duplicates
    "DUPLICATE_INVOICE_NUMBER": FAIL,
    "POSSIBLE_DUPLICATE_AMOUNT_DATE": WARN,
    "INVOICE_NUMBER_MISSING": FAIL,
    # R2 arithmetic
    "AMOUNT_MISSING": FAIL,
    "LINE_MATH": FAIL,
    "SUBTOTAL_MISMATCH": FAIL,
    "TAX_SUM_MISMATCH": FAIL,
    "TOTAL_MISMATCH": FAIL,
    "INCLUSIVE_TAX_UNSUPPORTED": WARN,
    # R3 tax codes
    "TAX_CODE_MISSING": FAIL,
    "TAX_CODE_UNMAPPED": FAIL,
    "TAX_RATE_MISMATCH": WARN,
    # R4 account codes
    "ACCOUNT_CODE_MISSING": FAIL,
    "ACCOUNT_CODE_UNMAPPED": FAIL,
    "ACCOUNT_CODE_INACTIVE": FAIL,
    # R5 dates
    "BILL_DATE_MISSING": FAIL,
    "BEFORE_PO_DATE": WARN,
    "FUTURE_DATE": FAIL,
    "CLOSED_PERIOD": FAIL,
    # R6 vendor
    "PO_NOT_FOUND": FAIL,
    "VENDOR_MISMATCH": FAIL,
    "VENDOR_INACTIVE": FAIL,
    "VENDOR_STATUS_UNKNOWN": WARN,
    # R7 three-way match
    "NO_PO_LINE": WARN,
    "PO_LINE_NOT_FOUND": FAIL,
    "BILLED_OVER_RECEIVED": FAIL,
    "BILLED_OVER_ORDERED": FAIL,
    "PRICE_VARIANCE_WARN": WARN,
    "PRICE_VARIANCE_FAIL": FAIL,
    # R8 currency
    "CURRENCY_MISSING": FAIL,
    "CURRENCY_MISMATCH": FAIL,
}


@dataclass
class Finding:
    code: str
    status: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"code": self.code, "status": self.status, "message": self.message, "evidence": self.evidence}


@dataclass
class RuleResult:
    rule_id: str
    title: str
    status: str
    summary: str
    findings: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "title": self.title,
            "status": self.status,
            "summary": self.summary,
            "findings": [f.to_dict() for f in self.findings],
        }


@dataclass
class Context:
    config: dict
    as_of: date
    tolerance: Decimal
    pos_by_id: dict[int, dict]
    line_by_id: dict[int, tuple[dict, dict]]  # orderitem id -> (po, order_item)
    received_by_line: dict[int, Decimal]
    receipts_by_line: dict[int, list[dict]]
    vendors_by_id: dict[int, dict] | None
    all_bills: list[dict]  # sorted by id

    def severity(self, code: str) -> str:
        override = (self.config.get("severity_overrides") or {}).get(code)
        status = override or DEFAULT_SEVERITY[code]
        if status not in _RANK:
            raise ValueError(f"severity_overrides.{code}: must be PASS, WARN or FAIL")
        return status

    def finding(self, code: str, message: str, **evidence: Any) -> Finding:
        return Finding(code=code, status=self.severity(code), message=message, evidence=evidence)

    def earlier_bills(self, bill: dict) -> list[dict]:
        """Bills that precede this one (lower Procurify bill id). Ids are
        assigned in creation order, so "earlier" means "drafted first"."""
        bid = bill.get("id")
        return [b for b in self.all_bills if b.get("id") < bid]


def worst(statuses: list[str]) -> str:
    return max(statuses, key=lambda s: _RANK[s], default=PASS)


def _result(rule_id: str, title: str, findings: list[Finding], pass_summary: str) -> RuleResult:
    # Findings downgraded to PASS by severity_overrides are dropped from the result.
    findings = [f for f in findings if f.status != PASS]
    status = worst([f.status for f in findings])
    summary = pass_summary if not findings else "; ".join(f.message for f in findings)
    return RuleResult(rule_id=rule_id, title=title, status=status, summary=summary, findings=findings)


# ---------------------------------------------------------------- accessors


def vendor_id(bill: dict) -> int | None:
    v = bill.get("vendor")
    return v.get("id") if isinstance(v, dict) else v


def items(bill: dict) -> list[dict]:
    return bill.get("items") or []


def item_po_id(item: dict) -> int | None:
    po = item.get("po")
    return po.get("id") if isinstance(po, dict) else po


def item_line_id(item: dict) -> int | None:
    oi = item.get("orderitem")
    return oi.get("id") if isinstance(oi, dict) else oi


def bill_po_ids(bill: dict) -> list[int]:
    return sorted({pid for it in items(bill) if (pid := item_po_id(it)) is not None})


def currency_name(obj: Any) -> str | None:
    if isinstance(obj, dict):
        return obj.get("name")
    return obj


def bill_total(bill: dict) -> Decimal | None:
    """The total printed on the vendor's invoice, as drafted."""
    return to_decimal(bill.get("invoice_total"), "invoice_total")


def norm_inv(bill: dict, ctx_config: dict) -> str:
    prefixes = ctx_config.get("duplicate_check", {}).get("ignore_prefixes", [])
    return normalize_invoice_number(bill.get("invoice_number"), prefixes)


# ---------------------------------------------------------------- R1


def r1_duplicate_invoice(bill: dict, ctx: Context) -> RuleResult:
    """Duplicate invoice for the same vendor across ALL purchase orders.

    (a) Same vendor id and same normalised invoice number as an earlier bill
        -> DUPLICATE_INVOICE_NUMBER (FAIL by default), whatever PO it is on.
    (b) Same vendor id, same invoice total to the cent, invoice dates within
        N days, but a different normalised number
        -> POSSIBLE_DUPLICATE_AMOUNT_DATE (WARN by default).
    """
    title = "Duplicate invoice (same vendor, any PO)"
    f: list[Finding] = []
    raw = bill.get("invoice_number")
    mine = norm_inv(bill, ctx.config)
    if not mine:
        f.append(ctx.finding("INVOICE_NUMBER_MISSING", "invoice number is empty", invoice_number=raw))
        return _result("R1", title, f, "")
    window = int(ctx.config["duplicate_check"]["same_amount_window_days"])
    vid = vendor_id(bill)
    total = bill_total(bill)
    d = parse_date(bill.get("invoice_date"))
    for other in ctx.earlier_bills(bill):
        if vendor_id(other) != vid:
            continue
        theirs = norm_inv(other, ctx.config)
        if theirs == mine:
            f.append(
                ctx.finding(
                    "DUPLICATE_INVOICE_NUMBER",
                    f"invoice '{raw}' matches bill {other.get('id')} invoice '{other.get('invoice_number')}' "
                    f"(normalised '{mine}')",
                    this_invoice_number=raw,
                    other_bill_id=other.get("id"),
                    other_invoice_number=other.get("invoice_number"),
                    normalised=mine,
                    this_po_ids=bill_po_ids(bill),
                    other_po_ids=bill_po_ids(other),
                    same_po=bool(set(bill_po_ids(bill)) & set(bill_po_ids(other))),
                )
            )
            continue
        ot = bill_total(other)
        od = parse_date(other.get("invoice_date"))
        if total is not None and ot is not None and money(total) == money(ot) and d and od:
            gap = abs((d - od).days)
            if gap <= window:
                f.append(
                    ctx.finding(
                        "POSSIBLE_DUPLICATE_AMOUNT_DATE",
                        f"same vendor and total {mstr(total)} as bill {other.get('id')} "
                        f"('{other.get('invoice_number')}'), {gap} day(s) apart",
                        other_bill_id=other.get("id"),
                        other_invoice_number=other.get("invoice_number"),
                        total=mstr(total),
                        days_apart=gap,
                        window_days=window,
                    )
                )
    return _result("R1", title, f, f"no earlier bill from this vendor with invoice '{mine}' or same total within {window} days")


# ---------------------------------------------------------------- R2


def r2_arithmetic(bill: dict, ctx: Context) -> RuleResult:
    """Lines x price = line total; lines sum to subtotal; line taxes sum to
    tax; subtotal + tax + additional costs (shipping etc.) = invoice total.
    Each comparison allows ``amount_tolerance`` (default 0.01)."""
    title = "Totals add up"
    tol = ctx.tolerance
    f: list[Finding] = []
    line_sum = Decimal(0)
    line_tax_sum = Decimal(0)
    have_line_tax = False
    for it in items(bill):
        qty = to_decimal(it.get("quantity"), "quantity")
        unit = to_decimal(it.get("unit_cost"), "unit_cost")
        tot = to_decimal(it.get("total_cost"), "total_cost")
        if it.get("tax_is_inclusive"):
            f.append(ctx.finding("INCLUSIVE_TAX_UNSUPPORTED", f"line {it.get('id')}: tax-inclusive lines are not checked by this prototype", line_id=it.get("id")))
        if qty is None or unit is None or tot is None:
            f.append(ctx.finding("AMOUNT_MISSING", f"line {it.get('id')}: quantity, unit_cost or total_cost missing", line_id=it.get("id")))
            continue
        expected = money(qty * unit)
        if abs(expected - tot) > tol:
            f.append(
                ctx.finding(
                    "LINE_MATH",
                    f"line {it.get('id')}: {dstr(qty)} x {mstr(unit)} = {mstr(expected)}, line says {mstr(tot)}",
                    line_id=it.get("id"), quantity=dstr(qty), unit_cost=mstr(unit),
                    expected_total=mstr(expected), stated_total=mstr(tot),
                )
            )
        line_sum += tot
        lt = to_decimal(it.get("tax_amount"), "tax_amount")
        if lt is not None:
            have_line_tax = True
            line_tax_sum += lt

    subtotal = to_decimal(bill.get("subtotal_cost"), "subtotal_cost")
    tax = to_decimal(bill.get("tax_amount"), "tax_amount")
    total = bill_total(bill)
    costs = [(c.get("name"), to_decimal(c.get("amount"), "costs.amount")) for c in (bill.get("costs") or [])]
    costs_sum = sum((a for _, a in costs if a is not None), Decimal(0))

    missing = [n for n, v in (("subtotal_cost", subtotal), ("tax_amount", tax), ("invoice_total", total)) if v is None]
    if missing:
        f.append(ctx.finding("AMOUNT_MISSING", f"header amount(s) missing: {', '.join(missing)}", missing=missing))
        return _result("R2", title, f, "")

    if abs(line_sum - subtotal) > tol:
        f.append(ctx.finding("SUBTOTAL_MISMATCH", f"lines sum to {mstr(line_sum)}, subtotal says {mstr(subtotal)}",
                             lines_sum=mstr(line_sum), subtotal=mstr(subtotal), difference=mstr(subtotal - line_sum)))
    if have_line_tax and abs(line_tax_sum - tax) > tol:
        f.append(ctx.finding("TAX_SUM_MISMATCH", f"line taxes sum to {mstr(line_tax_sum)}, tax says {mstr(tax)}",
                             line_tax_sum=mstr(line_tax_sum), tax_amount=mstr(tax), difference=mstr(tax - line_tax_sum)))
    expected_total = subtotal + tax + costs_sum
    if abs(expected_total - total) > tol:
        f.append(
            ctx.finding(
                "TOTAL_MISMATCH",
                f"subtotal {mstr(subtotal)} + tax {mstr(tax)} + other costs {mstr(costs_sum)} = {mstr(expected_total)}, "
                f"invoice total says {mstr(total)}",
                subtotal=mstr(subtotal), tax_amount=mstr(tax),
                other_costs=[{"name": n, "amount": mstr(a)} for n, a in costs],
                expected_total=mstr(expected_total), invoice_total=mstr(total),
                difference=mstr(total - expected_total), tolerance=mstr(tol),
            )
        )
    return _result("R2", title, f,
                   f"{len(items(bill))} line(s) = {mstr(line_sum)}; + tax {mstr(tax)} + other {mstr(costs_sum)} = {mstr(total)}")


# ---------------------------------------------------------------- R3


def r3_tax_code(bill: dict, ctx: Context) -> RuleResult:
    """Every line carries a Procurify tax id, and that id is mapped to an ERP
    tax code in config["erp_mappings"]["tax_codes"]. If the mapping records a
    rate, the line's tax_percentage must equal it."""
    title = "Tax code present and mapped"
    mapping = ctx.config["erp_mappings"]["tax_codes"]
    f: list[Finding] = []
    for it in items(bill):
        tax_id = it.get("tax")
        if tax_id is None:
            f.append(ctx.finding("TAX_CODE_MISSING", f"line {it.get('id')}: no tax code", line_id=it.get("id"), tax_name=it.get("tax_name")))
            continue
        m = mapping.get(str(tax_id))
        if not m or not m.get("erp_tax_code"):
            f.append(
                ctx.finding(
                    "TAX_CODE_UNMAPPED",
                    f"line {it.get('id')}: tax '{it.get('tax_name')}' (id {tax_id}) has no ERP tax code mapping",
                    line_id=it.get("id"), tax_id=tax_id, tax_name=it.get("tax_name"),
                    mapped_tax_ids=sorted(mapping.keys()),
                )
            )
            continue
        rate = to_decimal(m.get("rate"), "rate")
        pct = to_decimal(it.get("tax_percentage"), "tax_percentage")
        if rate is not None and pct is not None and rate != pct:
            f.append(ctx.finding("TAX_RATE_MISMATCH",
                                 f"line {it.get('id')}: tax {tax_id} is {dstr(pct)}% on bill, mapping says {dstr(rate)}%",
                                 line_id=it.get("id"), tax_id=tax_id, bill_rate=dstr(pct), mapped_rate=dstr(rate)))
    codes = sorted({str(mapping[str(it.get('tax'))]['erp_tax_code']) for it in items(bill) if str(it.get('tax')) in mapping and mapping[str(it.get('tax'))].get('erp_tax_code')})
    return _result("R3", title, f, f"mapped to {', '.join(codes) or '-'}")


# ---------------------------------------------------------------- R4


def line_account_code(item: dict, ctx: Context) -> str | None:
    """Account code for a bill line: the bill line's own orderitem.accountCode
    if present, otherwise the PO line's accountCode (Procurify carries the
    order's account code onto the draft bill)."""
    oi = item.get("orderitem")
    if isinstance(oi, dict) and oi.get("accountCode"):
        return oi["accountCode"]
    lid = item_line_id(item)
    if lid is not None and lid in ctx.line_by_id:
        return ctx.line_by_id[lid][1].get("accountCode")
    return None


def r4_account_code(bill: dict, ctx: Context) -> RuleResult:
    title = "GL account code present and mapped"
    mapping = ctx.config["erp_mappings"]["account_codes"]
    f: list[Finding] = []
    seen: list[str] = []
    for it in items(bill):
        code = line_account_code(it, ctx)
        if not code:
            f.append(ctx.finding("ACCOUNT_CODE_MISSING", f"line {it.get('id')}: no account code", line_id=it.get("id")))
            continue
        m = mapping.get(code)
        if not m or not m.get("erp_account"):
            f.append(ctx.finding("ACCOUNT_CODE_UNMAPPED", f"line {it.get('id')}: account code {code} has no ERP account mapping",
                                 line_id=it.get("id"), account_code=code))
            continue
        if m.get("active") is False:
            f.append(ctx.finding("ACCOUNT_CODE_INACTIVE", f"line {it.get('id')}: account code {code} is inactive",
                                 line_id=it.get("id"), account_code=code))
            continue
        if code not in seen:
            seen.append(code)
    pairs = [c + " -> " + str(mapping[c]["erp_account"]) for c in seen]
    return _result("R4", title, f, ", ".join(pairs) or "-")


# ---------------------------------------------------------------- R5


def _closed_period(d: date, periods: list[dict]) -> dict | None:
    for p in periods:
        if parse_date(p["start"]) <= d <= parse_date(p["end"]):
            return p
    return None


def r5_bill_date(bill: dict, ctx: Context) -> RuleResult:
    """Invoice date present; not after the as-of date; not before the PO date
    (WARN by default: after-the-fact POs exist). The posting date
    (gl_post_date, else invoice_date) must not fall in a closed period."""
    title = "Bill date valid"
    f: list[Finding] = []
    d = parse_date(bill.get("invoice_date"))
    if d is None:
        f.append(ctx.finding("BILL_DATE_MISSING", "invoice_date is empty"))
        return _result("R5", title, f, "")
    if d > ctx.as_of:
        f.append(ctx.finding("FUTURE_DATE", f"invoice date {d} is after as-of date {ctx.as_of}",
                             invoice_date=str(d), as_of_date=str(ctx.as_of)))
    for pid in bill_po_ids(bill):
        po = ctx.pos_by_id.get(pid)
        if not po:
            continue  # reported by R6
        pod = parse_date(po.get("date"))
        if pod and d < pod:
            f.append(ctx.finding("BEFORE_PO_DATE", f"invoice date {d} is before PO {po.get('number')} date {pod}",
                                 invoice_date=str(d), po_number=po.get("number"), po_date=str(pod)))
    post = parse_date(bill.get("gl_post_date")) or d
    cp = _closed_period(post, ctx.config.get("closed_periods") or [])
    if cp:
        f.append(ctx.finding("CLOSED_PERIOD", f"posting date {post} is in closed period {cp.get('name', '')} ({cp['start']}..{cp['end']})",
                             posting_date=str(post), period=cp))
    return _result("R5", title, f, f"invoice date {d}; posting date {post} in an open period")


# ---------------------------------------------------------------- R6


def r6_vendor(bill: dict, ctx: Context) -> RuleResult:
    """Vendor on the bill is the PO's vendor (pref_vendor) for every PO the
    bill references, and the vendor is active (vendors file if given, else
    the PO's pref_vendor.active)."""
    title = "Vendor matches PO and is active"
    f: list[Finding] = []
    vid = vendor_id(bill)
    vname = (bill.get("vendor") or {}).get("name") if isinstance(bill.get("vendor"), dict) else None
    active: bool | None = None
    for pid in bill_po_ids(bill):
        po = ctx.pos_by_id.get(pid)
        if not po:
            f.append(ctx.finding("PO_NOT_FOUND", f"PO id {pid} is not in the purchase orders file", po_id=pid))
            continue
        pv = po.get("pref_vendor") or {}
        if pv.get("id") != vid:
            f.append(ctx.finding("VENDOR_MISMATCH",
                                 f"bill vendor {vid} ({vname}) differs from PO {po.get('number')} vendor {pv.get('id')} ({pv.get('name')})",
                                 bill_vendor_id=vid, bill_vendor_name=vname, po_number=po.get("number"),
                                 po_vendor_id=pv.get("id"), po_vendor_name=pv.get("name")))
        if active is None and "active" in pv:
            active = bool(pv["active"])
    source = "purchase order pref_vendor"
    if ctx.vendors_by_id is not None:
        v = ctx.vendors_by_id.get(vid)
        source = "vendors file"
        active = None if v is None else bool(v.get("active"))
    if active is None:
        f.append(ctx.finding("VENDOR_STATUS_UNKNOWN", f"active status of vendor {vid} is unknown", vendor_id=vid, source=source))
    elif not active:
        f.append(ctx.finding("VENDOR_INACTIVE", f"vendor {vid} ({vname}) is inactive", vendor_id=vid, source=source))
    return _result("R6", title, f, f"vendor {vid} matches PO(s) {', '.join(str(ctx.pos_by_id[p].get('number')) for p in bill_po_ids(bill) if p in ctx.pos_by_id) or '-'}; active")


# ---------------------------------------------------------------- R7


def r7_three_way_match(bill: dict, ctx: Context) -> RuleResult:
    """Per line: cumulative billed quantity (earlier bills in this batch +
    this bill) <= received pass quantity, and <= ordered quantity; unit price
    within the configured % of the PO price."""
    title = "Three-way match (PO / receipt / bill)"
    twm = ctx.config["three_way_match"]
    warn_pct = to_decimal(twm["price_warn_pct"])
    fail_pct = to_decimal(twm["price_fail_pct"])
    f: list[Finding] = []
    earlier = ctx.earlier_bills(bill)
    matched = 0
    for it in items(bill):
        lid = item_line_id(it)
        if lid is None:
            f.append(ctx.finding("NO_PO_LINE", f"line {it.get('id')}: not linked to a PO line; cannot three-way match", line_id=it.get("id")))
            continue
        if lid not in ctx.line_by_id:
            f.append(ctx.finding("PO_LINE_NOT_FOUND", f"line {it.get('id')}: PO line {lid} not found", line_id=it.get("id"), order_item=lid))
            continue
        po, line = ctx.line_by_id[lid]
        qty = to_decimal(it.get("quantity"), "quantity") or Decimal(0)
        ordered = to_decimal(line.get("quantity"), "quantity") or Decimal(0)
        received = ctx.received_by_line.get(lid, Decimal(0))
        prior = Decimal(0)
        prior_bills: list[int] = []
        for ob in earlier:
            for oit in items(ob):
                if item_line_id(oit) == lid:
                    prior += to_decimal(oit.get("quantity"), "quantity") or Decimal(0)
                    prior_bills.append(ob.get("id"))
        cumulative = prior + qty
        ev = {
            "line_id": it.get("id"), "po_number": po.get("number"), "po_line": line.get("PO_line_num"),
            "order_item": lid, "item": line.get("name"),
            "ordered": dstr(ordered), "received_pass": dstr(received),
            "received_fail": dstr(sum((to_decimal(r.get("fail_quantity")) or Decimal(0) for r in ctx.receipts_by_line.get(lid, [])), Decimal(0))),
            "billed_earlier": dstr(prior), "billed_earlier_on_bills": prior_bills,
            "billed_this_bill": dstr(qty), "billed_cumulative": dstr(cumulative),
        }
        if cumulative > received:
            f.append(ctx.finding("BILLED_OVER_RECEIVED",
                                 f"{line.get('name')}: billing {dstr(cumulative)} but only {dstr(received)} received (PO {po.get('number')} line {line.get('PO_line_num')})",
                                 **ev))
        if cumulative > ordered:
            f.append(ctx.finding("BILLED_OVER_ORDERED",
                                 f"{line.get('name')}: billing {dstr(cumulative)} but only {dstr(ordered)} ordered", **ev))
        unit = to_decimal(it.get("unit_cost"), "unit_cost")
        po_price = to_decimal(line.get("price"), "price")
        if unit is not None and po_price:
            pct = (unit - po_price) / po_price * 100
            pct_s = f"{pct.quantize(Decimal('0.01')):+}"
            pev = {"line_id": it.get("id"), "item": line.get("name"), "po_number": po.get("number"),
                   "bill_unit_cost": mstr(unit), "po_price": mstr(po_price), "variance_pct": pct_s,
                   "warn_above_pct": dstr(warn_pct), "fail_above_pct": dstr(fail_pct)}
            if abs(pct) > fail_pct:
                f.append(ctx.finding("PRICE_VARIANCE_FAIL", f"{line.get('name')}: unit price {mstr(unit)} vs PO {mstr(po_price)} ({pct_s}%)", **pev))
            elif abs(pct) > warn_pct:
                f.append(ctx.finding("PRICE_VARIANCE_WARN", f"{line.get('name')}: unit price {mstr(unit)} vs PO {mstr(po_price)} ({pct_s}%)", **pev))
        matched += 1
    return _result("R7", title, f, f"{matched} line(s): billed <= received <= ordered; prices within {dstr(warn_pct)}%")


# ---------------------------------------------------------------- R8


def r8_currency(bill: dict, ctx: Context) -> RuleResult:
    title = "Currency matches PO"
    f: list[Finding] = []
    cur = currency_name(bill.get("currency"))
    if not cur:
        f.append(ctx.finding("CURRENCY_MISSING", "bill has no currency"))
        return _result("R8", title, f, "")
    for pid in bill_po_ids(bill):
        po = ctx.pos_by_id.get(pid)
        if not po:
            continue
        pc = currency_name(po.get("purchased_currency"))
        if pc != cur:
            f.append(ctx.finding("CURRENCY_MISMATCH", f"bill currency {cur} differs from PO {po.get('number')} currency {pc}",
                                 bill_currency=cur, po_number=po.get("number"), po_currency=pc))
    return _result("R8", title, f, f"{cur}")


RULES: list[tuple[str, Callable[[dict, Context], RuleResult]]] = [
    ("R1", r1_duplicate_invoice),
    ("R2", r2_arithmetic),
    ("R3", r3_tax_code),
    ("R4", r4_account_code),
    ("R5", r5_bill_date),
    ("R6", r6_vendor),
    ("R7", r7_three_way_match),
    ("R8", r8_currency),
]
