"""Regression tests against the archived example streams."""
import os
import unittest
from fractions import Fraction

from psscan.scan import scan

EXAMPLES = os.path.join(os.path.dirname(__file__), os.pardir, "examples")


def scan_example(name):
    with open(os.path.join(EXAMPLES, name), "rb") as fh:
        return scan(fh.read())


class VcdExampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = scan_example("vcd_track.mpg")

    def test_pack_layer(self):
        out = self.out
        self.assertEqual(out["format"], "MPEG-1")
        self.assertEqual(out["packs"], 3)
        self.assertEqual(out["mux_rate"], 3528 * 50)
        self.assertEqual(out["first_scr"], "3")
        self.assertEqual(out["scr_span"], str(Fraction(24000, 90000)))
        self.assertEqual(out["padding_bytes"], 0)

    def test_streams(self):
        streams = self.out["streams"]
        self.assertEqual(sorted(streams), ["0xc0", "0xe0"])
        video = streams["0xe0"]
        self.assertEqual(video["kind"], "video")
        self.assertEqual(video["packets"], 12)
        self.assertEqual(video["payload_bytes"], 4800)
        self.assertEqual(video["first_pts"], str(Fraction(273600, 90000)))
        self.assertEqual(video["last_pts"], str(Fraction(314894, 90000)))
        audio = streams["0xc0"]
        self.assertEqual(audio["kind"], "audio")
        self.assertEqual(audio["packets"], 12)
        self.assertEqual(audio["payload_bytes"], 1440)
        self.assertEqual(audio["first_pts"], "3")
        self.assertEqual(audio["last_pts"], str(Fraction(293760, 90000)))


class DvdExampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = scan_example("dvd_title.vob")

    def test_pack_layer(self):
        out = self.out
        self.assertEqual(out["format"], "MPEG-2")
        self.assertEqual(out["packs"], 8)
        self.assertEqual(out["mux_rate"], 25200 * 50)
        self.assertEqual(out["first_scr"], "600")
        self.assertEqual(out["scr_span"],
                         str(Fraction(54012600 * 300 + 259 - 54000000 * 300, 27000000)))
        self.assertEqual(out["padding_bytes"], 160)

    def test_streams(self):
        streams = self.out["streams"]
        self.assertEqual(sorted(streams), ["0xbd:0x20", "0xbd:0x80", "0xbd:0x81", "0xe0"])
        video = streams["0xe0"]
        self.assertEqual(video["kind"], "video")
        self.assertEqual(video["packets"], 8)
        self.assertEqual(video["payload_bytes"], 2400)
        self.assertEqual(video["first_pts"], str(Fraction(54006006, 90000)))
        self.assertEqual(video["last_pts"], str(Fraction(54027027, 90000)))
        ac3a = streams["0xbd:0x80"]
        self.assertEqual(ac3a["kind"], "ac3")
        self.assertEqual(ac3a["packets"], 8)
        self.assertEqual(ac3a["payload_bytes"], 816)
        self.assertEqual(ac3a["first_pts"], "600")
        self.assertEqual(ac3a["last_pts"], str(Fraction(54020160, 90000)))
        ac3b = streams["0xbd:0x81"]
        self.assertEqual(ac3b["kind"], "ac3")
        self.assertEqual(ac3b["packets"], 8)
        self.assertEqual(ac3b["payload_bytes"], 736)
        sub = streams["0xbd:0x20"]
        self.assertEqual(sub["kind"], "subpicture")
        self.assertEqual(sub["packets"], 8)
        self.assertEqual(sub["payload_bytes"], 240)
        self.assertEqual(sub["first_pts"], "600")
        self.assertEqual(sub["last_pts"], "600")


if __name__ == "__main__":
    unittest.main()
