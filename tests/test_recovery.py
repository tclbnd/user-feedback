import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pager  # noqa: E402
import recovery  # noqa: E402
from store import Engine  # noqa: E402
from wal import Log  # noqa: E402
from tests import crash_sim  # noqa: E402


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, workload):
        d = os.path.join(self.tmp, "db")
        crash_sim.build(workload, d)
        return d

    def test_torn_page_repaired_from_fpi(self):
        d = self._run(crash_sim.workload_with_checkpoint())
        crash_sim.tear_page(d, 0)
        info = recovery.recover(d)
        self.assertEqual(info["repaired_pages"], [0])
        self.assertEqual(info["undone"], [2])
        crash_sim.verify_recovery(d, "unittest torn")

    def test_crash_during_undo_resumes(self):
        d = self._run(crash_sim.workload_with_checkpoint())
        crash_sim.tear_page(d, 0)
        with self.assertRaises(recovery.CrashPoint):
            recovery.recover(d, crash_after_undo=1)
        # CLR durable, page write-back not done yet.
        types = [r["type"] for r in Log(os.path.join(d, "wal.log")).records()]
        self.assertIn("CLR", types)
        info = recovery.recover(d)
        self.assertEqual(info["undone"], [2])
        crash_sim.verify_recovery(d, "unittest undo crash")

    def test_half_written_checkpoint_ignored(self):
        d = self._run(crash_sim.workload_two_checkpoints())
        # Real torn page: cut page 1's 256 bytes in half.
        crash_sim.truncate_file(
            os.path.join(d, "pages.db"),
            pager.PAGE_SIZE + pager.PAGE_SIZE // 2)
        begins = [r["lsn"] for r in Log(os.path.join(d, "wal.log")).records()
                  if r["type"] == "CKPT_BEGIN"]
        crash_sim.truncate_file(
            os.path.join(d, "wal.log"),
            crash_sim.wal_offset_of_lsn(d, begins[-1]))
        info = recovery.recover(d)
        self.assertEqual(info["checkpoint"], begins[0])
        self.assertEqual(info["repaired_pages"], [1])
        crash_sim.verify_recovery(d, "unittest half checkpoint")

    def test_torn_wal_tail_ignored(self):
        d = self._run(crash_sim.workload_with_checkpoint())
        crash_sim.tear_page(d, 0)
        path = os.path.join(d, "wal.log")
        with open(path, "r+b") as fh:
            fh.truncate(os.path.getsize(path) - 7)
        recovery.recover(d)
        crash_sim.verify_recovery(d, "unittest torn wal")

    def test_no_checkpoint_redo_from_lsn_one(self):
        d = self._run(crash_sim.workload_no_checkpoint())
        crash_sim.tear_page(d, 0)
        info = recovery.recover(d)
        self.assertEqual(info["checkpoint"], 0)
        self.assertGreaterEqual(info["redo_start"], 1)
        crash_sim.verify_recovery(d, "unittest no ckpt")

    def test_every_recovered_page_has_valid_crc(self):
        d = self._run(crash_sim.workload_with_checkpoint())
        crash_sim.tear_page(d, 0)
        recovery.recover(d)
        pf = pager.PageFile(os.path.join(d, "pages.db"))
        for pid in range(pf.count()):
            self.assertNotEqual(pager.verify(pf.read_raw(pid)), "torn")

    def test_recovery_is_idempotent(self):
        d = self._run(crash_sim.workload_two_checkpoints())
        recovery.recover(d)
        pf = pager.PageFile(os.path.join(d, "pages.db"))
        before = {i: pf.read_raw(i) for i in range(pf.count())}
        recovery.recover(d)
        for i, raw in before.items():
            self.assertEqual(pf.read_raw(i), raw)


if __name__ == "__main__":
    unittest.main()
