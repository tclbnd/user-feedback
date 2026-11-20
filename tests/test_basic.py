import os
import unittest

from ivfscan import bdrate, ivf, obu, seqhdr
from ivfscan.summary import summarise

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(HERE, "..", "examples")


def read_example(name):
    with open(os.path.join(EXAMPLES, name), "rb") as f:
        return f.read()


def build_ivf(tus, header_len=32, declared_frames=0):
    out = bytearray(b"DKIF" + (0).to_bytes(2, "little") + header_len.to_bytes(2, "little"))
    out += b"AV01" + (16).to_bytes(2, "little") + (16).to_bytes(2, "little")
    out += (30000).to_bytes(4, "little") + (1001).to_bytes(4, "little")
    out += declared_frames.to_bytes(4, "little") + b"\0" * 4
    out += b"\0" * (header_len - len(out))
    for pts, tu in enumerate(tus):
        out += len(tu).to_bytes(4, "little") + pts.to_bytes(8, "little") + tu
    return bytes(out)


class BasicTests(unittest.TestCase):
    def test_small_obus(self):
        tu = bytes([0x12, 0x00]) + bytes([0x32, 0x03, 1, 2, 3])
        self.assertEqual([(o.type, o.payload) for o in obu.obus(tu)], [(2, b""), (6, b"\x01\x02\x03")])

    def test_gain(self):
        self.assertEqual(bdrate.average_gain([(1, 30.0), (2, 33.0)], [(1, 30.5), (2, 33.1), (3, 36)]), 0.3)


class Leb128Tests(unittest.TestCase):
    def test_one_byte(self):
        self.assertEqual(obu.leb128(b"\x7f", 0), (127, 1))

    def test_multi_byte(self):
        self.assertEqual(obu.leb128(b"\x80\x01", 0), (128, 2))
        self.assertEqual(obu.leb128(b"\xff\xff\x03", 0), (65535, 3))

    def test_rejects_overlong(self):
        with self.assertRaises(ValueError):
            obu.leb128(b"\x80" * 9, 0)


class ObuTests(unittest.TestCase):
    def test_large_obu(self):
        # Size > 127 needs a multi-byte leb128; the old parser read only one byte.
        payload = bytes(range(256)) * 2
        tu = bytes([0x32]) + b"\x80\x04" + payload
        self.assertEqual([(o.type, o.payload) for o in obu.obus(tu)], [(6, payload)])

    def test_extension_header(self):
        # OBU_FRAME + extension (temporal_id 1, spatial_id 1, reserved 0), sized;
        # then OBU_FRAME + extension without a size field, to the end of the TU.
        tu = bytes([0x36, 0x28, 0x01, 0xAA]) + bytes([0x34, 0x00, 0xBB])
        got = [(o.type, o.temporal_id, o.spatial_id, o.payload) for o in obu.obus(tu)]
        self.assertEqual(got, [(6, 1, 1, b"\xaa"), (6, 0, 0, b"\xbb")])

    def test_obu_without_size_field_runs_to_end(self):
        # Last OBU with obu_has_size_field == 0 extends to the end of the TU.
        tu = bytes([0x12, 0x00]) + bytes([0x30]) + b"\xde\xad\xbe\xef"
        got = [(o.type, o.payload) for o in obu.obus(tu)]
        self.assertEqual(got, [(2, b""), (6, b"\xde\xad\xbe\xef")])

    def test_obus_without_size_field_do_not_yield_type_zero(self):
        # A sized OBU followed by a trailing OBU without a size field: its
        # payload must not be re-read as OBU headers (the old type 0 flood).
        tu = bytes([0x1A, 0x02, 5, 6]) + bytes([0x18, 7, 8])
        self.assertEqual([(o.type, o.payload) for o in obu.obus(tu)],
                         [(3, b"\x05\x06"), (3, b"\x07\x08")])


class IvfTests(unittest.TestCase):
    def test_header_length_controls_frame_start(self):
        data = build_ivf([bytes([0x12, 0x00])], header_len=48)
        head, frames = ivf.read(data)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].pts, 0)

    def test_zero_frame_count_still_reports_frames(self):
        data = build_ivf([bytes([0x12, 0x00])] * 3, declared_frames=0)
        _, frames = ivf.read(data)
        self.assertEqual(len(frames), 3)

    def test_timebase_is_seconds_per_tick(self):
        head, _ = ivf.read(build_ivf([]))
        self.assertEqual(head["timebase"], "1001/30000")


class SampleClipTests(unittest.TestCase):
    def test_park_1080p(self):
        s = summarise(read_example("park_1080p.ivf"))
        self.assertEqual(s["frames"], 144)
        self.assertEqual(s["last_pts"], 143)
        self.assertEqual(s["timebase"], "1001/30000")
        self.assertEqual(s["obu_counts"], {"frame": 144, "sequence_header": 3, "temporal_delimiter": 144})
        self.assertEqual(s["keyframes"], 3)
        self.assertEqual(s["sequence"]["max_width"], 1920)
        self.assertEqual(s["sequence"]["max_height"], 1080)
        self.assertEqual(s["sequence"]["level"], 8)
        self.assertEqual(s["sequence"]["tier"], 1)

    def test_svc_720p(self):
        s = summarise(read_example("svc_720p.ivf"))
        self.assertEqual(s["frames"], 60)
        self.assertEqual(s["obu_counts"], {"frame": 120, "sequence_header": 1, "temporal_delimiter": 60})
        self.assertEqual(s["sequence"]["operating_points"], 2)
        self.assertEqual(s["sequence"]["max_width"], 1280)
        self.assertEqual(s["sequence"]["max_height"], 720)


class SequenceHeaderTests(unittest.TestCase):
    def test_reduced_still_picture(self):
        # profile 0, still_picture 1, reduced_still_picture_header 1, level 8,
        # frame_width_bits_minus_1 10, frame_height_bits_minus_1 10, 320x240.
        bits = "000" + "1" + "1" + "01000" + "1010" + "1010" + \
            format(320 - 1, "011b") + format(240 - 1, "011b")
        bits += "0" * (-len(bits) % 8)
        payload = bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits), 8))
        self.assertEqual(seqhdr.parse(payload), {
            "profile": 0, "still_picture": True, "level": 8, "tier": 0, "operating_points": 1,
            "max_width": 320, "max_height": 240})


if __name__ == "__main__":
    unittest.main()
