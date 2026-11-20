import os
import unittest

from dolbyscan import compliance
from dolbyscan.stream import scan
from dolbyscan.syncframe import frame_words, parse

EXAMPLES = os.path.join(os.path.dirname(__file__), os.pardir, "examples")


def scan_example(name):
    with open(os.path.join(EXAMPLES, name), "rb") as fh:
        return scan(fh.read())


def pack(*fields):
    """Pack (value, width) bit fields into bytes, MSB first."""
    out, acc, nbits = bytearray(), 0, 0
    for value, width in fields:
        acc, nbits = (acc << width) | value, nbits + width
        while nbits >= 8:
            nbits -= 8
            out.append((acc >> nbits) & 0xFF)
    if nbits:
        out.append((acc << (8 - nbits)) & 0xFF)
    return bytes(out)


def ac3_frame(fscod, frmsizecod, acmod, lfe, dialnorm, bsmod=0,
              cmixlev=1, surmixlev=1, dsurmod=2, fill=0):
    fields = [(0x0B77, 16), (0, 16), (fscod, 2), (frmsizecod, 6), (8, 5), (bsmod, 3), (acmod, 3)]
    if acmod & 0x1 and acmod != 0x1:
        fields.append((cmixlev, 2))
    if acmod & 0x4:
        fields.append((surmixlev, 2))
    if acmod == 0x2:
        fields.append((dsurmod, 2))
    fields += [(lfe, 1), (dialnorm, 5)]
    head = pack(*fields)
    size = frame_words(fscod, frmsizecod) * 2
    return head + bytes([fill]) * (size - len(head))


def eac3_frame(strmtyp, substreamid, frmsiz, fscod, acmod, lfe, dialnorm,
               fscod2=0, numblkscod=3, fill=0):
    fields = [(0x0B77, 16), (strmtyp, 2), (substreamid, 3), (frmsiz, 11), (fscod, 2)]
    fields.append((fscod2, 2) if fscod == 3 else (numblkscod, 2))
    fields += [(acmod, 3), (lfe, 1), (16, 5), (dialnorm, 5)]
    head = pack(*fields)
    size = (frmsiz + 1) * 2
    return head + bytes([fill]) * (size - len(head))


class BasicTests(unittest.TestCase):
    def test_frame_words_48k(self):
        self.assertEqual(frame_words(0, 30), 896)
        self.assertEqual(frame_words(0, 0), 64)

    def test_frame_words_32k(self):
        self.assertEqual(frame_words(2, 0), 96)
        self.assertEqual(frame_words(2, 30), 1344)

    def test_frame_words_44k(self):
        # A/52 table 5.18: 44.1 kHz sizes alternate between a pair of values
        self.assertEqual(frame_words(1, 20), 417)
        self.assertEqual(frame_words(1, 21), 418)
        self.assertEqual(frame_words(1, 36), 1393)
        self.assertEqual(frame_words(1, 37), 1394)

    def test_bsi_51(self):
        # acmod 3/2 carries cmixlev and surmixlev before lfeon and dialnorm
        f = parse(ac3_frame(0, 30, acmod=7, lfe=1, dialnorm=27), 0)
        self.assertEqual((f["channels"], f["lfe"], f["dialnorm"]), (6, True, -27))

    def test_bsi_stereo_dsurmod(self):
        # dsurmod in 2/0 must not be mistaken for the LFE flag
        f = parse(ac3_frame(0, 0, acmod=2, lfe=0, dialnorm=24, dsurmod=2), 0)
        self.assertEqual((f["channels"], f["lfe"], f["dialnorm"]), (2, False, -24))

    def test_eac3_header(self):
        f = parse(eac3_frame(0, 0, 639, 0, acmod=7, lfe=1, dialnorm=24), 0)
        self.assertEqual(f["codec"], "eac3")
        self.assertEqual((f["size"], f["samples"], f["blocks"]), (1280, 1536, 6))
        self.assertEqual((f["channels"], f["dialnorm"]), (6, -24))

    def test_eac3_reduced_rate(self):
        # fscod 3 selects a reduced sample rate via fscod2 and implies 6 blocks
        f = parse(eac3_frame(0, 0, 63, 3, acmod=2, lfe=0, dialnorm=31, fscod2=1), 0)
        self.assertEqual((f["sample_rate"], f["samples"]), (22050, 1536))

    def test_eac3_blocks(self):
        for numblkscod, blocks in ((0, 1), (1, 2), (2, 3), (3, 6)):
            f = parse(eac3_frame(0, 0, 63, 0, acmod=2, lfe=0, dialnorm=31,
                                 numblkscod=numblkscod), 0)
            self.assertEqual(f["samples"], blocks * 256)

    def test_feature_51(self):
        s = scan_example("feature_51.ac3")
        self.assertEqual((s["codec"], s["sample_rate"]), ("ac3", 48000))
        self.assertEqual((s["channels"], s["lfe"], s["acmod"]), (6, True, 7))
        self.assertEqual((s["dialnorm"], s["bitrate"]), (-27, 448000))
        self.assertEqual((s["frames"], s["samples"], s["duration"]), (150, 230400, "24/5"))

    def test_promo_stereo_44k(self):
        s = scan_example("promo_stereo_441.ac3")
        self.assertEqual((s["sample_rate"], s["channels"], s["lfe"]), (44100, 2, False))
        self.assertEqual((s["dialnorm"], s["bitrate"]), (-31, 192000))
        self.assertEqual((s["frames"], s["samples"]), (120, 184320))

    def test_series_ddp(self):
        s = scan_example("series_ddp.ec3")
        self.assertEqual((s["codec"], s["sample_rate"]), ("eac3", 48000))
        # independent 5.1 plus dependent 2/0: channels add up, duration does not
        self.assertEqual((s["channels"], s["lfe"], s["acmod"]), (8, True, 7))
        self.assertEqual((s["dialnorm"], s["bitrate"]), (-24, 384000))
        self.assertEqual((s["frames"], s["samples"], s["duration"]), (120, 92160, "48/25"))

    def test_bad_bytes_mid_stream(self):
        with open(os.path.join(EXAMPLES, "feature_51.ac3"), "rb") as fh:
            data = fh.read()
        damaged = data[:100000] + b"\xde\xad\xbe\xef" + data[100000:]
        s = scan(damaged)
        self.assertEqual((s["frames"], s["samples"]), (150, 230400))

    def test_truncated_tail(self):
        with open(os.path.join(EXAMPLES, "feature_51.ac3"), "rb") as fh:
            data = fh.read()
        s = scan(data[:1792 * 100 + 500])
        self.assertEqual((s["frames"], s["samples"]), (100, 153600))

    def test_compliance(self):
        s = {"sample_rate": 48000, "channels": 6, "dialnorm": -27, "bitrate": 448000}
        self.assertEqual(compliance.check(s, {"channels": 6, "dialnorm": -24}), ["dialnorm"])


if __name__ == "__main__":
    unittest.main()
