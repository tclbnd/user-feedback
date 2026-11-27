import unittest

from schlkit.stream import SchlError, parse


def chunk(tag, payload):
    return tag + (len(payload) + 8).to_bytes(4, "little") + payload


def header(channels, codec, rate=22050):
    return chunk(b"SCHl", bytes([channels, codec]) + rate.to_bytes(4, "little"))


class BasicTests(unittest.TestCase):
    def test_unknown_codec(self):
        with self.assertRaises(SchlError):
            parse(chunk(b"SCHl", bytes([1, 9]) + (22050).to_bytes(4, "little")))

    def test_missing_header(self):
        with self.assertRaises(SchlError):
            parse(chunk(b"SCDl", b"\0\0"))

    def test_pcm16(self):
        body = b"".join(v.to_bytes(2, "little", signed=True) for v in (1, -2, 300, -400))
        info = parse(header(1, 0, 11025) + chunk(b"SCDl", body) + chunk(b"SCEl", b""))
        self.assertEqual(info["codec"], "pcm16")
        self.assertEqual(info["packets"], [4])
        self.assertEqual(info["frames"], 4)
        self.assertEqual(info["channel"][0]["sum"], -101)
        self.assertEqual(info["channel"][0]["peak"], 400)

    def test_pcm8_is_signed(self):
        info = parse(header(1, 1) + chunk(b"SCDl", bytes([0x00, 0x7F, 0x80, 0xFF])))
        self.assertEqual(info["frames"], 4)
        # signed 8-bit scaled to 16 bits: 0, 32512, -32768, -256
        self.assertEqual(info["channel"][0]["sum"], -512)
        self.assertEqual(info["channel"][0]["peak"], 32768)

    def test_truncated_chunk(self):
        data = header(1, 0) + b"SCDl" + (1000).to_bytes(4, "little") + b"\0\0"
        with self.assertRaises(SchlError):
            parse(data)

    def test_tiny_chunk_size(self):
        data = header(1, 0) + b"SCDl" + (4).to_bytes(4, "little")
        with self.assertRaises(SchlError):
            parse(data)

    def adpcm_packet(self, frames, hist, pieces):
        body = frames.to_bytes(4, "little")
        body += b"".join(v.to_bytes(2, "little", signed=True) for v in hist)
        return chunk(b"SCDl", body + b"".join(pieces))

    def test_ea_adpcm(self):
        # one piece: left control 0x10 (coeffs 240, 0), right 0x00 (coeffs 0, 0)
        piece = bytes([0x10, 0x00, 0x11]) + bytes(27)
        packet = self.adpcm_packet(28, (256, 0, -256, 0), [piece])
        info = parse(header(2, 2) + packet + chunk(b"SCEl", b""))
        self.assertEqual(info["codec"], "ea-adpcm")
        self.assertEqual(info["packets"], [56])
        self.assertEqual(info["frames"], 28)
        left, right = info["channel"]
        # left: 496 then (240 * prev) >> 8 each step
        self.assertEqual(left["samples"], 28)
        self.assertEqual(left["peak"], 496)
        self.assertEqual(left["sum"], 6516)
        # right: 256 then silence
        self.assertEqual(right["peak"], 256)
        self.assertEqual(right["sum"], 256)

    def test_ea_adpcm_bad_packet_counts_zero(self):
        # claims 56 frames but carries a single 28-frame piece
        piece = bytes([0x10, 0x00]) + bytes(28)
        packet = self.adpcm_packet(56, (0, 0, 0, 0), [piece])
        info = parse(header(2, 2) + packet + chunk(b"SCEl", b""))
        self.assertEqual(info["packets"], [0])
        self.assertEqual(info["frames"], 0)


if __name__ == "__main__":
    unittest.main()
