import unittest

from burstkit.burst import parse

from captures import ac3_burst, burst, silence, swap_words, wav


class BasicTests(unittest.TestCase):
    def test_rate(self):
        self.assertEqual(parse(wav(ac3_burst()))["sample_rate"], 48000)

    def test_ac3(self):
        s = parse(wav(ac3_burst() * 3))["streams"]["0/AC-3"]
        self.assertEqual((s["bursts"], s["payload_bytes"]), (3, 3840))


class Iec61937Tests(unittest.TestCase):
    def test_byte_swapped_capture(self):
        capture = wav(swap_words(ac3_burst() * 2))
        result = parse(capture)
        self.assertEqual(result["byte_order"], "big")
        self.assertEqual(result["streams"]["0/AC-3"]["bursts"], 2)

    def test_byte_swapped_matches_plain(self):
        plain = parse(wav(ac3_burst() * 2))["streams"]
        swapped = parse(wav(swap_words(ac3_burst() * 2)))["streams"]
        self.assertEqual(plain, swapped)

    def test_irregular_spacing_and_merged_capture(self):
        # Bursts every 1536 frames plus one shorter gap: all of them counted,
        # and the odd gap reported as irregular.
        capture = burst(1, 12288) + silence(512) + burst(1, 12288) + silence(1024)
        capture += burst(1, 12288)
        result = parse(wav(capture))
        self.assertEqual(result["streams"]["0/AC-3"]["bursts"], 3)
        self.assertEqual(result["irregular"]["0/AC-3"], 2)

    def test_eac3_payload_counts_bytes(self):
        s = parse(wav(burst(21, 1792)))["streams"]["0/E-AC-3"]
        self.assertEqual(s["payload_bytes"], 1792)

    def test_error_flag_is_not_a_type(self):
        result = parse(wav(burst(1, 12288, error=True)))
        self.assertNotIn("0/type_129", result["streams"])
        self.assertEqual(result["streams"]["0/AC-3"]["errors"], 1)

    def test_silence_does_not_stop_counting(self):
        capture = burst(1, 12288) + silence(4096) + burst(1, 12288) + burst(1, 12288)
        self.assertEqual(parse(wav(capture))["streams"]["0/AC-3"]["bursts"], 3)

    def test_pause_burst(self):
        capture = burst(1, 12288) + burst(3, 32) + burst(1, 12288)
        result = parse(wav(capture))
        self.assertEqual(result["pauses"], 1)
        self.assertNotIn("3", result["streams"])
        self.assertNotIn("0/pause", result["streams"])

    def test_streams_split_by_number(self):
        capture = burst(11, 8048) + burst(11, 8048, number=1)
        self.assertEqual(sorted(parse(wav(capture))["streams"]), ["0/DTS-I", "1/DTS-I"])

    def test_bsmod(self):
        capture = burst(1, 12288, bsmod=2) + burst(21, 1792, bsmod=5) + burst(11, 8048)
        result = parse(wav(capture))
        self.assertEqual(result["bsmod"]["2/AC-3"], 2)
        self.assertEqual(result["bsmod"]["5/E-AC-3"], 5)
        self.assertIsNone(result["bsmod"]["0/DTS-I"])

    def test_bsmod_from_first_burst(self):
        capture = burst(1, 12288, bsmod=1) + burst(1, 12288, bsmod=1)
        result = parse(wav(capture))
        self.assertEqual(result["bsmod"]["1/AC-3"], 1)
        self.assertEqual(result["streams"]["1/AC-3"]["bursts"], 2)

    def test_seconds_use_per_type_period(self):
        capture = burst(11, 8048) + burst(11, 8048) + burst(5, 1536, period=1152)
        streams = parse(wav(capture))["streams"]
        self.assertEqual(streams["0/DTS-I"]["seconds"], "8/375")         # 2 * 512 / 48000
        self.assertEqual(streams["0/MPEG-1 L2/L3"]["seconds"], "3/125")  # 1 * 1152 / 48000

    def test_null_bursts_skipped(self):
        capture = burst(0, 0) + burst(1, 12288)
        result = parse(wav(capture))
        self.assertEqual(list(result["streams"]), ["0/AC-3"])

    def test_preamble_inside_payload_not_counted(self):
        payload = [0x1234, 0xF872, 0x4E1F, 1, 0x3000] + [0] * 1000
        words = [0xF872, 0x4E1F, 1, 12288] + payload
        words += [0] * (1536 * 2 - len(words))
        words += burst(1, 12288)
        self.assertEqual(parse(wav(words))["streams"]["0/AC-3"]["bursts"], 2)

    def test_resume_after_long_silence(self):
        capture = burst(1, 12288) + silence(20000) + burst(1, 12288)
        self.assertEqual(parse(wav(capture))["streams"]["0/AC-3"]["bursts"], 2)
