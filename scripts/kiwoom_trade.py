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


def account_holdings():
    """실계좌 보유종목 조회(kt00018 계좌평가잔고내역요청, 읽기전용). 계좌번호는 토큰 바인딩(body 불필요).

    반환: {"holdings":[{code,name,qty,tradable_qty,avg_price,cur_price,eval_pl,profit_rate,eval_amt}],
           "summary":{tot_pur,tot_eval,tot_pl,profit_rate,deposit}}. code는 'A' 접두 제거한 6자리.
    """
    res = kw._call("kt00018", "/api/dostk/acnt", {"qry_tp": "1", "dmst_stex_tp": "KRX"})
    _i = lambda v: int(kw._f(v))  # 제로패딩 문자열 → 정수(부호 유지)
    holdings = []
    for r in res.get("acnt_evlt_remn_indv_tot", []) or []:
        code = (r.get("stk_cd") or "").strip().lstrip("A")  # 'A090410' → '090410'
        if not code:
            continue
        holdings.append({
            "code": code,
            "name": (r.get("stk_nm") or "").strip(),
            "qty": _i(r.get("rmnd_qty")),
            "tradable_qty": _i(r.get("trde_able_qty")),
            "avg_price": _i(r.get("pur_pric")),
            "cur_price": _i(r.get("cur_prc")),
            "eval_pl": _i(r.get("evltv_prft")),
            "profit_rate": kw._f(r.get("prft_rt")),
            "eval_amt": _i(r.get("evlt_amt")),
        })
    return {"holdings": holdings, "summary": {
        "tot_pur": _i(res.get("tot_pur_amt")),
        "tot_eval": _i(res.get("tot_evlt_amt")),
        "tot_pl": _i(res.get("tot_evlt_pl")),
        "profit_rate": kw._f(res.get("tot_prft_rt")),
        "deposit": _i(res.get("prsm_dpst_aset_amt")),
    }}


def order_status(code, ord_no, market="NXT", order_date="", side="sell"):
    """주문번호별 누적 체결수량·주문잔량 조회(kt00007, 읽기전용).

    주문잔량 0만 종결 상태로 취급할 수 있도록 원주문 행을 그대로 정규화한다.
    조회 결과에 아직 주문이 반영되지 않았으면 None을 반환한다.
    """
    target = str(ord_no or "").strip()
    if not target:
        raise RuntimeError(f"{code} 주문상태 조회 주문번호 없음")
    side = str(side or "").lower()
    if side not in ("buy", "sell"):
        raise ValueError("side must be buy or sell")
    res = kw._call("kt00007", "/api/dostk/acnt", {
        "ord_dt": str(order_date or ""),
        "qry_tp": "1",
        "stk_bond_tp": "1",
        # 키움 계약상 1=매도, 2=매수. 실계좌 검증 완료 전 LIVE 매수는
        # AUTOTRADE_ORDER_FIELDS_VERIFIED 게이트가 별도로 차단한다.
        "sell_tp": "1" if side == "sell" else "2",
        "stk_cd": str(code),
        "fr_ord_no": "",
        "dmst_stex_tp": market,
    })

    def normalized(value):
        value = str(value or "").strip()
        return value.lstrip("0") or "0"

    rows = res.get("acnt_ord_cntr_prps_dtl", []) or []
    original = None
    cancel_confirmed = False
    for row in rows:
        row_code = str(row.get("stk_cd") or "").strip().lstrip("AJQ")
        if row_code and row_code != str(code):
            continue
        if normalized(row.get("ord_no")) == normalized(target):
            original = row
        if (normalized(row.get("ori_ord")) == normalized(target)
                and "취소확인" in str(row.get("mdfy_cncl") or "")):
            cancel_confirmed = True
    if original is not None:
        row = original

        def qty(key):
            raw = row.get(key)
            if raw is None or not str(raw).strip():
                raise RuntimeError(f"{code} 주문 {target} 상태 필드 누락: {key}")
            return abs(int(kw._f(raw)))

        return {
            "ord_no": str(row.get("ord_no") or target).strip(),
            "ordered_qty": qty("ord_qty"),
            "filled_qty": qty("cntr_qty"),
            "remaining_qty": qty("ord_remnq"),
            "side": side,
            "avg_fill_price": next((abs(kw._f(row.get(k))) for k in
                                    ("avg_cntr_pric", "cntr_avg_pric", "cntr_pric")
                                    if row.get(k) not in (None, "")), None),
            "accept_type": str(row.get("acpt_tp") or "").strip(),
            "modify_cancel": str(row.get("mdfy_cncl") or "").strip(),
            "cancel_confirmed": cancel_confirmed,
        }
    return None


def prepare_buy_market_krx(code, krw):
    p = kw.price_now(code, market="J")
    price = p.get("price") or 0
    qty = _qty_for_krw(price, krw)
    if qty <= 0:
        raise RuntimeError(f"{code} 매수수량 0 (price={price}, krw={krw})")
    return {"api_id": "kt10000", "code": code, "qty": qty, "ref_price": price,
            "market": "KRX", "body": {"dmst_stex_tp": "KRX", "stk_cd": code,
            "ord_qty": str(qty), "ord_uv": "0", "trde_tp": "3"},
            "label": f"KRX시장가매수 {code} {qty}주(~{price:,.0f}원)"}


def prepare_buy_limit_nxt(code, krw):
    ob = orderbook(code, market="NX")
    asks = [a for a in ob["asks"] if a > 0]
    if not asks:
        raise RuntimeError(f"{code} NXT 매도호가 없음 — 지정가 산출 불가")
    px = asks[4] if len(asks) >= 5 else asks[-1]
    qty = _qty_for_krw(px, krw)
    if qty <= 0:
        raise RuntimeError(f"{code} NXT 매수수량 0 (px={px}, krw={krw})")
    return {"api_id": "kt10000", "code": code, "qty": qty, "ref_price": px,
            "market": "NXT", "body": {"dmst_stex_tp": "NXT", "stk_cd": code,
            "ord_qty": str(qty), "ord_uv": str(int(px)), "trde_tp": "0"},
            "label": f"NXT지정가매수 {code} {qty}주@{px:,.0f}(매도5호가)"}


def submit_prepared_buy(plan, dry=True):
    out = _send_or_dry(plan["api_id"], plan["body"], dry, plan["label"])
    out.update({k: plan[k] for k in ("code", "qty", "ref_price", "market")})
    return out


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
    return submit_prepared_buy(prepare_buy_market_krx(code, krw), dry=dry)


def buy_limit_nxt(code, krw, dry=True):
    """NXT 지정가 매수 — 매도 5호가 위 가격으로(시장가 효과). 19:50 종가베팅용. trde_tp=0(지정가)."""
    return submit_prepared_buy(prepare_buy_limit_nxt(code, krw), dry=dry)


# ── 지정가 매수(스모크·범용) ─────────────────────────────────────────
def _krx_tick(price):
    """KRX 호가단위(2023 개편) — 지정가가 유효하려면 tick의 배수여야 함."""
    if price < 2_000:
        return 1
    if price < 5_000:
        return 5
    if price < 20_000:
        return 10
    if price < 50_000:
        return 50
    if price < 200_000:
        return 100
    if price < 500_000:
        return 500
    return 1_000


def _floor_to_tick(price):
    """price 이하의 가장 가까운 호가단위 배수."""
    t = _krx_tick(price)
    return int(price // t * t)


def buy_limit(code, qty, price, market="KRX", dry=True):
    """지정가 매수(qty주 @price). 스모크·범용. trde_tp=0(지정가). price는 호가단위로 정렬해 전달.

    ⚠ 매수 지정가가 시장가보다 낮으면 체결되지 않고 대기 → 스모크(접수 후 취소)에 사용.
    """
    qty = int(qty)
    px = int(price)
    if qty <= 0 or px <= 0:
        raise RuntimeError(f"{code} 지정가 매수 파라미터 오류 (qty={qty}, price={px})")
    body = {"dmst_stex_tp": market, "stk_cd": code, "ord_qty": str(qty),
            "ord_uv": str(px), "trde_tp": "0"}
    out = _send_or_dry("kt10000", body, dry, f"{market}지정가매수 {code} {qty}주@{px:,.0f}")
    out.update({"code": code, "qty": qty, "ref_price": px, "market": market})
    return out


# ── 주문 취소(kt10003) ────────────────────────────────────────────────
# ⚠ kt10002는 정정(MODIFY), kt10003이 취소(CANCEL). 키움 공식 API 확인(2026-07-06).
def _extract_ord_no(res):
    """주문 응답에서 원주문번호 추출(키움 필드 편차 대비 다중 키 탐색)."""
    if not isinstance(res, dict):
        return None
    for k in ("ord_no", "odno", "orig_ord_no", "ord_num"):
        v = res.get(k)
        if v:
            return str(v).strip()
    return None


def cancel_order(code, orig_ord_no, market="KRX", qty=0, dry=True):
    """주문 취소(kt10003). qty=0이면 전량 취소. dry/AUTOTRADE_LIVE 게이트 동일 적용.

    ⚠ 취소는 kt10003. kt10002는 정정(MODIFY)이라 취소 바디를 보내면 취소가 안 됨(실주문 잔존).
    """
    orig = str(orig_ord_no).strip()
    if not orig:
        raise RuntimeError(f"{code} 취소 원주문번호 없음")
    body = {"dmst_stex_tp": market, "orig_ord_no": orig, "stk_cd": code,
            "cncl_qty": str(int(qty))}
    return _send_or_dry("kt10003", body, dry, f"{market}주문취소 {code} 원주문={orig} 취소수량={qty or '전량'}")


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
