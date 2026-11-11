"""Subprocess-level crash recovery tests (hard os._exit + original-input replay)."""

import os
import tempfile
import unittest

from ndjson_pipe.crash_acceptance import CRASH_POINTS, crash_recovery_matrix
from ndjson_pipe.importer import CRASH_EXIT_CODE
from tests.corpus import fixed_reject_ndjson, legal_ndjson
from tests.helpers import write_source


class CrashRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix="ndjson-crash-test-")
        # mixed versions, unicode, fixed business rejects and blank lines
        self.data = (
            legal_ndjson(seed=99, n=18)
            + fixed_reject_ndjson()
            + legal_ndjson(seed=101, n=6)
        )
        self.source_file = os.path.join(self.workdir, "source.ndjson")
        write_source(self.source_file, self.data)

    def test_all_three_crash_points_converge_to_baseline(self):
        ok, reports = crash_recovery_matrix(
            self.source_file,
            "crash-source",
            self.workdir,
            chunk_size=5,
            crash_line=4,
        )
        for report in reports:
            with self.subTest(point=report["point"]):
                self.assertEqual(report["exit_code"], CRASH_EXIT_CODE)
                self.assertEqual(report["diffs_after_resume"], [])
                self.assertEqual(report["diffs_after_full_scan"], [])
                self.assertTrue(report["checkpoint_ok"])
        self.assertTrue(ok, msg=reports)

    def test_crash_points_are_distinct_phases(self):
        # guard against accidental constant folding of the three phases
        self.assertEqual(len(set(CRASH_POINTS)), 3)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
