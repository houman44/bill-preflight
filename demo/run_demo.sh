#!/usr/bin/env bash
# Bill Pre-flight demo: check a batch of AI-drafted bills, prove the
# certificate is reproducible, then show naive vs idempotent ERP sync under a
# simulated timeout. Terminal output is also saved to demo/demo_output.txt.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if command -v billcheck >/dev/null 2>&1; then
  BILLCHECK=(billcheck)
else
  BILLCHECK=(env PYTHONPATH="$ROOT/src" python3 -m billcheck)
fi

OUT=demo/out
rm -rf "$OUT" && mkdir -p "$OUT"
INPUTS=(--bills demo/bills.json --pos demo/pos.json --receipts demo/receipts.json
        --vendors demo/vendors.json --config demo/rules.json)

body_hash() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["body_sha256"])' "$1"; }

{
echo "=================================================================================="
echo " 1. Check 12 AI-drafted bills against POs, receipts and ERP mappings"
echo "=================================================================================="
echo "\$ billcheck check ${INPUTS[*]} --out demo/report.json --html demo/report.html"
echo
"${BILLCHECK[@]}" check "${INPUTS[@]}" --out demo/report.json --html demo/report.html
echo
echo "=================================================================================="
echo " 2. Re-run the same check: the certificate body must be byte-identical"
echo "=================================================================================="
sleep 1  # make sure generated_at differs
"${BILLCHECK[@]}" check "${INPUTS[@]}" --out "$OUT/report.rerun.json" --html "$OUT/report.rerun.html" --quiet
echo "run 1 body_sha256: $(body_hash demo/report.json)"
echo "run 2 body_sha256: $(body_hash "$OUT/report.rerun.json")"
if [ "$(body_hash demo/report.json)" = "$(body_hash "$OUT/report.rerun.json")" ]; then
  echo "=> identical"
else
  echo "=> DIFFERENT"; exit 1
fi
echo "Only difference between the two report.json files (timestamp sits outside the hashed body):"
diff demo/report.json "$OUT/report.rerun.json" || true
cmp -s demo/report.html "$OUT/report.rerun.html" && echo "report.html: byte-identical across runs"
echo
echo "\$ billcheck verify demo/report.json --input demo/bills.json --input demo/pos.json --input demo/receipts.json"
"${BILLCHECK[@]}" verify demo/report.json --input demo/bills.json --input demo/pos.json --input demo/receipts.json
echo
echo "=================================================================================="
echo " 3. Sync the 6 PASS bills to a mock ERP that times out after committing"
echo "=================================================================================="
echo "\$ billcheck sync ... --mode compare --timeout-on-bills 7002,7008 --timeout-kind after_commit"
echo
"${BILLCHECK[@]}" sync "${INPUTS[@]}" --mode compare --timeout-on-bills 7002,7008 --timeout-kind after_commit
echo
echo "=================================================================================="
echo " 4. Idempotent sync with a persisted ledger, run twice (e.g. a scheduler retry)"
echo "=================================================================================="
echo "\$ billcheck sync ... --mode idempotent --ledger $OUT/ledger.json --erp-state $OUT/mock_erp.json --timeout-on-bills 7003"
"${BILLCHECK[@]}" sync "${INPUTS[@]}" --mode idempotent --ledger "$OUT/ledger.json" --erp-state "$OUT/mock_erp.json" --timeout-on-bills 7003
echo
echo "\$ billcheck sync ... --mode idempotent --ledger $OUT/ledger.json --erp-state $OUT/mock_erp.json   (second run, ERP healthy)"
"${BILLCHECK[@]}" sync "${INPUTS[@]}" --mode idempotent --ledger "$OUT/ledger.json" --erp-state "$OUT/mock_erp.json"
echo
echo "Done. Open demo/report.html for the screenshot-ready report."
} 2>&1 | tee demo/demo_output.txt
