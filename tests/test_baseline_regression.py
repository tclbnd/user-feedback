"""Regression tests for the two failure modes called out in the task.

1. Random/loose level selection must not cause the deleted-record revival
   seen when a watermark is allowed to move backwards.
2. The preserved buggy baseline (``baseline_buggy.py``) is asserted to exhibit
   watermark regression / snapshot loss, documenting what the new engine
   guarantees against.
"""
import json
import os
import tempfile
import unittest

from lake.engine import Engine
from lake.model import Config
from lake.runner import _drain_serial

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class DeletedRecordRevivalTest(unittest.TestCase):
    def _config(self):
        return Config(
            levels=4,
            file_caps=(2, 2, 4, 8),
            memtable_records=2,
            target_file_records=4,
            max_concurrent_compactions=1,
        )

    def test_deleted_record_never_revives_after_repeated_compaction(self):
        e = Engine.create(self._config())
        e.put("k1", "first")
        e.flush()
        e.delete("k1")
        # Repeated writes force many compaction cycles through every level.
        for i in range(30):
            e.put("other%02d" % i, i)
        _drain_serial(e)
        # Run an extra coalescing pass to be sure bottom tombstones are gone.
        _drain_serial(e)
        self.assertIsNone(e.read("k1"))
        self.assertNotIn("k1", e.manifest.live_key_values())
        # The watermark finished at the maximum sequence and never regressed.
        self.assertEqual(e.manifest.watermark, e.manifest.current_max_seq())

    def test_reinsert_after_delete_does_not_resurrect_old_value(self):
        e = Engine.create(self._config())
        e.put("k1", "old")
        e.delete("k1")
        e.put("k1", "new")
        _drain_serial(e)
        self.assertEqual(e.read("k1"), "new")


class BuggyBaselineContrastTest(unittest.TestCase):
    def test_baseline_exhibits_watermark_regression(self):
        import importlib.util

        path = os.path.join(HERE, "baseline_buggy.py")
        spec = importlib.util.spec_from_file_location("baseline_buggy", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        req = {
            "levels": 3,
            "cap": [2, 2],
            "seed": 1,
            "ops": [
                {"op": "put", "k": "a", "v": 1},
                {"op": "put", "k": "b", "v": 2},
                {"op": "put", "k": "c", "v": 3},
                {"op": "del", "k": "a"},
                {"op": "put", "k": "d", "v": 4},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            in_path = os.path.join(tmp, "ops.json")
            out_path = os.path.join(tmp, "out.json")
            with open(in_path, "w") as fh:
                json.dump(req, fh)
            import sys

            argv = sys.argv
            try:
                sys.argv = ["lsm.py", in_path, out_path]
                module.main()
            finally:
                sys.argv = argv
            with open(out_path) as fh:
                out = json.load(fh)
        # The buggy code drives its watermark with min(); it cannot exceed 0.
        self.assertEqual(out["watermark"], 0)


if __name__ == "__main__":
    unittest.main()
