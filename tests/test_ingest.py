import hashlib
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import synthetic
from ingest import CaptureError, read_capture


def _fragment(root: Path, name: str, track_id: int, seq: int,
              video: bool = True) -> Path:
    samples = (synthetic.video_gop(90000 * 10, seq - 1)
               if video else synthetic.audio_gop(48000 * 10, seq - 1))
    payload = synthetic.make_fragment(track_id, seq, samples)
    path = root / name
    path.write_bytes(payload)
    return path


def _event(epoch: str, track: str, seq: int, path: Path,
           declared: str = None, size: int = None) -> dict:
    event = {
        "type": "fragment", "epoch": epoch, "track": track, "seq": seq,
        "path": str(path.name),
        "sha256": declared or hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    if size is not None:
        event["size"] = size
    return event


class IngestTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "video-init.mp4").write_bytes(
            synthetic.make_init(7, 90000, b"vide"))
        (self.root / "audio-init.mp4").write_bytes(
            synthetic.make_init(9, 48000, b"soun"))

    def tearDown(self):
        self.temp.cleanup()

    def write(self, events) -> Path:
        log = self.root / "capture.jsonl"
        log.write_text("".join(json.dumps(event) + "\n" for event in events))
        return log

    def open_event(self, epoch="recording-0") -> dict:
        return {
            "type": "open", "epoch": epoch,
            "tracks": {
                "video": {"init": "video-init.mp4", "timescale": 90000,
                          "clock_origin": 90000 * 10},
                "audio": {"init": "audio-init.mp4", "timescale": 48000,
                          "clock_origin": 48000 * 10},
            },
        }

    def test_out_of_order_and_duplicate_deliveries_are_identical(self):
        events = [self.open_event()]
        video = [_fragment(self.root, "v%d.m4s" % seq, 7, seq)
                 for seq in (1, 2, 3)]
        audio = [_fragment(self.root, "a%d.m4s" % seq, 9, seq, video=False)
                 for seq in (1, 2, 3)]
        arrivals = [
            _event("recording-0", "video", 3, video[2]),
            _event("recording-0", "audio", 1, audio[0]),
            _event("recording-0", "video", 1, video[0]),
            _event("recording-0", "video", 2, video[1]),
            _event("recording-0", "video", 1, video[0]),   # duplicate
            _event("recording-0", "audio", 3, audio[2]),
            _event("recording-0", "audio", 2, audio[1]),
            _event("recording-0", "audio", 3, audio[2]),   # duplicate
        ]
        events += arrivals
        events.append({"type": "seal", "epoch": "recording-0",
                       "last": {"video": 3, "audio": 3}})
        session = read_capture(self.write(events))
        for name in ("video", "audio"):
            track = session["tracks"][name]
            self.assertEqual([path.name for path in track["fragments"]],
                             ["%s%d.m4s" % (name[0], seq)
                              for seq in (1, 2, 3)])
            self.assertEqual(track["missing"], [])

    def test_complete_conflicting_content_is_reported(self):
        events = [self.open_event()]
        first = _fragment(self.root, "first.m4s", 7, 1)
        second = _fragment(self.root, "second.m4s", 7, 1)
        second.write_bytes(second.read_bytes() + b"different")
        events.append(_event("recording-0", "video", 1, first))
        events.append(_event("recording-0", "video", 1, second))
        events.append({"type": "seal", "epoch": "recording-0",
                       "last": {"video": 1, "audio": 0}})
        with self.assertRaises(CaptureError) as caught:
            read_capture(self.write(events))
        self.assertIn("conflict", str(caught.exception))

    def test_truncated_delivery_may_be_followed_by_complete_one(self):
        events = [self.open_event()]
        full = _fragment(self.root, "full.m4s", 7, 1)
        truncated = self.root / "short.m4s"
        truncated.write_bytes(full.read_bytes()[:-11])
        declared = hashlib.sha256(full.read_bytes()).hexdigest()
        events.append(_event("recording-0", "video", 1, truncated,
                             declared=declared))
        events.append(_event("recording-0", "video", 1, full))
        events.append({"type": "seal", "epoch": "recording-0",
                       "last": {"video": 1, "audio": 0}})
        session = read_capture(self.write(events))
        self.assertEqual(session["tracks"]["video"]["fragments"][0], full)
        self.assertEqual(session["tracks"]["video"]["rejected"][0]["reason"],
                         "digest")

    def test_declared_size_must_match(self):
        events = [self.open_event()]
        full = _fragment(self.root, "full.m4s", 7, 1)
        events.append(_event("recording-0", "video", 1, full,
                             size=full.stat().st_size + 1))
        events.append(_event("recording-0", "video", 1, full))
        events.append({"type": "seal", "epoch": "recording-0",
                       "last": {"video": 1, "audio": 0}})
        session = read_capture(self.write(events))
        self.assertEqual(session["tracks"]["video"]["missing"], [])

    def test_holes_are_permanent_only_after_seal(self):
        events = [self.open_event()]
        events.append(_event("recording-0", "video", 1,
                             _fragment(self.root, "v1.m4s", 7, 1)))
        events.append(_event("recording-0", "video", 3,
                             _fragment(self.root, "v3.m4s", 7, 3)))
        # Not sealed: the capture is not publishable.
        with self.assertRaises(CaptureError):
            read_capture(self.write(events))
        events.append({"type": "seal", "epoch": "recording-0",
                       "last": {"video": 3, "audio": 0}})
        session = read_capture(self.write(events))
        self.assertEqual(session["tracks"]["video"]["missing"], [2])
        # A late retransmission may still fill the hole after sealing.
        late = self.write(events + [
            _event("recording-0", "video", 2, self.root / "v2.m4s",
                   declared=hashlib.sha256(
                       _fragment(self.root, "v2.m4s", 7, 2).read_bytes()
                   ).hexdigest())
        ])
        session = read_capture(late)
        self.assertEqual(session["tracks"]["video"]["missing"], [])

    def test_multiple_generations_are_kept_apart(self):
        events = []
        for generation in ("recording-0", "recording-1"):
            events.append(self.open_event(generation))
            for seq in (1, 2):
                events.append(_event(generation, "video", seq,
                                     _fragment(self.root, "v%d.m4s" % seq,
                                               7, seq)))
            events.append({"type": "seal", "epoch": generation,
                           "last": {"video": 2, "audio": 0}})
        session = read_capture(self.write(events))
        self.assertEqual([epoch["epoch"] for epoch in session["epochs"]],
                         ["recording-0", "recording-1"])
        self.assertEqual(session["epoch"], "recording-0")


if __name__ == "__main__":
    unittest.main()
