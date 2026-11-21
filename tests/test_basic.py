import sys
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build import cluster, el, frame_bytes, group, mkv, simple, track, u  # noqa: E402
from mkvkit.ebml import EbmlError  # noqa: E402
from mkvkit.mkv import scan  # noqa: E402


class BasicTest(unittest.TestCase):
    def test_two_tracks(self):
        data = mkv([track(1, 1, "V_VP9"), track(2, 2, "A_OPUS")],
                   [cluster(0, [simple(1, 0, [frame_bytes(500)]), simple(2, 0, [frame_bytes(100), frame_bytes(120)], mode="xiph")]),
                    cluster(40, [simple(1, 0, [frame_bytes(300)], key=False)])], duration=80.0)
        out = scan(data)
        v, a = out["tracks"]
        self.assertEqual((v["codec"], v["blocks"], v["keyframes"], v["bytes"]), ("V_VP9", 2, 1, 800))
        self.assertEqual((a["frames"], a["bytes"]), (2, 220))
        self.assertEqual(v["last_ms"], "40")
        self.assertEqual(out["clusters"], 2)

    def test_not_ebml(self):
        with self.assertRaises(EbmlError):
            scan(b"RIFF" + bytes(20))

    def test_unknown_size_segment_and_clusters(self):
        """Live streams leave Segment and Cluster sizes unknown."""
        blocks = [simple(1, 0, [frame_bytes(100)]),
                  simple(2, -6, [frame_bytes(40), frame_bytes(50), frame_bytes(60)], mode="ebml")]
        data = mkv([track(1, 1, "V_VP8"), track(2, 2, "A_OPUS")],
                   [cluster(0, blocks, unknown=True),
                    cluster(500, [simple(1, 0, [frame_bytes(70)], key=False)], unknown=True)],
                   seg_unknown=True)
        out = scan(data)
        v, a = out["tracks"]
        self.assertEqual(out["clusters"], 2)
        self.assertEqual((v["blocks"], v["bytes"]), (2, 170))
        self.assertEqual((a["blocks"], a["frames"], a["bytes"]), (1, 3, 150))

    def test_ebml_lacing_signed_sizes(self):
        for sizes in ([10, 391], [703, 222, 433], [744, 30, 541, 228]):
            frames = [frame_bytes(n, i) for i, n in enumerate(sizes)]
            data = mkv([track(1, 2, "A_OPUS")], [cluster(0, [simple(1, 0, frames, mode="ebml")])])
            t = scan(data)["tracks"][0]
            self.assertEqual((t["frames"], t["bytes"]), (len(sizes), sum(sizes)))

    def test_xiph_and_fixed_lacing(self):
        for mode, frames in (("xiph", [frame_bytes(300), frame_bytes(20), frame_bytes(700)]),
                             ("fixed", [frame_bytes(64)] * 3)):
            data = mkv([track(1, 2, "A_OPUS")], [cluster(0, [simple(1, 0, frames, mode=mode)])])
            t = scan(data)["tracks"][0]
            self.assertEqual((t["frames"], t["bytes"]), (3, sum(len(f) for f in frames)))

    def test_negative_relative_timestamp(self):
        """Audio that starts before the cluster timestamp (codec delay)."""
        data = mkv([track(2, 2, "A_OPUS")],
                   [cluster(0, [simple(2, -6, [frame_bytes(60)])])])
        t = scan(data)["tracks"][0]
        self.assertEqual((t["first_ms"], t["last_ms"]), ("-6", "-6"))

    def test_timestamp_scale_not_one_ms(self):
        """Editor exports ticks in 10 ms units; timestamps must use the scale."""
        data = mkv([track(1, 1, "V_MPEG4/ISO/AVC")],
                   [cluster(1000, [simple(1, 0, [frame_bytes(50)]),
                                   simple(1, 40, [frame_bytes(50)], key=False)])],
                   scale=10000000, duration=200.0, double=True)
        out = scan(data)
        self.assertEqual(out["timestamp_scale"], 10000000)
        self.assertEqual(out["duration_ms"], 2000.0)
        self.assertEqual((out["tracks"][0]["first_ms"], out["tracks"][0]["last_ms"]), ("10000", "10400"))

    def test_duration_float_and_double(self):
        for double in (False, True):
            data = mkv([track(1, 1, "V_VP9")], [cluster(0, [simple(1, 0, [frame_bytes(1)])])],
                       duration=1234.5, double=double)
            self.assertEqual(scan(data)["duration_ms"], 1234.5)

    def test_blockgroup_video_keyframes(self):
        """BlockGroups are keyframes unless they carry a ReferenceBlock."""
        data = mkv([track(1, 1, "V_MPEG4/ISO/AVC")],
                   [cluster(0, [group(1, 0, [frame_bytes(50)]),
                                group(1, 40, [frame_bytes(50)], ref=-40),
                                group(1, 80, [frame_bytes(50)])])])
        t = scan(data)["tracks"][0]
        self.assertEqual((t["blocks"], t["frames"], t["keyframes"]), (3, 3, 2))

    def test_blockgroup_laced_frames(self):
        data = mkv([track(1, 2, "A_OPUS")],
                   [cluster(0, [group(1, 0, [frame_bytes(11), frame_bytes(22), frame_bytes(5)],
                                      mode="ebml", duration=20)])])
        t = scan(data)["tracks"][0]
        self.assertEqual((t["blocks"], t["frames"], t["bytes"]), (1, 3, 38))

    def test_content_encoding_compression_and_header_stripping(self):
        raw = frame_bytes(200)
        stripped = raw[:4]
        tracks = [
            el(0xAE, u(0xD7, 1) + u(0x73C5, 1) + u(0x83, 2) + el(0x86, b"A_OPUS")
               + el(0x6D80, el(0x6240, el(0x5034, u(0x4254, 0))))),
            el(0xAE, u(0xD7, 2) + u(0x73C5, 2) + u(0x83, 2) + el(0x86, b"A_AAC")
               + el(0x6D80, el(0x6240, el(0x5034, u(0x4254, 3) + el(0x4255, stripped))))),
        ]
        data = mkv(tracks, [cluster(0, [simple(1, 0, [zlib.compress(raw)]),
                                        simple(2, 0, [raw[4:]])])])
        t1, t2 = scan(data)["tracks"]
        self.assertEqual(t1["bytes"], len(raw))
        self.assertEqual(t2["bytes"], len(raw))

    def test_language_bcp47_and_flags(self):
        tracks = [el(0xAE, u(0xD7, 1) + u(0x83, 1) + el(0x86, b"V_VP9")
                     + el(0x22B59C, b"und") + el(0x22B59D, b"en-US") + u(0x88, 0)),
                  el(0xAE, u(0xD7, 2) + u(0x83, 2) + el(0x86, b"A_OPUS")
                     + el(0x22B59C, b"fra") + u(0xB9, 0))]
        data = mkv(tracks, [cluster(0, [simple(1, 0, [frame_bytes(1)])])])
        t1, t2 = scan(data)["tracks"]
        self.assertEqual((t1["language"], t1["default"]), ("en-US", False))
        self.assertEqual((t2["language"], t2["enabled"]), ("fra", False))

    def test_truncated_live_tail(self):
        """A recording cut mid-block keeps the blocks before the cut."""
        data = mkv([track(1, 1, "V_VP9")],
                   [cluster(0, [simple(1, 0, [frame_bytes(100)]),
                                simple(1, 40, [frame_bytes(100)])], unknown=True)],
                   seg_unknown=True)
        out = scan(data[:-40])
        self.assertEqual(out["clusters"], 1)
        self.assertEqual(out["tracks"][0]["blocks"], 1)

    def test_examples(self):
        root = Path(__file__).resolve().parents[1] / "examples"
        meeting = scan((root / "meeting.webm").read_bytes())
        v, a = meeting["tracks"]
        self.assertEqual((v["codec"], v["blocks"], v["frames"], v["keyframes"]), ("V_VP8", 6, 6, 3))
        self.assertEqual((a["codec"], a["frames"]), ("A_OPUS", 18))
        self.assertEqual((v["first_ms"], v["last_ms"]), ("0", "4033"))
        self.assertEqual((a["first_ms"], a["last_ms"]), ("-6", "4014"))

        edit = scan((root / "edit.mkv").read_bytes())
        v, a = edit["tracks"]
        self.assertEqual(edit["timestamp_scale"], 10000000)
        self.assertEqual(edit["duration_ms"], 2100.0)
        self.assertEqual((v["blocks"], v["frames"], v["keyframes"]), (8, 8, 4))
        self.assertEqual((v["first_ms"], v["last_ms"]), ("0", "15040"))
        self.assertEqual((a["first_ms"], a["last_ms"]), ("0", "15100"))


if __name__ == "__main__":
    unittest.main()

