from billcheck.engine import BLOCK, OK, REVIEW, resolve_as_of, run_checks, verdict_for
from billcheck.loader import load_inputs
from billcheck.rules import FAIL, PASS, WARN, RuleResult
from conftest import check, codes, make_bill


def rr(status):
    return RuleResult("RX", "x", status, "")


def test_verdict_logic():
    assert verdict_for([rr(PASS)] * 8) == OK
    assert verdict_for([rr(PASS), rr(WARN)]) == REVIEW
    assert verdict_for([rr(WARN), rr(FAIL)]) == BLOCK
    assert verdict_for([rr(FAIL), rr(PASS)]) == BLOCK


def test_every_bill_gets_all_eight_rules():
    v = check([make_bill()])[1]
    assert [r.rule_id for r in v.results] == [f"R{i}" for i in range(1, 9)]
    assert v.verdict == OK


def test_severity_override_changes_verdict():
    b = make_bill(date="2026-09-30")  # before PO date (2026-10-01)
    cfg = {"closed_periods": []}
    assert check([b], config=cfg)[1].verdict == REVIEW
    assert check([b], config={**cfg, "severity_overrides": {"BEFORE_PO_DATE": "FAIL"}})[1].verdict == BLOCK
    assert check([b], config={**cfg, "severity_overrides": {"BEFORE_PO_DATE": "PASS"}})[1].verdict == OK


def test_demo_batch_expected_verdicts(demo_paths):
    inputs = load_inputs(**demo_paths)
    _, verdicts = run_checks(inputs, resolve_as_of(inputs.config))
    by_id = {v.bill_id: v for v in verdicts}
    assert len(verdicts) == 12
    assert sorted(i for i, v in by_id.items() if v.verdict == BLOCK) == [7004, 7005, 7006, 7010]
    assert sorted(i for i, v in by_id.items() if v.verdict == REVIEW) == [7007, 7012]
    assert sum(v.verdict == OK for v in verdicts) == 6
    # Each blocked bill is blocked for exactly one, different reason.
    assert codes(by_id[7004]) == ["TOTAL_MISMATCH"]
    assert codes(by_id[7005]) == ["BILLED_OVER_RECEIVED"]
    assert codes(by_id[7006]) == ["TAX_CODE_UNMAPPED"]
    assert codes(by_id[7010]) == ["DUPLICATE_INVOICE_NUMBER"]
    assert codes(by_id[7007]) == ["PRICE_VARIANCE_WARN"]
    assert codes(by_id[7012]) == ["POSSIBLE_DUPLICATE_AMOUNT_DATE"]
