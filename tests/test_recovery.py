"""Recovery: permanent gaps, timestamp reset / init switch, crash consistency."""

import hashlib
import json
import os
import shutil
import unittest
from pathlib import Path

from fmp4 import read_fragment, read_track_runs, trex_defaults
from mux import CRASH_AFTER_SEGMENT, CRASH_BEFORE_PROGRESS
from tests._support import FixtureTestCase, decode_playlist, run_recorder


def _source_digests(fixture: Path, track: str, is_video: bool):
    init = (fixture / f"{track}-init.mp4").read_bytes()
    trex = next(iter(trex_defaults(init).values()))
    result = {}
    for path in sorted(fixture.glob(f"{track}-0*.m4s")):
        seq = int(path.stem.split("-")[1])
        fragment = read_fragment(path.read_bytes(), is_video, trex)
        result[seq] = [hashlib.sha256(s.payload).hexdigest()
                       for s in fragment.samples]
    return result


class MissingFragmentTest(FixtureTestCase):
    scenario = "missing"

    def test_recovers_at_next_independently_decodable_boundary(self):
        code, output = self.publish("pub")
        self.assertEqual(code, 0)
        result = decode_playlist(output / "index.m3u8")
        self.assertEqual(result.returncode, 0, result.stderr.decode())

        video_src = _source_digests(self.fixture, "video", True)
        audio_src = _source_digests(self.fixture, "audio", False)
        segments = sorted(output.glob("segment-*.m4s"))
        first = read_track_runs(segments[0].read_bytes())
        second = read_track_runs(segments[1].read_bytes())

        # First segment keeps source seq 1; recovery resumes exactly at seq 3.
        self.assertEqual(
            [hashlib.sha256(s.payload).hexdigest() for s in first[1].samples],
            video_src[1])
        self.assertEqual(
            [hashlib.sha256(s.payload).hexdigest() for s in second[1].samples],
            video_src[3])
        self.assertTrue(second[1].samples[0].is_sync)

        # No audio payload from the missing audio fragment leaks through.
        retained_audio = set()
        for seq, digests in audio_src.items():
            if seq != 2:
                retained_audio.update(digests)
        for path in segments:
            for track_id, run in read_track_runs(path.read_bytes()).items():
                if track_id != 2:
                    continue
                for sample in run.samples:
                    self.assertIn(hashlib.sha256(sample.payload).hexdigest(),
                                  retained_audio)

        # The recovered segment re-aligns audio to the video boundary.
        v_start = second[1].base_media_decode_time / 90000
        self.assertIn(2, read_track_runs(segments[1].read_bytes()))
        a_start = second[2].base_media_decode_time / 48000
        self.assertLess(abs(v_start - a_start), 0.025)

    def test_playlist_marks_single_discontinuity_and_records_gap(self):
        _, output = self.publish("pub2")
        playlist = (output / "index.m3u8").read_text()
        self.assertEqual(playlist.count("#EXT-X-DISCONTINUITY"), 1)
        progress = json.loads((output / "progress.json").read_text())
        missing = progress["epochs"][0]["tracks"]["video"]["missing"]
        self.assertEqual(missing, [2])


class TimestampResetTest(FixtureTestCase):
    scenario = "reset"

    def test_two_init_maps_and_a_discontinuity(self):
        code, output = self.publish("pub")
        self.assertEqual(code, 0)
        playlist = (output / "index.m3u8").read_text()
        maps = [line for line in playlist.splitlines()
                if line.startswith("#EXT-X-MAP")]
        self.assertEqual(len(maps), 2)
        self.assertEqual(playlist.count("#EXT-X-DISCONTINUITY"), 1)

    def test_each_generation_decodes_cleanly_with_its_own_init(self):
        _, output = self.publish("pub2")
        progress = json.loads((output / "progress.json").read_text())
        by_init = {}
        for entry in progress["segments"]:
            by_init.setdefault(entry["init"], []).append(entry["file"])
        self.assertEqual(len(by_init), 2)
        # Concatenate each init with only the segments that reference it and
        # decode via the plain MP4 demuxer (avoids ffmpeg's HLS demuxer not
        # resetting its decoder on a mid-playlist moov change).
        for init_name, files in by_init.items():
            combined = output / ("combined-" + init_name)
            with combined.open("wb") as handle:
                handle.write((output / init_name).read_bytes())
                for name in files:
                    handle.write((output / name).read_bytes())
            result = decode_playlist(combined)
            self.assertEqual(result.returncode, 0, result.stderr.decode())


class CrashConsistencyTest(FixtureTestCase):
    scenario = "sample"

    def _baseline(self, base: Path) -> Path:
        baseline = base / "baseline"
        self.assertEqual(run_recorder(self.capture, baseline), 0)
        return baseline

    def _assert_playlist_references_only_readable_files(self, output: Path):
        playlist = output / "index.m3u8"
        if not playlist.exists():
            return  # crash point 1: playlist not published yet
        text = playlist.read_text()
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("#EXT-X-MAP"):
                self.assertTrue((output / line.split('"')[1]).is_file())
            elif line and not line.startswith("#"):
                self.assertTrue((output / line).is_file())
        self.assertIn("#EXT-X-ENDLIST", text)

    def test_crash_after_segment_write(self):
        with __import__("tempfile").TemporaryDirectory() as temp:
            temp = Path(temp)
            crashed = temp / "crashed"
            code = run_recorder(self.capture, crashed,
                                {CRASH_AFTER_SEGMENT: "1"})
            self.assertNotEqual(code, 0)
            self.assertFalse((crashed / "index.m3u8").exists())
            self.assertFalse((crashed / "progress.json").exists())
            self.assertFalse(list(crashed.glob("*.tmp")))
            # Replay the same log to completion.
            self.assertEqual(run_recorder(self.capture, crashed), 0)
            baseline = self._baseline(temp)
            for name in sorted(p.name for p in baseline.iterdir()):
                self.assertEqual((crashed / name).read_bytes(),
                                 (baseline / name).read_bytes(), name)

    def test_crash_between_playlist_and_progress(self):
        with __import__("tempfile").TemporaryDirectory() as temp:
            temp = Path(temp)
            crashed = temp / "crashed"
            code = run_recorder(self.capture, crashed,
                                {CRASH_BEFORE_PROGRESS: "1"})
            self.assertNotEqual(code, 0)
            self.assertTrue((crashed / "index.m3u8").exists())
            self.assertFalse((crashed / "progress.json").exists())
            self._assert_playlist_references_only_readable_files(crashed)
            # Snapshot published entries, then replay and require stability.
            before = {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in crashed.glob("segment-*.m4s")}
            self.assertEqual(run_recorder(self.capture, crashed), 0)
            for name, digest in before.items():
                self.assertEqual(
                    hashlib.sha256((crashed / name).read_bytes()).hexdigest(),
                    digest, name)
            baseline = self._baseline(temp)
            for name in sorted(p.name for p in baseline.iterdir()):
                self.assertEqual((crashed / name).read_bytes(),
                                 (baseline / name).read_bytes(), name)


if __name__ == "__main__":
    unittest.main()
