import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import synthetic
from mp4 import parse_fragment, parse_init_tracks

WORK = Path(__file__).resolve().parents[1]
RECORDER = WORK / "recorder.py"


def _write_capture(root: Path, last: int, name: str = "capture.jsonl") -> Path:
    (root / "video-init.mp4").write_bytes(
        synthetic.make_init(7, 90000, b"vide"))
    (root / "audio-init.mp4").write_bytes(
        synthetic.make_init(9, 48000, b"soun"))
    events = [{
        "type": "open", "epoch": "recording-0",
        "tracks": {
            "video": {"init": "video-init.mp4", "timescale": 90000,
                      "clock_origin": 90000 * 10},
            "audio": {"init": "audio-init.mp4", "timescale": 48000,
                      "clock_origin": 48000 * 10},
        },
    }]
    for seq in range(1, last + 1):
        for track, track_id, origin, video in (
                ("video", 7, 90000 * 10, True),
                ("audio", 9, 48000 * 10, False)):
            payload = (
                synthetic.make_fragment(
                    track_id, seq,
                    synthetic.video_gop(origin, seq - 1) if video
                    else synthetic.audio_gop(origin, seq - 1))
            )
            media = root / ("%s-%03d.m4s" % (track, seq))
            media.write_bytes(payload)
            events.append({
                "type": "fragment", "epoch": "recording-0", "track": track,
                "seq": seq, "path": media.name,
                "sha256": hashlib.sha256(payload).hexdigest(),
            })
    events.append({"type": "seal", "epoch": "recording-0",
                   "last": {"video": last, "audio": last}})
    log = root / name
    log.write_text("".join(json.dumps(event) + "\n" for event in events))
    return log


def _run(log: Path, output: Path, crash: str = None) -> int:
    environment = dict(os.environ)
    environment.pop("RECORDER_CRASH_AT", None)
    environment.pop("HLS_CRASH_AT", None)
    if crash:
        environment["RECORDER_CRASH_AT"] = crash
    return subprocess.run(
        [sys.executable, str(RECORDER), str(log), str(output)],
        env=environment,
    ).returncode


def _hash_tree(directory: Path) -> dict:
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(directory.iterdir()) if path.is_file()}


def _check_playlist(output: Path) -> None:
    index = output / "index.m3u8"
    if not index.exists():
        return
    text = index.read_text()
    assert text.endswith("\n")
    assert "#EXT-X-ENDLIST" in text
    for line in text.splitlines():
        if line and not line.startswith("#"):
            media = output / line
            assert media.is_file(), line
            # The referenced media must already be complete and readable.
            parse_fragment(media.read_bytes())
    for name in text.split("EXT-X-MAP:URI=\"")[1:]:
        init = output / name.split("\"")[0]
        assert init.is_file()
        parse_init_tracks(init.read_bytes())


class PublicationTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.log = _write_capture(self.source, 3)

    def tearDown(self):
        self.temp.cleanup()

    def test_crash_before_playlist_leaves_consistent_state_and_replay_matches(self):
        baseline = self.root / "baseline"
        self.assertEqual(_run(self.log, baseline), 0)
        expected = _hash_tree(baseline)

        crashed = self.root / "crashed"
        self.assertEqual(_run(self.log, crashed, "before_playlist"), -9)
        _check_playlist(crashed)
        self.assertFalse((crashed / "index.m3u8").exists())
        for media in crashed.iterdir():
            if media.is_file() and media.name.startswith("segment-"):
                parse_fragment(media.read_bytes())

        self.assertEqual(_run(self.log, crashed), 0)
        self.assertEqual(_hash_tree(crashed), expected)

    def test_crash_after_playlist_leaves_progress_unwritten_then_replays(self):
        baseline = self.root / "baseline"
        self.assertEqual(_run(self.log, baseline), 0)
        expected = _hash_tree(baseline)

        crashed = self.root / "crashed"
        self.assertEqual(_run(self.log, crashed, "after_playlist"), -9)
        _check_playlist(crashed)
        self.assertTrue((crashed / "index.m3u8").is_file())
        self.assertFalse((crashed / "progress.json").exists())

        self.assertEqual(_run(self.log, crashed), 0)
        self.assertEqual(_hash_tree(crashed), expected)

    def test_already_published_entries_stay_stable_when_the_capture_grows(self):
        partial = self.root / "partial"
        partial.mkdir()
        prefix_log = _write_capture(partial, 2)
        output = self.root / "growing"
        self.assertEqual(_run(prefix_log, output), 0)
        before = _hash_tree(output)

        self.assertEqual(_run(self.log, output), 0)
        after = _hash_tree(output)
        for name in ("init.mp4", "segment-000.m4s", "segment-001.m4s"):
            self.assertEqual(before[name], after[name])
        self.assertTrue((output / "segment-002.m4s").is_file())
        _check_playlist(output)

    def test_published_objects_are_always_readable(self):
        output = self.root / "output"
        self.assertEqual(_run(self.log, output), 0)
        _check_playlist(output)
        progress = json.loads((output / "progress.json").read_text())
        self.assertEqual(progress["epoch"], "recording-0")
        self.assertEqual(progress["last"], {"video": 3, "audio": 3})

    def test_packet_level_verifier_accepts_the_publication(self):
        import verify
        output = self.root / "output"
        self.assertEqual(_run(self.log, output), 0)
        verify.playlist_entries(output)
        stats = verify.verify_packets(self.log, output)
        self.assertEqual(stats["recording-0"][1]["published"], 180)
        self.assertEqual(stats["recording-0"][2]["published"], 282)


if __name__ == "__main__":
    unittest.main()
