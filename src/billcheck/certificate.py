"""Batch certificate: a canonical JSON body plus its SHA-256.

The body holds everything that determines the verdicts (rules version,
config hash, input file hashes, as-of date, per-bill results) and nothing
that changes between runs (no timestamps, no absolute paths, no hostnames).
Re-running the same inputs gives a byte-identical body and so the same
``body_sha256``. ``generated_at`` sits outside the body.

The certificate is NOT signed. The hash shows the body has not changed since
it was produced; it does not show who produced it. Anyone can recompute it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from billcheck import __version__
from billcheck.engine import BLOCK, IDEMPOTENCY_KEY_VERSION, OK, REVIEW, BillVerdict
from billcheck.loader import Inputs
from billcheck.normalize import dstr
from billcheck.rules import FAIL, RULES, RULES_VERSION, WARN

CERT_FORMAT = "bill-preflight-certificate/1"


def _default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return dstr(o)
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")


def canonical_bytes(obj: Any) -> bytes:
    """Canonical JSON: sorted keys, no insignificant whitespace, UTF-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_default).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_body(inputs: Inputs, verdicts: list[BillVerdict], as_of: date) -> dict:
    counts = {OK: 0, REVIEW: 0, BLOCK: 0}
    rule_fail = {rid: 0 for rid, _ in RULES}
    rule_warn = {rid: 0 for rid, _ in RULES}
    for v in verdicts:
        counts[v.verdict] += 1
        for r in v.results:
            if r.status == FAIL:
                rule_fail[r.rule_id] += 1
            elif r.status == WARN:
                rule_warn[r.rule_id] += 1
    config_file = next((f for f in inputs.files if f.role == "rules_config"), None)
    return {
        "certificate_format": CERT_FORMAT,
        "tool": {"name": "billcheck", "version": __version__},
        "rules_version": RULES_VERSION,
        "idempotency_key_version": IDEMPOTENCY_KEY_VERSION,
        "rules_config_name": inputs.config.get("rules_config_name"),
        "rules_config_sha256": config_file.sha256 if config_file else None,
        "as_of_date": as_of.isoformat(),
        "inputs": sorted(
            ({"role": f.role, "file": f.path.name, "sha256": f.sha256} for f in inputs.files),
            key=lambda x: x["role"],
        ),
        "summary": {
            "bills": len(verdicts),
            "verdicts": counts,
            "rule_fail_counts": rule_fail,
            "rule_warn_counts": rule_warn,
        },
        "bills": [v.to_dict() for v in sorted(verdicts, key=lambda v: v.bill_id)],
    }


def build_certificate(inputs: Inputs, verdicts: list[BillVerdict], as_of: date, generated_at: str | None = None) -> dict:
    body = build_body(inputs, verdicts, as_of)
    # Round-trip so the in-memory body is exactly what gets hashed and written.
    body = json.loads(canonical_bytes(body))
    return {
        "body": body,
        "body_sha256": sha256_hex(canonical_bytes(body)),
        "hash_scope": "SHA-256 of canonical JSON of 'body' (sorted keys, separators ',' ':', UTF-8)",
        "signed": False,
        "generated_at": generated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }


def write_certificate(cert: dict, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cert, fh, indent=2, sort_keys=False, ensure_ascii=False)
        fh.write("\n")


def verify_certificate(cert: dict, input_paths: list[Path] | None = None) -> list[str]:
    """Return a list of problems; empty means the certificate checks out."""
    problems: list[str] = []
    body = cert.get("body")
    if body is None:
        return ["no 'body' in certificate"]
    actual = sha256_hex(canonical_bytes(body))
    if actual != cert.get("body_sha256"):
        problems.append(f"body_sha256 mismatch: recorded {cert.get('body_sha256')}, recomputed {actual}")
    if input_paths:
        from billcheck.loader import sha256_file

        recorded = {i["file"]: i["sha256"] for i in body.get("inputs", [])}
        for p in input_paths:
            if p.name not in recorded:
                problems.append(f"{p.name}: not listed in certificate inputs")
            elif sha256_file(p) != recorded[p.name]:
                problems.append(f"{p.name}: sha256 differs from certificate (file changed since check)")
    return problems
