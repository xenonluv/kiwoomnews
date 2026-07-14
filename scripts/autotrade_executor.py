#!/usr/bin/env python3
"""자동매매 실행기 — 종가베팅 매수. Windows Task Scheduler에서 15:18/19:50 호출.

  python3 scripts/autotrade_executor.py --slot krx   # 15:18 — NXT 불가 종목 KRX 시장가 매수
  python3 scripts/autotrade_executor.py --slot nxt   # 19:50 — NXT 가능 종목 NXT 지정가(5호가위) 매수

흐름: KV 토글 ON? → 오늘 미매수? → 레이더 1위(suspects[0]) 안전필터 통과? →
      NXT 거래가능 여부로 슬롯 분기 → 100만원 매수 → 포지션 기록.
⚠ 실발주는 kiwoom_trade가 AUTOTRADE_LIVE=1 일 때만(아니면 dry 로그). 기본은 안전(미발주).
"""
import os
import sys
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autotrade_common as ac
import kiwoom_trade as kt
import market_state
import autotrade_orders


def _entry_audit_fields(slot, top, rank, decision):
    model_version = top.get("rank_model_version") or decision.get("rank_model_version")
    raw_top_codes = decision.get("top_codes")
    top_codes = list(raw_top_codes) if isinstance(raw_top_codes, (list, tuple)) else []
    return {
        "radar_generated_at": decision.get("radar_generated_at"),
        "model_version": model_version,
        "rank_model_version": model_version,
        "rank_bucket": top.get("rank_bucket"),
        "reason": top.get("rank_reason"),
        "rank_reason": top.get("rank_reason"),
        "top_codes": top_codes,
        "top_codes_at_decision": top_codes,
        "change_basis": top.get("change_basis"),
        "selected_rank": rank,
        "decision_slot": slot,
        "decision_at": decision.get("decision_at"),
        "precut_rank": top.get("precut_rank"),
        "published_rank": top.get("published_rank") or rank,
    }


def _record_order_outcome(slot, top, rank, decision, *, attempted, result,
                          order_reason=None, error=None, dry=False):
    event = {
        "type": "order_outcome",
        "entry_date": ac.today_str(),
        "slot": slot,
        "code": top.get("code"),
        "name": top.get("name"),
        "rank": rank,
        "order_attempted": bool(attempted),
        "order_result": result,
        "order_reason": order_reason,
        "error": str(error) if error is not None else None,
        "dry": bool(dry),
        **_entry_audit_fields(slot, top, rank, decision),
    }
    ac.append_local_trade_event(event)


def _record(slot, top, res, rank, alloc_krw, decision):
    """실매수 체결분 핵심 포지션을 먼저 저장하고 선택적 감사 원장을 기록한다."""
    code, name = top["code"], top.get("name", "")
    audit = _entry_audit_fields(slot, top, rank, decision)
    event_id = f"{code}-{ac.today_str()}-{slot}"
    try:
        data = ac.load_positions()
        data["positions"].append({
            "id": event_id,
            "code": code, "name": name,
            "entry_date": ac.today_str(),
            "entry_price": res["ref_price"],
            "qty": res["qty"], "qty_open": res["qty"],
            "market": res["market"], "alloc_krw": alloc_krw, "rank": rank,
            "tp1_done": False, "status": "open",
            "opened_at": datetime.now(ac.KST).strftime("%Y-%m-%d %H:%M:%S"),
            "pattern": top.get("pattern"), "suspicion_score": top.get("suspicion_score"),
            "order_attempted": True, "order_result": "submitted",
            **audit,
        })
        ac.save_positions(data)
    except Exception as e:
        ac.log(f"[exec:{slot}] 🚨 매수는 체결됐으나 포지션 기록 실패: {e} — 수동 확인 필요(중복매수·청산누락 위험)")
        ac.notify_trade(
            f"🚨 [자동매매] 체결 후 핵심 포지션 기록 실패: {rank}위 {name}({code})\n"
            "중복매수·청산누락 방지를 위해 계좌와 원장을 즉시 수동 대조해야 합니다.")
        failed_event = {
            "type": "entry_position_record_failed", "id": event_id,
            "code": code, "name": name, "entry_date": ac.today_str(),
            "market": res.get("market"), "slot": slot, "qty": res.get("qty"),
            "entry_price": res.get("ref_price"), "alloc_krw": alloc_krw,
            "rank": rank, "position_recorded": False, "order_attempted": True,
            "order_result": "submitted_position_record_failed", "error": str(e), **audit,
        }
        ac.append_trade_event(failed_event)
        ac.append_local_trade_event(failed_event)
        return
    ac.log(f"[exec:{slot}] ★매수 체결·기록: {rank}위 {name}({code}) {res['qty']}주 @~{res['ref_price']:,.0f} "
           f"({res['market']}, 배정 {alloc_krw:,}원)")
    ac.notify_trade(
        f"🟢 [자동매매] 신규 매수 {rank}위 {name}({code}) {res['qty']}주 @~{res['ref_price']:,.0f} "
        f"({res['market']} · 배정 {alloc_krw:,}원)\n"
        f"pattern={top.get('pattern')} score={top.get('suspicion_score')} · 익일 14:50 강제청산")
    entry_event = {
        "type": "entry", "id": event_id, "code": code, "name": name,
        "entry_date": ac.today_str(), "market": res["market"], "slot": slot,
        "qty": res["qty"], "entry_price": res["ref_price"], "alloc_krw": alloc_krw, "rank": rank,
        "pattern": top.get("pattern"), "suspicion_score": top.get("suspicion_score"),
        "position_recorded": True, "order_attempted": True, "order_result": "submitted",
        "dry": False, **audit,
    }
    ac.append_trade_event(entry_event)
    ac.append_local_trade_event(entry_event)


def _persist_post_submit(data, pending, warning=None):
    """브로커 제출 뒤 상태를 저장한다. 불명 경고는 저장보다 먼저 보장한다."""
    if warning:
        sent = ac.notify_trade(
            f"🚨 [자동매매] {pending.get('name','')}({pending.get('code')}) {warning}\n"
            "자동 재주문을 차단했습니다. HTS 주문·체결·잔고를 즉시 확인하세요.")
        pending["last_alert_attempt_at"] = datetime.now(ac.KST).strftime(
            "%Y-%m-%d %H:%M:%S KST")
        if sent:
            pending["last_alert_success_at"] = pending["last_alert_attempt_at"]
            pending["last_alert_success_date"] = ac.today_str()
            pending["alert_count"] = int(pending.get("alert_count") or 0) + 1
    try:
        ac.save_positions(data)
        return True
    except Exception as exc:
        ord_no = pending.get("ord_no") or "없음/불명"
        ac.log(f"[exec] 🚨 제출 후 pending 저장 실패(ord_no={ord_no}): {exc}")
        ac.notify_trade(
            f"🚨 [자동매매] 제출 후 원장 저장 실패 — {pending.get('name','')}({pending.get('code')})\n"
            f"market={pending.get('market')} qty={pending.get('requested_qty')} "
            f"ord_no={ord_no} · 오류={str(exc)[:200]}\n"
            "추가 발주를 중단했습니다. HTS와 원장을 수동 대조하세요.")
        return False


def _run_unlocked(slot, dry=True):
    try:
        data = ac.load_positions()
    except Exception as e:
        # 포지션 상태 불명(파일 읽기 실패)이면 중복매수 여부 확인 불가 → fail-closed(매수 중단).
        ac.log(f"[exec:{slot}] 포지션 상태 확인 실패({e}) — fail-closed(매수 중단)")
        return
    data.setdefault("pending_entries", [])
    if not ac.autotrade_enabled():
        ac.log(f"[exec:{slot}] 자동매매 OFF(KV autotrade:enabled≠1) — 매수 안 함")
        return
    if data.get("pending_entries"):
        try:
            # 증권사 주문상태를 먼저 대조한다. 이미 체결·취소 완료된 로컬 pending을
            # 재조정 전에 경고하면 false positive가 되므로 경고는 이 함수 끝에서만 보낸다.
            unresolved = autotrade_orders.reconcile_pending_entries(data, dry=dry)
        except Exception as e:
            ac.log(f"[exec:{slot}] 매수 pending 재조정 실패 — 신규 매수 차단: {e}")
            return
        if unresolved:
            ac.log(f"[exec:{slot}] 미확정 매수 주문 존재 — 신규 매수 차단")
            return
    # 전날 이월 미청산 포지션이 남아있으면 = 14:50 강제청산 실패 → 갈아타기 불가. 신규 매수 차단(중복 보유 방지).
    stale = [p for p in ac.open_positions(data) if p.get("entry_date") != ac.today_str()]
    if stale:
        names = ", ".join(f"{p.get('name','')}({p['code']})" for p in stale)
        ac.log(f"[exec:{slot}] 🚨 전날 미청산 포지션 잔존({names}) — 강제청산 실패 의심. 신규 매수 차단(중복보유 방지)")
        ac.notify_trade(
            f"🚨 [자동매매] 전날 포지션 미청산: {names}\n"
            f"14:50 강제청산이 안 된 상태라 오늘 신규 매수를 차단했습니다. 수동 확인 필요.")
        return

    # 하루 최대 2종목. 이미 오늘 매수한 만큼 슬롯 차감.
    slots_left = ac.MAX_AUTOTRADE_STOCKS - len(ac.todays_positions(data)) - len(ac.todays_pending_entries(data))
    if slots_left <= 0:
        ac.log(f"[exec:{slot}] 오늘 최대 매수 종목수({ac.MAX_AUTOTRADE_STOCKS}) 도달 — 추가 매수 안 함")
        return

    ranks = ac.read_ranks()
    decision_at = datetime.now(ac.KST)
    # 실발주 여부는 kiwoom_trade._send_or_dry와 동일 게이트(dry=False AND AUTOTRADE_LIVE=1).
    # --dry 플래그만 보면 AUTOTRADE_LIVE 미설정 dry 실행이 '실발주 결정'으로 위장 기록됨(재검증 반박 1).
    live_execution = (not dry) and os.environ.get("AUTOTRADE_LIVE") == "1"
    if live_execution:
        trading_ok, trading_state = market_state.require_trading_day(kt.kw)
        if not trading_ok:
            ac.log(f"[exec:{slot}] 거래일 확인 실패 — 매수 중단: {trading_state}")
            return
    radar_snapshot = ac.read_radar_snapshot(now=decision_at)
    top = ((radar_snapshot or {}).get("suspects") or [])[:3]
    try:
        radar_meta = ac.radar_snapshot_meta(radar_snapshot, now=decision_at)
    except Exception as e:
        # 저장은 선택적이지만 결정시각 검증 자체는 주문 전 필수다.
        ac.log(f"[audit] radar root 메타 계산 실패: {e}")
        radar_meta = {
            "trade_date": decision_at.strftime("%Y%m%d"),
            "radar_generated_at": (radar_snapshot or {}).get("generated_at"),
            "rank_model_version": (radar_snapshot or {}).get("rank_model_version"),
            "top_codes": [s.get("code") for s in top if s.get("code")],
            "valid_for_decision": False,
        }
    candidate_rows = []
    for published_rank, suspect in enumerate(top, 1):
        candidate_rows.append({
            "code": suspect.get("code"),
            "name": suspect.get("name"),
            "precut_rank": suspect.get("precut_rank"),
            "published_rank": suspect.get("published_rank") or published_rank,
            "rank_bucket": suspect.get("rank_bucket"),
            "rank_reason": suspect.get("rank_reason"),
            "rank_model_version": (suspect.get("rank_model_version")
                                   or radar_meta.get("rank_model_version")),
            "price": suspect.get("price"),
            "change_pct": suspect.get("change_pct"),
            "change_basis": suspect.get("change_basis"),
            "pattern": suspect.get("pattern"),
            "suspicion_score": suspect.get("suspicion_score"),
            "requested": published_rank in ranks,
            "selected": False,
            "safety_ok": None,
            "safety_reason": None,
        })
    if not radar_meta.get("valid_for_decision"):
        invalid_decision = {
            "schema_version": 1,
            "record_type": "decision_snapshot",
            **radar_meta,
            "slot": "KRX_1518" if slot == "krx" else "NXT_1950",
            "decision_at": decision_at.strftime("%Y-%m-%d %H:%M:%S KST"),
            "decision_source": "autotrade_executor",
            # dry 실행이 '실발주 결정'으로 위장 기록되던 라벨 거짓 정정(적대 리뷰 M2 + 재검증 반박 1)
            "actual_autotrade_execution": live_execution,
            "dry": not live_execution,
            "storage_scope": "executor_host_local_auxiliary",
            "selected_ranks": list(ranks),
            "ordered_candidates": candidate_rows,
        }
        ac.write_local_decision(invalid_decision["slot"], invalid_decision)
        ac.log(f"[exec:{slot}] radar 의사결정 시점 검증 실패"
               f"(generated_at={radar_meta.get('radar_generated_at')}, "
               f"stale={radar_meta.get('stale_seconds')}) — fail-closed(매수 중단)")
        return
    # 자격 종목: 선택 랭크·존재·safety 통과·오늘 그 코드 미매수. 코드 dedup. 하루 최대치로 캡.
    eligible = []
    seen = set()
    for r in ranks:
        if r - 1 >= len(top):
            continue
        s = top[r - 1]
        code = s.get("code")
        if not code or code in seen:
            continue
        ok, reason = ac.safety_ok(s)
        candidate_rows[r - 1]["safety_ok"] = ok
        candidate_rows[r - 1]["safety_reason"] = reason
        if not ok:
            ac.log(f"[exec:{slot}] {r}위 {code} 안전필터 차단: {reason}")
            continue
        if ac.already_bought(code, data):
            ac.log(f"[exec:{slot}] {r}위 {code} 오늘 이미 매수 — 스킵")
            continue
        seen.add(code)
        eligible.append((r, s))
    eligible = eligible[:slots_left]
    selected_keys = {(rank, suspect.get("code")) for rank, suspect in eligible}
    for row in candidate_rows:
        row["selected"] = (row["published_rank"], row.get("code")) in selected_keys
    decision = {
        "schema_version": 1,
        "record_type": "decision_snapshot",
        **radar_meta,
        "slot": "KRX_1518" if slot == "krx" else "NXT_1950",
        "decision_at": decision_at.strftime("%Y-%m-%d %H:%M:%S KST"),
        "decision_source": "autotrade_executor",
        # dry 실행이 '실발주 결정'으로 위장 기록되던 라벨 거짓 정정(적대 리뷰 M2 + 재검증 반박 1)
        "actual_autotrade_execution": live_execution,
        "dry": not live_execution,
        "storage_scope": "executor_host_local_auxiliary",
        "selected_ranks": list(ranks),
        "ordered_candidates": candidate_rows,
    }
    ac.write_local_decision(decision["slot"], decision)
    if not eligible:
        ac.log(f"[exec:{slot}] 매수 자격 종목 없음 (선택 랭크 {ranks})")
        return

    # 당일 총예산(웹 설정 KV, 기본 100만) − 이미 집행분. 단, 매수 전 실계좌 예수금을 1회 조회해
    # 그 이하로 캡(설정 예산 초과·자금부족 주문거부 방지 / 예산 설정 타이밍 무관하게 견고).
    budget = ac.read_budget()
    try:
        deposit = int(kt.account_holdings()["summary"]["deposit"])
        if deposit < 0:
            raise ValueError("예수금 음수")
    except Exception as e:
        ac.log(f"[exec:{slot}] 예수금 조회 실패({e}) — 계좌 안전상태 불명, fail-closed(매수 중단)")
        for rank, top_s in eligible:
            _record_order_outcome(
                slot, top_s, rank, decision, attempted=False,
                result="blocked_account_state_unknown", order_reason="deposit_lookup_failed",
                error=e, dry=not live_execution)
        return
    remaining_budget = budget - ac.deployed_today(data)
    remaining_budget = min(remaining_budget, deposit)   # 실제 예수금 초과 매수 금지
    per_stock = remaining_budget // max(1, len(eligible))
    if per_stock <= 0:
        ac.log(f"[exec:{slot}] 잔여 예산 0(설정 {budget:,}·예수금 {deposit}) — 매수 안 함")
        return
    ac.log(f"[exec:{slot}] 예산 설정 {budget:,}·예수금 {deposit}·잔여 {remaining_budget:,}·종목당 {per_stock:,}")
    ac.log(f"[exec:{slot}] 선택랭크={ranks} 자격={[(r, s['code']) for r, s in eligible]} "
           f"잔여예산={remaining_budget:,} 종목당={per_stock:,}")

    for rank, top_s in eligible:
        code, name = top_s["code"], top_s.get("name", "")
        nxt = kt.is_nxt_tradable(code)
        # 슬롯 라우팅: krx=비NXT 종목만 지금 / nxt=NXT 종목만 지금 (반대 슬롯은 위임)
        if slot == "krx" and nxt:
            ac.log(f"[exec:krx] {rank}위 {code} NXT 거래가능 → 19:50 NXT 슬롯 위임")
            _record_order_outcome(
                slot, top_s, rank, decision, attempted=False,
                result="delegated_nxt", order_reason="nxt_tradable",
                dry=not live_execution)
            continue
        if slot == "nxt" and not nxt:
            ac.log(f"[exec:nxt] {rank}위 {code} NXT 불가 → 15:18 KRX 슬롯 대상")
            _record_order_outcome(
                slot, top_s, rank, decision, attempted=False,
                result="not_routable_in_nxt", order_reason="nxt_not_tradable",
                dry=not live_execution)
            continue
        if not live_execution:
            try:
                res = (kt.buy_market_krx(code, per_stock, dry=dry) if slot == "krx"
                       else kt.buy_limit_nxt(code, per_stock, dry=dry))
            except Exception as e:
                ac.log(f"[exec:{slot}] {rank}위 {code} 매수 실패(스킵): {e}")
                _record_order_outcome(slot, top_s, rank, decision, attempted=True,
                                      result="order_failed", order_reason="broker_order_error",
                                      error=e, dry=True)
                continue
            if res.get("dry"):
                ac.log(f"[exec:{slot}] {rank}위 {code} DRY — 발주 안 함({res.get('reason')}). 미기록.")
                _record_order_outcome(slot, top_s, rank, decision, attempted=False,
                                      result="dry_no_order", order_reason=res.get("reason"), dry=True)
            else:
                # 테스트/비LIVE 가짜 응답 호환. 실제 브로커는 AUTOTRADE_LIVE 없이는 항상 dry다.
                _record(slot, top_s, res, rank, per_stock, decision)
            continue
        if os.environ.get("AUTOTRADE_ORDER_FIELDS_VERIFIED") != "1":
            ac.log(f"[exec:{slot}] 주문조회 필드 실계좌 미검증 — LIVE 매수 차단")
            _record_order_outcome(slot, top_s, rank, decision, attempted=False,
                                  result="blocked_order_fields_unverified",
                                  order_reason="AUTOTRADE_ORDER_FIELDS_VERIFIED!=1", dry=False)
            continue
        try:
            holding = next((h for h in kt.account_holdings().get("holdings", [])
                            if h.get("code") == code), None)
            manual_baseline_qty = int((holding or {}).get("qty") or 0)
        except Exception as e:
            ac.log(f"[exec:{slot}] {code} 매수 전 계좌 기준선 조회 실패 — LIVE 매수 차단: {e}")
            _record_order_outcome(slot, top_s, rank, decision, attempted=False,
                                  result="blocked_holdings_unavailable",
                                  order_reason="manual_baseline_query_failed", error=e, dry=False)
            continue
        try:
            plan = (kt.prepare_buy_market_krx(code, per_stock) if slot == "krx"
                    else kt.prepare_buy_limit_nxt(code, per_stock))
        except Exception as e:
            ac.log(f"[exec:{slot}] {rank}위 {code} 매수 계획 실패(스킵): {e}")
            _record_order_outcome(slot, top_s, rank, decision, attempted=False,
                                  result="order_failed", order_reason="buy_plan_error",
                                  error=e, dry=False)
            continue
        audit = _entry_audit_fields(slot, top_s, rank, decision)
        pending = {
            "intent_id": f"{code}-{ac.today_str()}-{slot}-{rank}",
            "state": "PREPARED", "code": code, "name": name,
            "entry_date": ac.today_str(), "order_date": ac.today_str(),
            "market": plan["market"], "requested_qty": plan["qty"],
            "accounted_filled": 0, "ref_price": plan["ref_price"],
            "manual_baseline_qty": manual_baseline_qty,
            "alloc_krw": per_stock, "rank": rank, "pattern": top_s.get("pattern"),
            "suspicion_score": top_s.get("suspicion_score"), "audit": audit,
            "ord_no": None, "created_at": datetime.now(ac.KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        }
        data["pending_entries"].append(pending)
        try:
            ac.save_positions(data)
        except Exception as e:
            data["pending_entries"].remove(pending)
            ac.log(f"[exec:{slot}] pending 저장 실패 — 브로커 주문 차단: {e}")
            continue
        try:
            res = kt.submit_prepared_buy(plan, dry=False)
        except Exception as e:
            pending["state"] = "SUBMIT_UNKNOWN"
            pending["submit_error"] = str(e)[:500]
            if not _persist_post_submit(
                    data, pending, warning=f"매수 제출 결과 불명: {str(e)[:160]}"):
                return
            continue
        ord_no = kt._extract_ord_no((res.get("result") or {}) if isinstance(res.get("result"), dict) else res)
        pending["ord_no"] = ord_no
        pending["state"] = "ACCEPTED" if ord_no else "SUBMIT_UNKNOWN"
        warning = None if ord_no else "매수 응답에 주문번호 없음"
        if not _persist_post_submit(data, pending, warning=warning):
            return
        if not ord_no:
            continue
        try:
            autotrade_orders.reconcile_pending_entries(data, dry=False)
        except Exception as e:
            ac.log(f"[exec:{slot}] 주문 접수 후 재조정 보류: {e}")
        _record_order_outcome(slot, top_s, rank, decision, attempted=True,
                              result="submitted_pending_confirmation",
                              order_reason="awaiting_order_status", dry=False)


def run(slot, dry=True):
    # 15:18에는 1분 모니터와 정상 경합할 수 있다. 하루 1회 실행기가 짧게 기다려
    # 모니터가 잠금을 놓친 직후 진입하되, 무한 대기는 하지 않는다.
    with ac.acquire_execution_lock(f"executor:{slot}", timeout_seconds=50) as acquired:
        if not acquired:
            return
        return _run_unlocked(slot, dry=dry)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", choices=["krx", "nxt"], required=True)
    ap.add_argument("--dry", action="store_true", help="강제 dry(발주 안 함). 미지정 시 kiwoom_trade 기본 dry=False지만 AUTOTRADE_LIVE=1 없으면 여전히 미발주.")
    a = ap.parse_args()
    run(a.slot, dry=a.dry)
