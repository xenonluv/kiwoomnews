#!/usr/bin/env python3
"""키움 주문 클라이언트 — 자동매매 전용(조회 클라이언트 kiwoom_client와 분리).

⚠⚠ 실계좌 주문이 나가는 모듈. 이중 안전장치:
  1) 모든 주문 함수 기본 dry=True → 실제 발주 안 하고 '보낼 주문'만 로그.
  2) dry=False여도 환경변수 AUTOTRADE_LIVE=1 이 없으면 발주 차단(로그만).
  → 실발주는 dry=False AND AUTOTRADE_LIVE=1 둘 다일 때만.

주문 TR: kt10000(매수)/kt10001(매도) POST /api/dostk/ordr
  body: dmst_stex_tp(KRX|NXT|SOR), stk_cd, ord_qty, ord_uv, trde_tp(0=지정가,3=시장가)
  계좌번호는 앱키/토큰에 묶여 자동 결정(body 불필요).

주문은 **재시도 금지**(중복 발주 위험) — 단일 시도, 실패는 그대로 예외.
"""
import os
import sys
import json
import time
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kiwoom_client as kw  # 토큰·키·BASE·_abs·_f·price_now·_mkt 재사용

ORDER_PATH = "/api/dostk/ordr"


def _order_call(api_id, body):
    """주문 단일 POST(재시도 없음). 성공 시 응답 dict, 실패 시 예외."""
    app_key, app_secret = kw._keys()
    data = json.dumps(body).encode()
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": "Bearer " + kw.get_token(),
        "appkey": app_key,
        "secretkey": app_secret,
        "api-id": api_id,
    }
    req = urllib.request.Request(kw.BASE + ORDER_PATH, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        res = json.load(r)
    rc = res.get("return_code")
    if rc not in (0, None):
        raise RuntimeError(f"주문 실패 {api_id} rc={rc}: {(res.get('return_msg') or '').strip()}")
    return res


def _send_or_dry(api_id, body, dry, label):
    """dry/AUTOTRADE_LIVE 게이트. 실발주 조건 미충족이면 발주 없이 계획만 반환."""
    live = os.environ.get("AUTOTRADE_LIVE") == "1"
    plan = {"api_id": api_id, "body": body, "label": label}
    if dry or not live:
        reason = "dry=True" if dry else "AUTOTRADE_LIVE!=1"
        sys.stderr.write(f"[trade][DRY] {label} 발주 안 함({reason}): {json.dumps(body, ensure_ascii=False)}\n")
        return {"dry": True, "reason": reason, "plan": plan}
    res = _order_call(api_id, body)
    sys.stderr.write(f"[trade][LIVE] {label} 발주 완료: {json.dumps(res, ensure_ascii=False)}\n")
    return {"dry": False, "plan": plan, "result": res}


# ── 호가 조회(ka10004) — NXT 5호가 위 지정가 산출용 ──────────────────
def _ob_field(side, n):
    """ka10004 호가 필드명: 1호가=sel_fpr_bid, 2~10호가=sel_2th_pre_bid…(side=sel/buy)."""
    return f"{side}_fpr_bid" if n == 1 else f"{side}_{n}th_pre_bid"


def orderbook(code, market="KRX"):
    """호가 조회(ka10004). 매도호가(asks)·매수호가(bids) 1~10단계 리스트 반환. market: KRX/NXT/통합.

    asks[0]=매도1호가(best ask) … asks[4]=매도5호가. bids[0]=매수1호가.
    """
    res = kw._call("ka10004", "/api/dostk/mrkcond", {"stk_cd": kw._mkt(code, market)})
    asks = [kw._abs(res.get(_ob_field("sel", i))) for i in range(1, 11)]
    bids = [kw._abs(res.get(_ob_field("buy", i))) for i in range(1, 11)]
    return {"asks": asks, "bids": bids}


def is_nxt_tradable(code):
    """NXT 거래가능 종목인지 프로브 — NXT 현재가가 유효(>0)하면 True."""
    try:
        p = kw.price_now(code, market="NX")
        return (p.get("price") or 0) > 0
    except Exception:
        return False


def _qty_for_krw(price, krw):
    """고정 금액(krw)으로 살 수 있는 정수 주식수. price<=0이면 0."""
    if not price or price <= 0:
        return 0
    return int(krw // price)


# ── 매수 ─────────────────────────────────────────────────────────────
def buy_market_krx(code, krw, dry=True):
    """KRX 시장가 매수(고정 krw). 15:18 종가베팅용. trde_tp=3(시장가)."""
    p = kw.price_now(code, market="J")
    price = p.get("price") or 0
    qty = _qty_for_krw(price, krw)
    if qty <= 0:
        raise RuntimeError(f"{code} 매수수량 0 (price={price}, krw={krw})")
    body = {"dmst_stex_tp": "KRX", "stk_cd": code, "ord_qty": str(qty),
            "ord_uv": "0", "trde_tp": "3"}
    out = _send_or_dry("kt10000", body, dry, f"KRX시장가매수 {code} {qty}주(~{price:,.0f}원)")
    out.update({"code": code, "qty": qty, "ref_price": price, "market": "KRX"})
    return out


def buy_limit_nxt(code, krw, dry=True):
    """NXT 지정가 매수 — 매도 5호가 위 가격으로(시장가 효과). 19:50 종가베팅용. trde_tp=0(지정가)."""
    ob = orderbook(code, market="NX")
    asks = [a for a in ob["asks"] if a > 0]
    if not asks:
        raise RuntimeError(f"{code} NXT 매도호가 없음 — 지정가 산출 불가")
    # 매도 5호가(없으면 가장 높은 호가) — 5단계 위를 쳐서 즉시 체결 유도
    px = asks[4] if len(asks) >= 5 else asks[-1]
    qty = _qty_for_krw(px, krw)
    if qty <= 0:
        raise RuntimeError(f"{code} NXT 매수수량 0 (px={px}, krw={krw})")
    body = {"dmst_stex_tp": "NXT", "stk_cd": code, "ord_qty": str(qty),
            "ord_uv": str(int(px)), "trde_tp": "0"}
    out = _send_or_dry("kt10000", body, dry, f"NXT지정가매수 {code} {qty}주@{px:,.0f}(매도5호가)")
    out.update({"code": code, "qty": qty, "ref_price": px, "market": "NXT"})
    return out


# ── 매도 ─────────────────────────────────────────────────────────────
def sell_market(code, qty, market="KRX", dry=True):
    """시장가 매도(qty주). 손절·익절 청산용. market NXT면 지정가 불가라 NXT 시간엔 별도 처리 필요."""
    qty = int(qty)
    if qty <= 0:
        raise RuntimeError(f"{code} 매도수량 0")
    if market == "NXT":
        # NXT는 지정가만 — 매수 5호가 아래로 던져 즉시 체결 유도
        ob = orderbook(code, market="NX")
        bids = [b for b in ob["bids"] if b > 0]
        if not bids:
            raise RuntimeError(f"{code} NXT 매수호가 없음 — 매도 지정가 산출 불가")
        px = bids[4] if len(bids) >= 5 else bids[-1]
        body = {"dmst_stex_tp": "NXT", "stk_cd": code, "ord_qty": str(qty),
                "ord_uv": str(int(px)), "trde_tp": "0"}
        return _send_or_dry("kt10001", body, dry, f"NXT지정가매도 {code} {qty}주@{px:,.0f}(매수5호가)")
    body = {"dmst_stex_tp": "KRX", "stk_cd": code, "ord_qty": str(qty),
            "ord_uv": "0", "trde_tp": "3"}
    return _send_or_dry("kt10001", body, dry, f"KRX시장가매도 {code} {qty}주")


if __name__ == "__main__":
    # 점검: 주문은 절대 안 나감(dry=True 기본). NXT 거래가능·호가·수량 산출만 확인.
    code = sys.argv[1] if len(sys.argv) > 1 else "005930"
    krw = int(sys.argv[2]) if len(sys.argv) > 2 else 1_000_000
    print(f"[probe] {code} NXT 거래가능:", is_nxt_tradable(code))
    p = kw.price_now(code, market="J")
    print(f"[probe] KRX 현재가: {p.get('price'):,.0f}  → {krw:,}원이면 {_qty_for_krw(p.get('price'),krw)}주")
    try:
        ob = orderbook(code, market="NX")
        print(f"[probe] NXT 매도호가 1~5: {ob['asks'][:5]}")
    except Exception as e:
        print(f"[probe] NXT 호가 조회 실패: {e}")
    print("[probe] --- dry 매수 계획(발주 안 함) ---")
    print(json.dumps(buy_market_krx(code, krw, dry=True), ensure_ascii=False, default=str))
