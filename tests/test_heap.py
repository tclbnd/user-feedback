import os
import random
import struct
import sys
import tempfile
import unittest
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from heap import Heap  # noqa: E402
from page import SIZE, Page  # noqa: E402


class HeapTests(unittest.TestCase):
    def test_insert_and_read(self):
        with tempfile.TemporaryDirectory() as d:
            h = Heap(d)
            rids = [h.insert(f"row{i}") for i in range(3)]
            self.assertEqual([h.read(r) for r in rids], ["row0", "row1", "row2"])

    def test_shrinking_update_stays_in_place(self):
        with tempfile.TemporaryDirectory() as d:
            h = Heap(d)
            rid = h.insert("a" * 40)
            h.update(rid, "short")
            self.assertEqual(h.read(rid), "short")


class InvariantTests(unittest.TestCase):
    """Page-level and heap-level invariant checks shared by the churn test."""

    def check(self, h, live=None):
        self.check_pages(h, live)
        self.check_no_double_hop(h)

    def check_pages(self, h, live=None):
        reached = set()
        overflow_refs = Counter()
        free_overflow = set()
        for pid, raw in enumerate(h.pages):
            p = Page(raw)
            n = p.nslots
            if raw[:1] == b"O":
                nxt, used = struct.unpack_from(">HH", raw, 1)
                self.assertLessEqual(used, SIZE - 5)
                if (nxt, used) == (0xFFFF, 0):
                    free_overflow.add(pid)  # unlinked, available for reuse
                continue
            self.assertEqual(raw[0], ord("D"))
            self.assertEqual(raw[1], 0)
            free_end = struct.unpack_from(">H", raw, 4)[0]
            self.assertGreaterEqual(free_end, 6 + 4 * n)
            for i in range(n):
                off, length = p.slot(i)
                if not length:
                    continue
                self.assertGreaterEqual(off, free_end)
                self.assertLessEqual(off + length, SIZE)
                rec = raw[off:off + length]
                self.assertIn(rec[:1], (b"T", b"M", b"F", b"L"))
                if rec[:1] == b"F":
                    tp, ts = struct.unpack(">HH", rec[1:5])
                    self.assertEqual(length, 5)
                    self.assertLess(tp, len(h.pages))
                    target = Page(h.pages[tp])
                    self.assertLess(ts, target.nslots)
                    trec = target.record(ts)
                    self.assertIsNotNone(trec)
                    self.assertIn(trec[:1], (b"M", b"L"))
                    self.assertNotEqual(trec[:1], b"F")
                if rec[:1] == b"L":
                    first, nbytes = struct.unpack(">HI", rec[1:7])
                    self.assertEqual(length, 7)
                    total, cur, hops = 0, first, 0
                    while cur != 0xFFFF:
                        self.assertLess(cur, len(h.pages))
                        raw2 = h.pages[cur]
                        self.assertEqual(raw2[0], ord("O"))
                        cnext, cused = struct.unpack_from(">HH", raw2, 1)
                        self.assertLessEqual(cused, SIZE - 5)
                        overflow_refs[cur] += 1
                        total += cused
                        hops += 1
                        self.assertLess(hops, 1000)
                        cur = cnext
                    self.assertEqual(total, nbytes)
            # free space accounting: no overlap between live records and directory
            spans = sorted((p.slot(i) for i in range(n) if p.slot(i)[1]))
            end = 6 + 4 * n
            for off, length in spans:
                self.assertGreaterEqual(off, end)
                end = off + length
            self.assertGreaterEqual(p.free(), 0)
            self.assertEqual(p.contiguous(), free_end - 6 - 4 * n)
            self.assertGreaterEqual(p.free(), p.contiguous())
            # Every live slot must be convertible into a 5-byte forward pointer,
            # otherwise a row could become impossible to move.
            for i in range(n):
                if p.slot(i)[1]:
                    self.assertTrue(p.fits(5, reuse=i), (pid, i, p.free(), p.reserve(i)))
        for pid, count in overflow_refs.items():
            self.assertEqual(count, 1, f"overflow page {pid} shared by {count} chains")
            self.assertNotIn(pid, free_overflow)
        for pid in free_overflow:
            self.assertNotIn(pid, overflow_refs)
        if live is not None:
            for rid in live:
                self.assertLess(rid[0], len(h.pages))
                self.assertLess(rid[1], Page(h.pages[rid[0]]).nslots)
                self.assertIsNotNone(Page(h.pages[rid[0]]).record(rid[1]), rid)

    def check_no_double_hop(self, h):
        for pid, raw in enumerate(h.pages):
            if raw[:1] != b"D":
                continue
            p = Page(raw)
            for i in range(p.nslots):
                rec = p.record(i)
                if rec is not None and rec[:1] == b"F":
                    tp, ts = struct.unpack(">HH", rec[1:5])
                    self.assertNotEqual(tp, pid, "forward pointer must leave the page")
                    self.assertIn(Page(h.pages[tp]).record(ts)[:1], (b"M", b"L"))


class ChurnTests(InvariantTests):
    def test_long_random_sequence(self):
        for seed in (1234, 7, 99):
            with self.subTest(seed=seed):
                self._churn(seed, 500)

    def _churn(self, seed, steps):
        rng = random.Random(seed)
        with tempfile.TemporaryDirectory() as d:
            h = Heap(d)
            live = {}

            def value(n):
                return "".join(rng.choice("abcXYZ019-_") for _ in range(n))

            for step in range(steps):
                roll = rng.random()
                if roll < 0.45 or not live:
                    v = value(rng.choice([1, 5, 20, 60, 100, 121, 200, 400, 900]))
                    rid = h.insert(v)
                    key = tuple(rid)
                    self.assertNotIn(key, live)
                    live[key] = v
                elif roll < 0.80:
                    key = rng.choice(list(live))
                    v = value(rng.choice([0, 3, 30, 90, 119, 120, 121, 300, 700, 1000]))
                    h.update(list(key), v)
                    live[key] = v
                else:
                    key = rng.choice(list(live))
                    h.delete(list(key))
                    del live[key]
                self.check(h, live)
                if step % 50 == 0:  # reload from disk now and then
                    h.save()
                    h = Heap(d)
                for rid, expected in live.items():
                    self.assertEqual(h.read(list(rid)), expected, (step, rid))
            h.save()

    def test_fragmentation_then_insert_reuses_space(self):
        with tempfile.TemporaryDirectory() as d:
            h = Heap(d)
            rids = [h.insert("x" * 30) for _ in range(6)]
            for rid in (rids[1], rids[3]):
                h.delete(rid)
            InvariantTests.check(self, h)
            before = len(h.pages)
            rid = h.insert("y" * 20)
            self.assertEqual(rid, [0, 1])  # lowest free slot reused
            self.assertEqual(len(h.pages), before)
            self.assertEqual(h.read(rids[0]), "x" * 30)

    def test_compaction_keeps_row_ids(self):
        with tempfile.TemporaryDirectory() as d:
            h = Heap(d)
            rids = [h.insert(f"r{i:02d}" + "v" * 10) for i in range(8)]
            for rid in (rids[1], rids[2], rids[4], rids[5], rids[6]):
                h.delete(rid)
            page = Page(h.pages[0])
            before = [page.slot(i) for i in range(page.nslots)]
            page.compact()
            h.store(0, page)
            after = [page.slot(i) for i in range(page.nslots)]
            # Slot numbers and liveness are preserved; only offsets move.
            self.assertEqual(page.nslots, len(before))
            self.assertEqual([s[1] > 0 for s in before], [s[1] > 0 for s in after])
            live_bytes = sum(s[1] for s in after)
            self.assertEqual(page.contiguous(), SIZE - 6 - 4 * page.nslots - live_bytes)
            self.assertEqual(page.free(), page.contiguous())  # no holes left
            for rid in (rids[0], rids[3], rids[7]):
                self.assertEqual(h.read(rid), f"r{rid[1]:02d}" + "v" * 10)
            self.check(h)

    def test_fragmentation_triggers_compaction(self):
        with tempfile.TemporaryDirectory() as d:
            h = Heap(d)
            rids = [h.insert("x" * 30) for _ in range(7)]
            contig = []
            for rid in rids[1:]:
                h.delete(rid)
                page = Page(h.pages[0])
                # The fragmentation threshold is enforced after every delete.
                self.assertLessEqual(page.free() - page.contiguous(), SIZE // 2)
                contig.append(page.contiguous())
            # Compaction packed the surviving rows back towards the page end.
            self.assertGreater(max(contig), contig[0])
            self.assertEqual(h.read(rids[0]), "x" * 30)
            self.check(h)

    def test_grow_moves_with_forward_pointer(self):
        with tempfile.TemporaryDirectory() as d:
            h = Heap(d)
            others = [h.insert("x" * 90) for _ in range(2)]
            rid = h.insert("a" * 40)
            h.update(rid, "b" * 119)  # no longer fits beside the other rows
            self.assertEqual(h.read(rid), "b" * 119)
            self.assertEqual(Page(h.pages[rid[0]]).record(rid[1])[:1], b"F")
            # Every further update must repoint the original slot, not chain hops.
            h.update(rid, "c" * 10)
            self.assertEqual(h.read(rid), "c" * 10)
            h.update(rid, "d" * 118)
            self.assertEqual(h.read(rid), "d" * 118)
            InvariantTests.check(self, h)
            h.update(rid, "e" * 300)  # moves into an overflow chain
            self.assertEqual(h.read(rid), "e" * 300)
            self.assertEqual(Page(h.pages[rid[0]]).record(rid[1])[:1], b"F")
            InvariantTests.check(self, h)
            for other in others:
                self.assertEqual(h.read(other), "x" * 90)
            h.delete(rid)
            InvariantTests.check(self, h)


if __name__ == "__main__":
    unittest.main()
