import os
import unittest

from telcodec import g711, vad, wav

EXAMPLES = os.path.join(os.path.dirname(__file__), os.pardir, "examples")


def read_example(name):
    with open(os.path.join(EXAMPLES, name), "rb") as f:
        return f.read()


class BasicTests(unittest.TestCase):
    def test_ulaw_decode(self):
        self.assertEqual(g711.ulaw_decode(0xFF), 0)
        self.assertEqual(g711.ulaw_decode(0x80), 32124)

    def test_vad(self):
        speech = [3000, -3000] * 80
        self.assertEqual(vad.active([0] * 160 + speech + [0] * 800), [False, True, True, True, True, False, False])


class G711Tests(unittest.TestCase):
    def test_ulaw_round_trip(self):
        for code in range(256):
            rt = g711.ulaw_encode(g711.ulaw_decode(code))
            if code == 0x7F:  # mu-law negative zero decodes to 0, re-encodes as 0xFF
                self.assertEqual(rt, 0xFF)
            else:
                self.assertEqual(rt, code)

    def test_alaw_round_trip(self):
        for code in range(256):
            self.assertEqual(g711.alaw_encode(g711.alaw_decode(code)), code)

    def test_ulaw_encode(self):
        self.assertEqual(g711.ulaw_encode(0), 0xFF)  # silence, not max amplitude
        self.assertEqual(g711.ulaw_encode(32124), 0x80)
        self.assertEqual(g711.ulaw_encode(-32124), 0x00)

    def test_alaw_known_values(self):
        self.assertEqual(g711.alaw_encode(0), 0xD5)
        self.assertEqual(g711.alaw_decode(0xD5), 8)
        self.assertEqual(g711.alaw_decode(0x55), -8)

    def test_encode_clips_without_wrapping(self):
        self.assertEqual(g711.ulaw_encode(32767), 0x80)
        self.assertEqual(g711.ulaw_encode(-32768), 0x00)
        self.assertEqual(g711.alaw_encode(32767), 0xAA)
        self.assertEqual(g711.alaw_encode(-32768), 0x2A)


class WavTests(unittest.TestCase):
    def test_mono_alaw_with_fact(self):
        meta = wav.info(read_example("switch_a_mono.wav"))
        self.assertEqual(meta, {"format": "alaw", "channels": 1, "sample_rate": 8000,
                                "frames": 12001, "duration": 1.5})

    def test_stereo_extensible_with_odd_list(self):
        meta = wav.info(read_example("switch_b_stereo.wav"))
        self.assertEqual(meta, {"format": "ulaw", "channels": 2, "sample_rate": 8000,
                                "frames": 16000, "duration": 2.0})

    def test_write_round_trip(self):
        payload = bytes(range(256)) * 5
        for fmt in ("pcm16", "alaw", "ulaw"):
            data = wav.write(fmt, 2, 8000, payload * (2 if fmt == "pcm16" else 1))
            meta, back = wav.read(data)
            self.assertEqual(meta["format"], fmt)
            self.assertEqual(back, payload * (2 if fmt == "pcm16" else 1))
            self.assertEqual(wav.info(data)["frames"], 640)

    def test_non_pcm_write_has_cbsize_and_fact(self):
        ch = wav.chunks(wav.write("alaw", 1, 8000, b"\xd5" * 100))
        self.assertEqual(ch[b"fmt "][1], 18)  # WAVEFORMATEX with cbSize
        self.assertIn(b"fact", ch)

    def test_odd_payload_padded(self):
        data = wav.write("ulaw", 1, 8000, b"\xff" * 7)
        self.assertEqual(len(data) % 2, 0)
        self.assertEqual(wav.read(data)[1], b"\xff" * 7)

    def test_decode_encode_round_trip(self):
        raw = read_example("switch_a_mono.wav")
        meta, payload = wav.read(wav.encode(wav.decode(raw), "alaw"))
        self.assertEqual(meta["format"], "alaw")
        self.assertEqual(payload, wav.read(raw)[1])


if __name__ == "__main__":
    unittest.main()
