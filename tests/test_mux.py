"""Muxing correctness: payload preservation, PTS/DTS, B-frames, A/V alignment."""

import hashlib
import unittest
from pathlib import Path

from fmp4 import read_fragment, read_track_runs, trex_defaults
from tests._support import FixtureTestCase, decode_playlist

VIDEO_TICKS = 90000
AUDIO_TICKS = 48000


def _source_samples(fixture: Path, track: str, is_video: bool):
    init = (fixture / f"{track}-init.mp4").read_bytes()
    trex = next(iter(trex_defaults(init).values()))
    rows = []
    for path in sorted(fixture.glob(f"{track}-0*.m4s")):
        fragment = read_fragment(path.read_bytes(), is_video, trex)
        cursor = fragment.base_media_decode_time
        for sample in fragment.samples:
            rows.append((cursor, sample.cto, sample.duration,
                         hashlib.sha256(sample.payload).hexdigest()))
            cursor += sample.duration
    return rows


def _published_samples(output: Path):
    per_track = {1: [], 2: []}
    for path in sorted(output.glob("segment-*.m4s")):
        runs = read_track_runs(path.read_bytes())
        for track_id, run in runs.items():
            cursor = run.base_media_decode_time
            for sample in run.samples:
                per_track[track_id].append(
                    (cursor, sample.cto, sample.duration,
                     hashlib.sha256(sample.payload).hexdigest()))
                cursor += sample.duration
    return per_track


class PackagingTest(FixtureTestCase):
    scenario = "sample"

    def setUp(self):
        code, self.output = self.publish("pub")
        self.assertEqual(code, 0)

    def test_both_tracks_actually_decode(self):
        result = decode_playlist(self.output / "index.m3u8")
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_every_sample_payload_dts_cto_duration_preserved(self):
        source_video = _source_samples(self.fixture, "video", True)
        source_audio = _source_samples(self.fixture, "audio", False)
        published = _published_samples(self.output)
        self.assertEqual(len(source_video), len(published[1]))
        self.assertEqual(len(source_audio), len(published[2]))
        for src, pub in ((source_video, published[1]),
                         (source_audio, published[2])):
            for a, b in zip(src, pub):
                self.assertEqual(a, b)

    def test_b_frame_decode_order_and_pts_dts_offset_preserved(self):
        published = _published_samples(self.output)[1]
        # Composition offsets must survive and PTS must differ from DTS.
        self.assertTrue(any(cto != 0 for _, cto, _, _ in published))
        pts_dts = [(dts + cto, dts) for dts, cto, _, _ in published]
        self.assertTrue(any(pts != dts for pts, dts in pts_dts))
        # DTS stays strictly monotonic (decode order is intact).
        dts_values = [dts for dts, _, _, _ in published]
        self.assertEqual(dts_values, sorted(dts_values))

    def test_audio_video_share_one_timeline_via_clock_origins(self):
        published = _published_samples(self.output)
        first_video = published[1][0][0] / VIDEO_TICKS
        first_audio = published[2][0][0] / AUDIO_TICKS
        self.assertAlmostEqual(first_video, first_audio, delta=1e-6)
        # Non-integer frame rate: consecutive video delta is 3003/90000 s.
        d0, d1 = published[1][0][0], published[1][1][0]
        self.assertEqual(d1 - d0, 3003)

    def test_every_segment_starts_on_a_sync_sample(self):
        for path in sorted(self.output.glob("segment-*.m4s")):
            runs = read_track_runs(path.read_bytes())
            self.assertTrue(runs[1].samples[0].is_sync, path.name)


class ReorderPackagingTest(FixtureTestCase):
    scenario = "reorder"

    def test_nonzero_clock_origins_align_the_two_tracks(self):
        code, output = self.publish("pub")
        self.assertEqual(code, 0)
        result = decode_playlist(output / "index.m3u8")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        published = _published_samples(output)
        # Fixture declares origins 9000 (video) and 4800 (audio) = 0.1 s.
        v0 = published[1][0][0] / VIDEO_TICKS
        a0 = published[2][0][0] / AUDIO_TICKS
        self.assertAlmostEqual(v0, a0, delta=1e-6)


class PacketVerificationTest(FixtureTestCase):
    scenario = "sample"

    def test_verify_module_checks_every_retained_packet(self):
        from ingest import read_capture
        from verify import verify_packets, verify_generation_coverage
        code, output = self.publish("pub")
        self.assertEqual(code, 0)
        session = read_capture(self.capture)
        # Raises AssertionError on any payload/timestamp/A-V mismatch.
        verify_packets(session, output)
        coverage = verify_generation_coverage(session, output)
        self.assertEqual(coverage["recording-0"]["video"]["missing"], [])
        self.assertEqual(coverage["recording-0"]["audio"]["missing"], [])


if __name__ == "__main__":
    unittest.main()
