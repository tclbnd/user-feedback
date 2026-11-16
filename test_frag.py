#!/usr/bin/env python3
"""Unit tests for the JSON fragment timeline rebuild in frag.py."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

import frag

HERE = os.path.dirname(__file__)
TMPROOT = os.path.join(HERE, "out", "tmp")


class TimelineTests(unittest.TestCase):
    def test_out_of_order_arrival_sorted_by_decode_time(self):
        fragments = [
            {"bmdt": 200, "samples": [{"duration": 100, "size": 2}]},
            {"bmdt": 0, "samples": [{"duration": 200, "size": 1}]},
        ]
        merged = frag.build_timeline(fragments)
        self.assertEqual([s["pts"] for s in merged], [0, 200])
        self.assertEqual([s["size"] for s in merged], [1, 2])

    def test_higher_seq_replaces_lower_on_overlap(self):
        fragments = [
            {"bmdt": 0, "seq": 3,
             "samples": [{"duration": 100, "size": 9}]},
            {"bmdt": 0, "seq": 1,
             "samples": [{"duration": 100, "size": 1}]},
        ]
        merged = frag.build_timeline(fragments)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["size"], 9)

    def test_later_arrival_wins_without_seq(self):
        fragments = [
            {"bmdt": 0, "samples": [{"duration": 100, "size": 1}]},
            {"bmdt": 0, "samples": [{"duration": 100, "size": 2}]},
        ]
        merged = frag.build_timeline(fragments)
        self.assertEqual(merged[0]["size"], 2)

    def test_partial_overlap_replaces_only_shared_start(self):
        fragments = [
            {"bmdt": 0, "seq": 1, "samples": [
                {"duration": 100, "size": 1}, {"duration": 100, "size": 2}]},
            {"bmdt": 100, "seq": 2, "samples": [
                {"duration": 100, "size": 20}]},
        ]
        merged = frag.build_timeline(fragments)
        self.assertEqual([(s["pts"], s["size"]) for s in merged],
                         [(0, 1), (100, 20)])

    def test_gaps_between_samples_only(self):
        samples = frag.build_timeline([
            {"bmdt": 0, "samples": [{"duration": 100, "size": 1}]},
            {"bmdt": 300, "samples": [{"duration": 100, "size": 2}]},
        ])
        gaps = frag.build_gaps(samples)
        self.assertEqual(gaps, [{"pts": 100, "dur": 200}])
        # trailing hole is not a gap
        self.assertEqual(frag.build_gaps(samples[:1]), [])

    def test_probe_gap_and_nearest(self):
        samples = frag.build_timeline([
            {"bmdt": 0, "samples": [{"duration": 100, "size": 1}]},
            {"bmdt": 300, "samples": [{"duration": 100, "size": 2}]},
        ])
        gaps = frag.build_gaps(samples)
        found, pts, dur, size = frag.probe(samples, gaps, 50)
        self.assertTrue(found)
        self.assertEqual((pts, dur, size), (0, 100, 1))
        found, pts, *_ = frag.probe(samples, gaps, 200)
        self.assertFalse(found)
        self.assertEqual(pts, 300)  # next sample after the gap
        found, pts, *_ = frag.probe(samples, gaps, 9999)
        self.assertFalse(found)
        self.assertIsNone(pts)     # past the last sample


class CliTests(unittest.TestCase):
    def test_end_to_end(self):
        payload = {
            "tracks": [{
                "id": "v",
                "timescale": 1000,
                "fragments": [
                    {"bmdt": 200, "samples": [{"duration": 100, "size": 3}]},
                    {"bmdt": 0, "seq": 5,
                     "samples": [{"duration": 200, "size": 7}]},
                    {"bmdt": 0, "seq": 2,
                     "samples": [{"duration": 200, "size": 1}]},
                ],
            }],
            "targets": [
                {"track": "v", "t_ms": 50},
                {"track": "v", "t_ms": 250},
            ],
        }
        os.makedirs(TMPROOT, exist_ok=True)
        td = tempfile.mkdtemp(dir=TMPROOT)
        ip = os.path.join(td, "in.json")
        op = os.path.join(td, "out.json")
        with open(ip, "w") as f:
            json.dump(payload, f)
        subprocess.run([sys.executable, os.path.join(HERE, "frag.py"),
                        ip, op], check=True)
        with open(op) as f:
            out = json.load(f)
        t = out["tracks"][0]
        self.assertEqual([s["pts"] for s in t["timeline"]], [0, 200])
        self.assertEqual(t["timeline"][0]["size"], 7)  # seq 5 beats seq 2
        self.assertEqual(t["gaps"], [])
        self.assertEqual(t["duration_ms"], 0.3)
        self.assertEqual([(a["found"], a["pts"]) for a in out["answers"]],
                         [(True, 0), (True, 200)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
