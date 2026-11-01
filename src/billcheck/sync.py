"""Mock ERP and two syncers: naive retry vs idempotent retry.

MockERP stands in for NetSuite. It is in-process and simulated: there is no
network and no real NetSuite API here. It can be told to "time out" on given
create calls. The timeout can happen after the bill is committed (the write
landed but the response never arrived) or before (the write was lost).

Record field names (externalId, tranId, entity, tranDate, currency, memo)
follow NetSuite's vendor bill vocabulary for readability only.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from billcheck.normalize import mstr, parse_date
from billcheck.rules import bill_total, currency_name, vendor_id

AFTER_COMMIT = "after_commit"
# Hypothetical NetSuite custom body field carrying the Procurify bill id.
SOURCE_FIELD = "custbody_procurify_bill_id"
BEFORE_COMMIT = "before_commit"


class ERPTimeout(Exception):
    """The ERP did not answer in time. The write may or may not have landed."""


class MockERP:
    def __init__(
        self,
        timeout_on_calls: set[int] | None = None,
        timeout_kind: str = AFTER_COMMIT,
        state_path: Path | None = None,
        timeout_on_bills: set[int] | None = None,
    ):
        """timeout_on_calls: create-call numbers (1-based) that time out.
        timeout_on_bills: Procurify bill ids whose FIRST create call times out.
        """
        if timeout_kind not in (AFTER_COMMIT, BEFORE_COMMIT):
            raise ValueError("timeout_kind must be after_commit or before_commit")
        self.timeout_on_calls = set(timeout_on_calls or ())
        self.timeout_on_bills = set(timeout_on_bills or ())
        self.timeout_kind = timeout_kind
        self._bills_timed_out: set[int] = set()
        self.records: list[dict] = []
        self.create_calls = 0
        self.lookup_calls = 0
        self.state_path = state_path
        if state_path and state_path.exists():
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.records = saved["records"]
            self.create_calls = saved["create_calls"]
            self._bills_timed_out = set(saved.get("bills_timed_out", []))

    def save(self) -> None:
        if self.state_path:
            _atomic_write_json(self.state_path, {"records": self.records, "create_calls": self.create_calls,
                                                "bills_timed_out": sorted(self._bills_timed_out)})

    def create_vendor_bill(self, payload: dict) -> int:
        """Create a bill. Like NetSuite's add, it does not refuse a second
        bill with the same vendor and invoice number by itself."""
        self.create_calls += 1
        call = self.create_calls
        bill_id = payload.get(SOURCE_FIELD)
        will_timeout = call in self.timeout_on_calls
        if bill_id in self.timeout_on_bills and bill_id not in self._bills_timed_out:
            self._bills_timed_out.add(bill_id)
            will_timeout = True
        if will_timeout and self.timeout_kind == BEFORE_COMMIT:
            self.save()
            raise ERPTimeout("no response; nothing was written")
        internal_id = 9000 + len(self.records) + 1
        self.records.append({"internalId": internal_id, **payload})
        self.save()
        if will_timeout:
            raise ERPTimeout(f"no response, but ERP committed internalId {internal_id}")
        return internal_id

    def find_by_external_id(self, external_id: str) -> dict | None:
        self.lookup_calls += 1
        for r in self.records:
            if r.get("externalId") == external_id:
                return r
        return None

    def duplicates(self) -> list[dict]:
        """Groups of ERP records with the same vendor + invoice number."""
        groups: dict[tuple, list[dict]] = {}
        for r in self.records:
            groups.setdefault((r["entity"], r["tranId"]), []).append(r)
        return [
            {"entity": k[0], "tranId": k[1], "internalIds": [r["internalId"] for r in v]}
            for k, v in sorted(groups.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1])))
            if len(v) > 1
        ]


def short(key: str) -> str:
    return key[:16] + "..."


def _atomic_write_json(path: Path, obj: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


class Ledger:
    """Local record of idempotency keys: pending -> synced.

    Persisted to JSON after every change when a path is given, so a crash
    between "write sent" and "response received" is recoverable on restart.
    """

    def __init__(self, path: Path | None = None):
        self.path = path
        self.entries: dict[str, dict] = {}
        if path and path.exists():
            self.entries = json.loads(path.read_text(encoding="utf-8"))

    def get(self, key: str) -> dict | None:
        return self.entries.get(key)

    def set(self, key: str, **fields: Any) -> None:
        self.entries[key] = {**self.entries.get(key, {}), **fields}
        if self.path:
            _atomic_write_json(self.path, self.entries)


def bill_payload(bill: dict, vendor_external_ids: dict[int, str] | None = None, external_id: str | None = None) -> dict:
    vid = vendor_id(bill)
    payload = {
        "entity": (vendor_external_ids or {}).get(vid) or f"procurify-vendor-{vid}",
        "tranId": bill.get("invoice_number"),
        "tranDate": str(parse_date(bill.get("invoice_date"))),
        "currency": currency_name(bill.get("currency")),
        "total": mstr(bill_total(bill)),
        "memo": f"Procurify bill {bill.get('id')}",
        SOURCE_FIELD: bill.get("id"),
    }
    if external_id:
        payload["externalId"] = external_id
    return payload


@dataclass
class SyncOutcome:
    bill_id: int
    invoice_number: str
    outcome: str  # created | recovered_after_timeout | already_synced | failed
    attempts: int
    erp_internal_ids: list[int] = field(default_factory=list)
    log: list[str] = field(default_factory=list)


def sync_naive(bills: list[dict], erp: MockERP, max_attempts: int = 3, vendor_external_ids: dict[int, str] | None = None) -> list[SyncOutcome]:
    """Retry on timeout by sending the same create again. This is the bug."""
    out: list[SyncOutcome] = []
    for bill in bills:
        o = SyncOutcome(bill_id=bill["id"], invoice_number=bill.get("invoice_number"), outcome="failed", attempts=0)
        before = {r["internalId"] for r in erp.records}
        for attempt in range(1, max_attempts + 1):
            o.attempts = attempt
            try:
                iid = erp.create_vendor_bill(bill_payload(bill, vendor_external_ids))
                o.log.append(f"attempt {attempt}: create -> internalId {iid}")
                o.outcome = "created"
                break
            except ERPTimeout as e:
                o.log.append(f"attempt {attempt}: create -> TIMEOUT ({e})")
        o.erp_internal_ids = [r["internalId"] for r in erp.records if r["internalId"] not in before]
        out.append(o)
    return out


def sync_idempotent(
    bills: list[dict],
    keys: dict[int, str],
    erp: MockERP,
    ledger: Ledger,
    max_attempts: int = 3,
    vendor_external_ids: dict[int, str] | None = None,
) -> list[SyncOutcome]:
    """Before every write, ask the ERP whether a bill with this key exists.

    1. If the ledger says the key is synced, skip.
    2. Mark the key pending in the ledger (persisted).
    3. Each attempt: look up the key in the ERP. Found -> record and stop.
       Not found -> create with externalId = key. Timeout -> next attempt.
    So a write that landed but whose response was lost is found on the next
    attempt, and a write that was really lost is retried.
    """
    out: list[SyncOutcome] = []
    for bill in bills:
        key = keys[bill["id"]]
        o = SyncOutcome(bill_id=bill["id"], invoice_number=bill.get("invoice_number"), outcome="failed", attempts=0)
        entry = ledger.get(key)
        if entry and entry.get("status") == "synced":
            o.outcome = "already_synced"
            o.erp_internal_ids = [entry["erp_internal_id"]]
            o.log.append(f"ledger: {short(key)} already synced as internalId {entry['erp_internal_id']}; no write")
            out.append(o)
            continue
        ledger.set(key, status="pending", bill_id=bill["id"])
        had_timeout = False
        for attempt in range(1, max_attempts + 1):
            o.attempts = attempt
            existing = erp.find_by_external_id(key)
            if existing:
                ledger.set(key, status="synced", erp_internal_id=existing["internalId"])
                o.outcome = "recovered_after_timeout" if had_timeout else "already_in_erp"
                o.erp_internal_ids = [existing["internalId"]]
                o.log.append(f"attempt {attempt}: lookup {short(key)} -> found internalId {existing['internalId']}; no write")
                break
            try:
                iid = erp.create_vendor_bill(bill_payload(bill, vendor_external_ids, external_id=key))
                ledger.set(key, status="synced", erp_internal_id=iid)
                o.outcome = "created"
                o.erp_internal_ids = [iid]
                o.log.append(f"attempt {attempt}: lookup {short(key)} -> none; create -> internalId {iid}")
                break
            except ERPTimeout as e:
                had_timeout = True
                o.log.append(f"attempt {attempt}: lookup {short(key)} -> none; create -> TIMEOUT ({e})")
        out.append(o)
    return out
