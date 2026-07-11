#!/usr/bin/env python3
"""장 세션 밖 미해소 매수 pending 재알림 전용 잡. 주문·조회·취소 API를 호출하지 않는다."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autotrade_common as ac
import autotrade_orders


def run():
    with ac.acquire_execution_lock("pending-attention", timeout_seconds=10) as acquired:
        if not acquired:
            return False
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
