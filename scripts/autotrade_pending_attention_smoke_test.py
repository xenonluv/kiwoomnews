#!/usr/bin/env python3
import os
import subprocess
import unittest
from contextlib import contextmanager
from unittest import mock

import autotrade_pending_attention as attention


class PendingAttentionJobTest(unittest.TestCase):
    def test_job_only_reviews_local_pending_without_broker_calls(self):
        data = {"positions": [], "pending_entries": [{"state": "SUBMIT_UNKNOWN"}]}

        @contextmanager
        def acquired():
            yield True

        with mock.patch.object(attention.ac, "acquire_execution_lock", return_value=acquired()), \
                mock.patch.object(attention.ac, "autotrade_enabled", return_value=True), \
                mock.patch.object(attention.ac, "load_positions", return_value=data), \
                mock.patch.object(attention.autotrade_orders, "review_pending_attention") as review:
            self.assertTrue(attention.run())
        review.assert_called_once()

    def test_web_off_skips_pending_load_and_notification(self):
        @contextmanager
        def acquired():
            yield True

        with mock.patch.object(attention.ac, "acquire_execution_lock", return_value=acquired()), \
                mock.patch.object(attention.ac, "autotrade_enabled", return_value=False), \
                mock.patch.object(attention.ac, "load_positions") as load, \
                mock.patch.object(attention.autotrade_orders, "review_pending_attention") as review, \
                mock.patch.object(attention.ac, "log"):
            self.assertTrue(attention.run())
        load.assert_not_called()
        review.assert_not_called()

    def test_installer_has_after_slot_retry_schedules(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        result = subprocess.run(
            ["bash", os.path.join(repo, "scripts", "install_cron_kiwoom.sh"), "--dry-run"],
            cwd=repo, text=True, capture_output=True, check=True)
        self.assertIn("20 16 * * 1-5", result.stdout)
        self.assertIn("55 20 * * 1-5", result.stdout)
        self.assertEqual(result.stdout.count("scripts/autotrade_pending_attention.py"), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
