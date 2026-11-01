"""One or more tests per rule, PASS and non-PASS paths."""

from conftest import check, codes, make_bill, make_po, receipt, rule


def two_po_setup():
    pos = [make_po(1, lines=((100, 10, "5.00", "6100-00"),)), make_po(2, lines=((200, 10, "5.00", "6100-00"),))]
    rec = [receipt(100, 10), receipt(200, 10)]
    return pos, rec


# R1 duplicates -------------------------------------------------------------


def test_r1_clean():
    v = check([make_bill()])[1]
    assert rule(v, "R1").status == "PASS"


def test_r1_duplicate_across_pos_with_reformatted_number():
    pos, rec = two_po_setup()
    b1 = make_bill(1, inv="INV-00417", lines=((100, 5, "5.00", 2, 1),))
    b2 = make_bill(2, inv="inv 417", lines=((200, 5, "5.00", 2, 2),))
    vs = check([b1, b2], pos=pos, receipts=rec)
    assert rule(vs[1], "R1").status == "PASS"  # first occurrence passes
    r = rule(vs[2], "R1")
    assert r.status == "FAIL"
    f = r.findings[0]
    assert f.code == "DUPLICATE_INVOICE_NUMBER"
    assert f.evidence["other_bill_id"] == 1
    assert f.evidence["same_po"] is False
    assert f.evidence["normalised"] == "417"


def test_r1_same_number_different_vendor_is_fine():
    pos = [make_po(1, vendor=10), make_po(2, vendor=11, lines=((200, 10, "5.00", "6100-00"),))]
    b1 = make_bill(1, vendor=10, inv="417")
    b2 = make_bill(2, vendor=11, inv="417", lines=((200, 10, "5.00", 2, 2),))
    vs = check([b1, b2], pos=pos, receipts=[receipt(100, 10), receipt(200, 10)])
    assert rule(vs[2], "R1").status == "PASS"


def test_r1_same_amount_within_window_warns():
    pos, rec = two_po_setup()
    b1 = make_bill(1, inv="A-1", date="2026-10-10", lines=((100, 5, "5.00", 2, 1),))
    b2 = make_bill(2, inv="A-2", date="2026-10-15", lines=((200, 5, "5.00", 2, 2),))
    vs = check([b1, b2], pos=pos, receipts=rec)
    assert codes(vs[2], "R1") == ["POSSIBLE_DUPLICATE_AMOUNT_DATE"]
    assert rule(vs[2], "R1").status == "WARN"


def test_r1_same_amount_outside_window_passes():
    pos, rec = two_po_setup()
    b1 = make_bill(1, inv="A-1", date="2026-10-01", lines=((100, 5, "5.00", 2, 1),))
    b2 = make_bill(2, inv="A-2", date="2026-10-09", lines=((200, 5, "5.00", 2, 2),))
    vs = check([b1, b2], pos=pos, receipts=rec)
    assert rule(vs[2], "R1").status == "PASS"


def test_r1_missing_invoice_number():
    assert codes(check([make_bill(inv="  ")])[1], "R1") == ["INVOICE_NUMBER_MISSING"]


# R2 arithmetic -------------------------------------------------------------


def test_r2_clean_with_shipping():
    v = check([make_bill(costs=[("Freight", "12.00")])])[1]
    assert rule(v, "R2").status == "PASS"


def test_r2_total_mismatch():
    v = check([make_bill(total="102.50")])[1]  # correct is 52.50
    r = rule(v, "R2")
    assert r.status == "FAIL" and codes(v, "R2") == ["TOTAL_MISMATCH"]
    assert r.findings[0].evidence["expected_total"] == "52.50"
    assert r.findings[0].evidence["difference"] == "50.00"


def test_r2_within_tolerance():
    assert rule(check([make_bill(total="52.51")])[1], "R2").status == "PASS"
    assert rule(check([make_bill(total="52.52")])[1], "R2").status == "FAIL"


def test_r2_subtotal_and_line_math():
    b = make_bill(subtotal="60.00", total="63.00")
    b["items"][0]["total_cost"] = b["items"][0]["total_cost"] + 10  # 60 instead of 50
    v = check([b])[1]
    assert "LINE_MATH" in codes(v, "R2")
    b2 = make_bill(subtotal="55.00")
    assert "SUBTOTAL_MISMATCH" in codes(check([b2])[1], "R2")


def test_r2_tax_sum_mismatch():
    b = make_bill(tax="3.00")  # lines carry 2.50
    assert "TAX_SUM_MISMATCH" in codes(check([b])[1], "R2")


# R3 tax codes --------------------------------------------------------------


def test_r3_mapped():
    r = rule(check([make_bill()])[1], "R3")
    assert r.status == "PASS" and "GST5" in r.summary


def test_r3_unmapped():
    v = check([make_bill(lines=((100, 10, "5.00", 7, 1),))])[1]
    assert codes(v, "R3") == ["TAX_CODE_UNMAPPED"] and rule(v, "R3").status == "FAIL"


def test_r3_missing():
    v = check([make_bill(lines=((100, 10, "5.00", None, 1),))])[1]
    assert codes(v, "R3") == ["TAX_CODE_MISSING"]


def test_r3_rate_mismatch_warns():
    b = make_bill()
    b["items"][0]["tax_percentage"] = "7"
    v = check([b])[1]
    assert codes(v, "R3") == ["TAX_RATE_MISMATCH"] and rule(v, "R3").status == "WARN"


# R4 account codes ----------------------------------------------------------


def test_r4_from_po_line():
    assert rule(check([make_bill()])[1], "R4").status == "PASS"


def test_r4_unmapped_and_inactive_and_missing():
    assert codes(check([make_bill(account_code="7777-00")])[1], "R4") == ["ACCOUNT_CODE_UNMAPPED"]
    assert codes(check([make_bill(account_code="6999-00")])[1], "R4") == ["ACCOUNT_CODE_INACTIVE"]
    po = make_po(lines=((100, 10, "5.00", ""),))
    assert codes(check([make_bill()], pos=[po])[1], "R4") == ["ACCOUNT_CODE_MISSING"]


# R5 dates ------------------------------------------------------------------


def test_r5_clean():
    assert rule(check([make_bill()])[1], "R5").status == "PASS"


def test_r5_before_po_date_warns_by_default():
    v = check([make_bill(date="2026-09-30")], config={"closed_periods": []})[1]
    assert codes(v, "R5") == ["BEFORE_PO_DATE"] and rule(v, "R5").status == "WARN"


def test_r5_future_and_closed_period_and_missing():
    assert codes(check([make_bill(date="2026-11-02")])[1], "R5") == ["FUTURE_DATE"]
    po = make_po(date="2026-09-01T00:00:00Z")
    assert codes(check([make_bill(date="2026-09-15")], pos=[po])[1], "R5") == ["CLOSED_PERIOD"]
    assert codes(check([make_bill(date=None)])[1], "R5") == ["BILL_DATE_MISSING"]


def test_r5_posting_date_overrides_invoice_date_for_period():
    po = make_po(date="2026-09-01T00:00:00Z")
    b = make_bill(date="2026-09-15")
    b["gl_post_date"] = "2026-10-01T00:00:00Z"
    assert rule(check([b], pos=[po])[1], "R5").status == "PASS"


# R6 vendor -----------------------------------------------------------------


def test_r6_clean():
    assert rule(check([make_bill()])[1], "R6").status == "PASS"


def test_r6_vendor_mismatch():
    v = check([make_bill(vendor=99)])[1]
    assert "VENDOR_MISMATCH" in codes(v, "R6")


def test_r6_inactive_from_po_or_vendors_file():
    assert codes(check([make_bill()], pos=[make_po(vendor_active=False)])[1], "R6") == ["VENDOR_INACTIVE"]
    vendors = [{"id": 10, "name": "Vendor 10", "active": False}]
    assert codes(check([make_bill()], vendors=vendors)[1], "R6") == ["VENDOR_INACTIVE"]
    assert codes(check([make_bill()], vendors=[])[1], "R6") == ["VENDOR_STATUS_UNKNOWN"]


def test_r6_po_not_found():
    v = check([make_bill(lines=((100, 10, "5.00", 2, 42),))])[1]
    assert "PO_NOT_FOUND" in codes(v, "R6")


# R7 three-way match --------------------------------------------------------


def test_r7_clean():
    assert rule(check([make_bill()])[1], "R7").status == "PASS"


def test_r7_billed_over_received():
    v = check([make_bill()], receipts=[receipt(100, 8, fail=1)])[1]
    r = rule(v, "R7")
    assert r.status == "FAIL" and codes(v, "R7") == ["BILLED_OVER_RECEIVED"]
    ev = r.findings[0].evidence
    assert (ev["received_pass"], ev["received_fail"], ev["billed_cumulative"]) == ("8", "1", "10")


def test_r7_cumulative_across_bills():
    b1 = make_bill(1, inv="A-1", lines=((100, 6, "5.00", 2, 1),))
    b2 = make_bill(2, inv="A-2", date="2026-10-25", lines=((100, 6, "5.00", 2, 1),))
    vs = check([b1, b2])
    assert rule(vs[1], "R7").status == "PASS"
    assert "BILLED_OVER_RECEIVED" in codes(vs[2], "R7")
    assert rule(vs[2], "R7").findings[0].evidence["billed_earlier_on_bills"] == [1]


def test_r7_over_ordered():
    v = check([make_bill(lines=((100, 12, "5.00", 2, 1),))], receipts=[receipt(100, 12)])[1]
    assert codes(v, "R7") == ["BILLED_OVER_ORDERED"]


def test_r7_price_variance_bands():
    warn = check([make_bill(lines=((100, 10, "5.10", 2, 1),))])[1]  # +2%
    fail = check([make_bill(lines=((100, 10, "5.30", 2, 1),))])[1]  # +6%
    ok = check([make_bill(lines=((100, 10, "5.05", 2, 1),))])[1]  # +1% (not above warn)
    assert codes(warn, "R7") == ["PRICE_VARIANCE_WARN"]
    assert codes(fail, "R7") == ["PRICE_VARIANCE_FAIL"]
    assert rule(ok, "R7").status == "PASS"


def test_r7_unlinked_line_and_missing_po_line():
    assert codes(check([make_bill(lines=((None, 1, "5.00", 2, 1),))])[1], "R7") == ["NO_PO_LINE"]
    assert codes(check([make_bill(lines=((555, 1, "5.00", 2, 1),))])[1], "R7") == ["PO_LINE_NOT_FOUND"]


# R8 currency ---------------------------------------------------------------


def test_r8():
    assert rule(check([make_bill()])[1], "R8").status == "PASS"
    v = check([make_bill(currency="USD")])[1]
    assert codes(v, "R8") == ["CURRENCY_MISMATCH"]
    b = make_bill()
    b["currency"] = None
    assert codes(check([b])[1], "R8") == ["CURRENCY_MISSING"]
