#!/usr/bin/env python3
"""prior/retro/forward 분리와 상대순위 집계 회귀 테스트."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import radar_backtest as rb  # noqa: E402


def sample(code, date=None, bucket=4, high=10.0, rank=1, **extra):
    date = date or rb.FORWARD_EFFECTIVE_FROM
    row = {
        "date": date,
        "signal_date": date,
        "code": code,
        "name": code,
        "rank_model_version": rb.FORWARD_MODEL_VERSION,
        "rank_model_effective_from": rb.FORWARD_EFFECTIVE_FROM,
        "rank_bucket_at_signal": bucket,
        "evaluated_entry": 1000,
        "evaluated_entry_basis": "KRX_CLOSE",
        "next_high_pct": high,
        "next_high_pct_raw": high,
        "touch7": high >= 7,
        "touch13": high >= 13,
        "return_pct": high / 2,
        "eod_present": True,
        "eod_rank": rank,
        "final": True,
        "final_recorded": True,
        "latest_published_rank": rank,
        "shadow_bucket_at_signal": [],
    }
    row.update(extra)
    return row


class RankForwardTest(unittest.TestCase):
    def test_prior_is_bucket_specific_not_mislabeled_as_one_source(self):
        prior = rb.rank_prior()
        self.assertEqual(prior["source"], "bucket_specific")
        self.assertEqual(prior["strength"], "mixed")
        buckets = {row["bucket"]: row for row in prior["buckets"]}
        self.assertEqual(buckets[0]["source"], "live_final_and_rank4_v1_eod_20260723")
        self.assertEqual(buckets[4]["strength"], "observe")

    def test_retro_reclassifies_but_forward_uses_saved_bucket(self):
        original = rb.rank_bucket_info
        rb.rank_bucket_info = lambda _: {"rank_bucket": 2, "shadow_bucket": []}
        try:
            rows = [sample("A", bucket=9)]
            retro = rb.rank_bucket_stats_retro(rows)["exclusive_eod"]
            forward = rb.rank_bucket_stats_forward(rows)["eod"]
        finally:
            rb.rank_bucket_info = original

        self.assertEqual(next(c for c in retro["cells"] if c["bucket"] == 2)["n"], 1)
        saved = next(c for c in forward["cells"] if c["bucket"] == 9)
        self.assertEqual(saved["n"], 1)
        self.assertEqual(saved["touch7_rate"], 100.0)
        self.assertFalse(saved["valid"])

    def test_mixed_deployment_and_legacy_are_excluded(self):
        mixed = sample("MIXED", date="20260710")
        legacy = sample("LEGACY")
        legacy["rank_model_version"] = None
        missing_bucket = sample("NO_BUCKET")
        missing_bucket["rank_bucket_at_signal"] = None
        forward = rb.rank_bucket_stats_forward([mixed, legacy, missing_bucket])
        self.assertEqual(forward["eod"]["sample_n"], 0)

    def test_bucket_zero_is_a_valid_saved_forward_bucket(self):
        forward = rb.rank_bucket_stats_forward([sample("B0", bucket=0)])
        cell = next(c for c in forward["eod"]["cells"] if c["bucket"] == 0)
        self.assertEqual(cell["n"], 1)

    def test_missing_population_field_is_not_inferred(self):
        row = sample("A")
        self.assertEqual(rb.rank_bucket_stats_forward([row])["krx_decision"]["sample_n"], 0)
        row["krx_decision_present"] = True
        row["krx_decision_rank"] = 1
        self.assertEqual(rb.rank_bucket_stats_forward([row])["krx_decision"]["sample_n"], 1)

    def test_only_forward_kill_switch_is_actionable(self):
        row = sample("B1", bucket=1, high=5.0)
        forward = rb.rank_bucket_stats_forward([row])
        bucket1 = next(k for k in forward["kill_switches"] if k["key"] == "bucket1")
        self.assertTrue(bucket1["actionable"])
        self.assertEqual(bucket1["status"], "하향상신")

        legacy = rb.rank_bucket_stats([row])
        self.assertTrue(all(not k["actionable"] for k in legacy["kill_switches"]))


class RankEvalTest(unittest.TestCase):
    def test_each_model_version_keeps_its_own_effective_window(self):
        current = sample("V3")
        replaced = sample(
            "V2",
            date="20260724",
            rank_model_version="rank4-v2",
            rank_model_effective_from="20260724",
        )
        legacy = sample(
            "V1",
            date="20260713",
            rank_model_version="rank4-v1",
            rank_model_effective_from="20260713",
        )
        evaluated = rb.rank_eval([legacy, replaced, current])["by_model_version"]
        self.assertEqual(
            set(evaluated),
            {"rank4-v1", "rank4-v2", rb.FORWARD_MODEL_VERSION},
        )
        # 현행 actionable forward 표에는 v3만 들어간다.
        self.assertEqual(
            rb.rank_bucket_stats_forward([legacy, replaced, current])["eod"]["sample_n"],
            1,
        )

    def test_singleton_is_separate_from_topk_denominator(self):
        rows = [
            sample("A", high=12.0, rank=1),
            sample("B", high=5.0, rank=2),
            sample("C", date="20260727", high=8.0, rank=1),
        ]
        stats = rb.rank_eval(rows)["by_model_version"][rb.FORWARD_MODEL_VERSION]["populations"]["eod"]
        self.assertEqual(stats["multi_candidate_days"], 1)
        self.assertEqual(stats["single_candidate"]["days"], 1)
        self.assertEqual(stats["top1_n"], 1)
        self.assertEqual(stats["top1_hit"], 100.0)
        self.assertEqual(stats["top3_contains_winner"], 100.0)
        self.assertEqual(stats["spearman"], 1.0)
        self.assertEqual(stats["ndcg"], 1.0)

    def test_actual_high_tie_has_deterministic_shared_winner(self):
        rows = [sample("A", high=10.0, rank=1), sample("B", high=10.0, rank=2)]
        stats = rb.rank_eval(rows)["by_model_version"][rb.FORWARD_MODEL_VERSION]["populations"]["eod"]
        self.assertEqual(stats["top1_hit"], 100.0)
        self.assertEqual(stats["winner_published_rank"], [{"rank": 1, "days": 1}])


class DecisionLoadTest(unittest.TestCase):
    def test_decision_file_is_explicit_population_ssot(self):
        old_root = rb.LOCAL_RADAR_ROOT
        with tempfile.TemporaryDirectory() as root:
            rb.LOCAL_RADAR_ROOT = root
            path = os.path.join(root, "2026", "07", "13", "decisions")
            os.makedirs(path)
            with open(os.path.join(path, "krx_1518.json"), "w", encoding="utf-8") as f:
                json.dump({
                    "trade_date": "20260713",
                    "rank_model_version": "rank4-v1",
                    "ordered_candidates": [
                        {"code": "A", "published_rank": 2, "rank_bucket": 4},
                    ],
                }, f)
            try:
                data = rb.load_decision_memberships("20260713")["krx_decision"]
            finally:
                rb.LOCAL_RADAR_ROOT = old_root
        self.assertTrue(data["recorded"])
        self.assertEqual(data["rows"]["A"]["_decision_rank"], 2)

    def test_stale_decision_does_not_create_false_dropout_population(self):
        old_root = rb.LOCAL_RADAR_ROOT
        with tempfile.TemporaryDirectory() as root:
            rb.LOCAL_RADAR_ROOT = root
            path = os.path.join(root, "2026", "07", "13", "decisions")
            os.makedirs(path)
            with open(os.path.join(path, "operational_eod.json"), "w", encoding="utf-8") as f:
                json.dump({
                    "trade_date": "20260713",
                    "valid_for_decision": False,
                    "ordered_candidates": [{"code": "A", "published_rank": 1}],
                }, f)
            try:
                data = rb.load_decision_memberships("20260713")["eod"]
            finally:
                rb.LOCAL_RADAR_ROOT = old_root
        self.assertTrue(data["file_recorded"])
        self.assertFalse(data["recorded"])
        self.assertEqual(data["rows"], {})


class EvaluationStoreTest(unittest.TestCase):
    def test_forward_evaluation_is_written_as_local_json(self):
        env_key = "RADAR_JSON_STORE_ROOT"
        old_root = os.environ.get(env_key)
        with tempfile.TemporaryDirectory() as root:
            os.environ[env_key] = root
            try:
                rb.write_evaluation_json([sample("A", high=9.0, rank=1)])
            finally:
                if old_root is None:
                    os.environ.pop(env_key, None)
                else:
                    os.environ[env_key] = old_root
            date = rb.FORWARD_EFFECTIVE_FROM
            path = os.path.join(
                root, date[:4], date[4:6], date[6:8], "evaluation", "next_day.json"
            )
            self.assertTrue(os.path.exists(path))
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
            self.assertEqual(payload["record_type"], "next_day_evaluation")
            self.assertEqual(payload["results"][0]["rank_bucket_at_signal"], 4)
            self.assertEqual(payload["results"][0]["entry_price"], 1000)
            self.assertEqual(payload["results"][0]["populations"]["eod"]["rank_bucket"], 4)
            self.assertTrue(os.path.exists(os.path.join(
                root, date[:4], date[4:6], date[6:8], "manifest.json"
            )))


if __name__ == "__main__":
    unittest.main()
