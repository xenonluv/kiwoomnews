#!/usr/bin/env python3
import unittest
from contextlib import contextmanager
from unittest import mock

import autotrade_pending_admin as admin


def data():
    return {"positions": [], "pending_entries": [{
        "intent_id": "A-20260711-krx-1", "code": "A", "order_date": "20260711",
        "requested_qty": 10, "accounted_filled": 0, "state": "SUBMIT_UNKNOWN",
        "ord_no": None,
    }]}


class PendingAdminTest(unittest.TestCase):
    def test_fingerprint_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "fingerprint 불일치"):
            admin.resolve_confirmed_no_order(
                data(), intent_id="A-20260711-krx-1", code="WRONG",
                order_date="20260711", requested_qty=10)

    def test_confirmed_fill_cannot_be_discarded(self):
        payload = data()
        payload["pending_entries"][0]["accounted_filled"] = 1
        with self.assertRaisesRegex(ValueError, "체결수량"):
            admin.resolve_confirmed_no_order(
                payload, intent_id="A-20260711-krx-1", code="A",
                order_date="20260711", requested_qty=10)

    def test_no_order_resolution_keeps_tombstone(self):
        payload = data()
        row = admin.resolve_confirmed_no_order(
            payload, intent_id="A-20260711-krx-1", code="A",
            order_date="20260711", requested_qty=10)
        self.assertEqual(payload["pending_entries"], [])
        self.assertEqual(payload["resolved_pending_entries"][0]["resolution"],
                         "confirmed_no_order_no_fill")
        self.assertEqual(row["intent_id"], "A-20260711-krx-1")

    def test_attach_order_number_keeps_pending_for_normal_reconcile(self):
        payload = data()
        row = admin.attach_order_number(
            payload, intent_id="A-20260711-krx-1", code="A",
            order_date="20260711", requested_qty=10, order_no="9001")
        self.assertEqual(row["ord_no"], "9001")
        self.assertEqual(row["state"], "ACCEPTED_MANUAL_LINK")
        self.assertEqual(len(payload["pending_entries"]), 1)

    def test_existing_order_number_cannot_be_overwritten_or_resolved_as_no_order(self):
        payload = data()
        payload["pending_entries"][0]["ord_no"] = "9001"
        with self.assertRaisesRegex(ValueError, "덮어쓸"):
            admin.attach_order_number(
                payload, intent_id="A-20260711-krx-1", code="A",
                order_date="20260711", requested_qty=10, order_no="9002")
        with self.assertRaisesRegex(ValueError, "주문번호가 연결"):
            admin.resolve_confirmed_no_order(
                payload, intent_id="A-20260711-krx-1", code="A",
                order_date="20260711", requested_qty=10)

    def test_mutation_refuses_lock_failure_and_web_not_explicitly_off(self):
        argv = ["resolve-no-order", "--intent-id", "A-20260711-krx-1",
                "--code", "A", "--order-date", "20260711",
                "--requested-qty", "10", "--confirm-hts-no-order-no-fill"]

        @contextmanager
        def lock(value):
            yield value

        with mock.patch.object(admin.ac, "acquire_execution_lock", return_value=lock(False)):
            with self.assertRaisesRegex(RuntimeError, "잠금을 얻지 못"):
                admin.main(argv)
        with mock.patch.object(admin.ac, "acquire_execution_lock", return_value=lock(True)), \
                mock.patch.object(admin.ac, "kv_get", return_value="1"):
            with self.assertRaisesRegex(RuntimeError, "명시적 OFF"):
                admin.main(argv)


if __name__ == "__main__":
    unittest.main(verbosity=2)
