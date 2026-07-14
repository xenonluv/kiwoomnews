#!/usr/bin/env python3
"""AUTO ON일 때만 장외 미해소 매수 pending 알림 실패를 재시도한다.

주문·조회·취소 API를 호출하지 않으며 웹 신규매수가 OFF이면 조용히 종료한다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autotrade_common as ac
import autotrade_orders


def run():
    with ac.acquire_execution_lock("pending-attention", timeout_seconds=10) as acquired:
        if not acquired:
            return False
        if not ac.autotrade_enabled():
            ac.log("[entry-attention] 자동매매 OFF — pending 재알림 생략")
            return True
        try:
            data = ac.load_positions()
        except Exception as exc:
            ac.log(f"[entry-attention] 포지션 원장 로드 실패: {exc}")
            return False
        if not data.get("pending_entries"):
            return True
        autotrade_orders.review_pending_attention(
            data, persist=lambda: ac.save_positions(data))
        return True


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)
