"""Tests for the virtual-texture residency planner.

Run with:  python3 -m unittest discover -s tests -v
"""

import json
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vt


def make_path(seed=7, frames=40, tiles=64, per_frame=10):
    """Deterministic 'camera path': a sliding window over a tile grid."""
    rng = random.Random(seed)
    out = []
    cursor = 0.0
    for _ in range(frames):
        cursor += rng.uniform(0.5, 4.0)
        base = int(cursor) % tiles
        frame = []
        for i in range(per_frame):
            pid = (base + i) % tiles
            mip = 0 if i < per_frame // 2 else 1
            area = round(1.0 / (1 + i), 4)
            frame.append({"pid": pid, "mip": mip, "area": area})
        out.append(frame)
    return out


def base_request(**over):
    req = {"pages": 64, "phys": 12, "bw": 6, "frames": make_path()}
    req.update(over)
    return req


class InvarianceTests(unittest.TestCase):
    def test_replay_order_independent(self):
        frames = make_path(seed=11)
        req = base_request(frames=frames)
        baseline = vt.plan(req)

        rng = random.Random(99)
        for _ in range(25):
            shuffled = []
            for frame in frames:
                copy = list(frame) + list(frame[:3])   # duplicate records too
                rng.shuffle(copy)
                shuffled.append(copy)
            result = vt.plan(base_request(frames=shuffled))
            self.assertEqual(result["resident"], baseline["resident"])
            self.assertEqual(result["faults"], baseline["faults"])
            self.assertEqual(result["digest"], baseline["digest"])

    def test_chunk_and_thread_invariant(self):
        req = base_request()
        baseline = vt.plan(req)
        for chunk in (0, 1, 3, 7, 10_000):
            for threads in (1, 2, 4, 8):
                result = vt.plan(req, chunk=chunk, threads=threads)
                self.assertEqual(result["resident"], baseline["resident"], (chunk, threads))
                self.assertEqual(result["faults"], baseline["faults"], (chunk, threads))
                self.assertEqual(result["digest"], baseline["digest"], (chunk, threads))

    def test_digest_is_stable_hex(self):
        digest = vt.plan(base_request())["digest"]
        self.assertEqual(len(digest), 64)
        int(digest, 16)


class ResidencyTests(unittest.TestCase):
    def test_no_reload_of_resident_pages(self):
        """A page that stays requested must never be counted as a fault twice."""
        req = {
            "pages": 64, "phys": 4, "bw": 4,
            "frames": [[{"pid": 0, "mip": 0, "area": 1.0}],
                       [{"pid": 0, "mip": 0, "area": 1.0}],
                       [{"pid": 0, "mip": 0, "area": 1.0}]],
        }
        out = vt.plan(req)
        self.assertEqual(out["faults"], [1, 0, 0])
        self.assertEqual(out["resident"][-1], [0])

    def test_bandwidth_budget_respected(self):
        req = base_request()
        out = vt.plan(req)
        for faults in out["faults"]:
            self.assertLessEqual(faults, req["bw"])

    def test_capacity_respected(self):
        req = base_request()
        out = vt.plan(req)
        for frame in out["resident"]:
            self.assertLessEqual(len(frame), req["phys"])
            self.assertEqual(frame, sorted(frame))
            self.assertEqual(len(frame), len(set(frame)))

    def test_jitter_does_not_thrash(self):
        """Alternating two-page requests must page in once, then hit."""
        frames = []
        for i in range(20):
            pid = i % 2
            frames.append([{"pid": pid, "mip": 0, "area": 1.0},
                           {"pid": pid + 2, "mip": 0, "area": 0.9}])
        out = vt.plan({"pages": 64, "phys": 8, "bw": 2, "frames": frames})
        # frames 0 and 1 each bring in their own new pair; from then on
        # every page is still warm, so jitter costs nothing
        self.assertEqual(out["faults"][:2], [2, 2])
        self.assertEqual(sum(out["faults"][2:]), 0)
        self.assertEqual(out["resident"][-1], [0, 1, 2, 3])

    def test_fast_camera_missing_frames_within_budget(self):
        """High-speed path: pages served by mip fallback, faults bounded."""
        frames = []
        for i in range(30):
            base = (i * 5) % 64
            frames.append([{"pid": (base + k) % 64, "mip": 0, "area": 1.0 - k / 32}
                           for k in range(16)])
        req = {"pages": 64, "phys": 8, "bw": 4, "frames": frames}
        out = vt.plan(req)
        budget = len(frames) * req["bw"]
        total_faults = sum(out["faults"])
        self.assertLessEqual(total_faults, budget)
        # every requested page resolves (resident or coarse fallback)
        planner_capacity_ok = all(len(res) <= req["phys"] for res in out["resident"])
        self.assertTrue(planner_capacity_ok)


class PageTableTests(unittest.TestCase):
    def make_planner(self, **over):
        params = {"pages": 64, "phys": 4, "bw": 4, "hysteresis": 1}
        params.update(over)
        planner = vt.ResidencyPlanner(params["phys"], params["bw"],
                                      params["hysteresis"], vt.MipPyramid(params["pages"]))
        return planner

    def test_fallback_mapping_survives_eviction(self):
        planner = self.make_planner(phys=1, bw=1, hysteresis=0)
        # tile 0 at mip 0 has parent tile 0 at mip 1 (square layout: 8x8)
        records_a = vt.normalize_frame([{"pid": 0, "mip": 1, "area": 1.0}])
        planner.step(0, records_a)
        self.assertEqual(planner.resolve(1, 0)[0], "fallback")
        self.assertEqual(planner.resolve(1, 0)[2], 0)      # served by coarse page 0

        # a finer page with higher priority takes the only slot
        records_b = vt.normalize_frame([{"pid": 8, "mip": 0, "area": 5.0}])
        planner.step(1, records_b)
        # page 0 was dropped from residency, but the fallback lookup for
        # page 1 must not silently point at a dead slot
        kind, slot, owner = planner.resolve(1, 0)
        self.assertIn(kind, ("fallback", "missing"))
        if kind == "fallback":
            self.assertIn(owner, [p.pid for p in planner.table.values() if p.direct])

    def test_two_phase_commit_no_double_slot(self):
        planner = self.make_planner(phys=2, bw=2, hysteresis=0)
        planner.step(0, vt.normalize_frame(
            [{"pid": 0, "mip": 0, "area": 1.0}, {"pid": 1, "mip": 0, "area": 0.5}]))
        planner.step(1, vt.normalize_frame(
            [{"pid": 2, "mip": 0, "area": 2.0}, {"pid": 3, "mip": 0, "area": 2.0}]))
        slots = [p.slot for p in planner.table.values() if p.direct]
        self.assertEqual(len(slots), len(set(slots)))
        self.assertLessEqual(len(slots), 2)

    def test_every_direct_entry_has_valid_slot(self):
        planner = self.make_planner(phys=5, bw=3, hysteresis=1)
        frames = make_path(seed=3)
        for index, frame in enumerate(frames):
            planner.step(index, vt.normalize_frame(frame))
            direct = [p for p in planner.table.values() if p.direct]
            self.assertLessEqual(len(direct), 5)
            self.assertEqual(len({p.slot for p in direct}), len(direct))
            for page in direct:
                self.assertLess(page.slot, 5)


class CliTests(unittest.TestCase):
    def test_cli_roundtrip(self):
        import subprocess

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        req_path = os.path.join(root, "tests", "_req.json")
        out_path = os.path.join(root, "tests", "_out.json")
        with open(req_path, "w", encoding="utf-8") as handle:
            json.dump(base_request(), handle)
        try:
            subprocess.run([sys.executable, os.path.join(root, "vt.py"), req_path, out_path],
                           check=True, cwd=root)
            with open(out_path, "r", encoding="utf-8") as handle:
                result = json.load(handle)
            self.assertEqual(set(result), {"resident", "faults", "digest"})
            self.assertEqual(result["digest"], vt.plan(base_request())["digest"])
        finally:
            for path in (req_path, out_path):
                if os.path.exists(path):
                    os.remove(path)


class ReferencePlanner:
    """Independent reimplementation of the documented reference policy.

    It shares no code with ``vt.py`` beyond the mip-pyramid helper: residency
    is tracked as a plain pid -> entry dictionary plus an explicit slot owner
    map, installs are planned into a work list and committed in a separate
    pass, so a victim's slot stays registered until the commit happens.
    Used to check the production planner frame by frame.
    """

    def __init__(self, pages, phys, bw, hysteresis):
        self.pyramid = vt.MipPyramid(pages)
        self.phys = phys
        self.bw = bw
        self.hysteresis = hysteresis
        self.entries = {}      # pid -> dict(mip, area, last_req, last_touch, slot)

    # -- helpers -----------------------------------------------------------
    def _ancestor(self, pid, mip):
        for anc, want_mip, _d in self.pyramid.ancestors(pid, mip):
            entry = self.entries.get(anc)
            if entry is None or entry["slot"] is None:
                continue
            if want_mip is None or entry["mip"] == want_mip:
                return anc
        return None

    def _pinned(self, records=()):
        """Pages that back a fallback: existing ones plus this frame's needs."""
        pinned = set()
        for pid, entry in self.entries.items():
            if entry["slot"] is not None:
                continue
            owner = self._ancestor(pid, entry["mip"])
            if owner is not None:
                pinned.add(owner)
        for pid, mip, _a, _c in records:
            entry = self.entries.get(pid)
            if entry is not None and entry["slot"] is not None:
                continue
            owner = self._ancestor(pid, mip)
            if owner is not None:
                pinned.add(owner)
        return pinned

    def _used_slots(self):
        return {entry["slot"] for entry in self.entries.values()
                if entry["slot"] is not None}

    def _pick_victim(self, wanted, target, pinned, protected, already_demoted):
        best = None
        best_key = None
        for pid, entry in self.entries.items():
            if entry["slot"] is None or pid in target or pid in already_demoted:
                continue
            if pid in pinned and pid not in wanted:
                # a live fallback root is never recycled
                continue
            key = (
                1 if pid in protected else 0,
                1 if pid not in wanted else 0,
                entry["last_touch"],
                entry["mip"],
                entry["area"],
                -pid,
            )
            if best_key is None or key < best_key:
                best_key = key
                best = pid
        return best

    # -- one frame ---------------------------------------------------------
    def step(self, frame, records):
        wanted = {r[0] for r in records}
        target = {r[0] for r in records[:self.phys]}
        pinned = self._pinned(records)
        protected = pinned

        installs = []          # (pid, slot)
        demoted = []           # victims whose slot is handed over
        loads = 0
        claimed = set()        # slots earmarked during this frame
        used = self._used_slots()

        for pid, mip, area, _c in records:
            entry = self.entries.get(pid)
            if entry is None:
                entry = self.entries[pid] = {
                    "mip": mip, "area": area, "last_req": frame,
                    "last_touch": -1, "slot": None,
                }
            else:
                entry["mip"] = mip
                entry["area"] = area
                entry["last_req"] = frame
            if entry["slot"] is not None:
                entry["last_touch"] = frame
                continue
            if pid not in target or loads >= self.bw or self.phys <= 0:
                continue

            slot = None
            for candidate in range(self.phys):
                if candidate not in used and candidate not in claimed:
                    slot = candidate
                    break
            if slot is None:
                victim = self._pick_victim(wanted, target, pinned, protected, set(demoted))
                if victim is None:
                    continue
                slot = self.entries[victim]["slot"]
                demoted.append(victim)
            installs.append((pid, slot))
            claimed.add(slot)
            entry["last_touch"] = frame
            loads += 1

        # phase 1: install new mappings
        for pid, slot in installs:
            self.entries[pid]["slot"] = slot
        # phase 2: release the mappings they replaced
        for pid in demoted:
            self.entries[pid]["slot"] = None

        # hysteresis: keep entries around for a few extra frames
        stale = [pid for pid, entry in self.entries.items()
                 if pid not in wanted and frame - entry["last_req"] > self.hysteresis]
        for pid in stale:
            del self.entries[pid]

        resident = sorted(pid for pid, e in self.entries.items() if e["slot"] is not None)
        return resident, loads


class ReferenceComparisonTests(unittest.TestCase):
    def compare(self, req, **kwargs):
        out = vt.plan(req, **kwargs)
        ref = ReferencePlanner(req["pages"], req["phys"], req["bw"],
                               kwargs.get("hysteresis", vt.DEFAULT_HYSTERESIS))
        prepared = vt.normalize_frames(req["frames"])
        for index, records in enumerate(prepared):
            resident, loads = ref.step(index, records)
            self.assertEqual(out["resident"][index], resident,
                             "frame %d residency diverged from reference" % index)
            self.assertEqual(out["faults"][index], loads,
                             "frame %d fault count diverged from reference" % index)
        return out

    def test_preset_camera_paths_match_reference(self):
        for seed in (3, 7, 11, 23):
            for phys, bw in ((4, 2), (8, 8), (12, 6), (16, 32)):
                req = base_request(frames=make_path(seed=seed), phys=phys, bw=bw)
                self.compare(req)

    def test_reference_matches_with_zero_hysteresis(self):
        req = base_request(frames=make_path(seed=5), phys=6, bw=3)
        self.compare(req, hysteresis=0)
        self.compare(req, hysteresis=2)


class InvariantTests(unittest.TestCase):
    CONFIGS = [
        {"pages": 64, "phys": 4, "bw": 1},
        {"pages": 64, "phys": 8, "bw": 4},
        {"pages": 256, "phys": 32, "bw": 16},
        {"pages": 16, "phys": 16, "bw": 64},
        {"pages": 256, "phys": 1, "bw": 1},
    ]

    def test_structural_invariants(self):
        planner_hysteresis = vt.DEFAULT_HYSTERESIS
        for config in self.CONFIGS:
            req = dict(config, frames=make_path(seed=13, tiles=32, per_frame=14))
            path = vt.plan(req)
            planner = vt.ResidencyPlanner(req["phys"], req["bw"],
                                          planner_hysteresis, vt.MipPyramid(req["pages"]))
            records_per_frame = vt.normalize_frames(req["frames"])
            for index, records in enumerate(records_per_frame):
                planner.step(index, records)
                resident = path["resident"][index]
                self.assertLessEqual(len(resident), min(req["phys"], req["pages"]))
                self.assertEqual(resident, sorted(set(resident)))
                self.assertLessEqual(path["faults"][index], req["bw"])

                direct = [p for p in planner.table.values() if p.direct]
                slots = {p.slot for p in direct}
                self.assertEqual(len(slots), len(direct), "slot aliasing")
                for slot in slots:
                    self.assertLess(slot, req["phys"])

                # nothing points at a dead slot
                for page in planner.table.values():
                    if page.direct:
                        continue
                    kind, slot, owner = planner.resolve(page.pid, page.mip)
                    if kind == "fallback":
                        self.assertIsNotNone(slot)
                        self.assertIn(owner, {p.pid for p in direct})
                        self.assertEqual(planner.table[owner].slot, slot)

    def test_target_pages_never_evicted(self):
        for config in self.CONFIGS:
            req = dict(config, frames=make_path(seed=17, tiles=32, per_frame=14))
            path = vt.plan(req)
            records_per_frame = vt.normalize_frames(req["frames"])
            previous = []
            for index, records in enumerate(records_per_frame):
                target = {r[0] for r in records[:req["phys"]]}
                warm = target & set(previous)
                resident_now = path["resident"][index]
                self.assertTrue(warm <= set(resident_now),
                                "frame %d dropped a warm target page" % index)
                previous = resident_now

    def test_no_page_reloaded_twice_in_a_frame(self):
        req = base_request(frames=make_path(seed=19, tiles=32, per_frame=14), bw=64)
        path = vt.plan(req)
        records_per_frame = vt.normalize_frames(req["frames"])
        resident = set()
        for index, records in enumerate(records_per_frame):
            loads = [r[0] for r in records[:req["phys"]] if r[0] not in resident]
            fresh = {r[0] for r in records[:req["phys"]]}
            self.assertLessEqual(path["faults"][index], len(fresh))
            self.assertEqual(path["faults"][index], min(req["bw"], len(loads)))
            resident = set(path["resident"][index])

    def test_duplicate_records_are_coalesced(self):
        one = vt.plan({"pages": 64, "phys": 8, "bw": 8, "frames": [
            [{"pid": 5, "mip": 0, "area": 0.5}]]})
        many = vt.plan({"pages": 64, "phys": 8, "bw": 8, "frames": [
            [{"pid": 5, "mip": 0, "area": 0.5}] * 6]})
        self.assertEqual(one["faults"], many["faults"])
        self.assertEqual(one["resident"], many["resident"])
        self.assertEqual(one["digest"], many["digest"])

    def test_parent_hints_order_independent(self):
        base = {"pages": 64, "phys": 4, "bw": 2}
        forward = [{"pid": 1, "mip": 0, "area": 1.0, "parent": 7},
                   {"pid": 1, "mip": 0, "area": 1.0, "parent": {"pid": 3, "mip": 1}},
                   {"pid": 7, "mip": 1, "area": 0.5},
                   {"pid": 3, "mip": 1, "area": 0.4}]
        backward = list(reversed(forward))
        a = vt.plan(dict(base, frames=[forward, forward]))
        b = vt.plan(dict(base, frames=[backward, backward]))
        self.assertEqual(a["digest"], b["digest"])
        self.assertEqual(a["resident"], b["resident"])


class TwoPhaseCommitTests(unittest.TestCase):
    def test_old_mapping_addressable_until_commit(self):
        """During the transition the replaced mapping still resolves."""
        planner = vt.ResidencyPlanner(2, 2, 0, vt.MipPyramid(64))
        planner.step(0, vt.normalize_frame([{"pid": 0, "mip": 0, "area": 1.0}]))
        self.assertEqual(planner.resolve(0, 0)[0], "resident")
        # a higher priority page takes the only slot
        planner2 = vt.ResidencyPlanner(1, 1, 0, vt.MipPyramid(64))
        planner2.step(0, vt.normalize_frame([{"pid": 0, "mip": 0, "area": 1.0}]))
        before = planner2.resolve(0, 0)
        self.assertEqual(before[0], "resident")
        planner2.step(1, vt.normalize_frame([{"pid": 9, "mip": 0, "area": 9.0}]))
        # only after the commit is the slot handed over
        self.assertEqual(planner2.resolve(9, 0), ("resident", before[1], 9))
        self.assertNotEqual(planner2.resolve(0, 0)[0], "resident")

    def test_freed_slots_are_reused_not_leaked(self):
        planner = vt.ResidencyPlanner(3, 3, 0, vt.MipPyramid(128))
        for frame in range(20):
            records = vt.normalize_frame(
                [{"pid": (frame + k) % 32, "mip": 0, "area": 1.0 - k / 16}
                 for k in range(6)])
            planner.step(frame, records)
            direct = [p for p in planner.table.values() if p.direct]
            self.assertLessEqual(len(direct), 3)
            self.assertLessEqual(len(planner.free_slots) + len(direct), 3)


class FastPlaybackTests(unittest.TestCase):
    def speed_path(self, frames=40, tiles=128, per_frame=24, step=9):
        """Mip-consistent path: coarse pages really are ancestors of the fine ones."""
        coarse = tiles // 4          # mip 1 has a quarter of the tiles per axis
        out = []
        for i in range(frames):
            base = (i * step) % tiles
            frame = []
            for k in range(per_frame):
                fine = (base + k) % tiles
                if k < per_frame // 2:
                    frame.append({"pid": fine, "mip": 0,
                                  "area": round(1.0 - k / 32.0, 4)})
                else:
                    frame.append({"pid": (fine // 4) % coarse, "mip": 1,
                                  "area": round(1.0 - k / 32.0, 4)})
            out.append(frame)
        return out

    def unserved_frames(self, req):
        path = vt.plan(req)
        records_per_frame = vt.normalize_frames(req["frames"])
        unserved = 0
        for index, records in enumerate(records_per_frame):
            target = {r[0] for r in records[:req["phys"]]}
            if not target <= set(path["resident"][index]):
                unserved += 1
        return path, unserved

    def test_high_speed_large_bandwidth_fallback_frames_within_budget(self):
        """Ample bandwidth: at most 5% of frames have to run on fallback."""
        frames = self.speed_path()
        req = {"pages": 128, "phys": 24, "bw": 24, "frames": frames}
        path, unserved = self.unserved_frames(req)
        self.assertLessEqual(unserved, 0.05 * len(frames))
        self.assertLessEqual(sum(path["faults"]), req["bw"] * len(frames))

    def test_high_speed_tight_bandwidth_degrades_gracefully(self):
        """Tight bandwidth: faults stay within budget, fallbacks stay mapped.

        A page that never had a resident covering ancestor on the frame it
        is requested may be unmapped (nothing coarser has been paged in
        yet).  But once a frame starts with a resident covering ancestor
        for a page it is about to fall back on, that mapping must still be
        valid after the frame commits and reclaims.
        """
        frames = self.speed_path(frames=60, step=17)
        req = {"pages": 128, "phys": 24, "bw": 3, "frames": frames}
        path = vt.plan(req)
        self.assertLessEqual(sum(path["faults"]), req["bw"] * len(frames))
        for faults in path["faults"]:
            self.assertLessEqual(faults, req["bw"])

        planner = vt.ResidencyPlanner(req["phys"], req["bw"], vt.DEFAULT_HYSTERESIS,
                                      vt.MipPyramid(req["pages"]))
        for index, records in enumerate(vt.normalize_frames(frames)):
            resident_before = {p.pid for p in planner.table.values() if p.direct}
            uses_fallback = []      # (pid, mip, covering ancestor)
            for pid, mip, _a, _c in records:
                if pid in resident_before:
                    continue
                for anc, want, _d in planner.pyramid.ancestors(pid, mip):
                    entry = planner.table.get(anc)
                    if entry is None or not entry.direct:
                        continue
                    if want is None or entry.mip == want:
                        uses_fallback.append((pid, mip, anc, want))
                        break

            planner.step(index, records)

            for pid, mip, anc, want in uses_fallback:
                kind, slot, owner = planner.resolve(pid, mip)
                self.assertIn(kind, ("resident", "fallback"),
                              "frame %d pid %d lost its fallback" % (index, pid))
                if kind == "fallback":
                    self.assertEqual(owner, anc,
                                     "frame %d pid %d fell back to the wrong page"
                                     % (index, pid))
                    self.assertIsNotNone(slot)



if __name__ == "__main__":
    unittest.main()

