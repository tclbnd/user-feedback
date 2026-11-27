import unittest
import zlib

from zmbvkit.zmbv import Decoder, ZmbvError, parse


def pak(w, h, frames):
    return b"ZMBVPAK\0" + w.to_bytes(2, "little") + h.to_bytes(2, "little") + len(frames).to_bytes(2, "little") \
        + b"".join(len(f).to_bytes(4, "little") + f for f in frames)


def compressed_chunk(compressor, payload):
    return compressor.compress(payload) + compressor.flush(zlib.Z_SYNC_FLUSH)


class BasicTests(unittest.TestCase):
    def test_key_frame_16bit(self):
        pic = bytes(range(32))
        frame = bytes([1, 0, 1, 1, 6, 4, 4]) + zlib.compress(pic)
        out = parse(pak(4, 4, [frame]))
        self.assertEqual(out["bits"], 16)
        self.assertEqual(out["frames"][0]["crc32"], "%08x" % zlib.crc32(pic))

    def test_inter_frame_uses_continuing_zlib_stream(self):
        key_pic = bytes(range(32))
        compressor = zlib.compressobj()
        key_payload = compressor.compress(key_pic) + compressor.flush(zlib.Z_SYNC_FLUSH)
        key_frame = bytes([1, 0, 1, 1, 6, 2, 2]) + key_payload

        # A continued zlib chunk has no zlib header of its own.
        vectors = bytes([2, 1, 0xfe, 0, 0, 0, 0, 0])
        inter_payload = compressor.compress(vectors) + compressor.flush(zlib.Z_SYNC_FLUSH)
        self.assertNotEqual(inter_payload[:2], b"\x78\x9c")
        inter_frame = bytes([0]) + inter_payload

        out = parse(pak(4, 4, [key_frame, inter_frame]))
        expected = (
            key_pic[12:16] + key_pic[0:4] +
            key_pic[20:24] + key_pic[8:12] +
            key_pic[16:32]
        )
        self.assertEqual(out["frames"][1]["crc32"], "%08x" % zlib.crc32(expected))

    def test_successive_motion_uses_the_previous_frame_only(self):
        dec = Decoder(4, 4)
        key_pic = bytes(range(32))
        compressor = zlib.compressobj()
        dec.frame(bytes([1, 0, 1, 1, 6, 2, 2]) + compressed_chunk(compressor, key_pic))

        vectors = bytes([2, 0, 0, 0, 0, 0, 0, 0])
        first = bytearray(key_pic)
        first[0:4] = key_pic[4:8]
        first[8:12] = key_pic[12:16]
        dec.frame(bytes([0]) + compressed_chunk(compressor, vectors))
        self.assertEqual(bytes(dec.cur), bytes(first))

        # Repeating the same two-pixel motion must not move the pixels again.
        dec.frame(bytes([0]) + compressed_chunk(compressor, vectors))
        self.assertEqual(bytes(dec.cur), bytes(first))

    def test_odd_motion_byte_carries_delta_flag_and_even_vector(self):
        dec = Decoder(4, 4)
        key_pic = bytes(range(32))
        compressor = zlib.compressobj()
        dec.frame(bytes([1, 0, 1, 1, 6, 2, 2]) + compressed_chunk(compressor, key_pic))

        # vx=3 means vector dx=2 plus the update flag; its block is replaced.
        vectors = bytes([3, 0, 0xfe, 0, 0, 0, 0, 0])
        delta = bytes([0, 0, 0xaa, 0xbb, 0, 0, 0xcc, 0xdd])
        dec.frame(bytes([0]) + compressed_chunk(compressor, vectors + delta))

        expected = bytearray(
            key_pic[20:24] + key_pic[28:32] +
            key_pic[0:4] + key_pic[8:12] +
            key_pic[16:32]
        )
        expected[0:4] = bytes([0, 0, 0xaa, 0xbb])
        expected[4:8] = bytes([0, 1, 0x02, 0x03])
        expected[8:12] = bytes([0, 0, 0xcc, 0xdd])
        self.assertEqual(bytes(dec.cur), bytes(expected))

    def test_inter_frame_always_reads_complete_edge_blocks(self):
        dec = Decoder(5, 3)
        palette = bytes((i // 3 for i in range(768)))
        key_pic = bytes(range(15))
        compressor = zlib.compressobj()
        dec.frame(bytes([1, 0, 1, 1, 4, 2, 2]) + compressed_chunk(compressor, palette + key_pic))

        # Only the partly outside bottom-right block is updated.  The padding
        # bytes in its encoded rows must still be consumed.
        vectors = bytes([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0])
        block = bytes([10, 11, 0xff, 20, 21, 0xff])
        dec.frame(bytes([0]) + compressed_chunk(compressor, vectors + block))

        expected = bytearray(key_pic)
        expected[12:15] = bytes([12, 13, 10])
        self.assertEqual(bytes(dec.cur), bytes(expected))

    def test_palette_change_applies_to_current_picture(self):
        dec = Decoder(2, 1)
        palette1 = bytes(768)
        compressor = zlib.compressobj()
        dec.frame(bytes([1, 0, 1, 1, 4, 4, 4]) + compressed_chunk(compressor, palette1 + b"\x01\x02"))

        palette2 = bytearray(768)
        palette2[3:6] = bytes([10, 20, 30])
        palette2[6:9] = bytes([40, 50, 60])
        dec.frame(bytes([2]) + compressed_chunk(compressor, bytes(palette2) + bytes([0, 0])))

        self.assertEqual(dec.picture(), bytes([10, 20, 30, 40, 50, 60]))

    def test_uncompressed_frames_do_not_need_zlib_state(self):
        pic = bytes(range(32))
        key_frame = bytes([1, 0, 1, 0, 6, 4, 4]) + pic
        out = parse(pak(4, 4, [key_frame]))
        self.assertEqual(out["frames"][0]["crc32"], "%08x" % zlib.crc32(pic))

    def test_bad_magic(self):
        with self.assertRaises(ZmbvError):
            parse(b"ZMBVXXX\0" + bytes(6))


if __name__ == "__main__":
    unittest.main()
