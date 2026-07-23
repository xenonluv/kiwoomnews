#!/usr/bin/env python3
"""Independent regression tests for radar_json_store (no broker/API access)."""

import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import radar_json_store as store
from rank_policy import RANK_MODEL_VERSION, policy_metadata


DAY = "20260713"
STAMP = "2026-07-13 09:03:12 KST"


class RadarJsonStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "radar_raw"

    def tearDown(self):
        self.temp.cleanup()

    def read_json(self, path):
        with open(path, "r", encoding="utf-8") as source:
            return json.load(source)

    def sample_scan(self, code="263800"):
        return {
            "run": {
                "trade_date": DAY,
                "generated_at": STAMP,
                "rank_model_version": RANK_MODEL_VERSION,
                "scan_ok": True,
            },
            "universe": {"scope": "observed_union_not_full_market"},
            "observations": [{"code": code, "gate_decisions": []}],
            "published_candidates": [],
            "errors": [],
        }

    def test_initialize_schema_and_immutable_model(self):
        current = policy_metadata()
        self.assertEqual(store.DEFAULT_MODEL["model_version"], current["rank_model_version"])
        self.assertEqual(store.DEFAULT_MODEL["policy_name"], current["rank_policy_name"])
        self.assertEqual(store.DEFAULT_MODEL["source_commit"], current["rank_model_source_commit"])
        self.assertEqual(store.DEFAULT_MODEL["effective_from"], current["rank_model_effective_from"])
        self.assertEqual(store.DEFAULT_MODEL["effective_at"], current["rank_model_effective_at"])
        result = store.initialize_store(self.root)
        self.assertTrue(result.ok, result.error)
        schema = self.read_json(self.root / "schema.json")
        model = self.read_json(self.root / "models" / f"{RANK_MODEL_VERSION}.json")
        self.assertEqual(schema["authority"], "local_raw_json")
        self.assertTrue(schema["immutable_scan_files"])
        self.assertEqual(model["prior"]["strength"], "medium")
        self.assertTrue(store.verify_payload_integrity(schema))
        self.assertTrue(store.verify_payload_integrity(model))

        conflict = dict(store.DEFAULT_MODEL)
        conflict["policy_name"] = "changed-without-version"
        refused = store.write_model(conflict, self.root)
        self.assertFalse(refused.ok)
        self.assertIn("conflicts", refused.error)

    def test_scan_unique_immutable_and_manifest_rebuild(self):
        first = store.write_scan(
            self.sample_scan("263800"), root=self.root, run_id="fixed-run", observed_at=STAMP
        )
        second = store.write_scan(
            self.sample_scan("079650"), root=self.root, run_id="fixed-run", observed_at=STAMP
        )
        self.assertTrue(first.ok, first.error)
        self.assertTrue(second.ok, second.error)
        self.assertTrue((self.root / "schema.json").is_file())
        self.assertTrue((self.root / "models" / f"{RANK_MODEL_VERSION}.json").is_file())
        self.assertNotEqual(first.path, second.path)
        self.assertTrue(Path(first.path).name.startswith("scan_090312_"))
        original = Path(first.path).read_bytes()

        refused = store.atomic_write_json(first.path, {"replacement": True}, overwrite=False)
        self.assertFalse(refused.ok)
        self.assertEqual(Path(first.path).read_bytes(), original)
        forced = store.atomic_write_json(first.path, {"replacement": True}, overwrite=True)
        self.assertFalse(forced.ok)
        self.assertEqual(Path(first.path).read_bytes(), original)

        manifest_path = store.local_day_dir(DAY, self.root) / "manifest.json"
        manifest = self.read_json(manifest_path)
        self.assertEqual(manifest["counts"]["scan_runs"], 2)
        self.assertEqual(manifest["counts"]["observations"], 2)
        self.assertTrue(store.verify_manifest(DAY, self.root).ok)

        manifest_path.unlink()
        rebuilt = store.rebuild_manifest(DAY, self.root)
        self.assertTrue(rebuilt.ok, rebuilt.error)
        self.assertEqual(self.read_json(manifest_path)["counts"]["scan_runs"], 2)

    def test_concurrent_same_run_id_keeps_every_scan(self):
        def save(index):
            return store.write_scan(
                self.sample_scan(str(index)),
                root=self.root,
                run_id="concurrent-run",
                observed_at=STAMP,
            )

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(save, range(6)))
        self.assertTrue(all(result.ok for result in results), [result.error for result in results])
        paths = {result.path for result in results}
        self.assertEqual(len(paths), 6)
        manifest = self.read_json(store.local_day_dir(DAY, self.root) / "manifest.json")
        self.assertEqual(manifest["counts"]["scan_runs"], 6)
        self.assertEqual(manifest["counts"]["observations"], 6)

    def test_manifest_detects_file_checksum_change(self):
        scan = store.write_scan(self.sample_scan(), root=self.root, run_id="checksum", observed_at=STAMP)
        self.assertTrue(scan.ok, scan.error)
        self.assertTrue(store.verify_manifest(DAY, self.root).ok)
        with open(scan.path, "ab") as output:
            output.write(b"\n")
            output.flush()
            os.fsync(output.fileno())
        checked = store.verify_manifest(DAY, self.root)
        self.assertFalse(checked.ok)
        self.assertIn("FILE_CHECKSUM_MISMATCH", {item["code"] for item in checked.value["issues"]})
        rebuilt = store.rebuild_manifest(DAY, self.root)
        self.assertTrue(rebuilt.ok, rebuilt.error)
        self.assertTrue(store.verify_manifest(DAY, self.root).ok)

        unmanifested_path = store.local_day_dir(DAY, self.root) / "summaries" / "untracked.json"
        unmanifested = store.atomic_write_json(unmanifested_path, {"record_type": "test"})
        self.assertTrue(unmanifested.ok, unmanifested.error)
        checked = store.verify_manifest(DAY, self.root)
        self.assertFalse(checked.ok)
        self.assertIn("UNMANIFESTED_FILE", {item["code"] for item in checked.value["issues"]})

        self.assertTrue(store.rebuild_manifest(DAY, self.root).ok)
        damaged = self.read_json(unmanifested_path)
        damaged["changed_without_new_payload_checksum"] = True
        with open(unmanifested_path, "w", encoding="utf-8") as output:
            json.dump(damaged, output, ensure_ascii=False)
        self.assertTrue(store.rebuild_manifest(DAY, self.root).ok)
        checked = store.verify_manifest(DAY, self.root)
        self.assertFalse(checked.ok)
        self.assertIn("PAYLOAD_CHECKSUM_MISMATCH", {item["code"] for item in checked.value["issues"]})

    def test_failed_atomic_write_preserves_existing_file(self):
        target = self.root / "atomic.json"
        good = store.atomic_write_json(target, {"value": 1})
        self.assertTrue(good.ok, good.error)
        original = target.read_bytes()
        failed = store.atomic_write_json(target, {"not_json": object()})
        self.assertFalse(failed.ok)
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(list(target.parent.glob("*.tmp")), [])
        self.assertFalse(store.atomic_write_json(None, {"x": 1}).ok)

    def test_secret_redaction_is_recursive_and_non_mutating(self):
        target = self.root / "redaction.json"
        payload = {
            "api_key": "api-secret-value",
            "api_secret": "second-secret-value",
            "account_number": "1234567890",
            "headers": {"Authorization": "Bearer bearer-secret-value"},
            "url": "https://example.test/x?token=query-secret-value&ok=1",
            "message": "account_no=9988776655",
            "safe": "keep-me",
        }
        result = store.atomic_write_json(target, payload)
        self.assertTrue(result.ok, result.error)
        raw = target.read_text(encoding="utf-8")
        for secret in (
            "api-secret-value",
            "second-secret-value",
            "1234567890",
            "bearer-secret-value",
            "query-secret-value",
            "9988776655",
        ):
            self.assertNotIn(secret, raw)
        saved = self.read_json(target)
        self.assertEqual(saved["safe"], "keep-me")
        self.assertEqual(saved["api_key"], "[REDACTED]")
        self.assertEqual(payload["api_key"], "api-secret-value")

    def test_secret_redaction_covers_kiwoom_secretkey_and_token_suffix(self):
        """적대 리뷰 2026-07-11 M1 — 키움 실필드명 secretkey·범용 *token 키·쿼리 secretkey= 커버."""
        target = self.root / "redaction_kiwoom.json"
        payload = {
            # kiwoom_client.py 토큰 요청 body 실형태
            "token_request_body": {"appkey": "KWAPP123", "secretkey": "KWSECRET456"},
            "secret_key": "UNDERSCORE-SECRET",
            "kiwoom_token": "KWTOKEN789",
            "bearer_token": "BEARER000",
            "url": "https://api.test/oauth?appkey=QAPP&secretkey=QSECRET&x=1",
            "url2": "https://api.test/oauth?secret_key=QSECRET2&x=1",
            "safe_field": "turnover_pct=245",
        }
        result = store.atomic_write_json(target, payload)
        self.assertTrue(result.ok, result.error)
        raw = target.read_text(encoding="utf-8")
        for secret in ("KWAPP123", "KWSECRET456", "UNDERSCORE-SECRET",
                       "KWTOKEN789", "BEARER000", "QSECRET2"):
            self.assertNotIn(secret, raw, f"leaked: {secret}")
        self.assertNotIn("secretkey=QSECRET", raw)
        saved = self.read_json(target)
        self.assertEqual(saved["safe_field"], "turnover_pct=245")

    def test_trade_event_and_decision_skip_manifest_when_disabled(self):
        """적대 리뷰 M3 — update_manifest=False면 당일 전체 재빌드를 생략(주문 경로 지연 제거)."""
        event = {"trade_date": DAY, "type": "order_outcome", "code": "000001"}
        result = store.append_trade_event("decisions", event, trade_date=DAY,
                                          root=self.root, update_manifest=False)
        self.assertTrue(result.ok, result.error)
        snap = store.write_decision_snapshot(
            "KRX_TEST", {"trade_date": DAY, "slot": "KRX_TEST"},
            trade_date=DAY, root=self.root, overwrite=False, update_manifest=False)
        self.assertTrue(snap.ok, snap.error)
        manifest_path = store.local_day_dir(DAY, self.root) / "manifest.json"
        before = self.read_json(manifest_path) if manifest_path.exists() else {"files": []}
        indexed = {f["path"] for f in before.get("files", [])}
        self.assertNotIn("trades/decisions.jsonl", indexed)
        self.assertNotIn("decisions/krx_test.json", indexed)
        # 이후 명시적 재빌드 1회로 전부 색인된다(publish 회차 패턴).
        rebuilt = store.rebuild_manifest(DAY, self.root)
        self.assertTrue(rebuilt.ok, rebuilt.error)
        after = self.read_json(manifest_path)
        indexed = {f["path"] for f in after.get("files", [])}
        self.assertIn("trades/decisions.jsonl", indexed)
        self.assertIn("decisions/krx_test.json", indexed)

    def test_minute_merge_deduplicates_and_keeps_complete_fields(self):
        first = store.merge_minute_bars(
            DAY,
            "263800",
            [
                {"time": "090000", "open": 100, "high": None, "volume": 10},
                {"time": "090100", "open": 105, "close": 106, "volume": 20},
            ],
            source_broker="kiwoom",
            fetched_at="2026-07-13 09:02:00 KST",
            root=self.root,
        )
        self.assertTrue(first.ok, first.error)
        second = store.merge_minute_bars(
            DAY,
            "263800",
            [
                {"time": "090000", "high": 110, "close": 108, "volume": 15},
                {"time": "090200", "open": 108, "close": 109, "volume": 30},
            ],
            fetched_at="2026-07-13 09:03:00 KST",
            root=self.root,
        )
        self.assertTrue(second.ok, second.error)
        minute = self.read_json(second.path)
        self.assertEqual([bar["time"] for bar in minute["bars"]], ["090000", "090100", "090200"])
        self.assertEqual(minute["bars"][0]["open"], 100)
        self.assertEqual(minute["bars"][0]["high"], 110)
        self.assertEqual(minute["bars"][0]["volume"], 15)
        self.assertTrue(store.verify_payload_integrity(minute))

        before_failure = Path(second.path).read_bytes()
        failed = store.merge_minute_bars(
            DAY,
            "263800",
            [{"time": "090300", "close": object()}],
            root=self.root,
        )
        self.assertFalse(failed.ok)
        self.assertEqual(Path(second.path).read_bytes(), before_failure)

    def test_minute_empty_and_api_error_are_distinct_in_manifest(self):
        empty = store.merge_minute_bars(
            DAY, "263800", [], fetch_status="ok", fetched_at="empty-at", root=self.root
        )
        failed = store.merge_minute_bars(
            DAY,
            "263800",
            [],
            fetch_status="error",
            error="upstream timeout",
            fetched_at="error-at",
            root=self.root,
        )
        self.assertTrue(empty.ok, empty.error)
        self.assertTrue(failed.ok, failed.error)
        manifest = self.read_json(store.local_day_dir(DAY, self.root) / "manifest.json")
        codes = {item["code"] for item in manifest["errors"]}
        self.assertIn("MINUTE_FETCH_EMPTY", codes)
        self.assertIn("MINUTE_FETCH_ERROR", codes)

    def test_minute_strict_conflict_is_atomic_and_default_merge_is_unchanged(self):
        initial = store.merge_minute_bars(
            DAY,
            "263800",
            [{"time": "090000", "open": 100, "high": 110, "low": 95,
              "close": 105, "volume": 10}],
            market_basis="J",
            root=self.root,
            update_manifest=False,
        )
        self.assertTrue(initial.ok, initial.error)
        original = Path(initial.path).read_bytes()

        refused = store.merge_minute_bars(
            DAY,
            "263800",
            [{"time": "090000", "open": 100.0, "high": 111, "low": 95,
              "close": 105, "vol": 10.0},
             {"time": "090100", "open": 105, "high": 106, "low": 104,
              "close": 106, "vol": 20}],
            market_basis="J",
            root=self.root,
            update_manifest=False,
            conflict_policy="error",
        )
        self.assertFalse(refused.ok)
        self.assertIn("minute bar conflict at 090000 fields=high", refused.error)
        self.assertEqual(Path(initial.path).read_bytes(), original)

        # The default remains the historical later-wins behavior.
        merged = store.merge_minute_bars(
            DAY,
            "263800",
            [{"time": "090000", "high": 111}],
            market_basis="J",
            root=self.root,
            update_manifest=False,
        )
        self.assertTrue(merged.ok, merged.error)
        self.assertEqual(self.read_json(merged.path)["bars"][0]["high"], 111)

    def test_minute_strict_allows_equal_alias_and_new_bars(self):
        initial = store.merge_minute_bars(
            DAY,
            "263800",
            [{"time": "090000", "open": 100, "high": 110, "low": 95,
              "close": 105, "volume": 10}],
            market_basis="J",
            root=self.root,
            update_manifest=False,
        )
        self.assertTrue(initial.ok, initial.error)
        merged = store.merge_minute_bars(
            DAY,
            "263800",
            [{"time": "090000", "open": 100.0, "high": 110.0, "low": 95.0,
              "close": 105.0, "vol": 10.0},
             {"time": "090100", "open": 105, "high": 108, "low": 104,
              "close": 107, "vol": 20}],
            market_basis="J",
            root=self.root,
            update_manifest=False,
            conflict_policy="error",
        )
        self.assertTrue(merged.ok, merged.error)
        saved = self.read_json(merged.path)
        self.assertEqual([bar["time"] for bar in saved["bars"]], ["090000", "090100"])
        self.assertTrue(all("vol" in bar and "volume" not in bar for bar in saved["bars"]))

    def test_minute_strict_rejects_conflicting_duplicate_in_one_response(self):
        refused = store.merge_minute_bars(
            DAY,
            "263800",
            [{"time": "090000", "open": 100, "high": 110, "low": 95,
              "close": 105, "vol": 10},
             {"time": "090000", "open": 100, "high": 110, "low": 95,
              "close": 104, "vol": 10}],
            market_basis="J",
            root=self.root,
            update_manifest=False,
            conflict_policy="error",
        )
        self.assertFalse(refused.ok)
        self.assertIn("minute bar conflict at 090000 fields=close", refused.error)
        expected = store.local_day_dir(DAY, self.root) / "minute" / "263800_J.json"
        self.assertFalse(expected.exists())

    def test_minute_strict_concurrent_conflicts_are_serialized(self):
        initial = store.merge_minute_bars(
            DAY,
            "263800",
            [{"time": "090000", "open": 100, "high": 100, "low": 100,
              "close": 100, "vol": 10}],
            market_basis="J",
            root=self.root,
            update_manifest=False,
            conflict_policy="error",
        )
        self.assertTrue(initial.ok, initial.error)

        def writer(close):
            return store.merge_minute_bars(
                DAY,
                "263800",
                [{"time": "090100", "open": 100, "high": 110, "low": 99,
                  "close": close, "vol": 20}],
                market_basis="J",
                root=self.root,
                update_manifest=False,
                conflict_policy="error",
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(writer, (105, 106)))
        self.assertEqual(sum(result.ok for result in results), 1)
        self.assertEqual(sum("minute bar conflict" in str(result.error) for result in results), 1)
        saved = self.read_json(initial.path)
        self.assertTrue(store.verify_payload_integrity(saved))
        self.assertIn(saved["bars"][1]["close"], (105, 106))

    def test_decision_first_success_is_immutable(self):
        first = store.write_decision_snapshot(
            "KRX_1518", {"trade_date": DAY, "source_run_id": "before-cutoff"}, root=self.root
        )
        late = store.write_decision_snapshot(
            "KRX_1518", {"trade_date": DAY, "source_run_id": "after-cutoff"}, root=self.root
        )
        self.assertTrue(first.ok, first.error)
        self.assertFalse(late.ok)
        saved = self.read_json(first.path)
        self.assertEqual(saved["source_run_id"], "before-cutoff")

    def test_jsonl_append_repairs_only_corrupt_trailing_line(self):
        target = store.local_day_dir(DAY, self.root) / "trades" / "events.jsonl"
        first = store.append_jsonl(target, {"record_type": "trade_event", "seq": 1})
        self.assertTrue(first.ok, first.error)
        with open(target, "ab") as output:
            output.write(b'{"broken":')
            output.flush()
            os.fsync(output.fileno())

        second = store.append_jsonl(
            target,
            {"record_type": "trade_event", "seq": 2, "Authorization": "Bearer hidden-token"},
        )
        self.assertTrue(second.ok, second.error)
        self.assertTrue(second.warnings)
        read = store.read_jsonl(target)
        self.assertTrue(read.ok, read.error)
        self.assertEqual([item["seq"] for item in read.value["records"]], [1, 2])
        self.assertNotIn("hidden-token", target.read_text(encoding="utf-8"))
        report = Path(second.value["corruption_report"])
        self.assertTrue(report.is_file())
        report_text = report.read_text(encoding="utf-8")
        self.assertIn("raw_sha256", report_text)
        self.assertNotIn('{"broken":', report_text)

    def test_jsonl_refuses_non_trailing_corruption(self):
        target = self.root / "middle.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('{"ok":1}\n{broken}\n{"ok":2}\n', encoding="utf-8")
        original = target.read_bytes()
        result = store.append_jsonl(target, {"ok": 3})
        self.assertFalse(result.ok)
        self.assertEqual(target.read_bytes(), original)

    def test_jsonl_failed_atomic_tail_repair_preserves_original(self):
        target = self.root / "repair.jsonl"
        first = store.append_jsonl(target, {"seq": 1})
        self.assertTrue(first.ok, first.error)
        with open(target, "ab") as output:
            output.write(b"{broken-tail")
        original = target.read_bytes()
        with mock.patch.object(store.os, "replace", side_effect=OSError("simulated replace failure")):
            failed = store.append_jsonl(target, {"seq": 2})
        self.assertFalse(failed.ok)
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(list(target.parent.glob("*.tmp")), [])

        recovered = store.append_jsonl(target, {"seq": 2})
        self.assertTrue(recovered.ok, recovered.error)
        records = store.read_jsonl(target).value["records"]
        self.assertEqual([record["seq"] for record in records], [1, 2])

    def test_evaluation_uses_signal_day_and_updates_manifest(self):
        result = store.write_evaluation(
            {"signal_date": DAY, "results": [{"code": "263800", "next_high_pct": 12.3}]},
            root=self.root,
        )
        self.assertTrue(result.ok, result.error)
        self.assertEqual(Path(result.path).name, "next_day.json")
        manifest = self.read_json(store.local_day_dir(DAY, self.root) / "manifest.json")
        self.assertEqual(manifest["counts"]["evaluation_files"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
