"""Ingestion semantics: ordering window, redelivery, conflict, truncation."""

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ingest import (  # noqa: E402
    ConflictError, IngestError, WindowError, read_capture)
from tests._support import FixtureTestCase, ensure_fixture, run_recorder


class ReorderTest(FixtureTestCase):
    scenario = "reorder"

    def test_shuffled_redelivery_publishes_clean_playlist(self):
        code, output = self.publish("pub")
        self.assertEqual(code, 0)
        playlist = (output / "index.m3u8").read_text()
        self.assertIn("#EXT-X-PLAYLIST-TYPE:EVENT", playlist)
        self.assertIn("#EXT-X-ENDLIST", playlist)
        self.assertNotIn("DISCONTINUITY", playlist)

    def test_shuffled_matches_in_order_delivery_byte_for_byte(self):
        # Build an in-order variant of the same media and compare outputs.
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            ordered_dir = temp / "ordered"
            shutil.copytree(self.fixture, ordered_dir)
            events = [json.loads(line) for line in
                      (ordered_dir / "capture.jsonl").read_text().splitlines()]
            head = [e for e in events if e["type"] == "open"]
            tail = [e for e in events if e["type"] == "seal"]
            frags = sorted((e for e in events if e["type"] == "fragment"),
                           key=lambda e: (e["track"], e["seq"]))
            (ordered_dir / "capture.jsonl").write_text(
                "\n".join(json.dumps(e) for e in head + frags + tail) + "\n")
            out_a = temp / "a"
            out_b = temp / "b"
            self.assertEqual(run_recorder(self.capture, out_a), 0)
            self.assertEqual(run_recorder(ordered_dir / "capture.jsonl", out_b), 0)
            names = sorted(p.name for p in out_a.iterdir())
            self.assertEqual(names, sorted(p.name for p in out_b.iterdir()))
            for name in names:
                self.assertEqual((out_a / name).read_bytes(),
                                 (out_b / name).read_bytes(), name)


class TruncatedTest(FixtureTestCase):
    scenario = "truncated"

    def test_truncated_then_completed_recovers(self):
        code, output = self.publish("pub")
        self.assertEqual(code, 0)
        # The completed redelivery must win; no permanent gap is recorded.
        progress = json.loads((output / "progress.json").read_text())
        for epoch in progress["epochs"]:
            for track in epoch["tracks"].values():
                self.assertEqual(track["missing"], [])

    def test_truncated_only_at_seal_is_rejected(self):
        # Drop the completing redeliveries, leaving the truncated first
        # deliveries unresolved when the generation is sealed.
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            d = temp / "cap"
            shutil.copytree(self.fixture, d)
            events = [json.loads(line) for line in
                      (d / "capture.jsonl").read_text().splitlines()]
            kept = [e for e in events
                    if "complete redelivery" not in e.get("summary", "")]
            (d / "capture.jsonl").write_text(
                "\n".join(json.dumps(e) for e in kept) + "\n")
            with self.assertRaises(IngestError):
                read_capture(d / "capture.jsonl")


class ConflictTest(FixtureTestCase):
    scenario = "conflict"

    def test_same_seq_different_content_raises_conflict(self):
        with self.assertRaises(ConflictError):
            read_capture(self.capture)

    def test_recorder_exits_nonzero_on_conflict(self):
        with tempfile.TemporaryDirectory() as temp:
            code = run_recorder(self.capture, Path(temp) / "out")
            self.assertNotEqual(code, 0)


class WindowTest(unittest.TestCase):
    def test_delivery_beyond_window_is_rejected(self):
        # The missing fixture has six video fragments, enough to lead the
        # first expected sequence number by more than the four-deep window.
        fixture = ensure_fixture("missing")
        events = [json.loads(line) for line in
                  (fixture / "capture.jsonl").read_text().splitlines()]
        head = [e for e in events if e["type"] == "open"]
        tail = [e for e in events if e["type"] == "seal"]
        frags = [e for e in events if e["type"] == "fragment"]
        far = next(e for e in frags if e["track"] == "video" and e["seq"] >= 6)
        reordered = head + [far] + frags + tail
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            d = temp / "cap"
            shutil.copytree(fixture, d)
            (d / "capture.jsonl").write_text(
                "\n".join(json.dumps(e) for e in reordered) + "\n")
            with self.assertRaises(WindowError):
                read_capture(d / "capture.jsonl")


if __name__ == "__main__":
    unittest.main()
