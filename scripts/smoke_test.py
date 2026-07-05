#!/usr/bin/env python3
"""주문 배관 스모크 테스트 — 안 체결될 1주 지정가 접수 → 접수번호 확인 → 즉시 취소.

목적: 실계좌 실발주 켜기 전에 주문 POST(kt10000)·취소(kt10002) 엔드포인트가
      실제로 접수/취소되는지(인증·TR라우팅·계좌바인딩) 한 번 검증.

안전장치:
  · 1주만, 시장가보다 ~20% 낮은 지정가 → 절대 체결 안 됨(대기만).
  · 기본 DRY(발주 안 함). 실주문은 AUTOTRADE_LIVE=1 일 때만 (kiwoom_trade 이중 게이트).
  · 매수 접수 성공 후 취소 실패 시 → 계좌에 미체결 주문이 남으므로 큰 경고 + exit 2.

사용:
  python3 scripts/smoke_test.py [종목코드]              # DRY(계획만)
  AUTOTRADE_LIVE=1 python3 scripts/smoke_test.py 005930 # 실접수→실취소 (평일 장중에만!)

⚠ 평일 장중(09:00~15:30 KRX)에만 의미 있음. 장 밖에선 거래소가 거부한다.
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kiwoom_client as kw
import kiwoom_trade as kt

LIMIT_DISCOUNT = 0.80  # 시장가의 80% = -20% 지정가 (체결 방지)


def main():
    code = sys.argv[1] if len(sys.argv) > 1 else "005930"
    live = os.environ.get("AUTOTRADE_LIVE") == "1"
    dry = not live  # LIVE 아니면 무조건 dry

    print(f"=== 스모크 테스트: {code} ===")
    print(f"모드: {'🔴 LIVE (실접수→실취소)' if live else '🟢 DRY (계획만, 발주 없음)'}")

    p = kw.price_now(code, market="J")
    cur = p.get("price") or 0
    if cur <= 0:
        print(f"❌ 현재가 조회 실패 (price={cur}) — 장 밖이거나 종목 오류")
        return 1
    limit_px = kt._floor_to_tick(cur * LIMIT_DISCOUNT)
    print(f"현재가 {cur:,.0f}원 → 지정가 {limit_px:,.0f}원(-{(1-LIMIT_DISCOUNT)*100:.0f}%, 호가정렬) 1주 매수")

    # 1) 매수 접수
    print("\n[1/2] 지정가 1주 매수 접수…")
    buy = kt.buy_limit(code, qty=1, price=limit_px, market="KRX", dry=dry)
    if buy.get("dry"):
        print(f"  DRY — 발주 안 함(사유={buy.get('reason')}). 실검증하려면 AUTOTRADE_LIVE=1 (평일 장중).")
        print(f"  보낼 주문: {json.dumps(buy['plan']['body'], ensure_ascii=False)}")
        return 0

    res = buy.get("result") or {}
    print(f"  ✅ 매수 접수 응답: {json.dumps(res, ensure_ascii=False)}")
    ord_no = kt._extract_ord_no(res)
    if not ord_no:
        print("  ⚠️  접수됐으나 원주문번호를 응답에서 못 찾음 — 취소 불가.")
        print("  ⚠️  키움 앱/HTS에서 미체결 주문을 직접 취소하세요! 응답 필드명 확인 후 _extract_ord_no 보강 필요.")
        return 2
    print(f"  원주문번호: {ord_no}")

    # 2) 즉시 취소
    print("\n[2/2] 원주문 취소…")
    try:
        cxl = kt.cancel_order(code, ord_no, market="KRX", qty=0, dry=dry)
        print(f"  ✅ 취소 응답: {json.dumps(cxl.get('result', cxl), ensure_ascii=False)}")
    except Exception as e:
        print(f"  ❌❌ 취소 실패: {e}")
        print(f"  ⚠️⚠️ 계좌에 미체결 매수주문(원주문={ord_no})이 남아있습니다 — 키움 앱에서 즉시 취소하세요!")
        return 2

    print("\n✅ 스모크 통과 — 주문 접수·취소 배관 정상.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
