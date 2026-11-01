"""Terminal summary and single-file HTML report, both built from the certificate."""

from __future__ import annotations

import html
import json
import textwrap
from typing import Any

from billcheck.normalize import fmt_money, to_decimal

RULE_TITLES = {
    "R1": "Duplicate invoice (same vendor, any PO)",
    "R2": "Totals add up",
    "R3": "Tax code present and mapped",
    "R4": "GL account code present and mapped",
    "R5": "Bill date valid",
    "R6": "Vendor matches PO and is active",
    "R7": "Three-way match (PO / receipt / bill)",
    "R8": "Currency matches PO",
}

_ORDER = {"BLOCK": 0, "REVIEW": 1, "PASS": 2}


def _po_label(b: dict) -> str:
    return ",".join(str(p) for p in b.get("po_ids", [])) or "-"


def _issues(b: dict) -> list[tuple[str, dict]]:
    out = []
    for r in b["rules"]:
        for f in r["findings"]:
            out.append((r["rule_id"], f))
    return out


def terminal_summary(cert: dict, width: int = 100) -> str:
    body = cert["body"]
    lines: list[str] = []
    lines.append(f"Bill Pre-flight | billcheck {body['tool']['version']} | rules {body['rules_version']} | as of {body['as_of_date']}")
    lines.append("Inputs:")
    for i in body["inputs"]:
        lines.append(f"  {i['file']:<16} sha256 {i['sha256']}")
    lines.append("")
    hdr = f"{'BILL':<5} {'VENDOR':<31} {'INVOICE':<14} {'PO':<6} {'TOTAL':>11} {'CUR':<4} VERDICT"
    lines.append(hdr)
    lines.append("-" * len(hdr))
    for b in sorted(body["bills"], key=lambda b: (_ORDER[b["verdict"]], b["bill_id"])):
        lines.append(
            f"{b['bill_id']:<5} {(b['vendor']['name'] or '')[:31]:<31} {str(b['invoice_number'])[:14]:<14} "
            f"{_po_label(b):<6} {fmt_money(to_decimal(b['invoice_total'])):>11} {b['currency'] or '':<4} {b['verdict']}"
        )
        for rid, f in _issues(b):
            lines.extend(
                textwrap.wrap(
                    f"{rid} {f['status']} {f['code']}: {f['message']}",
                    width=width, initial_indent=" " * 6, subsequent_indent=" " * 10,
                )
            )
    s = body["summary"]
    v = s["verdicts"]
    lines.append("")
    lines.append(f"Verdicts: {s['bills']} bills | PASS {v['PASS']} | REVIEW {v['REVIEW']} | BLOCK {v['BLOCK']}")
    lines.append(
        "Rule hits (fail/warn): "
        + "  ".join(f"{rid} {s['rule_fail_counts'][rid]}/{s['rule_warn_counts'][rid]}" for rid in s["rule_fail_counts"])
    )
    lines.append(f"Certificate body sha256: {cert['body_sha256']}  (unsigned)")
    return "\n".join(lines)


# ------------------------------------------------------------------ HTML

_CSS = """
:root{--ink:#1c2430;--muted:#5b6675;--line:#e3e7ec;--bg:#f6f7f9;--card:#fff;
--pass:#1f7a4d;--pass-bg:#e6f4ec;--warn:#8a5a00;--warn-bg:#fff4dc;--fail:#a4262c;--fail-bg:#fde8e8;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:28px 24px 40px}
h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--muted);margin:0 0 20px}
.tiles{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:20px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.tile .n{font-size:28px;font-weight:650;line-height:1.1}
.tile .l{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.tile.pass .n{color:var(--pass)} .tile.review .n{color:var(--warn)} .tile.block .n{color:var(--fail)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;margin-bottom:20px}
.card h2{font-size:14px;margin:0;padding:12px 16px;border-bottom:1px solid var(--line);background:#fbfcfd}
table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.03em}
tr:last-child td{border-bottom:0}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:650}
.b-PASS{background:var(--pass-bg);color:var(--pass)} .b-REVIEW,.b-WARN{background:var(--warn-bg);color:var(--warn)}
.b-BLOCK,.b-FAIL{background:var(--fail-bg);color:var(--fail)}
.finding{margin:0 0 8px} .finding:last-child{margin:0}
.finding .msg{font-weight:550}
.ev{color:var(--muted);font-size:12px;margin-top:2px}
.ev span{display:inline-block;margin-right:10px}
.ok{color:var(--muted)}
.norm{color:var(--muted);font-size:12px}
.nowrap{white-space:nowrap}
.foot{color:var(--muted);font-size:12px}
.foot td{padding:6px 12px}
.disclaimer{color:var(--muted);font-size:12px;margin-top:16px}
"""


def _e(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def _ev_value(v: Any) -> str:
    if isinstance(v, (list, dict, bool)) or v is None:
        return json.dumps(v, separators=(", ", ": "), ensure_ascii=False)
    return str(v)


_EV_SKIP = {"line_id"}


def _finding_html(rid: str, f: dict) -> str:
    ev = "".join(
        f"<span><b>{_e(k)}</b> {_e(_ev_value(v))}</span>" for k, v in f["evidence"].items() if k not in _EV_SKIP and v not in ([], {}, None)
    )
    return (
        f'<div class="finding"><span class="badge b-{_e(f["status"])}">{_e(rid)} {_e(f["status"])}</span> '
        f'<span class="mono">{_e(f["code"])}</span><div class="msg">{_e(f["message"])}</div>'
        f'<div class="ev">{ev}</div></div>'
    )


def html_report(cert: dict) -> str:
    body = cert["body"]
    s = body["summary"]
    v = s["verdicts"]
    rows = []
    for b in sorted(body["bills"], key=lambda b: (_ORDER[b["verdict"]], b["bill_id"])):
        issues = _issues(b)
        detail = "".join(_finding_html(rid, f) for rid, f in issues) if issues else '<span class="ok">All 8 rules passed</span>'
        rows.append(
            "<tr>"
            f'<td class="mono">{_e(b["bill_id"])}</td>'
            f'<td>{_e(b["vendor"]["name"])}</td>'
            f'<td class="nowrap"><span class="mono">{_e(b["invoice_number"])}</span><div class="norm">norm: {_e(b["invoice_number_normalised"])}</div></td>'
            f'<td class="mono">{_e(_po_label(b))}</td>'
            f'<td class="mono nowrap">{_e(b["invoice_date"])}</td>'
            f'<td class="num">{_e(fmt_money(to_decimal(b["invoice_total"])))} {_e(b["currency"])}</td>'
            f'<td><span class="badge b-{_e(b["verdict"])}">{_e(b["verdict"])}</span></td>'
            f"<td>{detail}</td>"
            "</tr>"
        )
    rule_rows = "".join(
        f'<tr><td class="mono">{_e(rid)}</td><td>{_e(RULE_TITLES.get(rid, ""))}</td>'
        f'<td class="num">{s["rule_fail_counts"][rid]}</td><td class="num">{s["rule_warn_counts"][rid]}</td>'
        f'<td class="num">{s["bills"] - s["rule_fail_counts"][rid] - s["rule_warn_counts"][rid]}</td></tr>'
        for rid in s["rule_fail_counts"]
    )
    inputs = "".join(
        f'<tr><td>{_e(i["role"])}</td><td class="mono">{_e(i["file"])}</td><td class="mono">{_e(i["sha256"])}</td></tr>'
        for i in body["inputs"]
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bill Pre-flight report</title><style>{_CSS}</style></head>
<body><div class="wrap">
<h1>Bill Pre-flight report</h1>
<p class="sub">Deterministic checks between AI-drafted bills and the ERP &middot; as of {_e(body["as_of_date"])} &middot;
billcheck {_e(body["tool"]["version"])} &middot; rules {_e(body["rules_version"])} &middot; config &ldquo;{_e(body["rules_config_name"])}&rdquo;</p>
<div class="tiles">
<div class="tile"><div class="n">{s["bills"]}</div><div class="l">Bills checked</div></div>
<div class="tile pass"><div class="n">{v["PASS"]}</div><div class="l">Pass &middot; ready to sync</div></div>
<div class="tile review"><div class="n">{v["REVIEW"]}</div><div class="l">Review &middot; human decides</div></div>
<div class="tile block"><div class="n">{v["BLOCK"]}</div><div class="l">Block &middot; not synced</div></div>
</div>
<div class="card"><h2>Bills</h2><table>
<tr><th>Bill</th><th>Vendor</th><th>Invoice</th><th>PO</th><th>Date</th><th style="text-align:right">Total</th><th>Verdict</th><th>Failed / warned rules and evidence</th></tr>
{"".join(rows)}
</table></div>
<div class="card"><h2>Rule hits in this batch</h2><table>
<tr><th>Rule</th><th>Check</th><th style="text-align:right">Fail</th><th style="text-align:right">Warn</th><th style="text-align:right">Pass</th></tr>
{rule_rows}
</table></div>
<div class="card"><h2>Certificate</h2><table class="foot">
{inputs}
<tr><td><b>certificate body</b></td><td>canonical JSON</td><td class="mono"><b>{_e(cert["body_sha256"])}</b></td></tr>
</table></div>
<p class="disclaimer">The certificate body is canonical JSON and re-running the same inputs gives the same hash. It is not signed.
All data in this report is synthetic; vendors are fictional. Independent prototype by Houman Azimi (Barg Labs).
Not affiliated with or endorsed by Procurify. Uses only public information.</p>
</div></body></html>
"""
