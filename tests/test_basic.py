import struct
import unittest

from swavkit.swav import SwavError, parse, DS_CLOCK


def _file(kind, loop, rate, timer, loff, nlen, payload):
    info = struct.pack("<BBHHHI", kind, loop, rate, timer, loff, nlen)
    data = b"DATA" + struct.pack("<I", 8 + len(info) + len(payload)) + info + payload
    return b"SWAV" + struct.pack("<HHIHH", 0xFEFF, 0x0100, 16 + len(data), 16, 1) + data


def pcm16_file(samples, timer=759):
    payload = b"".join(struct.pack("<h", s) for s in samples)
    # halfword units: loop offset + length are counts of halfwords (samples)
    return _file(1, 0, 22050, timer, 0, len(samples), payload)


def pcm8_file(samples, loop=0, loff=0):
    payload = bytes(s & 0xFF for s in samples)
    return _file(0, loop, 16000, 1047, loff, len(samples) - loff, payload)


def adpcm_file(words, loop=0, loff=0, extra=b""):
    payload = b"".join(struct.pack("<I", w) for w in words) + extra
    return _file(2, loop, 32728, 512, loff, len(words) - loff, payload)


class BasicTests(unittest.TestCase):
    def test_pcm16_header(self):
        out = parse(pcm16_file([]))
        self.assertEqual(out["type"], "pcm16")
        self.assertEqual(out["sample_rate"], 22050)

    def test_not_swav(self):
        with self.assertRaises(SwavError):
            parse(b"STRM" + bytes(40))

    def test_pcm16_counts_halfword_units(self):
        out = parse(pcm16_file([1000, -2000, 32767]))
        self.assertEqual(out["samples"], 3)
        self.assertEqual(out["last_sample"], 32767)
        self.assertEqual(out["peak"], "%.6f" % (32767 / 32768))
        self.assertTrue(out["complete"])

    def test_pcm16_loop_start_in_samples(self):
        payload = struct.pack("<4h", 10, 20, 30, 40)
        out = parse(_file(1, 1, 22050, 759, 2, 2, payload))
        self.assertEqual(out["samples"], 4)
        self.assertEqual(out["loop_start"], 2)

    def test_pcm8_is_signed_and_scaled_x256(self):
        out = parse(pcm8_file([0, 1, -1, 127, -128]))
        self.assertEqual(out["type"], "pcm8")
        # byte 0x00 is signed zero, not the unsigned centre 128; no half-grid shift
        self.assertEqual(out["last_sample"], -128 * 256)
        self.assertEqual(out["peak"], "%.6f" % (128 * 256 / 32768))
        self.assertEqual(out["samples"], 5)

    def test_pcm8_loop_offset_bytes_equal_samples(self):
        out = parse(pcm8_file([10, 20, 30], loop=1, loff=2))
        self.assertEqual(out["loop_start"], 2)
        self.assertEqual(out["samples"], 3)

    def test_adpcm_is_supported(self):
        # header word (index 0, initial sample 0) + one block of zero nibbles
        out = parse(adpcm_file([0x00000000, 0x00000000]))
        self.assertEqual(out["type"], "adpcm")
        self.assertEqual(out["samples"], 7)
        self.assertEqual(out["last_sample"], 0)

    def test_adpcm_word_units_and_loop_start(self):
        # 3 words: header + 2 blocks -> 1 + 6*2 = 13 samples
        # loop offset 1 word -> first decoded sample of block 1 = 6*1 - 5 = 1
        out = parse(adpcm_file([0, 0, 0], loop=1, loff=1))
        self.assertEqual(out["samples"], 13)
        self.assertEqual(out["loop_start"], 1)

    def test_adpcm_loop_point_201_words(self):
        # mirrors bgm_loop.swav: loff=201 words -> 6*201 - 5 = 1201
        out = parse(adpcm_file([0] * 1001, loop=1, loff=201))
        self.assertEqual(out["loop_start"], 1201)
        self.assertEqual(out["samples"], 6001)

    def test_adpcm_decode_math(self):
        # 4-byte ADPCM header: byte0 step index 7, byte1 reserved,
        # bytes2..3 the initial little-endian predictor (100)
        head = 7 | (100 << 16)
        # block word: first byte 0x77 -> block index (upper nibble) 7,
        # three data bytes 0x77 -> six 0x7 nibbles
        block = 0x77777777
        out = parse(adpcm_file([head, block]))
        # the header word yields its predictor as the first decoded sample
        self.assertEqual(out["last_sample"], 2377)
        # reproduce the six nibbles by hand, exactly following the hardware:
        # the block header's upper nibble reloads the index, then each nibble
        # updates it through the IMA index table
        from swavkit.swav import _ADPCM_STEP, _ADPCM_INDEX
        pred, idx = 100, (block & 0xFF) >> 4
        for _ in range(6):
            step = _ADPCM_STEP[idx]
            diff = (step >> 3) + step + (step >> 1) + (step >> 2)  # nibble 0x7
            pred = min(32767, pred + diff)
            idx = max(0, min(88, idx + _ADPCM_INDEX[7]))
        self.assertEqual(out["last_sample"], pred)

    def test_adpcm_header_predictor_is_first_sample(self):
        # index 0, reserved 0, predictor -1234 in little endian
        head = (-1234 & 0xFFFF) << 16
        out = parse(adpcm_file([head]))
        self.assertEqual(out["samples"], 1)
        self.assertEqual(out["last_sample"], -1234)
        self.assertEqual(out["peak"], "%.6f" % (1234 / 32768))

    def test_timer_rate(self):
        for timer in (0, 512, 759, 1047):
            out = parse(pcm16_file([], timer=timer))
            period = timer + 1
            expected = (2 * DS_CLOCK + period) // (2 * period)
            self.assertEqual(out["timer_rate"], expected, timer)

    def test_duration_exact_fraction(self):
        out = parse(pcm8_file([0] * 750))  # 750 / 16000 = 3/64
        self.assertEqual(out["duration"], "3/64")

    def test_incomplete_wave(self):
        full = adpcm_file([0x00000000, 0x00000000])
        out = parse(full[:-3])  # truncate the trailing block word
        self.assertFalse(out["complete"])
        # counts still come from the wave information header
        self.assertEqual(out["samples"], 7)
        # header word alone still decodes to its one initial sample
        self.assertEqual(out["last_sample"], 0)

    def test_bad_type_rejected(self):
        with self.assertRaises(SwavError):
            parse(_file(9, 0, 22050, 759, 0, 0, b""))


if __name__ == "__main__":
    unittest.main()
