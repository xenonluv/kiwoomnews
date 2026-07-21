#!/usr/bin/env python3
import copy
import unittest
from unittest import mock

import alert_release as ar


def notice_text(five=60, fifteen=100):
    return f"""
투자경고종목 지정
| 2. 지정일 | 2026년 07월 01일 |
| 3. 지정사유 | 5일전의 종가보다 45% 이상 상승 |
| 5. 해제요건 | 위 종목은 지정일부터 계산하여 10일째 되는 날 이후의 날로서 |
| 어느 특정일(판단일, T)에 다음 사항에 모두 해당하지 않을 경우 |
| 그 다음 날에 해제됨 |
| ① 판단일(T)의 종가가 5일 전날(T-5)의 종가보다 {five}% 이상 상승 |
| ② 판단일(T)의 종가가 15일 전날(T-15)의 종가보다 {fifteen}% 이상 상승 |
| ③ 판단일(T)의 종가가 최근 15일 종가중 최고가 |
| *투자경고종목 해제여부의 최초 판단일은 07월 14일(예정) 이며, |
| 6. 근거규정 | 시장감시규정 |
"""


def rule(five=60, fifteen=100):
    return ar.parse_release_rule_text(
        notice_text(five, fifteen),
        notice_date="20260630",
        source_url="https://example.test/notice",
    )


def daily_fixture():
    dates = [f"202606{i:02d}" for i in range(1, 21)] + [
        "20260701", "20260702", "20260703", "20260706", "20260707",
        "20260708", "20260709", "20260710", "20260713", "20260714",
        "20260715",
    ]
    bars = []
    for date in dates:
        close = 120.0 if date == "20260710" else 100.0
        volume = 0.0 if date == "20260708" else 1000.0
        bars.append({
            "date": date, "open": close, "high": close, "low": close,
            "close": close, "volume": volume, "value": volume * close,
        })
    return bars


class ReleaseRuleParseTest(unittest.TestCase):
    def test_target_match_preserves_preferred_suffix(self):
        self.assertTrue(ar.notice_target_matches("(주) 금호건설", "금호 건설"))
        self.assertFalse(ar.notice_target_matches("금호건설", "금호건설우"))
        self.assertTrue(ar.notice_target_matches("금호건설우", "금호건설우"))

    def test_preferred_notice_is_skipped_for_common_stock(self):
        list_html = """
        <a href="/item/news_notice_read.naver?no=2&code=002990" class="tit">
          금호건설우 투자경고종목 지정해제</a><td class="date">2026.07.20</td>
        <a href="/item/news_notice_read.naver?no=1&code=002990" class="tit">
          금호건설 투자경고종목 지정</a><td class="date">2026.06.30</td>
        """.encode("euc-kr")
        bodies = {
            "no=2": "1. 대상종목 | 금호건설우 | 우선주\n" + notice_text(),
            "no=1": "1. 대상종목 | 금호건설 | 보통주\n" + notice_text(),
        }
        def fake_get(url, headers):
            if "read_content" not in url:
                return list_html
            return next(text for key, text in bodies.items() if key in url).encode("euc-kr")
        ar._NOTICE_CACHE.clear(); ar._RULE_CACHE.clear()
        with mock.patch.object(ar, "get_bytes", side_effect=fake_get):
            parsed = ar.fetch_release_rule("002990", expected_name="금호건설")
        self.assertEqual(parsed["notice_no"], "1")
        self.assertTrue(parsed["target_evidence"]["target_match"])
        self.assertEqual(parsed["target_evidence"]["notice_target_name"], "금호건설")

    def test_unparseable_latest_target_stops_fail_safe(self):
        list_html = """
        <a href="/item/news_notice_read.naver?no=2&code=002990" class="tit">
          투자경고종목 지정해제</a><td class="date">2026.07.20</td>
        <a href="/item/news_notice_read.naver?no=1&code=002990" class="tit">
          투자경고종목 지정</a><td class="date">2026.06.30</td>
        """.encode("euc-kr")
        ar._NOTICE_CACHE.clear(); ar._RULE_CACHE.clear()
        with mock.patch.object(ar, "get_bytes", side_effect=[list_html, b"no target"]):
            parsed = ar.fetch_release_rule("002990", expected_name="금호건설")
        self.assertEqual(parsed["parse_error"], "notice_unavailable")
        self.assertEqual(parsed["target_evidence"]["target_validation_status"], "target_unparseable")

    def test_risk_release_uses_same_target_validation(self):
        list_html = """
        <a href="/item/news_notice_read.naver?no=2&code=002990" class="tit">
          금호건설우 투자위험종목 지정해제</a><td class="date">2026.07.20</td>
        <a href="/item/news_notice_read.naver?no=1&code=002990" class="tit">
          금호건설 투자위험종목 지정해제</a><td class="date">2026.07.19</td>
        """.encode("euc-kr")
        def fake_get(url, headers):
            if "read_content" not in url:
                return list_html
            target = "금호건설우" if "no=2" in url else "금호건설"
            return f"1. 대상종목 | {target} | 보통주".encode("euc-kr")
        ar._RISK_CACHE.clear()
        with mock.patch.object(ar, "get_bytes", side_effect=fake_get):
            event = ar.risk_release_event("002990", expected_name="금호건설")
        self.assertEqual(event["date"], "20260719")
        self.assertEqual(event["evidence"]["notice_target_name"], "금호건설")
        self.assertTrue(event["evidence"]["target_match"])

    def test_parses_60_100_from_release_section_not_designation_reason(self):
        parsed = rule(60, 100)
        self.assertEqual(parsed["parse_status"], "ok")
        self.assertEqual(parsed["designation_date"], "20260701")
        self.assertEqual(parsed["first_review_date_notice"], "20260714")
        self.assertEqual(parsed["threshold_5d_pct"], 60.0)
        self.assertEqual(parsed["threshold_15d_pct"], 100.0)
        self.assertEqual(parsed["recent_high_window"], 15)
        self.assertEqual(parsed["min_elapsed_days"], 10)

    def test_parses_legacy_45_75_type(self):
        parsed = rule(45, 75)
        self.assertEqual(parsed["parse_status"], "ok")
        self.assertEqual(parsed["threshold_5d_pct"], 45.0)
        self.assertEqual(parsed["threshold_15d_pct"], 75.0)

    def test_missing_release_clause_is_fail_safe(self):
        parsed = ar.parse_release_rule_text(
            "2. 지정일 2026년 07월 01일\n5. 해제요건 본문 누락",
            notice_date="20260630",
        )
        self.assertEqual(parsed["parse_status"], "unavailable")
        self.assertEqual(parsed["parse_error"], "required_release_clause_missing")

    def test_fetches_rule_from_notice_list_and_content(self):
        list_html = """
        <a href="/item/news_notice_read.naver?no=123&code=002990&page_notice=1"
           class="tit">금호건설(주) 투자경고종목 지정</a>
        <td class="date">2026.06.30</td>
        """.encode("euc-kr")
        content_html = f"<html><body>{notice_text()}</body></html>".encode("euc-kr")

        def fake_get(url, headers):
            return content_html if "read_content" in url else list_html

        ar._NOTICE_CACHE.clear()
        ar._RULE_CACHE.clear()
        with mock.patch.object(ar, "get_bytes", side_effect=fake_get):
            parsed = ar.fetch_release_rule("002990")
        self.assertEqual(parsed["parse_status"], "ok")
        self.assertEqual(parsed["notice_no"], "123")
        self.assertEqual(parsed["source"], "KRX_KOSCOM")
        self.assertTrue(parsed["raw_text_hash"])

    def test_latest_release_event_does_not_fall_through_to_old_designation(self):
        list_html = """
        <a href="/item/news_notice_read.naver?no=2&code=002990" class="tit">
          금호건설(주) [투자주의]투자경고종목 지정해제 및 재지정 예고</a>
        <td class="date">2026.07.13</td>
        <a href="/item/news_notice_read.naver?no=1&code=002990" class="tit">
          금호건설(주) 투자경고종목 지정</a>
        <td class="date">2026.06.30</td>
        """.encode("euc-kr")
        ar._NOTICE_CACHE.clear()
        ar._RULE_CACHE.clear()
        with mock.patch.object(ar, "get_bytes", return_value=list_html):
            parsed = ar.fetch_release_rule("002990")
        self.assertEqual(parsed["parse_status"], "unavailable")
        self.assertEqual(parsed["parse_error"], "notice_released")

    def test_notice_for_different_code_is_ignored(self):
        list_html = """
        <a href="/item/news_notice_read.naver?no=9&code=999999" class="tit">
          다른종목(주) 투자경고종목 지정</a>
        <td class="date">2026.07.10</td>
        <a href="/item/news_notice_read.naver?no=1&code=002990" class="tit">
          금호건설(주) 투자경고종목 지정</a>
        <td class="date">2026.06.30</td>
        """.encode("euc-kr")
        content_html = f"<html><body>{notice_text()}</body></html>".encode("euc-kr")

        def fake_get(url, headers):
            return content_html if "read_content" in url else list_html

        ar._NOTICE_CACHE.clear()
        ar._RULE_CACHE.clear()
        with mock.patch.object(ar, "get_bytes", side_effect=fake_get):
            parsed = ar.fetch_release_rule("002990")
        self.assertEqual(parsed["notice_no"], "1")

    def test_newest_corrected_designation_notice_wins(self):
        list_html = """
        <a href="/item/news_notice_read.naver?no=2&code=002990" class="tit">
          금호건설(주) 투자경고종목 지정(정정)</a>
        <td class="date">2026.07.01</td>
        <a href="/item/news_notice_read.naver?no=1&code=002990" class="tit">
          금호건설(주) 투자경고종목 지정</a>
        <td class="date">2026.06.30</td>
        """.encode("euc-kr")
        content_html = f"<html><body>{notice_text()}</body></html>".encode("euc-kr")

        def fake_get(url, headers):
            return content_html if "read_content" in url else list_html

        ar._NOTICE_CACHE.clear()
        ar._RULE_CACHE.clear()
        with mock.patch.object(ar, "get_bytes", side_effect=fake_get):
            parsed = ar.fetch_release_rule("002990")
        self.assertEqual(parsed["notice_no"], "2")
        self.assertEqual(parsed["notice_date"], "20260701")


class ReleaseEvaluationTest(unittest.TestCase):
    def test_gold_construction_halt_is_excluded_on_july_13(self):
        bars = [b for b in daily_fixture() if b["date"] <= "20260713"]
        result = ar.evaluate_release(
            bars, 100, rule(), as_of_date="20260713")
        self.assertFalse(result["value"])
        self.assertEqual(result["reason"], "elapsed_not_met")
        self.assertEqual(result["checks"]["elapsed_days"], 8)
        self.assertEqual(result["checks"]["halt_days_excluded"], ["20260708"])
        self.assertIsNone(result["rule"]["first_review_date_adjusted"])

    def test_tenth_actual_trading_day_can_forecast_release(self):
        result = ar.evaluate_release(
            daily_fixture(), 100, rule(), as_of_date="20260715")
        self.assertTrue(result["value"])
        self.assertEqual(result["reason"], "all_release_conditions_met")
        self.assertEqual(result["checks"]["elapsed_days"], 10)
        self.assertEqual(result["rule"]["first_review_date_adjusted"], "20260715")
        self.assertTrue(result["checks"]["five_day_ok"])
        self.assertTrue(result["checks"]["fifteen_day_ok"])
        self.assertTrue(result["checks"]["not_recent_high_ok"])

    def test_exact_five_day_threshold_is_not_release(self):
        result = ar.evaluate_release(
            daily_fixture(), 160, rule(), as_of_date="20260715")
        self.assertFalse(result["value"])
        self.assertFalse(result["checks"]["five_day_ok"])

    def test_exact_fifteen_day_threshold_is_not_release(self):
        bars = daily_fixture()
        traded = [bar for bar in bars if bar["volume"] > 0]
        traded[-16]["close"] = 50.0
        result = ar.evaluate_release(
            bars, 100, rule(), as_of_date="20260715")
        self.assertFalse(result["value"])
        self.assertFalse(result["checks"]["fifteen_day_ok"])

    def test_equal_recent_high_is_not_release(self):
        bars = daily_fixture()
        for bar in bars[-15:]:
            bar["close"] = 90.0
        bars[-2]["close"] = 100.0
        result = ar.evaluate_release(
            bars, 100, rule(), as_of_date="20260715")
        self.assertFalse(result["value"])
        self.assertFalse(result["checks"]["not_recent_high_ok"])

    def test_consecutive_halts_are_excluded(self):
        bars = daily_fixture()
        for bar in bars:
            if bar["date"] in ("20260707", "20260708"):
                bar["volume"] = 0.0
        result = ar.evaluate_release(
            bars, 100, rule(), as_of_date="20260715")
        self.assertFalse(result["value"])
        self.assertEqual(result["reason"], "elapsed_not_met")
        self.assertEqual(result["checks"]["elapsed_days"], 9)
        self.assertEqual(result["checks"]["halt_days_excluded"], ["20260707", "20260708"])

    def test_t_minus_15_is_independent_of_recent_high_window(self):
        bars = [
            {"date": f"202606{i:02d}", "close": float(i), "volume": 1000.0}
            for i in range(1, 21)
        ]
        custom_rule = rule()
        custom_rule["designation_date"] = "20260601"
        custom_rule["min_elapsed_days"] = 1
        custom_rule["recent_high_window"] = 10
        result = ar.evaluate_release(
            bars, 10, custom_rule, as_of_date="20260620")
        self.assertEqual(result["checks"]["t_minus_5_close"], 15.0)
        self.assertEqual(result["checks"]["t_minus_15_close"], 5.0)

    def test_current_day_halt_is_unknown_not_true(self):
        bars = [b for b in daily_fixture() if b["date"] <= "20260708"]
        result = ar.evaluate_release(
            bars, 100, rule(), as_of_date="20260708")
        self.assertIsNone(result["value"])
        self.assertEqual(result["reason"], "current_day_halted")

    def test_missing_volume_is_unknown_not_true(self):
        bars = copy.deepcopy(daily_fixture())
        bars[-1].pop("volume")
        result = ar.evaluate_release(
            bars, 100, rule(), as_of_date="20260715")
        self.assertIsNone(result["value"])
        self.assertEqual(result["reason"], "volume_missing")

    def test_invalid_rule_is_unknown_not_true(self):
        bad_rule = rule()
        bad_rule.pop("threshold_15d_pct")
        result = ar.evaluate_release(
            daily_fixture(), 100, bad_rule, as_of_date="20260715")
        self.assertIsNone(result["value"])
        self.assertEqual(result["reason"], "rule_invalid")

    def test_trade_date_mismatch_is_unknown_not_true(self):
        bars = [b for b in daily_fixture() if b["date"] < "20260715"]
        result = ar.evaluate_release(
            bars, 100, rule(), as_of_date="20260715")
        self.assertIsNone(result["value"])
        self.assertEqual(result["reason"], "trade_date_mismatch")

    def test_legacy_elapsed_counter_also_excludes_halt(self):
        bars = [b for b in daily_fixture() if b["date"] <= "20260713"]
        self.assertEqual(ar.elapsed_trading_days(bars, "20260630"), 8)

    def test_unparsed_rule_never_promotes(self):
        result = ar.evaluate_release(
            daily_fixture(), 100, {"parse_status": "unavailable"},
            as_of_date="20260715",
        )
        self.assertIsNone(result["value"])
        self.assertEqual(result["reason"], "rule_unavailable")


if __name__ == "__main__":
    unittest.main()
