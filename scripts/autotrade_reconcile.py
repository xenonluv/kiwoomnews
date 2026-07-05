#!/usr/bin/env python3
"""실계좌 보유 조회 + 봇 포지션 정합성 대조 (읽기 전용 — 주문 없음).

  python3 scripts/autotrade_reconcile.py             # 콘솔 리포트
  python3 scripts/autotrade_reconcile.py --telegram  # + 텔레그램 리포트 1통

봇 포지션(data/autotrade_positions.json) vs 실계좌(kt00018)를 대조해:
  · 봇이 관리하는 포지션의 정합성(OK/QTY_SHORT/MISSING_IN_ACCOUNT)
  · 회장님 수동 보유(봇 미관리 — 자동매매가 절대 안 건드림)
를 보여준다. 조회 전용이라 어느 시간에나 안전.
"""
import sys
import argparse

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import autotrade_common as ac
import kiwoom_trade as kt


def build_report():
    acct = kt.account_holdings()
    rec = ac.reconcile(acct=acct)
    s = rec["summary"]
    lines = []
    lines.append(f"📋 실계좌 정합성 리포트")
    lines.append(f"평가 {s['tot_eval']:,}원 / 매입 {s['tot_pur']:,}원 / 손익 {s['tot_pl']:+,}원 ({s['profit_rate']:+.2f}%) / 예수금 {s['deposit']:,}원")

    lines.append("\n[봇 관리 포지션]")
    if rec["rows"]:
        for r in rec["rows"]:
            mark = {"OK": "✅", "QTY_SHORT": "⚠️", "MISSING_IN_ACCOUNT": "🚨"}.get(r["status"], "?")
            lines.append(f"  {mark} {r['name']}({r['code']}) {r['status']} — 봇기록 {r['bot_qty']} / 실계좌매도가능 {r['acct_tradable']}")
    else:
        lines.append("  (없음 — 봇이 보유 중인 포지션 없음)")

    lines.append("\n[수동 보유 (봇 미관리 — 자동매매 제외)]")
    if rec["manual_holdings"]:
        for h in rec["manual_holdings"]:
            lines.append(f"  • {h['name']}({h['code']}) {h['qty']}주 · 평단 {h['avg_price']:,} · 현재 {h['cur_price']:,} · {h['profit_rate']:+.2f}% ({h['eval_pl']:+,}원)")
    else:
        lines.append("  (없음)")
    return "\n".join(lines), rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--telegram", action="store_true", help="리포트를 텔레그램으로도 전송")
    a = ap.parse_args()
    report, _ = build_report()
    print(report)
    if a.telegram:
        ok = ac.notify_trade(report)
        print(f"\n텔레그램 전송: {'OK' if ok else '실패/미설정'}")


if __name__ == "__main__":
    main()
