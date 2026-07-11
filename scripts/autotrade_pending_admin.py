#!/usr/bin/env python3
"""HTS 대조 후 매수 pending을 안전하게 수동 해소하는 관리 도구(브로커 호출 없음)."""
import argparse
import json
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autotrade_common as ac


def _find(data, intent_id):
    rows = [row for row in data.get("pending_entries") or []
            if row.get("intent_id") == intent_id]
    if len(rows) != 1:
        raise ValueError(f"intent_id 일치 pending이 정확히 1건이어야 합니다: {intent_id} ({len(rows)}건)")
    return rows[0]


def _verify_fingerprint(row, *, code, order_date, requested_qty):
    expected = (str(row.get("code")), str(row.get("order_date")),
                int(row.get("requested_qty") or 0))
    supplied = (str(code), str(order_date), int(requested_qty))
    if expected != supplied:
        raise ValueError(f"pending fingerprint 불일치: expected={expected}, supplied={supplied}")


def attach_order_number(data, *, intent_id, code, order_date, requested_qty, order_no):
    row = _find(data, intent_id)
    _verify_fingerprint(row, code=code, order_date=order_date, requested_qty=requested_qty)
    if not str(order_no or "").strip():
        raise ValueError("order_no가 비어 있습니다")
    if row.get("ord_no") and str(row.get("ord_no")) != str(order_no).strip():
        raise ValueError(f"기존 주문번호 {row.get('ord_no')}를 다른 번호로 덮어쓸 수 없습니다")
    row["ord_no"] = str(order_no).strip()
    row["state"] = "ACCEPTED_MANUAL_LINK"
    row["manual_resolution"] = "order_number_attached_after_hts_check"
    row["manual_resolved_at"] = datetime.now(ac.KST).strftime("%Y-%m-%d %H:%M:%S KST")
    return row


def resolve_confirmed_no_order(data, *, intent_id, code, order_date, requested_qty):
    row = _find(data, intent_id)
    _verify_fingerprint(row, code=code, order_date=order_date, requested_qty=requested_qty)
    if int(row.get("accounted_filled") or 0) != 0:
        raise ValueError("확인 체결수량이 0이 아니므로 no-order/no-fill 해소를 거부합니다")
    if row.get("ord_no"):
        raise ValueError("주문번호가 연결된 pending은 no-order/no-fill로 해소할 수 없습니다")
    tombstone = dict(row)
    tombstone.update({
        "resolution": "confirmed_no_order_no_fill",
        "resolved_at": datetime.now(ac.KST).strftime("%Y-%m-%d %H:%M:%S KST"),
    })
    data["pending_entries"].remove(row)
    data.setdefault("resolved_pending_entries", []).append(tombstone)
    return tombstone


def _require_web_off():
    if os.environ.get("AUTOTRADE_FORCE_ON") == "1":
        raise RuntimeError("AUTOTRADE_FORCE_ON=1 — 관리 작업 거부")
    value = ac.kv_get("autotrade:enabled")
    if str(value) != "0":
        raise RuntimeError(
            f"웹 신규매수가 명시적 OFF(0)가 아닙니다(value={value!r}). 먼저 웹에서 OFF로 전환하세요.")


def _backup_positions():
    if not os.path.exists(ac.POS_PATH):
        return None
    stamp = datetime.now(ac.KST).strftime("%Y%m%d%H%M%S%f")
    path = f"{ac.POS_PATH}.manual-backup.{stamp}"
    shutil.copy2(ac.POS_PATH, path)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="pending 읽기 전용 목록")
    for name in ("attach-order", "resolve-no-order"):
        p = sub.add_parser(name)
        p.add_argument("--intent-id", required=True)
        p.add_argument("--code", required=True)
        p.add_argument("--order-date", required=True)
        p.add_argument("--requested-qty", required=True, type=int)
        if name == "attach-order":
            p.add_argument("--order-no", required=True)
            p.add_argument("--confirm-hts-checked", action="store_true", required=True)
        else:
            p.add_argument("--confirm-hts-no-order-no-fill", action="store_true", required=True)
    args = parser.parse_args(argv)

    if args.command == "list":
        data = ac.load_positions()
        print(json.dumps(data.get("pending_entries") or [], ensure_ascii=False, indent=2))
        return 0

    with ac.acquire_execution_lock("pending-admin", timeout_seconds=10) as acquired:
        if not acquired:
            raise RuntimeError("자동매매 전역 잠금을 얻지 못했습니다")
        # 잠금 획득 뒤 다시 확인해 실행기와의 TOCTOU 창을 줄인다.
        _require_web_off()
        data = ac.load_positions()
        backup = _backup_positions()
        kwargs = dict(intent_id=args.intent_id, code=args.code,
                      order_date=args.order_date, requested_qty=args.requested_qty)
        if args.command == "attach-order":
            result = attach_order_number(data, order_no=args.order_no, **kwargs)
            event_type = "pending_order_number_attached"
        else:
            result = resolve_confirmed_no_order(data, **kwargs)
            event_type = "pending_manual_resolved"
        ac.save_positions(data)
        ac.append_trade_event({
            "type": event_type, "intent_id": args.intent_id, "code": args.code,
            "order_date": args.order_date, "requested_qty": args.requested_qty,
            "backup_path": backup, "result": result,
        })
        ac.notify_trade(
            f"⚠️ [자동매매] pending 수동관리 완료: {event_type}\n"
            f"intent={args.intent_id} code={args.code} backup={backup}")
        print(json.dumps({"ok": True, "event": event_type, "backup": backup},
                         ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
