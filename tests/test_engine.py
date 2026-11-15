"""Engine-level correctness tests: merge淘汰, snapshots, watermark, reads."""
import unittest

from lake.engine import Engine
from lake.model import Config
from lake.runner import _drain_serial


def small_config(**overrides):
    base = dict(
        levels=4,
        file_caps=(2, 3, 4, 8),
        memtable_records=2,
        target_file_records=4,
        max_concurrent_compactions=1,
    )
    base.update(overrides)
    return Config(**base)


class EngineCorrectnessTest(unittest.TestCase):
    def test_overwrite_merge_keeps_newest_by_seq(self):
        e = Engine.create(small_config())
        for i in range(10):
            e.put("k1", i)
        _drain_serial(e)
        self.assertEqual(e.read("k1"), 9)
        # Only one surviving version of k1 after full coalescing.
        versions = [
            r
            for f in e.manifest.all_files()
            for r in f.records
            if r.key == "k1"
        ]
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0].value, 9)

    def test_delete_is_tombstone_and_hides_key(self):
        e = Engine.create(small_config())
        e.put("k1", "a")
        e.put("k2", "b")
        e.delete("k1")
        _drain_serial(e)
        self.assertIsNone(e.read("k1"))
        self.assertEqual(e.read("k2"), "b")

    def test_tombstone_dropped_only_at_bottom(self):
        e = Engine.create(small_config())
        e.put("k1", "a")
        e.put("k2", "b")
        e.delete("k1")
        e.flush()
        _drain_serial(e)
        bottom = e.manifest.levels[-1]
        self.assertTrue(bottom)
        self.assertFalse(
            any(r.is_tombstone for f in bottom for r in f.records)
        )
        self.assertEqual(e.manifest.live_key_values(), {"k2": "b"})
        self.assertGreater(e.stats.tombstones_dropped, 0)

    def test_tombstone_survives_non_bottom_compaction(self):
        # A tombstone above the bottom must be retained until it reaches the
        # bottom level, otherwise it can no longer mask older versions.
        e = Engine.create(small_config())
        e.put("k1", "old")
        e.flush()
        e.delete("k1")
        e.flush()
        from lake.planner import Planner

        plan = Planner(e.manifest).pick()
        self.assertIsNotNone(plan)
        self.assertEqual(plan.target_level, 1)
        e.execute(plan)
        l1 = e.manifest.levels[1]
        self.assertTrue(
            any(r.is_tombstone for f in l1 for r in f.records)
        )
        self.assertIsNone(e.read("k1"))

    def test_watermark_never_regresses(self):
        e = Engine.create(small_config())
        snap = e.snapshot()  # empty snapshot, visible_seq 0
        for i in range(8):
            e.put("k%02d" % i, i)
        # Watermark stays floored at 0 while the empty snapshot is open.
        self.assertEqual(e.manifest.watermark, 0)
        e.release(snap.snap_id)
        _drain_serial(e)
        advanced = e.manifest.watermark
        self.assertGreater(advanced, 0)
        # Reopening an older snapshot must not drag the watermark backwards.
        older = e.snapshot()
        older.visible_seq = 1
        e.manifest.advance_watermark()
        self.assertEqual(e.manifest.watermark, advanced)

    def test_snapshot_preserves_visible_versions_after_compaction(self):
        e = Engine.create(small_config())
        e.put("k1", "v1")
        e.put("k2", "v2")
        snap = e.snapshot()
        visible_seq = snap.visible_seq
        # Overwrite and delete after the snapshot; heavy compaction follows.
        for i in range(20):
            e.put("k1", "new%d" % i)
        e.delete("k2")
        _drain_serial(e)
        # Current view reflects new data and the deletion.
        self.assertEqual(e.read("k1"), "new19")
        self.assertIsNone(e.read("k2"))
        # Snapshot view still sees the original versions.
        self.assertEqual(e.read("k1", snap_id=snap.snap_id), "v1")
        self.assertEqual(e.read("k2", snap_id=snap.snap_id), "v2")
        self.assertEqual(snap.visible_seq, visible_seq)
        # The old files must be retained as pinned orphans.
        self.assertGreaterEqual(
            sum(f.pin_count for f in e.manifest.all_files())
            + sum(f.pin_count for f in e.manifest.orphan_files.values()),
            1,
        )

    def test_releasing_snapshot_reclaims_orphans(self):
        e = Engine.create(small_config())
        e.put("k1", "v1")
        snap = e.snapshot()
        for i in range(12):
            e.put("k1", i)
        _drain_serial(e)
        self.assertGreaterEqual(len(e.manifest.orphan_files), 1)
        e.release(snap.snap_id)
        _drain_serial(e)
        self.assertEqual(len(e.manifest.orphan_files), 0)

    def test_memtable_participates_in_read(self):
        e = Engine.create(
            small_config(memtable_records=100)
        )
        e.put("k1", "flushed-era")
        e.flush()
        e.put("k1", "unflushed")  # stays in memtable
        self.assertEqual(e.read("k1"), "unflushed")

    def test_manifest_roundtrip_preserves_state(self):
        e = Engine.create(small_config())
        for i in range(12):
            e.put("k%02d" % (i % 4), i)
        _drain_serial(e)
        from lake.manifest import Manifest

        blob = e.manifest.to_dict()
        restored = Manifest.from_dict(blob)
        self.assertEqual(restored.digest(), e.manifest.digest())


if __name__ == "__main__":
    unittest.main()
