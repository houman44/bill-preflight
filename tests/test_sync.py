"""Idempotent sync under simulated ERP timeouts."""

from billcheck.engine import idempotency_key
from billcheck.loader import load_config_obj
from billcheck.sync import AFTER_COMMIT, BEFORE_COMMIT, SOURCE_FIELD, Ledger, MockERP, sync_idempotent, sync_naive
from conftest import make_bill

CFG = load_config_obj({})


def bills(n=3):
    return [make_bill(i, vendor=10 + i, inv=f"INV-{i:04d}") for i in range(1, n + 1)]


def keys(bs):
    return {b["id"]: idempotency_key(b, CFG) for b in bs}


def test_key_is_deterministic_and_normalises_invoice_number():
    a = make_bill(1, inv="INV-00417")
    b = make_bill(99, inv="inv 417")  # different bill id, reformatted number
    assert idempotency_key(a, CFG) == idempotency_key(b, CFG)
    assert idempotency_key(a, CFG).startswith("BPF-") and len(idempotency_key(a, CFG)) == 36


def test_key_changes_with_vendor_number_or_amount():
    base = make_bill(1, inv="A-1")
    k = idempotency_key(base, CFG)
    assert idempotency_key(make_bill(1, inv="A-2"), CFG) != k
    assert idempotency_key(make_bill(1, inv="A-1", vendor=11), CFG) != k
    assert idempotency_key(make_bill(1, inv="A-1", total="99.99"), CFG) != k


def test_naive_retry_creates_duplicate_after_timeout():
    bs = bills()
    erp = MockERP(timeout_on_bills={2}, timeout_kind=AFTER_COMMIT)
    out = sync_naive(bs, erp)
    assert len(erp.records) == 4  # 3 bills, one written twice
    assert len(erp.duplicates()) == 1
    assert out[1].attempts == 2 and len(out[1].erp_internal_ids) == 2


def test_idempotent_retry_does_not_duplicate_after_timeout():
    bs = bills()
    erp = MockERP(timeout_on_bills={2}, timeout_kind=AFTER_COMMIT)
    ledger = Ledger()
    out = sync_idempotent(bs, keys(bs), erp, ledger)
    assert len(erp.records) == 3 and erp.duplicates() == []
    assert erp.create_calls == 3
    assert out[1].outcome == "recovered_after_timeout"
    assert all(e["status"] == "synced" for e in ledger.entries.values())


def test_idempotent_retry_writes_when_first_write_was_really_lost():
    bs = bills()
    erp = MockERP(timeout_on_bills={2}, timeout_kind=BEFORE_COMMIT)
    out = sync_idempotent(bs, keys(bs), erp, Ledger())
    assert len(erp.records) == 3 and erp.duplicates() == []
    assert out[1].outcome == "created" and out[1].attempts == 2


def test_every_record_carries_its_key_and_source_bill():
    bs = bills()
    erp = MockERP()
    sync_idempotent(bs, keys(bs), erp, Ledger())
    ks = keys(bs)
    assert sorted((r[SOURCE_FIELD], r["externalId"]) for r in erp.records) == sorted(ks.items())


def test_rerun_is_a_no_op(tmp_path):
    bs = bills()
    erp_path, ledger_path = tmp_path / "erp.json", tmp_path / "ledger.json"
    sync_idempotent(bs, keys(bs), MockERP(timeout_on_bills={1, 3}, state_path=erp_path), Ledger(ledger_path))
    erp2 = MockERP(state_path=erp_path)
    out = sync_idempotent(bs, keys(bs), erp2, Ledger(ledger_path))
    assert [o.outcome for o in out] == ["already_synced"] * 3
    assert len(erp2.records) == 3


def test_crash_between_write_and_ledger_update_is_recovered(tmp_path):
    """Process dies after the ERP committed but before the ledger said 'synced'."""
    bs = bills(1)
    k = keys(bs)
    erp_path, ledger_path = tmp_path / "erp.json", tmp_path / "ledger.json"
    erp = MockERP(state_path=erp_path)
    ledger = Ledger(ledger_path)
    ledger.set(k[1], status="pending", bill_id=1)
    from billcheck.sync import bill_payload

    erp.create_vendor_bill(bill_payload(bs[0], external_id=k[1]))  # landed; then "crash"
    out = sync_idempotent(bs, k, MockERP(state_path=erp_path), Ledger(ledger_path))
    assert out[0].outcome == "already_in_erp"
    assert len(MockERP(state_path=erp_path).records) == 1


def test_all_attempts_time_out_then_later_run_recovers_without_duplicate(tmp_path):
    bs = bills(1)
    erp_path, ledger_path = tmp_path / "erp.json", tmp_path / "ledger.json"
    erp = MockERP(timeout_on_calls={1, 2, 3}, state_path=erp_path)
    out = sync_idempotent(bs, keys(bs), erp, Ledger(ledger_path), max_attempts=3)
    # First create landed (after-commit timeout); attempt 2's lookup finds it.
    assert out[0].outcome == "recovered_after_timeout"
    assert len(erp.records) == 1

    erp_lost = MockERP(timeout_on_calls={1, 2, 3}, timeout_kind=BEFORE_COMMIT)
    led = Ledger()
    out = sync_idempotent(bs, keys(bs), erp_lost, led, max_attempts=3)
    assert out[0].outcome == "failed" and len(erp_lost.records) == 0
    assert led.get(keys(bs)[1])["status"] == "pending"
    out = sync_idempotent(bs, keys(bs), erp_lost, led, max_attempts=3)  # ERP healthy now (calls 4+)
    assert out[0].outcome == "created" and len(erp_lost.records) == 1
