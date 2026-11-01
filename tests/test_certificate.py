import json
import shutil

from billcheck.certificate import build_certificate, canonical_bytes, sha256_hex, verify_certificate, write_certificate
from billcheck.cli import main
from billcheck.engine import resolve_as_of, run_checks
from billcheck.loader import load_inputs


def certify(paths, generated_at=None):
    inputs = load_inputs(**paths)
    as_of = resolve_as_of(inputs.config)
    _, verdicts = run_checks(inputs, as_of)
    return build_certificate(inputs, verdicts, as_of, generated_at=generated_at)


def test_body_is_byte_identical_across_runs(demo_paths):
    a = certify(demo_paths, generated_at="2026-10-31T10:00:00+00:00")
    b = certify(demo_paths, generated_at="2026-11-01T23:59:59+00:00")
    assert canonical_bytes(a["body"]) == canonical_bytes(b["body"])
    assert a["body_sha256"] == b["body_sha256"]
    assert a["generated_at"] != b["generated_at"]  # timestamp lives outside the hashed body


def test_written_reports_differ_only_in_generated_at(demo_paths, tmp_path):
    p1, p2 = tmp_path / "r1.json", tmp_path / "r2.json"
    write_certificate(certify(demo_paths, "T1"), p1)
    write_certificate(certify(demo_paths, "T2"), p2)
    d1, d2 = json.loads(p1.read_text()), json.loads(p2.read_text())
    d1.pop("generated_at"), d2.pop("generated_at")
    assert d1 == d2


def test_body_records_hashes_rules_version_and_no_paths(demo_paths):
    cert = certify(demo_paths)
    body = cert["body"]
    assert body["rules_version"] == "1.0.0"
    assert {i["file"] for i in body["inputs"]} == {"bills.json", "pos.json", "receipts.json", "rules.json", "vendors.json"}
    assert all(len(i["sha256"]) == 64 for i in body["inputs"])
    assert "/" not in json.dumps(body["inputs"])  # basenames only, so moving the folder keeps the hash
    assert cert["signed"] is False
    assert cert["body_sha256"] == sha256_hex(canonical_bytes(body))


def test_input_change_changes_hash(demo_paths, tmp_path):
    for k, p in demo_paths.items():
        shutil.copy(p, tmp_path / p.name)
    paths = {k: tmp_path / p.name for k, p in demo_paths.items()}
    h1 = certify(paths)["body_sha256"]
    text = paths["bills"].read_text().replace('"INV-00452"', '"INV-00453"')
    paths["bills"].write_text(text)
    assert certify(paths)["body_sha256"] != h1


def test_verify_detects_tampering_and_changed_inputs(demo_paths, tmp_path):
    cert = certify(demo_paths)
    assert verify_certificate(cert, list(demo_paths.values())) == []
    cert["body"]["bills"][0]["verdict"] = "PASS" if cert["body"]["bills"][0]["verdict"] != "PASS" else "BLOCK"
    assert any("body_sha256 mismatch" in p for p in verify_certificate(cert))
    changed = tmp_path / "bills.json"
    changed.write_text(demo_paths["bills"].read_text() + " ")
    assert any("bills.json" in p for p in verify_certificate(certify(demo_paths), [changed]))


def test_cli_check_and_verify(demo_paths, tmp_path, capsys):
    out, html = tmp_path / "report.json", tmp_path / "report.html"
    args = ["check", "--bills", str(demo_paths["bills"]), "--pos", str(demo_paths["pos"]),
            "--receipts", str(demo_paths["receipts"]), "--config", str(demo_paths["config"]),
            "--vendors", str(demo_paths["vendors"]), "--out", str(out), "--html", str(html), "--quiet"]
    assert main(args) == 0
    assert main(args + ["--exit-code"]) == 2  # batch contains BLOCKs
    page = html.read_text()
    assert "<style>" in page and "<script" not in page and "http" not in page.split("</style>")[0]
    assert "7010" in page and "DUPLICATE_INVOICE_NUMBER" in page
    assert main(["verify", str(out), "--input", str(demo_paths["bills"])]) == 0
    first = html.read_text()
    main(args)
    assert html.read_text() == first  # HTML has no timestamp: byte-identical too
