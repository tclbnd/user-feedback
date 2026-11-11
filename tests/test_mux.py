import hashlib
import json
import sys
import unittest
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import synthetic
from ingest import read_capture
from mp4 import parse_fragment, parse_init_tracks
from mux import plan_session

VIDEO_TIMESCALE = 90000
AUDIO_TIMESCALE = 48000
VIDEO_ORIGIN = VIDEO_TIMESCALE * 10
AUDIO_ORIGIN = AUDIO_TIMESCALE * 10


class CaptureBuilder:
    def __init__(self, root: Path):
        self.root = root
        self.events = []

    def generation(self, tag: str, epoch: str, last: int, flavour: int = 0,
                   skip_video=(), skip_audio=()):
        video_init = "video-init-%s.mp4" % tag
        audio_init = "audio-init-%s.mp4" % tag
        (self.root / video_init).write_bytes(
            synthetic.make_init(7, VIDEO_TIMESCALE, b"vide", flavour))
        (self.root / audio_init).write_bytes(
            synthetic.make_init(9, AUDIO_TIMESCALE, b"soun", flavour))
        self.events.append({
            "type": "open", "epoch": epoch,
            "tracks": {
                "video": {"init": video_init,
                          "timescale": VIDEO_TIMESCALE,
                          "clock_origin": VIDEO_ORIGIN},
                "audio": {"init": audio_init,
                          "timescale": AUDIO_TIMESCALE,
                          "clock_origin": AUDIO_ORIGIN},
            },
        })
        for seq in range(1, last + 1):
            specs = (
                ("video", 7, 90000, VIDEO_ORIGIN, True, seq not in skip_video),
                ("audio", 9, 48000, AUDIO_ORIGIN, False, seq not in skip_audio),
            )
            for track, track_id, timescale, origin, video, include in specs:
                if not include:
                    continue
                payload = (
                    synthetic.make_fragment(
                        track_id, seq, synthetic.video_gop(origin, seq - 1))
                    if video else
                    synthetic.make_fragment(
                        track_id, seq, synthetic.audio_gop(origin, seq - 1))
                )
                name = "%s-%s-%03d.m4s" % (tag, track, seq)
                path = self.root / name
                path.write_bytes(payload)
                self.events.append({
                    "type": "fragment", "epoch": epoch, "track": track,
                    "seq": seq, "path": name,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                })
        self.events.append({
            "type": "seal", "epoch": epoch,
            "last": {"video": last, "audio": last},
        })

    def log(self, name: str = "capture.jsonl") -> Path:
        path = self.root / name
        path.write_text("".join(json.dumps(event) + "\n"
                                for event in self.events))
        return path


def _samples(segment, track_id):
    return parse_fragment(segment["data"])["tracks"].get(track_id, [])


def _source_samples(root, capture):
    result = {"video": [], "audio": []}
    for line in capture.read_text().splitlines():
        event = json.loads(line)
        if event["type"] != "fragment":
            continue
        parsed = parse_fragment((root / event["path"]).read_bytes())
        result[event["track"]].extend(next(iter(parsed["tracks"].values())))
    return result


class PackagingTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def plan(self, builder) -> dict:
        return plan_session(read_capture(builder.log()))

    def test_normal_capture_is_gop_aligned_and_payload_preserving(self):
        builder = CaptureBuilder(self.root)
        builder.generation("n", "recording-0", 3)
        capture = builder.log()
        plan = self.plan(builder)

        playlist = plan["playlist"]
        self.assertIn("#EXT-X-PLAYLIST-TYPE:EVENT", playlist)
        self.assertIn("#EXT-X-ENDLIST", playlist)
        self.assertIn('#EXT-X-MAP:URI="init.mp4"', playlist)
        self.assertEqual(len(plan["segments"]), 3)
        self.assertEqual(parse_init_tracks(plan["inits"]["init.mp4"]),
                         [{"track_id": 1, "timescale": VIDEO_TIMESCALE},
                          {"track_id": 2, "timescale": AUDIO_TIMESCALE}])

        source = _source_samples(self.root, capture)
        for segment in plan["segments"]:
            video = _samples(segment, 1)
            audio = _samples(segment, 2)
            # A segment starts at an independently decodable frame.
            self.assertEqual(video[0]["flags"] >> 16 & 1, 0,
                             "segment must start on a sync sample")
            # Decode order and PTS/DTS offsets are preserved.
            for index, sample in enumerate(video[1:], 1):
                self.assertEqual(sample["dts"],
                                 video[index - 1]["dts"]
                                 + video[index - 1]["duration"])
            self.assertEqual(video[0]["duration"], 3003)
            self.assertEqual(audio[0]["duration"], 1024)
            # Every retained compressed payload byte is unchanged.
            for sample in video + audio:
                self.assertIn(sample["data"], [source_sample["data"]
                                               for source_sample in
                                               source["video"] + source["audio"]])

            if audio:
                offset = abs(Fraction(video[0]["dts"], VIDEO_TIMESCALE)
                             - Fraction(audio[0]["dts"], AUDIO_TIMESCALE))
                self.assertLessEqual(offset,
                                     Fraction(1024, AUDIO_TIMESCALE))

        # The first segment's audio and video share the clock origin.
        first_video = _samples(plan["segments"][0], 1)[0]
        first_audio = _samples(plan["segments"][0], 2)[0]
        offset = abs(Fraction(first_video["pts"], VIDEO_TIMESCALE)
                     - Fraction(first_audio["pts"], AUDIO_TIMESCALE))
        self.assertLessEqual(offset, Fraction(1024, AUDIO_TIMESCALE))

    def test_reordering_and_duplicates_do_not_change_publication(self):
        builder = CaptureBuilder(self.root)
        builder.generation("n", "recording-0", 3)
        normal = self.plan(builder)

        reordered = CaptureBuilder(self.root)
        events = []
        reordered.generation("r", "recording-0", 3)
        events = list(reordered.events)
        fragments = [event for event in events if event["type"] == "fragment"]
        fixed = [event for event in events if event["type"] != "fragment"]
        head, tail = fixed[0], fixed[1]
        mixed = ([head] + list(reversed(fragments[:5]))
                 + fragments[:2] + fragments[5:] + [tail])
        log = self.root / "reordered.jsonl"
        log.write_text("".join(json.dumps(event) + "\n" for event in mixed))
        other = plan_session(read_capture(log))

        self.assertEqual(normal["playlist"], other["playlist"])
        self.assertEqual([segment["data"] for segment in normal["segments"]],
                         [segment["data"] for segment in other["segments"]])

    def test_permanent_hole_recovers_at_next_keyframe_and_drops_gap_audio(self):
        builder = CaptureBuilder(self.root)
        builder.generation("m", "recording-0", 3, skip_video=(2,))
        plan = self.plan(builder)
        self.assertEqual(len(plan["segments"]), 2)
        video = _samples(plan["segments"][0], 1)
        self.assertEqual(len(video), 60)
        second = _samples(plan["segments"][1], 1)
        self.assertEqual(second[0]["flags"] >> 16 & 1, 0)
        # Recovery starts at the third GOP's presentation timestamp.
        self.assertEqual(second[0]["dts"], 2 * 60 * 3003)
        # Audio belonging to the lost GOP is not published.
        audio = _samples(plan["segments"][0], 2)
        last_audio_end = (Fraction(audio[-1]["pts"] + audio[-1]["duration"],
                                   AUDIO_TIMESCALE) if audio else Fraction(0))
        self.assertLessEqual(last_audio_end,
                             Fraction(max(sample["pts"] + sample["duration"]
                                          for sample in video),
                                      VIDEO_TIMESCALE) + Fraction(1, 100))
        self.assertFalse(any(
            sample["data"].startswith(b"audio-1-")
            for segment in plan["segments"] for track in (2,)
            for sample in _samples(segment, track)
        ))

    def test_timestamp_reset_marks_discontinuity_and_rebases(self):
        builder = CaptureBuilder(self.root)
        builder.generation("a", "recording-0", 3)
        builder.generation("b", "recording-1", 3)
        plan = self.plan(builder)
        self.assertEqual(
            [segment["discontinuity"] for segment in plan["segments"]],
            [False, False, False, True, False, False],
        )
        self.assertEqual(plan["playlist"].count("#EXT-X-DISCONTINUITY"), 1)
        self.assertEqual(plan["playlist"].count("#EXT-X-MAP"), 1)

        epoch0 = plan["segments"][:3]
        epoch1 = plan["segments"][3:]
        last_end = max(Fraction(sample["pts"] + sample["duration"],
                                VIDEO_TIMESCALE)
                       for sample in _samples(epoch0[-1], 1))
        first = _samples(epoch1[0], 1)[0]
        self.assertGreaterEqual(Fraction(first["pts"], VIDEO_TIMESCALE),
                                last_end - Fraction(3003, VIDEO_TIMESCALE))
        first_audio = _samples(epoch1[0], 2)[0]
        self.assertLessEqual(
            abs(Fraction(first["pts"], VIDEO_TIMESCALE)
                - Fraction(first_audio["pts"], AUDIO_TIMESCALE)),
            Fraction(1024, AUDIO_TIMESCALE),
        )

    def test_changed_init_switches_map_and_marks_discontinuity(self):
        builder = CaptureBuilder(self.root)
        builder.generation("a", "recording-0", 2, flavour=0)
        builder.generation("b", "recording-1", 2, flavour=99)
        plan = self.plan(builder)
        self.assertEqual(len(plan["inits"]), 2)
        self.assertIn('#EXT-X-MAP:URI="init-2.mp4"', plan["playlist"])
        self.assertEqual(plan["playlist"].count("#EXT-X-DISCONTINUITY"), 1)
        self.assertEqual(plan["segments"][2]["discontinuity"], True)
        self.assertEqual(plan["segments"][2]["init"], "init-2.mp4")


if __name__ == "__main__":
    unittest.main()
