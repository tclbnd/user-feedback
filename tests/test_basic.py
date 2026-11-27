import unittest
import zlib

from wsakit.wsa import WsaError, lcw, parse, xor_delta


def wsa(w, h, frames):
    """Build a WSA. A frame of b"" gets offset 0, i.e. an empty frame."""
    n = len(frames)
    head = b"".join(v.to_bytes(2, "little") for v in (n, 0, 0, w, h, 256, 0))
    pos = len(head) + 4 * (n + 2)
    offs, body = [], b""
    for f in frames:
        offs.append(pos + len(body) if f else 0)
        body += f
    offs += [pos + len(body), 0]
    return head + b"".join(o.to_bytes(4, "little") for o in offs) + body


class LcwTests(unittest.TestCase):
    def test_literal_run(self):
        self.assertEqual(lcw(bytes([0x83, 1, 2, 3, 0x80]), 10), b"\x01\x02\x03")

    def test_fill(self):
        self.assertEqual(lcw(bytes([0xFE, 4, 0, 0xAA, 0x80]), 10), b"\xaa" * 4)

    def test_short_copy_repeats_the_last_byte(self):
        # Literal "A", then a short copy of length 3 at distance 1 repeats it.
        self.assertEqual(lcw(bytes([0x81, 0x41, 0x00, 0x00, 0x80]), 10), b"A" * 4)

    def test_short_copy_distance_is_measured_from_the_end(self):
        # 0x05 -> length 8, distance 5 back from the end of the output.
        self.assertEqual(lcw(bytes([0x85, 0x41, 0x42, 0x43, 0x44, 0x45, 0x00, 0x04, 0x80]), 20),
                         b"ABCDEABC")

    def test_long_copy(self):
        # 0xC0 -> length 3, distance 3 back.
        self.assertEqual(lcw(bytes([0x83, 0x58, 0x59, 0x5A, 0xC0, 0x03, 0x00, 0x80]), 10), b"XYZXYZ")

    def test_very_long_copy(self):
        # 0xFF -> 16-bit count 4, then 16-bit distance 4.
        self.assertEqual(lcw(bytes([0x84, 0x41, 0x42, 0x43, 0x44, 0xFF, 4, 0, 4, 0, 0x80]), 20),
                         b"ABCDABCD")

    def test_past_buffer_is_an_error(self):
        with self.assertRaises(WsaError):
            lcw(bytes([0xFE, 9, 0, 0xAA, 0x80]), 4)


class XorDeltaTests(unittest.TestCase):
    def test_long_run(self):
        # 0x8000 | count is a long XOR run of that many literal bytes.
        pic = bytearray(4)
        xor_delta(pic, bytes([0x80, 0x04, 0x80, 0xFF, 0x0F, 0x00, 0x00, 0x80, 0x00, 0x00]))
        self.assertEqual(bytes(pic), bytes([0xFF, 0x0F, 0x00, 0x00]))

    def test_long_fill(self):
        # 0xC000 | count is a long XOR fill: one value byte follows.
        pic = bytearray(4)
        xor_delta(pic, bytes([0x80, 0x04, 0xC0, 0xAA, 0x80, 0x00, 0x00]))
        self.assertEqual(bytes(pic), b"\xaa" * 4)

    def test_long_skip(self):
        # A word without bit 15 set skips that many unchanged bytes.
        pic = bytearray(4)
        xor_delta(pic, bytes([0x80, 0x02, 0x00, 0x01, 0x01, 0x80, 0x00, 0x00]))
        self.assertEqual(bytes(pic), bytes([0x00, 0x00, 0x01, 0x00]))

    def test_fill_and_run_and_skip(self):
        pic = bytearray(8)
        xor_delta(pic, bytes([0x02, 0x11, 0x22, 0x00, 0x02, 0x33, 0x83, 0x80, 0x00, 0x00]))
        self.assertEqual(bytes(pic), bytes([0x11, 0x22, 0x33, 0x33, 0x00, 0x00, 0x00, 0x00]))


class BasicTests(unittest.TestCase):
    def test_single_frame(self):
        frame = bytes([0x80 | 8, 4, 1, 2, 3, 4, 0x80, 0x00, 0x00, 0x80])
        out = parse(wsa(2, 2, [frame]))
        self.assertEqual(out["frames"], 1)
        self.assertEqual(out["loop"], False)

    def test_short_header(self):
        with self.assertRaises(WsaError):
            parse(b"\x01\x00")

    def test_empty_frame_keeps_the_previous_picture(self):
        first = bytes([0x80 | 8, 4, 1, 2, 3, 4, 0x80, 0x00, 0x00, 0x80])
        out = parse(wsa(2, 2, [first, b"", b""]))
        self.assertEqual(out["frames"], 3)
        self.assertEqual(out["crc32"][0], out["crc32"][1])
        self.assertEqual(out["crc32"][1], out["crc32"][2])

    def test_no_loop_frame(self):
        frame = bytes([0x80 | 8, 4, 1, 2, 3, 4, 0x80, 0x00, 0x00, 0x80])
        out = parse(wsa(2, 2, [frame, frame]))
        self.assertEqual(out["frames"], 2)
        self.assertFalse(out["loop"])
        self.assertNotIn("loop_crc32", out)

    def test_loop_frame_is_not_counted_as_a_frame(self):
        n = 2
        frame = bytes([0x80 | 8, 4, 1, 2, 3, 4, 0x80, 0x00, 0x00, 0x80])
        # The loop delta rewrites the picture back to zeros, so it must XOR
        # the same bytes as the first frame.
        loop = frame
        head = b"".join(v.to_bytes(2, "little") for v in (n, 0, 0, 2, 2, 256, 0))
        pos = len(head) + 4 * (n + 2)
        offs = [pos, pos + len(frame), pos + 2 * len(frame), pos + 2 * len(frame) + len(loop)]
        body = frame + frame + loop
        data = head + b"".join(o.to_bytes(4, "little") for o in offs) + body
        out = parse(data)
        self.assertEqual(out["frames"], 2)
        self.assertTrue(out["loop"])
        self.assertEqual(out["crc32"][0], "%08x" % zlib.crc32(bytes([1, 2, 3, 4])))
        self.assertEqual(out["crc32"][1], "%08x" % zlib.crc32(bytes(4)))
        self.assertEqual(out["loop_crc32"], "%08x" % zlib.crc32(bytes([1, 2, 3, 4])))

    def test_frame_crc_matches_the_picture(self):
        frame = bytes([0x80 | 8, 4, 1, 2, 3, 4, 0x80, 0x00, 0x00, 0x80])
        out = parse(wsa(2, 2, [frame]))
        self.assertEqual(out["crc32"][0], "%08x" % zlib.crc32(bytes([1, 2, 3, 4])))


if __name__ == "__main__":
    unittest.main()
