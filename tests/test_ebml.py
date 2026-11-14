"""Unit tests for the EBML primitives (run with python3 -m unittest)."""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from mkvrecover import ebml


class VintTests(unittest.TestCase):
    def test_roundtrip_widths(self):
        for value, width in ((0, 1), (126, 1), (127, 2), (16382, 2),
                             (16383, 3), (2 ** 20, 4), (2 ** 40, 6)):
            encoded = ebml.encode_vint(value, width)
            self.assertEqual(len(encoded), width)
            self.assertEqual(ebml.read_vint(encoded, 0), (width, value))

    def test_unknown_marker(self):
        encoded = ebml.encode_vint(0, unknown=True, width=8)
        self.assertEqual(ebml.read_vint(encoded, 0), (8, None))
        # Unknown marker must not be produced for a known size.
        with self.assertRaises(Exception):
            ebml.encode_vint((1 << 7) - 1, 1)

    def test_element_id_keeps_marker_bits(self):
        encoded = ebml.encode_element_id(ebml.ID_CLUSTER)
        self.assertEqual(encoded, b"\x1f\x43\xb6\x75")
        self.assertEqual(ebml.read_element_id(encoded, 0),
                         (4, ebml.ID_CLUSTER))

    def test_bad_marker(self):
        with self.assertRaises(ebml.EBMLSyntaxError):
            ebml.read_element_id(b"\x00\x00", 0)


class BlockTests(unittest.TestCase):
    def _block(self, flags, body, track=1, rel=0):
        return (ebml.encode_vint(track) +
                int(rel).to_bytes(2, "big", signed=True) +
                bytes([flags]) + body)

    def test_no_lacing(self):
        payload = self._block(0x80, b"ABCDE")
        block = ebml.parse_block(payload, 0, len(payload))
        self.assertEqual(block["track"], 1)
        self.assertEqual(block["lacing"], ebml.LACING_NONE)
        self.assertTrue(block["keyframe"])
        self.assertEqual(block["frames"], [(len(payload) - 5, 5)])

    def test_signed_relative_time(self):
        payload = self._block(0x80, b"X", rel=-40)
        block = ebml.parse_block(payload, 0, len(payload))
        self.assertEqual(block["relative_time"], -40)

    def test_fixed_lacing(self):
        frames = [b"aaaa", b"bbbb", b"cccc"]
        flags = ebml.LACING_FIXED << 1
        payload = self._block(flags, bytes([2]) + b"".join(frames))
        block = ebml.parse_block(payload, 0, len(payload))
        self.assertEqual(block["lacing"], ebml.LACING_FIXED)
        self.assertEqual([length for _o, length in block["frames"]],
                         [4, 4, 4])
        self.assertEqual(ebml.de_lace(payload, block), frames)

    def test_xiph_lacing(self):
        frames = [b"x" * 3, b"y" * 260, b"z" * 7]
        flags = ebml.LACING_XIPH << 1
        # Xiph encodes all but the last size; 260 needs 255 + 5.
        sizes = bytes([3, 0xFF, 5])
        payload = self._block(flags, bytes([2]) + sizes + b"".join(frames))
        block = ebml.parse_block(payload, 0, len(payload))
        self.assertEqual(block["lacing"], ebml.LACING_XIPH)
        self.assertEqual(ebml.de_lace(payload, block), frames)

    def test_ebml_lacing(self):
        frames = [b"a" * 10, b"b" * 4, b"c" * 30]
        flags = ebml.LACING_EBML << 1
        # First size is absolute, later values are signed deltas from the
        # previous size.  Only count-1 sizes are coded; the final frame size
        # is derived from the remaining payload.  Width-1 bias is 64.
        bias = 1 << (7 * 1 - 1)
        prefix = (ebml.encode_vint(10 + bias, 1) +
                  ebml.encode_vint(-6 + bias, 1))
        payload = self._block(flags, bytes([2]) + prefix + b"".join(frames))
        block = ebml.parse_block(payload, 0, len(payload))
        self.assertEqual(block["lacing"], ebml.LACING_EBML)
        self.assertEqual(ebml.de_lace(payload, block), frames)

    def test_truncated_block_rejected(self):
        payload = self._block(0x80, b"")           # no frame data at all
        with self.assertRaises(ebml.Truncated):
            ebml.parse_block(payload, 0, len(payload))


class ScanTests(unittest.TestCase):
    def test_truncated_payload_flagged(self):
        good = ebml.encode_element_id(ebml.ID_VOID) + \
            ebml.encode_vint(4) + b"abcd"
        partial = ebml.encode_element_id(ebml.ID_VOID) + \
            ebml.encode_vint(100) + b"zx"
        data = good + partial
        elements, _tail = ebml.scan_children(data, 0, len(data))
        self.assertEqual(len(elements), 2)
        self.assertFalse(elements[0].truncated)
        self.assertTrue(elements[1].truncated)
        self.assertEqual(elements[1].truncation, "payload")


if __name__ == "__main__":
    unittest.main()
