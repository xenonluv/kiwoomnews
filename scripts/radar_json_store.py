#!/usr/bin/env python3
"""Local JSON authority for radar audit data.

The module deliberately uses only the Python standard library. Public write
functions are fail-safe: they return ``StoreResult`` instead of raising, so an
optional audit write cannot alter trading control flow. Completed scan files
are immutable; mutable indexes and minute files use verified atomic replace.
"""

import copy
import hashlib
import json
import logging
import os
import re
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

try:
    import fcntl
except ImportError:  # pragma: no cover - the production target is macOS.
    fcntl = None


SCHEMA_VERSION = 1
KST = timezone(timedelta(hours=9))
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = REPO_ROOT / "data" / "local" / "radar_raw"
ROOT_ENV = "RADAR_JSON_STORE_ROOT"

SCHEMA_DOCUMENT = {
    "schema_version": SCHEMA_VERSION,
    "format": "radar-local-json",
    "authority": "local_raw_json",
    "scope": "observed_union_not_full_market",
    "timezone": "Asia/Seoul",
    "immutable_scan_files": True,
}

DEFAULT_MODEL = {
    "schema_version": SCHEMA_VERSION,
    "record_type": "rank_model",
    "model_version": "rank4-v2",
    "policy_name": "rank4",
    "source_commit": "c70b893",
    "effective_from": "20260724",
    "effective_at": "2026-07-24 09:00:00 KST",
    "prior": {
        "source": "full_market_shakeout_census_202601_202605",
        "strength": "observe",
        "summary": "very_good and comboD intersection before Tier1; Tier2-only demoted",
    },
}

_LOG = logging.getLogger(__name__)
_THREAD_LOCKS: Dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

_SENSITIVE_KEYS = {
    "authorization",
    "proxyauthorization",
    "token",
    "accesstoken",
    "refreshtoken",
    "apitoken",
    "apikey",
    "appkey",
    "appsecret",
    "clientsecret",
    "secret",
    "secretkey",       # 키움 실필드명(kiwoom_client.py 토큰 요청 body) — 적대 리뷰 2026-07-11 M1
    "password",
    "passwd",
    "account",
    "accountno",
    "accountnumber",
    "accountid",
    "cano",
    "acntno",
    "계좌번호",
}
_SENSITIVE_SUFFIXES = (
    "accesstoken",
    "refreshtoken",
    "apitoken",
    "apikey",
    "appkey",
    "appsecret",
    "clientsecret",
    "secret",
    "secretkey",       # *_secret_key / kiwoom_secretkey 등
    "token",           # kiwoom_token, bearer_token 등 범용 토큰 키 — 적대 리뷰 2026-07-11 M1
    "password",
)
_AUTH_VALUE_RE = re.compile(r"(?i)\b(bearer|basic)\s+[^\s\"',;}]+")
_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:access_token|refresh_token|token|api_key|api_secret|apikey|"
    r"appkey|app_secret|client_secret|secret_key|secretkey|secret|account_no|cano)=)[^&#\s]+"
)
_LABELED_ACCOUNT_RE = re.compile(
    r"(?i)\b(account(?:_?no|\s+number)?|acct|cano)\s*[:=]\s*[-0-9]{6,}"
)
_KOREAN_ACCOUNT_RE = re.compile(r"(계좌번호\s*[:=]\s*)[-0-9]{6,}")


@dataclass(frozen=True)
class StoreResult:
    """Non-throwing result returned by every public persistence operation."""

    ok: bool
    path: Optional[str] = None
    error: Optional[str] = None
    sha256: Optional[str] = None
    size: Optional[int] = None
    value: Any = None
    warnings: Tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.ok


def _now() -> datetime:
    return datetime.now(KST)


def _timestamp(value: Optional[datetime] = None) -> str:
    value = value or _now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=KST)
    return value.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST")


def store_root(root: Optional[Union[str, os.PathLike]] = None) -> Path:
    if root is not None:
        return Path(root).expanduser()
    configured = os.environ.get(ROOT_ENV)
    return Path(configured).expanduser() if configured else DEFAULT_ROOT


def normalize_trade_date(value: Any = None) -> str:
    """Return YYYYMMDD, rejecting path-like or ambiguous date values."""

    if value is None:
        return _now().strftime("%Y%m%d")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=KST)
        return value.astimezone(KST).strftime("%Y%m%d")
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    digits = re.sub(r"[^0-9]", "", str(value))
    if len(digits) != 8:
        raise ValueError("trade_date must be YYYYMMDD")
    datetime.strptime(digits, "%Y%m%d")
    return digits


def local_day_dir(
    trade_date: Any = None,
    root: Optional[Union[str, os.PathLike]] = None,
) -> Path:
    day = normalize_trade_date(trade_date)
    return store_root(root) / day[0:4] / day[4:6] / day[6:8]


def _normalize_key(key: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", str(key)).lower()


def _is_sensitive_key(key: Any) -> bool:
    normalized = _normalize_key(key)
    return normalized in _SENSITIVE_KEYS or normalized.endswith(_SENSITIVE_SUFFIXES)


def _redact_string(value: str) -> str:
    if "PRIVATE KEY-----" in value or "BEGIN PRIVATE KEY" in value:
        return "[REDACTED]"
    value = _AUTH_VALUE_RE.sub(lambda match: match.group(1) + " [REDACTED]", value)
    value = _QUERY_SECRET_RE.sub(lambda match: match.group(1) + "[REDACTED]", value)
    value = _LABELED_ACCOUNT_RE.sub(lambda match: match.group(1) + "=[REDACTED]", value)
    return _KOREAN_ACCOUNT_RE.sub(lambda match: match.group(1) + "[REDACTED]", value)


def redact_secrets(value: Any) -> Any:
    """Return a deep, JSON-compatible copy with known credentials removed."""

    if isinstance(value, Mapping):
        clean: Dict[str, Any] = {}
        for key, child in value.items():
            text_key = str(key)
            clean[text_key] = "[REDACTED]" if _is_sensitive_key(key) else redact_secrets(child)
        return clean
    if isinstance(value, (list, tuple)):
        return [redact_secrets(child) for child in value]
    if isinstance(value, str):
        return _redact_string(value)
    return copy.deepcopy(value)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _document_with_integrity(payload: Mapping[str, Any]) -> Dict[str, Any]:
    document = redact_secrets(payload)
    if not isinstance(document, dict):
        raise TypeError("JSON document must be an object")
    document.setdefault("schema_version", SCHEMA_VERSION)
    document.setdefault("created_at", _timestamp())
    integrity = document.get("integrity")
    if not isinstance(integrity, dict):
        integrity = {}
    else:
        integrity = dict(integrity)
    integrity.pop("payload_sha256", None)
    integrity["algorithm"] = "sha256"
    document["integrity"] = integrity
    integrity["payload_sha256"] = hashlib.sha256(_canonical_bytes(document)).hexdigest()
    return document


def verify_payload_integrity(document: Any) -> bool:
    if not isinstance(document, Mapping):
        return False
    integrity = document.get("integrity")
    if not isinstance(integrity, Mapping):
        return False
    expected = integrity.get("payload_sha256")
    if not isinstance(expected, str) or len(expected) != 64:
        return False
    candidate = copy.deepcopy(dict(document))
    candidate_integrity = dict(candidate.get("integrity") or {})
    candidate_integrity.pop("payload_sha256", None)
    candidate["integrity"] = candidate_integrity
    try:
        actual = hashlib.sha256(_canonical_bytes(candidate)).hexdigest()
    except (TypeError, ValueError):
        return False
    return actual == expected


def _thread_lock(path: Path) -> threading.Lock:
    key = str(path.absolute())
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _THREAD_LOCKS[key] = lock
        return lock


@contextmanager
def _exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    thread_lock = _thread_lock(path)
    with thread_lock:
        with open(path, "a+b") as lock_file:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(str(path), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _file_digest(path: Path) -> Tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as source:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def _atomic_replace_bytes(path: Path, payload: bytes) -> None:
    """Atomically replace arbitrary bytes after fsync and JSONL validation."""

    temp_path: Optional[Path] = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.parent / (
            "." + path.name + "." + str(os.getpid()) + "." + uuid.uuid4().hex + ".tmp"
        )
        with open(temp_path, "xb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        checked = temp_path.read_bytes()
        if checked != payload or _inspect_jsonl(checked)["corruptions"]:
            raise ValueError("temporary JSONL repair verification failed")
        os.replace(temp_path, path)
        temp_path = None
        _fsync_directory(path.parent)
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass


def _failure(path: Optional[Path], error: Any, value: Any = None) -> StoreResult:
    return StoreResult(ok=False, path=str(path) if path else None, error=str(error), value=value)


def atomic_write_json(
    path: Union[str, os.PathLike],
    payload: Mapping[str, Any],
    *,
    overwrite: bool = True,
) -> StoreResult:
    """Write one verified JSON object with temp+fsync+parse+os.replace.

    ``overwrite=False`` is used for immutable scan/decision artifacts. The
    target-specific process lock prevents two local writers from both passing
    the existence check.
    """

    target: Optional[Path] = None
    temp_path: Optional[Path] = None
    try:
        target = Path(path)
        document = _document_with_integrity(payload)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.parent.name == "scans" and target.name.startswith("scan_"):
            overwrite = False
        lock_path = target.parent / ("." + target.name + ".lock")
        with _exclusive_lock(lock_path):
            if not overwrite and target.exists():
                return _failure(target, "immutable target already exists")
            temp_path = target.parent / (
                "." + target.name + "." + str(os.getpid()) + "." + uuid.uuid4().hex + ".tmp"
            )
            with open(temp_path, "x", encoding="utf-8") as output:
                json.dump(
                    document,
                    output,
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                    allow_nan=False,
                )
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())

            with open(temp_path, "r", encoding="utf-8") as check:
                parsed = json.load(check)
            if parsed != document or not verify_payload_integrity(parsed):
                raise ValueError("temporary JSON verification failed")

            if not overwrite and target.exists():
                return _failure(target, "immutable target already exists")
            os.replace(temp_path, target)
            temp_path = None
            _fsync_directory(target.parent)
            digest, size = _file_digest(target)
            return StoreResult(
                ok=True,
                path=str(target),
                sha256=digest,
                size=size,
                value=document,
            )
    except Exception as exc:
        return _failure(target, exc)
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass


def _safe_component(value: Any, label: str) -> str:
    cleaned = _SAFE_NAME_RE.sub("_", str(value)).strip("._-")
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError(label + " is not a safe file component")
    return cleaned


def _read_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as source:
        value = json.load(source)
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object: " + str(path))
    return value


def _idempotent_document_write(path: Path, payload: Mapping[str, Any]) -> StoreResult:
    if path.exists():
        try:
            existing = _read_json(path)
            if not verify_payload_integrity(existing):
                return _failure(path, "existing document failed payload checksum")
            requested = redact_secrets(payload)
            for key, expected in requested.items():
                if key in {"created_at", "integrity"}:
                    continue
                if existing.get(key) != expected:
                    return _failure(path, "existing immutable document conflicts at field: " + str(key))
            return StoreResult(
                ok=True,
                path=str(path),
                sha256=_file_digest(path)[0],
                size=path.stat().st_size,
                value=existing,
                warnings=("document already existed",),
            )
        except Exception as exc:
            return _failure(path, exc)
    written = atomic_write_json(path, payload, overwrite=False)
    if not written.ok and written.error == "immutable target already exists":
        # Another process completed the same immutable bootstrap while this
        # process waited on the target lock. Validate that winner as idempotent.
        return _idempotent_document_write(path, payload)
    return written


def write_model(
    model: Mapping[str, Any],
    root: Optional[Union[str, os.PathLike]] = None,
    *,
    overwrite: bool = False,
) -> StoreResult:
    try:
        version = _safe_component(model.get("model_version"), "model_version")
        target = store_root(root) / "models" / (version + ".json")
        if not overwrite:
            return _idempotent_document_write(target, model)
        return atomic_write_json(target, model, overwrite=True)
    except Exception as exc:
        return _failure(None, exc)


def initialize_store(
    root: Optional[Union[str, os.PathLike]] = None,
    model: Optional[Mapping[str, Any]] = None,
) -> StoreResult:
    """Create/validate schema.json and one immutable model definition."""

    base = store_root(root)
    try:
        schema_payload = dict(SCHEMA_DOCUMENT)
        schema_payload["record_type"] = "schema_definition"
        schema = _idempotent_document_write(base / "schema.json", schema_payload)
        if not schema.ok:
            return schema
        model_result = write_model(model or DEFAULT_MODEL, base)
        if not model_result.ok:
            return model_result
        return StoreResult(
            ok=True,
            path=str(base),
            value={"schema": schema.path, "model": model_result.path},
            warnings=schema.warnings + model_result.warnings,
        )
    except Exception as exc:
        return _failure(base, exc)


def _extract_trade_date(payload: Mapping[str, Any], explicit: Any = None) -> str:
    if explicit is not None:
        return normalize_trade_date(explicit)
    candidates = [payload.get("trade_date"), payload.get("signal_date")]
    run = payload.get("run")
    if isinstance(run, Mapping):
        candidates.extend([run.get("trade_date"), run.get("generated_at"), run.get("started_at")])
    candidates.extend([payload.get("generated_at"), payload.get("created_at")])
    for candidate in candidates:
        if candidate is None:
            continue
        digits = re.sub(r"[^0-9]", "", str(candidate))
        if len(digits) >= 8:
            try:
                return normalize_trade_date(digits[:8])
            except ValueError:
                continue
    return normalize_trade_date()


def _extract_hhmmss(value: Any = None) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=KST)
        return value.astimezone(KST).strftime("%H%M%S")
    if value is not None:
        text = str(value)
        match = re.search(r"(?:T|\s)(\d{2}):?(\d{2}):?(\d{2})", text)
        if match:
            return "".join(match.groups())
        digits = re.sub(r"[^0-9]", "", text)
        if len(digits) == 6:
            return digits
        if len(digits) >= 14:
            return digits[8:14]
    return _now().strftime("%H%M%S")


def _result_with_manifest(
    result: StoreResult,
    trade_date: str,
    root: Optional[Union[str, os.PathLike]],
) -> StoreResult:
    if not result.ok:
        return result
    manifest = rebuild_manifest(trade_date, root)
    if manifest.ok:
        return result
    warning = "manifest update failed: " + str(manifest.error)
    _LOG.warning("%s (%s)", warning, result.path)
    return replace(result, warnings=result.warnings + (warning,))


def write_scan(
    payload: Mapping[str, Any],
    trade_date: Any = None,
    observed_at: Any = None,
    run_id: Optional[str] = None,
    root: Optional[Union[str, os.PathLike]] = None,
    *,
    update_manifest: bool = True,
) -> StoreResult:
    """Persist one immutable scan using a collision-safe unique filename."""

    try:
        initialized = initialize_store(root)
        if not initialized.ok:
            return _failure(store_root(root), "store initialization failed: " + str(initialized.error))
        day = _extract_trade_date(payload, trade_date)
        document = dict(payload)
        document.setdefault("schema_version", SCHEMA_VERSION)
        document.setdefault("record_type", "scan_run")
        document.setdefault("created_at", _timestamp())
        run = document.get("run")
        run = dict(run) if isinstance(run, Mapping) else {}
        actual_run_id = run_id or run.get("run_id") or uuid.uuid4().hex
        run["run_id"] = str(actual_run_id)
        run.setdefault("trade_date", day)
        document["run"] = run
        observed = observed_at or run.get("completed_at") or run.get("generated_at") or run.get("started_at")
        hhmmss = _extract_hhmmss(observed)
        stem_id = _safe_component(str(actual_run_id)[:16], "run_id")
        scans = local_day_dir(day, root) / "scans"
        scans.mkdir(parents=True, exist_ok=True)
        target = scans / ("scan_" + hhmmss + "_" + stem_id + ".json")
        result = atomic_write_json(target, document, overwrite=False)
        attempts = 0
        while (
            not result.ok
            and result.error == "immutable target already exists"
            and attempts < 10
        ):
            attempts += 1
            target = scans / (
                "scan_" + hhmmss + "_" + stem_id + "_" + uuid.uuid4().hex[:8] + ".json"
            )
            result = atomic_write_json(target, document, overwrite=False)
        return _result_with_manifest(result, day, root) if update_manifest else result
    except Exception as exc:
        return _failure(None, exc)


def _bar_time(bar: Mapping[str, Any]) -> str:
    value = bar.get("time")
    if value is None:
        value = bar.get("datetime") or bar.get("timestamp")
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    if len(digits) >= 14:
        return digits[-6:]
    if len(digits) >= 6:
        return digits[:6]
    if len(digits) == 4:
        return digits + "00"
    raise ValueError("minute bar requires HHMMSS time")


def _merge_one_bar(previous: Optional[Mapping[str, Any]], later: Mapping[str, Any]) -> Dict[str, Any]:
    merged = dict(previous or {})
    for key, value in later.items():
        if value is not None:
            merged[str(key)] = redact_secrets(value)
    merged["time"] = _bar_time(later)
    return merged


def _minute_conflict_fields(
    previous: Mapping[str, Any], later: Mapping[str, Any]
) -> List[str]:
    """Return canonical OHLCV fields that disagree when both sides provide them.

    ``vol`` is the canonical minute-volume key, while old audit files may use
    ``volume``.  Missing fields are fillable and therefore are not conflicts.
    Python numeric equality intentionally treats ``1000`` and ``1000.0`` as
    equal.
    """

    conflicts: List[str] = []
    for key in ("open", "high", "low", "close"):
        left, right = previous.get(key), later.get(key)
        if left is not None and right is not None and left != right:
            conflicts.append(key)

    left_vol = previous.get("vol")
    if left_vol is None:
        left_vol = previous.get("volume")
    right_vol = later.get("vol")
    if right_vol is None:
        right_vol = later.get("volume")
    if left_vol is not None and right_vol is not None and left_vol != right_vol:
        conflicts.append("vol")
    return conflicts


def merge_minute_bars(
    trade_date: Any,
    code: str,
    bars: Optional[Sequence[Mapping[str, Any]]],
    *,
    market_basis: str = "KRX",
    source_broker: Optional[str] = None,
    fetched_at: Any = None,
    fetch_status: str = "ok",
    error: Optional[str] = None,
    root: Optional[Union[str, os.PathLike]] = None,
    update_manifest: bool = True,
    conflict_policy: str = "merge",
) -> StoreResult:
    """Atomically merge one symbol/day minute series, deduplicated by time.

    ``conflict_policy="merge"`` preserves the historical fieldwise-later-wins
    behavior. ``"error"`` compares canonical OHLCV under the same per-file
    merge lock and refuses the entire write when a common timestamp differs.
    """

    target: Optional[Path] = None
    try:
        policy = str(conflict_policy or "merge").lower()
        if policy not in {"merge", "error"}:
            raise ValueError("conflict_policy must be merge or error")
        day = normalize_trade_date(trade_date)
        safe_code = _safe_component(code, "code")
        market = _safe_component(str(market_basis).upper(), "market_basis")
        target = local_day_dir(day, root) / "minute" / (safe_code + "_" + market + ".json")
        fetched_text = _timestamp(fetched_at if isinstance(fetched_at, datetime) else None)
        if fetched_at is not None and not isinstance(fetched_at, datetime):
            fetched_text = str(fetched_at)
        incoming = list(bars or [])
        status = str(fetch_status or "ok").lower()
        if status == "ok" and not incoming:
            status = "empty"
        if status not in {"ok", "empty", "error"}:
            raise ValueError("fetch_status must be ok, empty, or error")
        merge_lock = target.parent / ("." + target.name + ".merge.lock")
        with _exclusive_lock(merge_lock):
            if target.exists():
                existing = _read_json(target)
                if not verify_payload_integrity(existing):
                    raise ValueError("existing minute file failed payload checksum")
            else:
                existing = {
                    "schema_version": SCHEMA_VERSION,
                    "record_type": "minute_bars",
                    "trade_date": day,
                    "code": str(code),
                    "market_basis": market,
                    "source_broker": source_broker,
                    "fetched_first_at": fetched_text,
                    "bars": [],
                    "fetch_events": [],
                }

            indexed: Dict[str, Dict[str, Any]] = {}
            for bar in existing.get("bars") or []:
                if isinstance(bar, Mapping):
                    normalized = _merge_one_bar(None, bar)
                    if policy == "error":
                        if normalized.get("vol") is None and normalized.get("volume") is not None:
                            normalized["vol"] = normalized["volume"]
                        normalized.pop("volume", None)
                    indexed[_bar_time(bar)] = normalized
            for bar in incoming:
                if not isinstance(bar, Mapping):
                    raise TypeError("each minute bar must be an object")
                key = _bar_time(bar)
                previous = indexed.get(key)
                if policy == "error" and previous is not None:
                    conflicts = _minute_conflict_fields(previous, bar)
                    if conflicts:
                        raise ValueError(
                            "minute bar conflict at "
                            + key
                            + " fields="
                            + ",".join(conflicts)
                        )
                normalized = _merge_one_bar(indexed.get(key), bar)
                if policy == "error":
                    if normalized.get("vol") is None and normalized.get("volume") is not None:
                        normalized["vol"] = normalized["volume"]
                    normalized.pop("volume", None)
                indexed[key] = normalized

            events = list(existing.get("fetch_events") or [])
            events.append(
                {
                    "fetched_at": fetched_text,
                    "status": status,
                    "bar_count": len(incoming),
                    "error": redact_secrets(error) if error else None,
                }
            )
            document = dict(existing)
            document.pop("integrity", None)
            document["schema_version"] = SCHEMA_VERSION
            document["record_type"] = "minute_bars"
            document["trade_date"] = day
            document["code"] = str(code)
            document["market_basis"] = market
            if source_broker is not None:
                document["source_broker"] = source_broker
            document.setdefault("fetched_first_at", fetched_text)
            document["fetched_last_at"] = fetched_text
            document["fetch_status"] = status
            document["fetch_events"] = events
            document["bars"] = [indexed[key] for key in sorted(indexed)]
            result = atomic_write_json(target, document, overwrite=True)

        if result.ok:
            result = replace(
                result,
                value={
                    "bar_count": len(document["bars"]),
                    "incoming_count": len(incoming),
                    "fetch_status": status,
                },
            )
        return _result_with_manifest(result, day, root) if update_manifest else result
    except Exception as exc:
        return _failure(target, exc)


def write_decision_snapshot(
    slot: str,
    payload: Mapping[str, Any],
    *,
    trade_date: Any = None,
    root: Optional[Union[str, os.PathLike]] = None,
    overwrite: bool = False,
    update_manifest: bool = True,
) -> StoreResult:
    """Write a decision slot. First successful snapshot wins by default.

    update_manifest=False면 당일 manifest 전체 재빌드(하루 말 실측 ~1.6초)를 생략한다 —
    주문 경로/회차당 다중 저장 시 호출자가 마지막에 rebuild_manifest 1회로 몰아준다(적대 리뷰 M3).
    """

    target: Optional[Path] = None
    try:
        day = _extract_trade_date(payload, trade_date)
        safe_slot = _safe_component(str(slot).lower(), "slot")
        target = local_day_dir(day, root) / "decisions" / (safe_slot + ".json")
        document = dict(payload)
        document.setdefault("schema_version", SCHEMA_VERSION)
        document.setdefault("record_type", "decision_snapshot")
        document.setdefault("trade_date", day)
        document.setdefault("slot", str(slot).upper())
        document.setdefault("created_at", _timestamp())
        result = atomic_write_json(target, document, overwrite=overwrite)
        return _result_with_manifest(result, day, root) if update_manifest else result
    except Exception as exc:
        return _failure(target, exc)


def write_evaluation(
    payload: Mapping[str, Any],
    *,
    trade_date: Any = None,
    root: Optional[Union[str, os.PathLike]] = None,
) -> StoreResult:
    target: Optional[Path] = None
    try:
        day = _extract_trade_date(payload, trade_date)
        target = local_day_dir(day, root) / "evaluation" / "next_day.json"
        document = dict(payload)
        document.setdefault("schema_version", SCHEMA_VERSION)
        document.setdefault("record_type", "next_day_evaluation")
        document.setdefault("signal_date", day)
        document.setdefault("created_at", _timestamp())
        result = atomic_write_json(target, document, overwrite=True)
        return _result_with_manifest(result, day, root)
    except Exception as exc:
        return _failure(target, exc)


def write_daily_summary(
    payload: Mapping[str, Any],
    *,
    trade_date: Any = None,
    root: Optional[Union[str, os.PathLike]] = None,
) -> StoreResult:
    target: Optional[Path] = None
    try:
        day = _extract_trade_date(payload, trade_date)
        target = local_day_dir(day, root) / "summaries" / "daily_compact.json"
        document = dict(payload)
        document.setdefault("schema_version", SCHEMA_VERSION)
        document.setdefault("record_type", "daily_compact")
        document.setdefault("trade_date", day)
        result = atomic_write_json(target, document, overwrite=True)
        return _result_with_manifest(result, day, root)
    except Exception as exc:
        return _failure(target, exc)


def _inspect_jsonl(raw: bytes) -> Dict[str, Any]:
    records: List[Any] = []
    corruptions: List[Dict[str, Any]] = []
    offset = 0
    valid_prefix_end = 0
    lines = raw.splitlines(keepends=True)
    for index, line in enumerate(lines, 1):
        content = line.rstrip(b"\r\n")
        line_end = offset + len(line)
        if not content.strip():
            valid_prefix_end = line_end
            offset = line_end
            continue
        try:
            records.append(json.loads(content.decode("utf-8")))
            valid_prefix_end = line_end
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            trailing = all(not rest.rstrip(b"\r\n").strip() for rest in lines[index:])
            corruptions.append(
                {
                    "line": index,
                    "offset": offset,
                    "length": len(line),
                    "sha256": hashlib.sha256(line).hexdigest(),
                    "trailing": trailing,
                    "error": str(exc),
                }
            )
            if not trailing:
                valid_prefix_end = offset
            break
        offset = line_end
    return {
        "records": records,
        "corruptions": corruptions,
        "valid_prefix_end": valid_prefix_end,
        "raw_ends_newline": raw.endswith((b"\n", b"\r")),
    }


def read_jsonl(path: Union[str, os.PathLike]) -> StoreResult:
    target: Optional[Path] = None
    try:
        target = Path(path)
        if not target.exists():
            return StoreResult(ok=True, path=str(target), value={"records": [], "corruptions": []})
        lock_path = target.parent / ("." + target.name + ".append.lock")
        with _exclusive_lock(lock_path):
            raw = target.read_bytes()
        inspection = _inspect_jsonl(raw)
        corruptions = inspection["corruptions"]
        non_trailing = [item for item in corruptions if not item["trailing"]]
        warnings: Tuple[str, ...] = ()
        if corruptions:
            warnings = ("JSONL contains a corrupt trailing line",)
        digest = hashlib.sha256(raw).hexdigest()
        result = StoreResult(
            ok=not non_trailing,
            path=str(target),
            error="JSONL contains a non-trailing corrupt line" if non_trailing else None,
            sha256=digest,
            size=len(raw),
            value={"records": inspection["records"], "corruptions": corruptions},
            warnings=warnings,
        )
        return result
    except Exception as exc:
        return _failure(target, exc)


def _append_bytes_fsynced(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short JSONL write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _record_corrupt_tail(path: Path, corruption: Mapping[str, Any]) -> Path:
    report_path = path.with_name(path.name + ".corruptions.jsonl")
    report = {
        "schema_version": SCHEMA_VERSION,
        "record_type": "jsonl_corruption",
        "detected_at": _timestamp(),
        "source_path": path.name,
        "line": corruption.get("line"),
        "offset": corruption.get("offset"),
        "byte_length": corruption.get("length"),
        "raw_sha256": corruption.get("sha256"),
        "reason": corruption.get("error"),
    }
    result = append_jsonl(report_path, report)
    if not result.ok:
        raise OSError("failed to record JSONL corruption: " + str(result.error))
    return report_path


def append_jsonl(path: Union[str, os.PathLike], event: Mapping[str, Any]) -> StoreResult:
    """Append a redacted event and recover one corrupt trailing fragment.

    A corrupt middle line is never rewritten automatically. A corrupt final
    fragment is reported by hash, removed from the active stream, and the new
    complete event is then appended.
    """

    target: Optional[Path] = None
    try:
        target = Path(path)
        document = dict(event)
        document.setdefault("schema_version", SCHEMA_VERSION)
        document.setdefault("created_at", _timestamp())
        prepared = _document_with_integrity(document)
        line = _canonical_bytes(prepared) + b"\n"
        lock_path = target.parent / ("." + target.name + ".append.lock")
        warnings: List[str] = []
        corruption_report: Optional[Path] = None
        with _exclusive_lock(lock_path):
            raw = target.read_bytes() if target.exists() else b""
            inspection = _inspect_jsonl(raw)
            corruptions = inspection["corruptions"]
            if corruptions:
                corrupt = corruptions[0]
                if not corrupt.get("trailing"):
                    return _failure(target, "refusing append after non-trailing JSONL corruption")
                corruption_report = _record_corrupt_tail(target, corrupt)
                valid = raw[: inspection["valid_prefix_end"]]
                _atomic_replace_bytes(target, valid)
                warnings.append("corrupt trailing JSONL line was reported and removed")
                raw = valid
            prefix = b"" if not raw or raw.endswith((b"\n", b"\r")) else b"\n"
            _append_bytes_fsynced(target, prefix + line)
            _fsync_directory(target.parent)
        digest, size = _file_digest(target)
        return StoreResult(
            ok=True,
            path=str(target),
            sha256=digest,
            size=size,
            value={"event": prepared, "corruption_report": str(corruption_report) if corruption_report else None},
            warnings=tuple(warnings),
        )
    except Exception as exc:
        return _failure(target, exc)


def append_trade_event(
    stream: str,
    event: Mapping[str, Any],
    *,
    trade_date: Any = None,
    root: Optional[Union[str, os.PathLike]] = None,
    update_manifest: bool = True,
) -> StoreResult:
    target: Optional[Path] = None
    try:
        day = _extract_trade_date(event, trade_date)
        safe_stream = _safe_component(str(stream).lower(), "stream")
        target = local_day_dir(day, root) / "trades" / (safe_stream + ".jsonl")
        document = dict(event)
        document.setdefault("record_type", "trade_" + safe_stream.rstrip("s") + "_event")
        document.setdefault("trade_date", day)
        result = append_jsonl(target, document)
        # update_manifest=False: 주문 직전 경로에서 당일 전체 재빌드 지연(실측 회당 ~1.6초) 제거.
        # 색인은 다음 publish 회차의 rebuild가 수행한다(적대 리뷰 M3).
        return _result_with_manifest(result, day, root) if update_manifest else result
    except Exception as exc:
        return _failure(target, exc)


def _created_at_from_stat(path: Path) -> str:
    return _timestamp(datetime.fromtimestamp(path.stat().st_mtime, tz=KST))


def _manifest_file_entry(path: Path, day_dir: Path) -> Tuple[Dict[str, Any], List[Dict[str, Any]], int]:
    digest, size = _file_digest(path)
    relative = path.relative_to(day_dir).as_posix()
    entry: Dict[str, Any] = {
        "path": relative,
        "record_type": "unknown",
        "size": size,
        "sha256": digest,
        "created_at": _created_at_from_stat(path),
    }
    errors: List[Dict[str, Any]] = []
    observations = 0
    if path.suffix == ".json":
        try:
            document = _read_json(path)
            entry["record_type"] = document.get("record_type", "unknown")
            entry["created_at"] = document.get("created_at", entry["created_at"])
            if not verify_payload_integrity(document):
                errors.append({"path": relative, "code": "PAYLOAD_CHECKSUM_MISMATCH"})
            values = document.get("observations")
            observations = len(values) if isinstance(values, list) else 0
            if document.get("record_type") == "minute_bars":
                for fetch in document.get("fetch_events") or []:
                    if isinstance(fetch, Mapping) and fetch.get("status") in {"empty", "error"}:
                        errors.append(
                            {
                                "path": relative,
                                "code": "MINUTE_FETCH_" + str(fetch.get("status")).upper(),
                                "fetched_at": fetch.get("fetched_at"),
                                "error": fetch.get("error"),
                            }
                        )
        except Exception as exc:
            entry["record_type"] = "invalid_json"
            errors.append({"path": relative, "code": "INVALID_JSON", "error": str(exc)})
    elif path.suffix == ".jsonl":
        inspected = read_jsonl(path)
        records = (inspected.value or {}).get("records", []) if isinstance(inspected.value, dict) else []
        if records and isinstance(records[0], Mapping):
            entry["record_type"] = records[0].get("record_type", "jsonl")
            entry["created_at"] = records[0].get("created_at", entry["created_at"])
        else:
            entry["record_type"] = "jsonl"
        for corrupt in (inspected.value or {}).get("corruptions", []):
            errors.append({"path": relative, "code": "JSONL_CORRUPTION", **corrupt})
        for index, record in enumerate(records, 1):
            if not verify_payload_integrity(record):
                errors.append(
                    {"path": relative, "code": "PAYLOAD_CHECKSUM_MISMATCH", "line": index}
                )
    return entry, errors, observations


def _day_data_files(day_dir: Path, manifest_path: Path) -> List[Path]:
    return sorted(
        path
        for path in day_dir.rglob("*")
        if path.is_file()
        and path != manifest_path
        and not path.name.startswith(".")
        and path.suffix in {".json", ".jsonl"}
    )


def rebuild_manifest(
    trade_date: Any,
    root: Optional[Union[str, os.PathLike]] = None,
) -> StoreResult:
    """Rebuild a daily manifest exclusively from authoritative day files."""

    target: Optional[Path] = None
    try:
        day = normalize_trade_date(trade_date)
        day_dir = local_day_dir(day, root)
        day_dir.mkdir(parents=True, exist_ok=True)
        target = day_dir / "manifest.json"
        build_lock = day_dir / ".manifest.rebuild.lock"
        with _exclusive_lock(build_lock):
            files: List[Dict[str, Any]] = []
            errors: List[Dict[str, Any]] = []
            observation_count = 0
            candidates = _day_data_files(day_dir, target)
            for path in candidates:
                entry, file_errors, observations = _manifest_file_entry(path, day_dir)
                files.append(entry)
                errors.extend(file_errors)
                observation_count += observations
            record_types = [item.get("record_type") for item in files]
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "record_type": "daily_manifest",
                "trade_date": day,
                "scope": "observed_union_not_full_market",
                "created_at": _timestamp(),
                "files": files,
                "counts": {
                    "scan_runs": record_types.count("scan_run"),
                    "observations": observation_count,
                    "minute_files": record_types.count("minute_bars"),
                    "decision_files": record_types.count("decision_snapshot"),
                    "evaluation_files": record_types.count("next_day_evaluation"),
                    "jsonl_files": sum(1 for item in files if str(item.get("path", "")).endswith(".jsonl")),
                },
                "errors": errors,
            }
            return atomic_write_json(target, manifest, overwrite=True)
    except Exception as exc:
        return _failure(target, exc)


def verify_manifest(
    trade_date: Any,
    root: Optional[Union[str, os.PathLike]] = None,
) -> StoreResult:
    """Compare current files to the last manifest without changing either."""

    target: Optional[Path] = None
    try:
        day = normalize_trade_date(trade_date)
        day_dir = local_day_dir(day, root)
        target = day_dir / "manifest.json"
        manifest = _read_json(target)
        issues: List[Dict[str, Any]] = []
        if not verify_payload_integrity(manifest):
            issues.append({"path": "manifest.json", "code": "PAYLOAD_CHECKSUM_MISMATCH"})
        declared_paths: set = set()
        for entry in manifest.get("files") or []:
            if not isinstance(entry, Mapping):
                issues.append({"code": "INVALID_MANIFEST_ENTRY"})
                continue
            relative = str(entry.get("path") or "")
            if relative in declared_paths:
                issues.append({"path": relative, "code": "DUPLICATE_MANIFEST_ENTRY"})
            declared_paths.add(relative)
            candidate = day_dir / relative
            try:
                candidate.resolve().relative_to(day_dir.resolve())
            except ValueError:
                issues.append({"path": relative, "code": "PATH_ESCAPE"})
                continue
            if not candidate.is_file():
                issues.append({"path": relative, "code": "MISSING_FILE"})
                continue
            digest, size = _file_digest(candidate)
            if digest != entry.get("sha256"):
                issues.append({"path": relative, "code": "FILE_CHECKSUM_MISMATCH"})
            if size != entry.get("size"):
                issues.append({"path": relative, "code": "FILE_SIZE_MISMATCH"})
            if candidate.suffix == ".json":
                try:
                    if not verify_payload_integrity(_read_json(candidate)):
                        issues.append({"path": relative, "code": "PAYLOAD_CHECKSUM_MISMATCH"})
                except Exception as exc:
                    issues.append({"path": relative, "code": "INVALID_JSON", "error": str(exc)})
            elif candidate.suffix == ".jsonl":
                inspected = read_jsonl(candidate)
                records = (
                    (inspected.value or {}).get("records", [])
                    if isinstance(inspected.value, dict)
                    else []
                )
                for index, record in enumerate(records, 1):
                    if not verify_payload_integrity(record):
                        issues.append(
                            {
                                "path": relative,
                                "code": "PAYLOAD_CHECKSUM_MISMATCH",
                                "line": index,
                            }
                        )
                for corruption in (
                    (inspected.value or {}).get("corruptions", [])
                    if isinstance(inspected.value, dict)
                    else []
                ):
                    issues.append(
                        {"path": relative, "code": "JSONL_CORRUPTION", **corruption}
                    )
        actual_paths = {
            path.relative_to(day_dir).as_posix() for path in _day_data_files(day_dir, target)
        }
        for relative in sorted(actual_paths - declared_paths):
            issues.append({"path": relative, "code": "UNMANIFESTED_FILE"})
        return StoreResult(
            ok=not issues,
            path=str(target),
            error="manifest verification failed" if issues else None,
            sha256=_file_digest(target)[0],
            size=target.stat().st_size,
            value={"issues": issues, "file_count": len(manifest.get("files") or [])},
        )
    except Exception as exc:
        return _failure(target, exc)


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_ROOT",
    "ROOT_ENV",
    "SCHEMA_DOCUMENT",
    "SCHEMA_VERSION",
    "StoreResult",
    "append_jsonl",
    "append_trade_event",
    "atomic_write_json",
    "initialize_store",
    "local_day_dir",
    "merge_minute_bars",
    "normalize_trade_date",
    "read_jsonl",
    "rebuild_manifest",
    "redact_secrets",
    "store_root",
    "verify_manifest",
    "verify_payload_integrity",
    "write_daily_summary",
    "write_decision_snapshot",
    "write_evaluation",
    "write_model",
    "write_scan",
]
