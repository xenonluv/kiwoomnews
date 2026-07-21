#!/usr/bin/env python3
import copy
import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from team2_relevance import KST, material_freshness_view, parse_evidence_datetime
import publish
from publish import refresh_material_freshness


class MaterialFreshnessTest(unittest.TestCase):
    def test_parse_and_paseco_age(self):
        self.assertIsNotNone(parse_evidence_datetime("202607130926"))
        self.assertEqual(parse_evidence_datetime("2026-07-13T00:26:00Z").hour, 9)
        material = {"freshness_days": 0.02, "grade": "B", "evidence": [
            {"datetime": "2026-07-13 09:26:00 KST", "title": "재료"}]}
        original = copy.deepcopy(material)
        view = material_freshness_view(
            material, as_of=datetime(2026, 7, 20, 15, 30, tzinfo=KST))
        self.assertAlmostEqual(view["freshness_days"], 7.25, places=2)
        self.assertEqual(view["captured_freshness_days"], 0.02)
        self.assertEqual(view["grade"], "B")
        self.assertEqual(material, original)

    def test_statuses_and_idempotence(self):
        as_of = datetime(2026, 7, 20, 15, 30, tzinfo=KST)
        complete = material_freshness_view(
            {"freshness_days": 1, "evidence": [{"datetime": "202607191530"}]}, as_of=as_of)
        partial = material_freshness_view(
            {"evidence": [{"datetime": "202607191530"}, {"datetime": "bad"}]}, as_of=as_of)
        missing = material_freshness_view({"evidence": [{"datetime": "bad"}]}, as_of=as_of)
        future = material_freshness_view(
            {"evidence": [{"datetime": "202607211530"}]}, as_of=as_of)
        self.assertEqual(complete["freshness_parse_status"], "complete")
        self.assertEqual(partial["freshness_parse_status"], "partial")
        self.assertEqual(missing["freshness_parse_status"], "missing")
        self.assertEqual(future["freshness_parse_status"], "future_only")
        again = material_freshness_view(complete, as_of=as_of)
        self.assertEqual(again["captured_freshness_days"], 1)

    def test_publish_updates_both_arrays_without_rank_change(self):
        out = {"generated_at": "2026-07-20 15:30:00 KST", "suspects": [
            {"code": "1", "rank_bucket": 3, "material": {"evidence": [{"datetime": "202607191530"}]}}],
            "blocked_suspects": [{"code": "2", "material": {"evidence": [{"datetime": "202607181530"}]}}]}
        refresh_material_freshness(out)
        self.assertEqual(out["suspects"][0]["material"]["freshness_days"], 1.0)
        self.assertEqual(out["blocked_suspects"][0]["material"]["freshness_days"], 2.0)
        self.assertEqual(out["suspects"][0]["rank_bucket"], 3)

    def test_historical_republish_preserves_signal_snapshots_and_evaluation(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(publish, "HISTORY_DIR", tmp):
            row = {"code": "000001", "name": "테스트", "price": 1000,
                   "suspicion_score": 80, "signal_date": "20260720",
                   "material": {"freshness_days": 1},
                   "market_alert_snapshot": {"level": "NONE"}}
            first = {"generated_at": "2026-07-20 15:30:00 KST", "suspects": [row]}
            with mock.patch.object(publish, "history_date_for", return_value="20260720"):
                publish.record_history(first)
            path = os.path.join(tmp, "20260720.json")
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
            saved["suspects"]["000001"].update({
                "evaluated": True, "evaluation_status": "EXCLUDED_UNTRADABLE",
                "evaluation_exclusion": {"reason_code": "HALT_PLACEHOLDER"}})
            with open(path, "w", encoding="utf-8") as f:
                json.dump(saved, f)
            later = copy.deepcopy(first)
            later["generated_at"] = "2026-07-21 08:30:00 KST"
            later["suspects"][0]["material"] = {"freshness_days": 2}
            later["suspects"][0]["market_alert_snapshot"] = {"level": "WARNING"}
            with mock.patch.object(publish, "history_date_for", return_value="20260720"):
                publish.record_history(later)
            with open(path, encoding="utf-8") as f:
                final = json.load(f)["suspects"]["000001"]
            self.assertEqual(final["material"]["freshness_days"], 1)
            self.assertEqual(final["market_alert_snapshot"]["level"], "NONE")
            self.assertEqual(final["evaluation_status"], "EXCLUDED_UNTRADABLE")
            self.assertEqual(final["evaluation_exclusion"]["reason_code"], "HALT_PLACEHOLDER")


if __name__ == "__main__":
    unittest.main()
