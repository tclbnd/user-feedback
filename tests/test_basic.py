import unittest
import zlib

from dxakit.dxa import DxaError, parse, Decoder


def seq(w, h, frames):
    return b"DXASEQ\0\0" + w.to_bytes(2, "little") + h.to_bytes(2, "little") + len(frames).to_bytes(4, "little") \
        + b"".join(len(f).to_bytes(4, "little") + f for f in frames)


def full_frame(comp, pixels):
    return b"FRAM" + bytes([comp]) + len(pixels).to_bytes(4, "big") + zlib.compress(pixels)


def block_frame(comp, tag_region, payload, cmap=None):
    # The block frame is a 12-byte header followed by 32 RLE tag bytes and payload.
    if len(tag_region) < 32:
        tag_region = tag_region + bytes(32 - len(tag_region))
    raw = bytes(12) + tag_region[:32] + payload
    body = b"FRAM" + bytes([comp]) + len(raw).to_bytes(4, "big") + zlib.compress(raw)
    if cmap:
        body = b"CMAP" + cmap + body
    return body


class BasicTests(unittest.TestCase):
    def test_key_frame(self):
        pixels = bytes(range(16))
        frame = b"FRAM" + bytes([2]) + (16).to_bytes(4, "big") + zlib.compress(pixels)
        out = parse(seq(4, 4, [frame]))
        self.assertTrue(out["frames"][0]["ok"])
        self.assertEqual(out["colours"], 16)

    def test_odd_size(self):
        with self.assertRaises(DxaError):
            parse(seq(6, 4, []))

    # -- the three reported reading bugs -----------------------------------
    def test_delta_overlays_previous_frame(self):
        key = bytes([10] * 16)
        # Only the first four pixels change; zero entries must keep value 10.
        delta = bytes([20, 20, 20, 20] + [0] * 12)
        out = parse(seq(4, 4, [full_frame(2, key), full_frame(3, delta)]))
        self.assertTrue(all(f["ok"] for f in out["frames"]))
        dec = Decoder(4, 4)
        dec.frame(full_frame(2, key))
        dec.frame(full_frame(3, delta))
        self.assertEqual(bytes(dec.prev), bytes([20] * 4 + [10] * 12))

    def test_null_frame_keeps_previous_picture(self):
        key = bytes([77] * 16)
        out = parse(seq(4, 4, [full_frame(2, key), b"NULL"]))
        self.assertTrue(all(f["ok"] for f in out["frames"]))
        # Both frames must show the key picture; the NULL one is not black.
        self.assertEqual(out["frames"][0]["crc32"], out["frames"][1]["crc32"])
        dec = Decoder(4, 4)
        dec.frame(b"NULL")  # leading NULL leaves the all-black reference
        self.assertEqual(bytes(dec.prev), bytes(16))

    def test_palette_channels_are_not_swapped(self):
        cmap = bytearray(768)
        cmap[0:3] = bytes([11, 22, 33])  # stored R, G, B
        frame = block_frame(13, bytes([0]) + bytes(31), b"", cmap=bytes(cmap))
        dec = Decoder(4, 4)
        dec.frame(frame)
        self.assertEqual(dec.pal[0], (11, 22, 33))

    # -- block encodings 12 and 13 ------------------------------------------
    def test_block_raw_and_solid(self):
        # comp 13: one raw block (type 1) and one solid block (type 3).
        tag = bytes([1, 3]) + bytes(30)
        payload = bytes(range(50, 66)) + bytes([9])
        out = parse(seq(8, 4, [block_frame(13, tag, payload)]))
        self.assertTrue(out["frames"][0]["ok"])
        dec = Decoder(8, 4)
        dec.frame(block_frame(13, tag, payload))
        self.assertEqual(list(dec.prev[0:4]), [50, 51, 52, 53])
        self.assertTrue(all(dec.prev[y * 8 + x] == 9
                            for y in range(4) for x in range(4, 8)))

    def test_block_rle_run_marker(self):
        # Marker 33 means "repeat the next type twice": two solid blocks.
        tag = bytes([33, 3]) + bytes(30)
        payload = bytes([9, 7])
        dec = Decoder(8, 4)
        dec.frame(block_frame(13, tag, payload))
        self.assertTrue(all(dec.prev[y * 8 + x] == 9
                            for y in range(4) for x in range(4)))
        self.assertTrue(all(dec.prev[y * 8 + x] == 7
                            for y in range(4) for x in range(4, 8)))

    def test_block_two_colour_mask(self):
        tag = bytes([2]) + bytes(31)
        # Every row mask is zero so all 16 pixels take the first colour.
        payload = bytes([40, 60, 0, 0, 0, 0])
        dec = Decoder(4, 4)
        dec.frame(block_frame(13, tag, payload))
        self.assertEqual(bytes(dec.prev), bytes([40] * 16))

    def test_block_three_colour_transparent_index(self):
        dec = Decoder(4, 4)
        dec.prev = bytes([100] * 16)
        tag = bytes([4]) + bytes(31)
        idx = bytes([0, 1, 2, 3] * 4)  # index 3 means keep the previous pixel
        payload = bytes([10, 20, 30]) + idx
        dec.frame(block_frame(13, tag, payload))
        expected = bytes(100 if i % 4 == 3 else [10, 20, 30][i % 4]
                         for i in range(16))
        self.assertEqual(bytes(dec.prev), expected)

    def test_block_skip_type_zero(self):
        dec = Decoder(4, 4)
        dec.prev = bytes([55] * 16)
        # A single skip block (type 0) carries no payload and leaves pixels alone.
        dec.frame(block_frame(13, bytes([0]) + bytes(31), b""))
        self.assertEqual(bytes(dec.prev), bytes([55] * 16))


if __name__ == "__main__":
    unittest.main()
