import unittest
from fractions import Fraction

from psscan.scan import scan

from streams import (END, ac3_sub, lpcm_sub, pack1, pack2, pes1, pes2,
                     raw_pes, sub_sub, system_header)


def pts90(ticks):
    return str(Fraction(ticks, 90000))


class BasicTests(unittest.TestCase):
    def test_mpeg2_video(self):
        data = pack2(90000 * 2) + system_header() + pes2(0xE0, b"\x00" * 64, pts=180000) + pes2(0xC0, b"\x11" * 32, pts=180000) + END
        out = scan(data)
        self.assertEqual((out["format"], out["packs"]), ("MPEG-2", 1))
        self.assertEqual(out["streams"]["0xe0"]["payload_bytes"], 64)
        self.assertEqual(out["streams"]["0xc0"]["first_pts"], "2")


class Mpeg1Tests(unittest.TestCase):
    def test_pack_and_pes_headers(self):
        data = (pack1(270000) + system_header()
                + pes1(0xE0, b"\x40" * 400, pts=273600, dts=270000, stuffing=2, std=True)
                + pes1(0xC0, b"\x5a" * 120, pts=270000)
                + pes1(0xC0, b"\x5a" * 120)
                + pack1(282000)
                + pes1(0xE0, b"\x40" * 400, pts=277354, dts=273754, stuffing=1)
                + END)
        out = scan(data)
        self.assertEqual(out["format"], "MPEG-1")
        self.assertEqual(out["packs"], 2)
        # mux_rate is coded in units of 50 bytes/s
        self.assertEqual(out["mux_rate"], 3528 * 50)
        self.assertEqual(out["first_scr"], "3")
        self.assertEqual(out["scr_span"], str(Fraction(12000 * 300, 27000000)))
        video = out["streams"]["0xe0"]
        # stuffing bytes, STD buffer fields and PTS/DTS are not payload
        self.assertEqual((video["kind"], video["packets"], video["payload_bytes"]),
                         ("video", 2, 800))
        self.assertEqual(video["first_pts"], pts90(273600))
        self.assertEqual(video["last_pts"], pts90(277354))
        audio = out["streams"]["0xc0"]
        self.assertEqual((audio["kind"], audio["packets"], audio["payload_bytes"]),
                         ("audio", 2, 240))
        # second audio packet carries no PTS (0x0f marker byte)
        self.assertEqual(audio["first_pts"], "3")
        self.assertEqual(audio["last_pts"], "3")

    def test_scr_uses_all_33_bits(self):
        scr = (5 << 30) + 270000
        out = scan(pack1(scr) + pes1(0xC0, b"\x5a" * 8, pts=(7 << 30) + 45000) + END)
        self.assertEqual(out["first_scr"], pts90(scr))
        self.assertEqual(out["streams"]["0xc0"]["first_pts"], pts90((7 << 30) + 45000))


class Mpeg2Tests(unittest.TestCase):
    def test_pack_extension_and_stuffing(self):
        data = (pack2(54000000, scr_ext=37, mux=25200, stuffing=3)
                + pes2(0xE0, b"\x33" * 300, pts=54006006, dts=54000000) + END)
        out = scan(data)
        self.assertEqual(out["format"], "MPEG-2")
        self.assertEqual(out["packs"], 1)
        self.assertEqual(out["mux_rate"], 25200 * 50)
        self.assertEqual(out["first_scr"], str(Fraction(54000000 * 300 + 37, 27000000)))
        self.assertEqual(out["scr_span"], "0")
        video = out["streams"]["0xe0"]
        self.assertEqual(video["payload_bytes"], 300)
        self.assertEqual(video["first_pts"], pts90(54006006))

    def test_pts_does_not_wrap_at_2_pow_30(self):
        # a program past 2**30/90000 s (~3.3 h) sets the top PTS bits
        pts = (1 << 30) + 180000
        data = pack2(0) + pes2(0xE0, b"\x00" * 8, pts=pts) + END
        out = scan(data)
        self.assertEqual(out["streams"]["0xe0"]["first_pts"], pts90(pts))

    def test_private_stream_2(self):
        data = pack2(0) + raw_pes(0xBF, b"\x07" * 24) + END
        out = scan(data)
        s = out["streams"]["0xbf"]
        self.assertEqual((s["kind"], s["packets"], s["payload_bytes"]), ("private2", 1, 24))
        self.assertIsNone(s["first_pts"])


class DvdTests(unittest.TestCase):
    def test_private_stream_1_substreams(self):
        data = (pack2(54000000)
                + pes2(0xBD, ac3_sub(0, b"\x0b\x77" + b"\x01" * 98), pts=54000000)
                + pes2(0xBD, ac3_sub(1, b"\x0b\x77" + b"\x02" * 90), pts=54002880)
                + pes2(0xBD, bytes([0x89, 1, 0, 1]) + b"\x7f\xfe" + b"\x05" * 40, pts=54000000)
                + pes2(0xBD, lpcm_sub(0, b"\x03" * 50, bits=24, rate=96000, channels=6), pts=54000000)
                + pes2(0xBD, sub_sub(5, b"\x04" * 30), pts=54000000)
                + raw_pes(0xBE, b"\xff" * 20)
                + END)
        out = scan(data)
        streams = out["streams"]
        # padding stream is counted, not listed
        self.assertEqual(out["padding_bytes"], 20)
        self.assertNotIn("0xbe", streams)
        # each DVD sub-stream gets its own entry
        self.assertEqual(sorted(streams),
                         ["0xbd:0x25", "0xbd:0x80", "0xbd:0x81", "0xbd:0x89", "0xbd:0xa0"])
        ac3a, ac3b = streams["0xbd:0x80"], streams["0xbd:0x81"]
        self.assertEqual(ac3a["kind"], "ac3")
        # sub-stream header (4 bytes) is not payload
        self.assertEqual(ac3a["payload_bytes"], 100)
        self.assertEqual(ac3b["payload_bytes"], 92)
        self.assertEqual(ac3b["first_pts"], pts90(54002880))
        dts = streams["0xbd:0x89"]
        self.assertEqual((dts["kind"], dts["payload_bytes"]), ("dts", 42))
        lpcm = streams["0xbd:0xa0"]
        self.assertEqual(lpcm["kind"], "lpcm")
        self.assertEqual(lpcm["payload_bytes"], 50)
        self.assertEqual((lpcm["bits"], lpcm["sample_rate"], lpcm["channels"]),
                         (24, 96000, 6))
        sub = streams["0xbd:0x25"]
        self.assertEqual((sub["kind"], sub["payload_bytes"]), ("subpicture", 30))


if __name__ == "__main__":
    unittest.main()
