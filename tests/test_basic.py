import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cues import avail, insert, section, seg, time_signal  # noqa: E402
from scte.splice import SpliceError, parse  # noqa: E402


class BasicTest(unittest.TestCase):
    def test_insert(self):
        out = parse(section(insert(event_id=42, pts=900000, duration=30 * 90000, upid=9), avail(77)))
        ins = out["splice_insert"]
        self.assertEqual(out["command"], "splice_insert")
        self.assertEqual((ins["event_id"], ins["time"], ins["break"]["duration"], ins["unique_program_id"]), (42, "10", "30", 9))
        self.assertEqual(out["descriptors"], [{"tag": "avail", "provider_avail_id": 77}])

    def test_time_signal(self):
        out = parse(section(time_signal(450000), seg(upid=b"ABC123")))
        self.assertEqual(out["time"], "5")
        self.assertEqual(out["descriptors"][0]["upid"], "ABC123")

    def test_not_scte(self):
        with self.assertRaises(SpliceError):
            parse(b"\x00\x01")


if __name__ == "__main__":
    unittest.main()
