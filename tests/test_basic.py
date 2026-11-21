import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mux import simple  # noqa: E402
from tskit.ts import TsError, scan  # noqa: E402


class BasicTest(unittest.TestCase):
    def test_program(self):
        out = scan(simple(pcr=False).data())
        prog = out["programs"][0]
        self.assertEqual((prog["program"], prog["pmt_pid"], prog["pcr_pid"]), (1, 0x100, 0x101))
        video, audio = prog["streams"]
        self.assertEqual((video["stream_type"], video["pes"], video["first_pts"], video["last_pts"]), ("h264", 4, 0, 9009))
        self.assertEqual(audio["duration"], "16/375")
        self.assertEqual(out["cc_errors"], 0)

    def test_no_sync(self):
        with self.assertRaises(TsError):
            scan(b"\x00" * 400)


if __name__ == "__main__":
    unittest.main()
