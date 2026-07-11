#!/usr/bin/env python3
"""매수 pending 주문 재조정. 확인된 체결분만 포지션으로 전환한다."""
import copy
from datetime import datetime, timedelta

import autotrade_common as ac
import kiwoom_trade as kt


TERMINAL_STATES = {"FILLED", "CANCELLED", "REJECTED"}
MANUAL_REVIEW_STATES = {
    "PREPARED", "SUBMIT_UNKNOWN", "CANCEL_UNKNOWN", "STATUS_INCONSISTENT",
}
PENDING_ESCALATE_AFTER = timedelta(hours=2)
ALERT_RETRY_AFTER = timedelta(hours=1)


def _position_for_intent(data, intent_id):
    return next((p for p in data.get("positions", [])
                 if p.get("entry_intent_id") == intent_id), None)


def _now():
    return datetime.now(ac.KST)


def _parse_kst(value):
    raw = str(value or "").strip().replace(" KST", "")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=ac.KST)
        except ValueError:
            pass
    return None


def _safe_int(value, default=0):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _quarantine_pending_row(data, index, now):
    row = data["pending_entries"][index]
    if isinstance(row, dict):
        return row, False
    quarantined = {
        "intent_id": f"INVALID-PENDING-{now.strftime('%Y%m%d')}-{index}",
        "state": "INVALID_PENDING_ROW",
        "manual_review_status": "MANUAL_REVIEW_REQUIRED",
        "manual_review_reason": "pending_row_not_object",
        "invalid_value_type": type(row).__name__,
        "invalid_value_repr": repr(row)[:300],
        "quarantined_at": now.strftime("%Y-%m-%d %H:%M:%S KST"),
        "ord_no": None,
    }
    data["pending_entries"][index] = quarantined
    return quarantined, True


def _attention_reason(pending, now):
    today = now.strftime("%Y%m%d")
    order_date = str(pending.get("order_date") or pending.get("entry_date") or "")
    created = _parse_kst(pending.get("created_at"))
    if order_date and order_date < today:
        return "order_date_passed"
    if created and now - created >= PENDING_ESCALATE_AFTER:
        return "age_limit_exceeded"
    if pending.get("manual_review_status") == "MANUAL_REVIEW_REQUIRED":
        return "reconcile_row_failure"
    if not pending.get("ord_no") or pending.get("state") in MANUAL_REVIEW_STATES:
        return f"state:{pending.get('state') or 'UNKNOWN'}"
    if _safe_int(pending.get("reconcile_error_count")) >= 3:
        return "reconcile_repeated_failure"
    return None


def review_pending_attention(data, persist=None, now=None):
    """미해소 매수 pending을 자동 해제하지 않고 수동검토로 승격·일 1회 재알림한다.

    이 함수는 브로커 주문/취소 API를 호출하지 않는다. 웹 토글·장 세션과 무관하게
    호출할 수 있으며, 알림 실패 때는 한 시간 뒤 재시도한다.
    """
    now = now or _now()
    persist = persist or (lambda: ac.save_positions(data))
    due = []
    changed = False
    rows = data.get("pending_entries") or []
    for index in range(len(rows)):
        pending, quarantined = _quarantine_pending_row(data, index, now)
        changed = changed or quarantined
        try:
            reason = _attention_reason(pending, now)
        except Exception as exc:
            ac.log(f"[entry] pending 경고 판정 오류 격리({pending.get('intent_id')}): {exc}")
            reason = "attention_parse_error"
        if not reason:
            continue
        stale = reason in ("order_date_passed", "age_limit_exceeded")
        target_status = "EXPIRED_SUSPECTED" if stale else "MANUAL_REVIEW_REQUIRED"
        if pending.get("manual_review_status") != target_status:
            pending.setdefault("escalated_from", pending.get("state"))
            pending["manual_review_status"] = target_status
            pending["manual_review_reason"] = reason
            pending["escalated_at"] = now.strftime("%Y-%m-%d %H:%M:%S KST")
            changed = True
        if pending.get("last_alert_success_date") == now.strftime("%Y%m%d"):
            continue
        last_attempt = _parse_kst(pending.get("last_alert_attempt_at"))
        if last_attempt:
            elapsed = now - last_attempt
            if timedelta(0) <= elapsed < ALERT_RETRY_AFTER:
                continue
        pending["last_alert_attempt_at"] = now.strftime("%Y-%m-%d %H:%M:%S KST")
        due.append(pending)
        changed = True

    if due:
        rows = "\n".join(
            f"- {p.get('name','')}({p.get('code')}) intent={p.get('intent_id')} "
            f"state={p.get('state')} review={p.get('manual_review_status')}"
            for p in due)
        sent = ac.notify_trade(
            "🚨 [자동매매] 미해소 매수 pending 수동 확인 필요\n"
            f"{rows}\n"
            "웹 신규매수를 OFF로 두고 HTS 주문·체결·잔고를 대조한 뒤 "
            "scripts/autotrade_pending_admin.py로만 해소하세요. 자동 삭제하지 않습니다.")
        if sent:
            for pending in due:
                pending["last_alert_success_at"] = now.strftime("%Y-%m-%d %H:%M:%S KST")
                pending["last_alert_success_date"] = now.strftime("%Y%m%d")
                pending["alert_count"] = _safe_int(pending.get("alert_count")) + 1
            changed = True
    if changed:
        try:
            persist()
        except Exception as exc:
            ac.log(f"[entry] pending 경고상태 저장 실패(청산은 계속): {exc}")
    return len(due)


def _apply_fill(data, pending, status, newly_filled):
    intent_id = pending["intent_id"]
    pos = _position_for_intent(data, intent_id)
    cumulative = int(status.get("filled_qty") or 0)
    avg = float(status.get("avg_fill_price") or 0)
    verified = avg > 0
    if pos is not None and pos.get("status") != "open":
        raise RuntimeError(
            f"{pending.get('code')} 종료 포지션에 추가 매수체결 {newly_filled}주 — 수동 대조 필요")
    if pos is None:
        pos = {
            "id": intent_id, "entry_intent_id": intent_id,
            "code": pending["code"], "name": pending.get("name", ""),
            "entry_date": pending["entry_date"],
            "entry_price": avg if verified else float(pending.get("ref_price") or 0),
            "entry_price_verified": verified,
            "entry_ord_no": pending.get("ord_no"),
            "entry_order_date": pending.get("order_date"),
            "manual_baseline_qty": pending.get("manual_baseline_qty"),
            "qty": 0, "qty_open": 0, "market": pending["market"],
            "alloc_krw": pending.get("alloc_krw"), "rank": pending.get("rank"),
            "tp1_done": False, "status": "open",
            "opened_at": datetime.now(ac.KST).strftime("%Y-%m-%d %H:%M:%S"),
            "pattern": pending.get("pattern"),
            "suspicion_score": pending.get("suspicion_score"),
            **dict(pending.get("audit") or {}),
        }
        data["positions"].append(pos)
    pos["qty"] = cumulative
    pos["qty_open"] = int(pos.get("qty_open") or 0) + newly_filled
    if verified:
        pos["entry_price"] = avg
        pos["entry_price_verified"] = True
    ac.append_trade_event({
        "type": "entry_fill", "id": pos["id"], "code": pos["code"],
        "name": pos.get("name"), "entry_date": pos["entry_date"],
        "market": pos["market"], "filled_qty": newly_filled,
        "cumulative_filled_qty": cumulative, "entry_price": pos["entry_price"],
        "entry_price_verified": bool(pos.get("entry_price_verified")),
        "ord_no": pending.get("ord_no"), "dry": False,
    })
    if not pos.get("entry_price_verified"):
        ac.notify_trade(
            f"🚨 [자동매매] {pos.get('name','')}({pos['code']}) 매수 체결가는 미검증\n"
            "수량은 원장에 반영했지만 자동청산을 보류합니다. HTS 평균단가를 확인하세요.")


def _restore(data, snapshot):
    data.clear()
    data.update(copy.deepcopy(snapshot))


def _reconcile_one(data, pending, checkpoint, dry, now):
    state = pending.get("state")
    if state in TERMINAL_STATES:
        data["pending_entries"].remove(pending)
        checkpoint()
        return False, True
    ord_no = pending.get("ord_no")
    if not ord_no:
        ac.log(f"[entry] {pending.get('code')} {state} 주문번호 없음 — 신규 매수 차단 유지")
        return True, False

    status = kt.order_status(
        pending["code"], ord_no, market=pending["market"],
        order_date=pending.get("order_date") or "", side="buy")
    if status is None:
        pending["reconcile_error_count"] = _safe_int(pending.get("reconcile_error_count")) + 1
        pending["last_error"] = "order_status_not_found"
        pending["last_checked_at"] = now.strftime("%Y-%m-%d %H:%M:%S KST")
        checkpoint()
        return True, False

    requested = int(pending.get("requested_qty") or 0)
    ordered = int(status.get("ordered_qty") or 0)
    filled = int(status.get("filled_qty") or 0)
    remaining = int(status.get("remaining_qty") or 0)
    accounted = int(pending.get("accounted_filled") or 0)
    if (requested <= 0 or ordered != requested or filled < accounted
            or filled > ordered or remaining > ordered or filled + remaining > ordered):
        pending["state"] = "STATUS_INCONSISTENT"
        pending["last_status"] = status
        pending["reconcile_error_count"] = _safe_int(pending.get("reconcile_error_count")) + 1
        pending["last_checked_at"] = now.strftime("%Y-%m-%d %H:%M:%S KST")
        checkpoint()
        return True, False

    newly_filled = filled - accounted
    if newly_filled:
        _apply_fill(data, pending, status, newly_filled)
        pending["accounted_filled"] = filled
        pending["state"] = "FILLED" if filled == ordered and remaining == 0 else "PARTIAL"
    pending["last_status"] = status
    pending["last_checked_at"] = now.strftime("%Y-%m-%d %H:%M:%S KST")
    pending["reconcile_error_count"] = 0
    # 확인된 체결분을 먼저 영속화한 뒤에만 외부 취소를 호출한다.
    checkpoint()
    if remaining > 0:
        if not pending.get("cancel_requested"):
            try:
                result = kt.cancel_order(pending["code"], ord_no,
                                         market=pending["market"], qty=0, dry=dry)
                pending["cancel_requested"] = not result.get("dry")
                pending["state"] = "CANCEL_REQUESTED" if not dry else "CANCEL_DRY_BLOCKED"
            except Exception as exc:
                pending["state"] = "CANCEL_UNKNOWN"
                pending["last_error"] = str(exc)[:500]
                pending["reconcile_error_count"] = _safe_int(
                    pending.get("reconcile_error_count")) + 1
            checkpoint()
        return True, False

    pending["state"] = "FILLED" if filled else "CANCELLED"
    data["pending_entries"].remove(pending)
    checkpoint()
    return False, True


def reconcile_pending_entries(data, persist=None, dry=False):
    """매수 pending을 행별 트랜잭션으로 재조정한다.

    한 행의 변환·저장 오류는 해당 행만 원복·격리하며 다른 pending과 포지션의
    위험 청산을 막지 않는다. 반환값은 미해소 행 존재 여부다.
    """
    persist = persist or (lambda: ac.save_positions(data))
    unresolved = False
    index = 0
    now = _now()
    while index < len(data.get("pending_entries") or []):
        durable = copy.deepcopy(data)

        def checkpoint():
            nonlocal durable
            persist()
            durable = copy.deepcopy(data)

        pending, quarantined = _quarantine_pending_row(data, index, now)
        if quarantined:
            try:
                persist()
            except Exception as exc:
                ac.log(f"[entry] 비정상 pending 격리 저장 실패: {exc}")
            unresolved = True
            index += 1
            continue
        try:
            row_unresolved, removed = _reconcile_one(
                data, pending, checkpoint, dry=dry, now=now)
            unresolved = unresolved or row_unresolved
            if not removed:
                index += 1
        except Exception as exc:
            _restore(data, durable)
            if index >= len(data.get("pending_entries") or []):
                unresolved = True
                continue
            failed = data["pending_entries"][index]
            failed["last_error"] = str(exc)[:500]
            failed["last_checked_at"] = now.strftime("%Y-%m-%d %H:%M:%S KST")
            failed["reconcile_error_count"] = _safe_int(
                failed.get("reconcile_error_count")) + 1
            failed.setdefault("manual_review_status", "MANUAL_REVIEW_REQUIRED")
            ac.log(f"[entry] {failed.get('code')} pending 행 격리: {exc}")
            try:
                persist()
            except Exception as persist_exc:
                ac.log(f"[entry] 격리상태 저장 실패(다른 포지션 청산은 계속): {persist_exc}")
            unresolved = True
            index += 1

    review_pending_attention(data, persist=persist, now=now)
    return unresolved or bool(data.get("pending_entries"))
