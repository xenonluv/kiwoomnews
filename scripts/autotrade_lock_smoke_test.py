#!/usr/bin/env python3
import multiprocessing as mp
import os
import tempfile
import unittest

os.environ["AUTOTRADE_TEST_MODE"] = "1"

import autotrade_common as ac


def hold_lock(path, ready, release):
    ac.EXECUTION_LOCK_PATH = path
    with ac.acquire_execution_lock("child") as ok:
        ready.put(ok)
        release.get(timeout=5)


class LockTest(unittest.TestCase):
    def test_second_process_skips(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "lock")
            ready, release = mp.Queue(), mp.Queue()
            proc = mp.Process(target=hold_lock, args=(path, ready, release))
            proc.start()
            self.assertTrue(ready.get(timeout=5))
            old = ac.EXECUTION_LOCK_PATH
            ac.EXECUTION_LOCK_PATH = path
            try:
                with ac.acquire_execution_lock("parent") as ok:
                    self.assertFalse(ok)
            finally:
                ac.EXECUTION_LOCK_PATH = old
                release.put(True)
                proc.join(timeout=5)
            self.assertEqual(proc.exitcode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
