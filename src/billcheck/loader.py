"""Load bills, purchase orders, receipts, vendors and the rules config.

Input files mirror the response shapes of Procurify's public API reference
(https://developer.procurify.com/). Each file may be either a bare JSON list
or the API's list envelope ``{"data": [...], ...}``. JSON numbers are parsed
as Decimal so no float ever reaches a money comparison.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "rules_config_name": "default",
    # Date the checks treat as "today". Fixed in config so a re-run next
    # week produces the same verdicts and the same certificate hash.
    "as_of_date": None,
    "amount_tolerance": "0.01",
    "duplicate_check": {
        "ignore_prefixes": ["invoice", "inv", "no"],
        "same_amount_window_days": 7,
    },
    "three_way_match": {
        "price_warn_pct": "1.0",
        "price_fail_pct": "5.0",
    },
    "closed_periods": [],
    "erp_mappings": {
        "tax_codes": {},
        "account_codes": {},
    },
    "severity_overrides": {},
    "sync": {
        "sync_review_bills": False,
        "max_attempts": 3,
    },
}


@dataclass
class InputFile:
    role: str
    path: Path
    sha256: str
    data: Any


@dataclass
class Inputs:
    bills: list[dict]
    pos: list[dict]
    receipts: list[dict]
    vendors: list[dict] | None
    config: dict
    files: list[InputFile] = field(default_factory=list)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh, parse_float=Decimal)


def _unwrap_list(obj: Any, role: str) -> list[dict]:
    if isinstance(obj, dict) and "data" in obj:
        obj = obj["data"]
    if isinstance(obj, dict):
        obj = [obj]
    if not isinstance(obj, list) or not all(isinstance(x, dict) for x in obj):
        raise ValueError(f"{role}: expected a list of objects (or {{'data': [...]}})")
    return obj


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config_obj(obj: dict) -> dict:
    if not isinstance(obj, dict):
        raise ValueError("config: expected a JSON object")
    return _deep_merge(DEFAULT_CONFIG, obj)


def read_config(path: Path) -> Any:
    suffix = path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError as exc:  # pragma: no cover - depends on env
            raise SystemExit(
                "YAML config needs PyYAML (pip install pyyaml). "
                "JSON config works with no extra dependency."
            ) from exc
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        # Round-trip through JSON so numbers become Decimal the same way.
        return json.loads(json.dumps(raw, default=str), parse_float=Decimal)
    return _read_json(path)


def load_inputs(
    bills: Path,
    pos: Path,
    receipts: Path,
    config: Path,
    vendors: Path | None = None,
) -> Inputs:
    files: list[InputFile] = []

    def take(role: str, p: Path, reader=_read_json) -> Any:
        data = reader(p)
        files.append(InputFile(role=role, path=p, sha256=sha256_file(p), data=data))
        return data

    bills_l = _unwrap_list(take("bills", bills), "bills")
    pos_l = _unwrap_list(take("purchase_orders", pos), "purchase_orders")
    rec_l = _unwrap_list(take("receipts", receipts), "receipts")
    ven_l = _unwrap_list(take("vendors", vendors), "vendors") if vendors else None
    cfg = load_config_obj(take("rules_config", config, read_config))
    return Inputs(bills=bills_l, pos=pos_l, receipts=rec_l, vendors=ven_l, config=cfg, files=files)
