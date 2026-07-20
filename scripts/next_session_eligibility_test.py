#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import unittest
from datetime import datetime

import disclosure_client as dc
import next_session_eligibility as nse


GOLD_HALT = """
매매거래정지 및 정지해제
1.종목명
금호전기(주) 주권
2.매매거래정지일
2026년07월15일 부터
3.매매거래정지해제일
변경상장일
4.매매거래정지(해제)사유
- 주식의 병합, 분할 등 전자등록 변경, 말소
"""

GOLD_MERGER = """
주식병합 결정
2. 주식병합의 일정
주주총회예정일
2026-07-02
신주의 효력발생일
2026-07-17
매매거래정지기간
시작일
2026-07-15
종료일
2026-07-30
신주권상장예정일
2026-07-31
"""

MONAMI_HALT = """
매매거래 정지 및 재개(투자경고종목 지정중)
다음 종목은 현재 투자경고종목으로서 지정이후 주가 상승으로 1일간 매매거래가 정지되니,
투자에 주의하시기 바랍니다.
1. 대상종목
모나미 보통주
2. 정지사유
투자경고종목 지정이후 주가가 2일간 40%이상 급등
| 3. 정지일 | 2026년 07월 20일(1일간) |
4. 근거규정
시장감시규정
"""

MONAMI_NOTICE = """
매매거래정지 예고
2. 정지예고일
2026년 07월 16일(D)
3. 매매거래정지 여부
2026년 07월 16일 종가 조건을 충족하면 2026년 07월 20일(1일간) 매매거래가 정지됨
"""


def row(no, title, day="20260708"):
    return {
        "notice_id": str(no), "title": title, "date": day,
        "href": f"https://finance.naver.com/item/news_notice_read.naver?no={no}&code=001210",
    }


class DisclosureClientTest(unittest.TestCase):
    def test_parse_rows_keeps_notice_id_and_code(self):
        raw = """
        <a class="tit" href="/item/news_notice_read.naver?no=60886298&amp;code=001210">금호전기(주) 매매거래정지및정지해제(주식병합)</a>
        <td class="date">2026.07.08</td>
        """
        parsed = dc.parse_notice_rows(raw)
        self.assertEqual(parsed[0]["notice_id"], "60886298")
        self.assertEqual(parsed[0]["code"], "001210")
        self.assertEqual(parsed[0]["date"], "20260708")

    def test_empty_first_page_is_error_not_no_notice(self):
        with self.assertRaises(dc.DisclosureUnavailable):
            dc.fetch_notice_rows("001210", fetcher=lambda *_: b"<html></html>")


class CalendarTest(unittest.TestCase):
    def test_friday_resolves_monday(self):
        self.assertEqual(
            nse.resolve_next_trade_date("20260710", closed_dates=set()), "20260713")

    def test_official_substitute_holiday_is_skipped(self):
        self.assertEqual(nse.resolve_next_trade_date("20260814"), "20260818")

    def test_chuseok_and_weekend_are_skipped(self):
        self.assertEqual(nse.resolve_next_trade_date("20260923"), "20260928")

    def test_2026_constitution_day_temporary_holiday_is_skipped(self):
        self.assertEqual(nse.resolve_next_trade_date("20260716"), "20260720")

    def test_generated_at_timestamp_can_supply_signal_date(self):
        self.assertEqual(
            nse.signal_date_for({}, "2026-07-14 15:11:00 KST"), "20260714")

    def test_unsupported_year_fails_closed(self):
        self.assertIsNone(nse.resolve_next_trade_date("20261230"))


class NoticeParserTest(unittest.TestCase):
    def test_gold_electric_confirmed_halt_and_schedule(self):
        halt = nse.parse_notice_event(
            row(60886298, "금호전기(주) 매매거래정지및정지해제(주식병합)"), GOLD_HALT)
        schedule = nse.parse_notice_event(
            row(6862145, "금호전기(주) 주식병합 결정", "20260522"), GOLD_MERGER)
        self.assertEqual(halt["kind"], "HALT_CONFIRMED")
        self.assertEqual(halt["start"], "20260715")
        self.assertEqual(schedule["end"], "20260730")
        self.assertEqual(schedule["relisting_expected"], "20260731")
        result = nse.evaluate_events(
            [halt, schedule], "20260714", "20260715",
            now=datetime(2026, 7, 14, 15, 11, tzinfo=nse.KST))
        self.assertEqual(result["status"], "HALT_CONFIRMED")
        self.assertFalse(result["tradable_next_session"])
        self.assertFalse(result["recommendable"])
        self.assertEqual(result["restriction_end"], "20260730")
        self.assertEqual(result["relisting_expected"], "20260731")

    def test_halt_notice_is_not_confirmed(self):
        notice = nse.parse_notice_event(
            row(1, "테스트(주) 매매거래정지 예고"), "조건 충족 시 매매거래가 정지될 수 있음")
        result = nse.evaluate_events([notice], "20260714", "20260715")
        self.assertEqual(result["status"], "NOTICE_ONLY")
        self.assertTrue(result["recommendable"])
        self.assertFalse(result["auto_buy_allowed"])

    def test_short_halt_date_does_not_confuse_notice_or_release_labels(self):
        confirmed = nse.parse_notice_event(
            row(4, "테스트(주) 매매거래 정지 및 재개"),
            "3. 정지일\n2026년 07월 20일(1일간)\n4. 정지해제일\n2026년 07월 21일")
        notice = nse.parse_notice_event(
            row(5, "테스트(주) 매매거래 정지 예고"),
            "2. 정지예고일\n2026년 07월 16일")
        self.assertEqual(confirmed["start"], "20260720")
        self.assertEqual(confirmed["end"], "20260720")
        self.assertEqual(notice["kind"], "NOTICE_ONLY")
        self.assertIsNone(notice["start"])

    def test_liquidation_is_recommendation_block_not_false_halt(self):
        event = nse.parse_notice_event(
            row(2, "테스트(주) 상장폐지에 따른 정리매매 개시"), "정리매매기간 2026-07-15")
        result = nse.evaluate_events([event], "20260714", "20260715")
        self.assertEqual(result["status"], "RECOMMENDATION_BLOCKED")
        self.assertIsNone(result["tradable_next_session"])
        self.assertFalse(result["recommendable"])

    def test_old_open_ended_halt_does_not_override_observed_trading(self):
        event = nse.parse_notice_event(
            row(3, "테스트(주) 매매거래정지및정지해제"),
            "매매거래정지일 2026년01월02일 부터")
        result = nse.evaluate_events(
            [event], "20260714", "20260715", observed_trading=True)
        self.assertEqual(result["status"], "CLEAR_AS_CHECKED")

    def test_missing_target_is_unverified(self):
        result = nse.evaluate_events([], "20261230", None)
        self.assertEqual(result["status"], "UNVERIFIED")
        self.assertFalse(result["recommendable"])

    def test_confirmed_halt_with_unparsed_date_is_fail_closed(self):
        event = nse.parse_notice_event(
            row(9, "테스트(주) 매매거래정지및정지해제"),
            "매매거래정지 사유는 확인되나 표 형식을 읽지 못함")
        result = nse.evaluate_events([event], "20260714", "20260715")
        self.assertEqual(result["status"], "UNVERIFIED")
        self.assertEqual(result["reason_code"], "CONFIRMED_HALT_DATE_UNPARSED")

    def test_newer_corrected_schedule_conflict_is_not_old_confirmed_halt(self):
        confirmed = nse.parse_notice_event(
            row(10, "테스트(주) 매매거래정지및정지해제", "20260708"),
            "매매거래정지일 2026년07월15일 부터")
        corrected = nse.parse_notice_event(
            row(11, "테스트(주) (정정)주식병합 결정", "20260709"),
            "매매거래정지기간 시작일 2026-07-20 종료일 2026-07-30")
        result = nse.evaluate_events([confirmed, corrected], "20260714", "20260715")
        self.assertEqual(result["status"], "UNVERIFIED")
        self.assertEqual(result["reason_code"], "HALT_SCHEDULE_CONFLICT")


class EndToEndFixtureTest(unittest.TestCase):
    def test_safety_rejects_missing_or_expired_snapshot(self):
        self.assertFalse(nse.is_fresh(None))
        self.assertFalse(nse.is_fresh({"expires_at": "2020-01-01T00:00:00+09:00"}))
        self.assertEqual(nse.safety_allowed(None)[0], False)

    def test_fetch_failure_is_unverified(self):
        def fail_rows(*_args, **_kwargs):
            raise dc.DisclosureUnavailable("boom")

        result = nse.evaluate_for_code(
            "001210", "20260714", force_refresh=True, fetch_rows=fail_rows)
        self.assertEqual(result["status"], "UNVERIFIED")
        self.assertEqual(result["reason_code"], "DISCLOSURE_CHECK_FAILED")

    def test_gold_electric_fixture_end_to_end(self):
        rows = [
            row(60886298, "금호전기(주) 매매거래정지및정지해제(주식병합)"),
            row(6862145, "금호전기(주) 주식병합 결정", "20260522"),
        ]
        bodies = {"60886298": GOLD_HALT, "6862145": GOLD_MERGER}
        result = nse.evaluate_for_code(
            "001210", "20260714", force_refresh=True,
            fetch_rows=lambda *_args, **_kwargs: rows,
            fetch_body=lambda item: {"text": bodies[item["notice_id"]]},
        )
        self.assertEqual(result["target_trade_date"], "20260715")
        self.assertEqual(result["status"], "HALT_CONFIRMED")
        self.assertFalse(result["auto_buy_allowed"])

    def test_monami_spaced_title_confirmed_halt_beats_older_notice(self):
        confirmed_title = "(주)모나미 매매거래 정지 및 재개(투자경고종목 지정중)"
        rows = [
            row(70458882, confirmed_title, "20260716"),
            row(68428917, "(주)모나미 매매거래정지 예고", "20260715"),
        ]
        bodies = {"70458882": MONAMI_HALT, "68428917": MONAMI_NOTICE}
        result = nse.evaluate_for_code(
            "005360", "20260716", force_refresh=True,
            fetch_rows=lambda *_args, **_kwargs: rows,
            fetch_body=lambda item: {"text": bodies[item["notice_id"]]},
        )
        self.assertEqual(result["target_trade_date"], "20260720")
        self.assertEqual(result["status"], "HALT_CONFIRMED")
        self.assertEqual(result["restriction_start"], "20260720")
        self.assertEqual(result["restriction_end"], "20260720")
        self.assertFalse(result["tradable_next_session"])
        self.assertFalse(result["recommendable"])
        self.assertFalse(result["auto_buy_allowed"])
        self.assertEqual(result["evidence"]["title"], confirmed_title)


if __name__ == "__main__":
    unittest.main()
