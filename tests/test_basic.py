import unittest

from dvkit.dv import scan

from tapes import clip


class BasicTests(unittest.TestCase):
    def test_ntsc(self):
        out = scan(clip(4, False, start=(0, 0, 0, 0)))
        self.assertEqual((out["system"], out["frames"], out["frame_rate"]), ("525-60", 4, "30000/1001"))

    def test_audio(self):
        a = scan(clip(2, False, audio={"rate": 48000}))["audio"]
        self.assertEqual((a["sample_rate"], a["channels"], a["locked"]), (48000, 2, True))

    def test_pal_frame_count(self):
        out = scan(clip(5, True))
        self.assertEqual((out["system"], out["frames"], out["frame_rate"], out["duration"]),
                         ("625-50", 5, "25", "1/5"))

    def test_timecode_is_bcd(self):
        out = scan(clip(2, True, start=(10, 59, 59, 20)))
        self.assertEqual(out["first_timecode"], "10:59:59:20")
        self.assertEqual(out["last_timecode"], "10:59:59:21")

    def test_drop_frame_timecode(self):
        out = scan(clip(2, False, start=(1, 0, 0, 0), drop=True))
        self.assertEqual(out["first_timecode"], "01:00:00;00")
        self.assertEqual(out["last_timecode"], "01:00:00;01")

    def test_sample_rates(self):
        for rate in (48000, 44100, 32000):
            a = scan(clip(1, False, audio={"rate": rate}))["audio"]
            self.assertEqual(a["sample_rate"], rate)

    def test_audio_samples_from_aaux_pack(self):
        out = scan(clip(3, False, audio={"rate": 48000, "af": 100}))
        self.assertEqual(out["audio_samples"], 300)

    def test_twelve_bit_audio(self):
        a = scan(clip(1, False, audio={"rate": 32000, "twelve": True}))["audio"]
        self.assertEqual((a["bits"], a["channels"]), (12, 4))

    def test_aspect(self):
        for disp, want in ((0, "4:3"), (1, "14:9"), (2, "16:9")):
            self.assertEqual(scan(clip(1, True, disp=disp))["aspect"], want)

    def test_recorded_date(self):
        out = scan(clip(1, True, date=(2009, 3, 1), time=(9, 30, 0)))
        self.assertEqual(out["recorded"], "2009-03-01T09:30:00")
        out = scan(clip(1, True, date=(1998, 12, 31), time=(23, 59, 58)))
        self.assertEqual(out["recorded"], "1998-12-31T23:59:58")

    def test_trailing_bytes(self):
        data = clip(2, False)
        out = scan(data + b"\xff" * 100)
        self.assertEqual((out["frames"], out["trailing_bytes"]), (2, 100))
        out = scan(data + data[:len(data) // 3])
        self.assertEqual((out["frames"], out["trailing_bytes"]), (2, len(data) // 3))

    def test_dv50(self):
        out = scan(clip(3, False, channels=2))
        self.assertEqual((out["frames"], out["bitrate_mbps"]), (3, 50))
        out = scan(clip(3, True, channels=2))
        self.assertEqual((out["frames"], out["bitrate_mbps"]), (3, 50))


if __name__ == "__main__":
    unittest.main()
