"""Planner determinism and file-group selection tests."""
import unittest

from lake.engine import Engine
from lake.model import Config
from lake.planner import Planner
from lake.runner import _drain_serial


def cfg():
    return Config(
        levels=4,
        file_caps=(2, 2, 4, 8),
        memtable_records=2,
        target_file_records=4,
        max_concurrent_compactions=2,
    )


class PlannerTest(unittest.TestCase):
    def test_pick_is_deterministic_across_instances(self):
        e = Engine.create(cfg())
        for i in range(8):
            e.put("k%02d" % i, i)
        plans = [Planner(e.manifest).pick() for _ in range(5)]
        dicts = [p.to_dict() for p in plans]
        self.assertTrue(all(p is not None for p in plans))
        self.assertEqual(dicts[0], dicts[-1])

    def test_busy_files_are_excluded_from_group(self):
        e = Engine.create(cfg())
        for i in range(8):
            e.put("k%02d" % i, i)
        planner = Planner(e.manifest)
        first = planner.pick()
        self.assertIsNotNone(first)
        planner.busy.update(first.input_files)
        second = planner.pick()
        if second is not None:
            overlap = set(first.input_files) & set(second.input_files)
            self.assertEqual(overlap, set())

    def test_selection_uses_capacity_pressure_l0_first(self):
        e = Engine.create(cfg())
        for i in range(6):
            e.put("k%02d" % i, i)
        plan = Planner(e.manifest).pick()
        self.assertIsNotNone(plan)
        self.assertEqual(plan.source_level, 0)
        self.assertEqual(plan.target_level, 1)

    def test_overlap_ratio_is_recorded_for_replay(self):
        e = Engine.create(cfg())
        for i in range(10):
            e.put("k%02d" % (i % 4), i)
        _drain_serial(e)
        # Generate fresh L0 data overlapping existing L1 ranges.
        for i in range(6):
            e.put("k%02d" % (i % 4), 100 + i)
        plan = Planner(e.manifest).pick()
        self.assertIsNotNone(plan)
        self.assertIn("overlap_ratio", plan.to_dict())
        self.assertGreaterEqual(plan.overlap_ratio, 0.0)

    def test_input_guard_caps_group_size(self):
        guarded = Config(
            levels=4,
            file_caps=(2, 2, 4, 8),
            memtable_records=2,
            target_file_records=4,
            max_concurrent_compactions=1,
            max_compaction_inputs=3,
        )
        e = Engine.create(guarded)
        for i in range(16):
            e.put("k%02d" % i, i)
        plan = Planner(e.manifest).pick()
        self.assertIsNotNone(plan)
        self.assertLessEqual(len(plan.input_files), 3)


if __name__ == "__main__":
    unittest.main()
