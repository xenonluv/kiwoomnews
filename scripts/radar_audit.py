#!/usr/bin/env python3
"""In-memory audit collector for a radar scan.

Audit collection must never change a live signal. Persistence is intentionally
best-effort and isolated from the scanner's output and trading decisions.
"""

import hashlib
import json
import os
import re
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
_SECRET_KEY = re.compile(r"(secret|token|authorization|app_?key|account)", re.I)


def _clean(value):
    if isinstance(value, dict):
        return {str(k): ("[REDACTED]" if _SECRET_KEY.search(str(k)) else _clean(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, tuple):
        return [_clean(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class AuditCollector:
    def __init__(self, *, model_meta=None, dry_run=False, broker="kiwoom"):
        now = datetime.now(KST)
        self.started_at = now.strftime("%Y-%m-%d %H:%M:%S KST")
        self.run_id = now.strftime("%Y%m%dT%H%M%S") + f"-{os.getpid()}"
        self.trade_date = now.strftime("%Y%m%d")
        self.dry_run = bool(dry_run)
        self.broker = broker
        self.model_meta = dict(model_meta or {})
        self.observations = {}
        self.errors = []
        self.minute_payloads = {}
        self.precut_candidates = []

    def observe(self, code, *, name=None, source=None, **fields):
        if not code:
            return None
        code = str(code)
        row = self.observations.setdefault(code, {
            "code": code,
            "name": name or code,
            "source_universes": [],
            "observed_first_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
            "observed_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
            "market_basis": {"price": "KRX", "money": "UN", "change": "KRX"},
            "gate_decisions": [],
            "status": "NOT_APPLICABLE",
        })
        row["observed_last_at"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")
        if name and (not row.get("name") or row.get("name") == code):
            row["name"] = name
        if source and source not in row["source_universes"]:
            row["source_universes"].append(source)
        for key, value in fields.items():
            if value is not None:
                row[key] = _clean(value)
        return row

    def gate(self, code, track, gate, status, *, actual=None, threshold=None,
             reason_code=None, reason_text=None):
        row = self.observe(code)
        if row is None:
            return
        decision = {
            "track": track,
            "gate": gate,
            "status": status,
            "actual": _clean(actual),
            "threshold": _clean(threshold),
            "reason_code": reason_code,
            "reason_text": reason_text,
            "evaluated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        }
        # Stable key replacement avoids duplicate gates when a code arrives via
        # multiple universes in the same scan.
        key = (track, gate)
        for i, old in enumerate(row["gate_decisions"]):
            if (old.get("track"), old.get("gate")) == key:
                row["gate_decisions"][i] = decision
                break
        else:
            row["gate_decisions"].append(decision)
        if status == "PASS":
            if row.get("status") in (None, "NOT_APPLICABLE"):
                row["status"] = "PASS"
        elif status in ("API_ERROR", "MISSING_DATA"):
            row["status"] = status
        elif row.get("status") not in ("API_ERROR", "MISSING_DATA"):
            row["status"] = status

    def error(self, where, error, *, code=None):
        item = {
            "where": where,
            "code": code,
            "error": str(error)[:500],
            "at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        }
        self.errors.append(_clean(item))
        if code:
            self.gate(code, "collection", where, "API_ERROR",
                      reason_code="API_ERROR", reason_text=str(error)[:200])

    def minute_bars(self, code, bars, *, market_basis="UN", source_broker=None,
                    fetch_status="ok", error=None):
        if not code or not isinstance(bars, list):
            return
        cleaned = []
        for bar in bars:
            if not isinstance(bar, dict):
                continue
            item = {k: bar.get(k) for k in
                    ("time", "open", "high", "low", "close", "value")
                    if bar.get(k) is not None}
            volume = bar.get("volume") if bar.get("volume") is not None else bar.get("vol")
            if volume is not None:
                item["volume"] = volume
            cleaned.append(item)
        self.minute_payloads[(str(code), market_basis)] = {
            "trade_date": self.trade_date,
            "code": str(code),
            "market_basis": market_basis,
            "source_broker": source_broker or self.broker,
            "bars": cleaned,
            "fetch_status": fetch_status,
            "error": error,
        }

    def mark_ranked(self, suspects):
        candidate_codes = set()
        self.precut_candidates = []
        for s in suspects or []:
            code = s.get("code")
            if not code:
                continue
            candidate_codes.add(code)
            rank = {
                "rank_bucket": s.get("rank_bucket"),
                "rank_reason": s.get("rank_reason"),
                "shadow_bucket": s.get("shadow_bucket"),
                "precut_rank": s.get("precut_rank"),
                "published_rank": None,
                "published": False,
                "suspicion_score": s.get("suspicion_score"),
                "rank_model_version": self.model_meta.get("rank_model_version"),
            }
            row = self.observe(
                code, name=s.get("name"), source="suspect",
                pattern=s.get("pattern"), rank=rank,
                candidate_snapshot={
                    "price": s.get("price"), "change_pct": s.get("change_pct"),
                    "change_basis": s.get("change_basis"), "high_pct": s.get("high_pct"),
                    "turnover_pct": s.get("turnover_pct"),
                    "turnover_2d_pct": s.get("turnover_2d_pct"),
                    "peak_turnover_pct": s.get("peak_turnover_pct"),
                })
            if row is not None:
                row["status"] = "PASS"
            self.precut_candidates.append({
                "code": code, "name": s.get("name"), **rank,
            })
        for code, row in self.observations.items():
            row.setdefault("rank", {"published": False, "published_rank": None})
            if code not in candidate_codes and row.get("status") == "PASS":
                row["status"] = "REJECT_RULE"

    def payload(self, *, generated_at=None, scan_ok=True, market_phase=None):
        completed = datetime.now(KST)
        observations = sorted(self.observations.values(), key=lambda x: x.get("code", ""))
        source_counts = {}
        for row in observations:
            row["source_universes"] = sorted(set(row.get("source_universes") or []))
            for source in row["source_universes"]:
                source_counts[source] = source_counts.get(source, 0) + 1
        body = {
            "schema_version": 1,
            "record_type": "scan_run",
            "run": {
                "run_id": self.run_id,
                "started_at": self.started_at,
                "completed_at": completed.strftime("%Y-%m-%d %H:%M:%S KST"),
                "trade_date": self.trade_date,
                "generated_at": generated_at,
                "market_phase": market_phase or _market_phase(completed),
                "broker": self.broker,
                "price_basis": "KRX",
                "money_basis": "UN",
                "scan_ok": bool(scan_ok),
                "dry_run": self.dry_run,
                **self.model_meta,
            },
            "universe": {
                "scope": "observed_union_not_full_market",
                "source_universes": sorted(source_counts),
                "scan_n": getattr(self, "scan_n", None),
                "observed_count": len(observations),
                "source_counts": source_counts,
            },
            "observations": observations,
            "precut_candidates": self.precut_candidates,
            "published_candidates": [],
            "errors": self.errors,
        }
        canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        body["integrity"] = {"payload_sha256": hashlib.sha256(canonical.encode()).hexdigest()}
        return _clean(body)

    def persist(self, *, generated_at=None, scan_ok=True, market_phase=None):
        if self.dry_run:
            return None
        try:
            import radar_json_store as store
            initialized = store.initialize_store()
            if hasattr(initialized, "ok") and not initialized.ok:
                self.error("store_initialize", initialized.error)
                return initialized
            result = store.write_scan(self.payload(
                generated_at=generated_at, scan_ok=scan_ok, market_phase=market_phase),
                update_manifest=False)
            for payload in self.minute_payloads.values():
                minute_result = store.merge_minute_bars(**payload, update_manifest=False)
                if hasattr(minute_result, "ok") and not minute_result.ok:
                    self.error("minute_persist", minute_result.error, code=payload.get("code"))
            manifest = store.rebuild_manifest(self.trade_date)
            if hasattr(manifest, "ok") and not manifest.ok:
                self.error("manifest_rebuild", manifest.error)
            return result
        except Exception as exc:
            # Optional research persistence must not change the live radar.
            self.error("audit_persist", exc)
            return None


def _market_phase(now):
    hm = now.strftime("%H%M")
    if hm < "0900":
        return "preopen"
    if hm <= "1530":
        return "krx_open"
    if hm < "1600":
        return "krx_close"
    if hm <= "2000":
        return "nxt_after"
    return "operational_eod"
