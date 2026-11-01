"""Command line: billcheck check | sync | verify."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from billcheck import __version__
from billcheck.certificate import build_certificate, verify_certificate, write_certificate
from billcheck.engine import BLOCK, OK, REVIEW, resolve_as_of, run_checks
from billcheck.loader import load_inputs
from billcheck.report import html_report, terminal_summary
from billcheck.sync import AFTER_COMMIT, BEFORE_COMMIT, Ledger, MockERP, sync_idempotent, sync_naive


def _add_inputs(p: argparse.ArgumentParser) -> None:
    p.add_argument("--bills", required=True, type=Path, help="draft bills JSON (Procurify bill shape)")
    p.add_argument("--pos", required=True, type=Path, help="purchase orders JSON (Procurify PO shape)")
    p.add_argument("--receipts", required=True, type=Path, help="receipt items JSON (Procurify receipt item shape)")
    p.add_argument("--config", required=True, type=Path, help="rules config (.json; .yaml if PyYAML is installed)")
    p.add_argument("--vendors", type=Path, help="optional vendors JSON (Procurify vendor shape)")
    p.add_argument("--as-of", help="override config as_of_date (YYYY-MM-DD)")


def _run(args):
    inputs = load_inputs(args.bills, args.pos, args.receipts, args.config, args.vendors)
    as_of = resolve_as_of(inputs.config, args.as_of)
    ctx, verdicts = run_checks(inputs, as_of)
    cert = build_certificate(inputs, verdicts, as_of)
    return inputs, ctx, verdicts, cert


def cmd_check(args) -> int:
    _, _, verdicts, cert = _run(args)
    if args.out:
        write_certificate(cert, args.out)
    if args.html:
        args.html.write_text(html_report(cert), encoding="utf-8")
    if not args.quiet:
        print(terminal_summary(cert))
        if args.out:
            print(f"Wrote {args.out}")
        if args.html:
            print(f"Wrote {args.html}")
    if args.exit_code:
        vs = {v.verdict for v in verdicts}
        return 2 if BLOCK in vs else 1 if REVIEW in vs else 0
    return 0


def _parse_calls(s: str | None) -> set[int]:
    if not s:
        return set()
    return {int(x) for x in s.split(",") if x.strip()}


def _print_outcomes(outcomes, erp: MockERP, calls_before: int = 0) -> None:
    for o in outcomes:
        first, *rest = o.log or ["(no attempts)"]
        prefix = f"  bill {o.bill_id}  {str(o.invoice_number):<14} "
        print(prefix + first)
        for line in rest:
            print(" " * len(prefix) + line)
    dups = erp.duplicates()
    n_inv = len({(r["entity"], r["tranId"]) for r in erp.records})
    print(f"  ERP create calls this run: {erp.create_calls - calls_before}   ERP lookups this run: {erp.lookup_calls}")
    print(f"  ERP now holds {len(erp.records)} bill record(s) for {n_inv} distinct invoice(s).")
    if dups:
        for d in dups:
            print(f"  DUPLICATE in ERP: {d['entity']} invoice {d['tranId']} -> internalIds {d['internalIds']}")
    else:
        print("  Duplicates in ERP: none")


def cmd_sync(args) -> int:
    inputs, _, verdicts, _ = _run(args)
    allowed = {OK, REVIEW} if (args.include_review or inputs.config["sync"].get("sync_review_bills")) else {OK}
    eligible = [v for v in verdicts if v.verdict in allowed]
    held = [v for v in verdicts if v.verdict not in allowed]
    bills = [v.bill for v in eligible]
    keys = {v.bill_id: v.idempotency_key for v in eligible}
    ext_ids = {v["id"]: v.get("external_id") for v in (inputs.vendors or [])}
    calls = _parse_calls(args.timeout_on_calls)
    tbills = _parse_calls(args.timeout_on_bills)
    max_attempts = args.max_attempts or int(inputs.config["sync"].get("max_attempts", 3))

    held_desc = ", ".join(f"{v.bill_id} {v.verdict}" for v in held) or "none"
    print(f"Pre-sync gate: {len(eligible)} of {len(verdicts)} bills eligible to sync; held back: {held_desc}")
    when = "AFTER the bill is committed" if args.timeout_kind == AFTER_COMMIT else "BEFORE the bill is committed"
    if calls:
        print(f"Simulated ERP: create call(s) #{', #'.join(str(c) for c in sorted(calls))} time out {when}.")
    if tbills:
        print(f"Simulated ERP: the first create for bill(s) {', '.join(str(b) for b in sorted(tbills))} "
              f"times out {when}.")
    print(f"Retry policy: up to {max_attempts} attempts per bill.")

    modes = ["naive", "idempotent"] if args.mode == "compare" else [args.mode]
    results = {}
    for mode in modes:
        print()
        if mode == "naive":
            erp = MockERP(calls, args.timeout_kind, args.erp_state if args.mode != "compare" else None, tbills)
            print("[naive retry: resend the create on timeout]")
            before = erp.create_calls
            out = sync_naive(bills, erp, max_attempts, ext_ids)
        else:
            erp = MockERP(calls, args.timeout_kind, args.erp_state if args.mode != "compare" else None, tbills)
            ledger = Ledger(args.ledger if args.mode != "compare" else None)
            print("[idempotent retry: deterministic key, look up before every write]")
            before = erp.create_calls
            out = sync_idempotent(bills, keys, erp, ledger, max_attempts, ext_ids)
        _print_outcomes(out, erp, before)
        results[mode] = erp
    if args.mode == "compare":
        n, i = results["naive"], results["idempotent"]
        print()
        print(f"Result: naive retry -> {len(n.records)} ERP bills ({len(n.records) - len(bills)} duplicate(s)); "
              f"idempotent retry -> {len(i.records)} ERP bills ({len(i.records) - len(bills)} duplicate(s)) "
              f"for {len(bills)} eligible bills.")
    return 0


def cmd_verify(args) -> int:
    cert = json.loads(args.report.read_text(encoding="utf-8"))
    problems = verify_certificate(cert, args.input)
    if problems:
        for p in problems:
            print(f"FAIL  {p}")
        return 1
    print(f"OK    body_sha256 {cert['body_sha256']} matches the canonical body")
    for p in args.input or []:
        print(f"OK    {p.name} matches the hash recorded in the certificate")
    print("Note: the certificate is unsigned; this checks integrity, not authorship.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="billcheck", description="Deterministic pre-sync checks for AI-drafted bills.")
    ap.add_argument("--version", action="version", version=f"billcheck {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="run the rules and write a certificate / HTML report")
    _add_inputs(c)
    c.add_argument("--out", type=Path, help="write certificate JSON here")
    c.add_argument("--html", type=Path, help="write single-file HTML report here")
    c.add_argument("--quiet", action="store_true", help="no terminal summary")
    c.add_argument("--exit-code", action="store_true", help="exit 2 if any BLOCK, 1 if any REVIEW, else 0")
    c.set_defaults(func=cmd_check)

    s = sub.add_parser("sync", help="sync PASS bills into the mock ERP (idempotency demo)")
    _add_inputs(s)
    s.add_argument("--mode", choices=["naive", "idempotent", "compare"], default="compare")
    s.add_argument("--timeout-on-calls", default="", help="comma list of ERP create-call numbers that time out, e.g. 2,5")
    s.add_argument("--timeout-on-bills", default="", help="comma list of bill ids whose first ERP create times out, e.g. 7002,7008")
    s.add_argument("--timeout-kind", choices=[AFTER_COMMIT, BEFORE_COMMIT], default=AFTER_COMMIT)
    s.add_argument("--max-attempts", type=int)
    s.add_argument("--include-review", action="store_true", help="also sync REVIEW bills (default: PASS only)")
    s.add_argument("--ledger", type=Path, help="idempotency ledger file (single-mode runs only)")
    s.add_argument("--erp-state", type=Path, help="persist mock ERP records to this file (single-mode runs only)")
    s.set_defaults(func=cmd_sync)

    v = sub.add_parser("verify", help="recompute a certificate's body hash; optionally re-hash input files")
    v.add_argument("report", type=Path)
    v.add_argument("--input", type=Path, action="append", help="input file to compare against recorded sha256 (repeatable)")
    v.set_defaults(func=cmd_verify)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, KeyError, FileNotFoundError) as exc:
        print(f"billcheck: error: {exc}", file=sys.stderr)
        return 64
