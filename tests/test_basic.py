import unittest

from dvkit.dv import scan

from tapes import clip, frame


class BasicTests(unittest.TestCase):
    def test_ntsc(self):
        out = scan(clip(4, False, start=(0, 0, 0, 0)))
        self.assertEqual((out["system"], out["frames"], out["frame_rate"]), ("525-60", 4, "30000/1001"))

    def test_audio(self):
        a = scan(clip(2, False, audio={"rate": 48000}))["audio"]
        self.assertEqual((a["sample_rate"], a["channels"], a["locked"]), (48000, 2, True))

    def test_pal_frame_count(self):
        out = scan(clip(5, True))
        self.assertEqual((out["system"], out["frames"], out["frame_rate"]), ("625-50", 5, "25"))
        self.assertEqual(out["duration"], "1/5")

    def test_timecode_is_bcd(self):
        out = scan(clip(2, False, start=(10, 0, 0, 28)))
        self.assertEqual(out["first_timecode"], "10:00:00:28")
        self.assertEqual(out["last_timecode"], "10:00:00:29")

    def test_drop_frame_timecode(self):
        data = clip(2, False, start=(10, 0, 0, 0))
        data = data.replace(bytes([0x13, 0x00, 0x00, 0x00, 0x10]),
                            bytes([0x13, 0x40, 0x00, 0x00, 0x10]))
        self.assertEqual(scan(data)["first_timecode"], "10:00:00;00")

    def test_rate_44k1(self):
        a = scan(clip(2, False, audio={"rate": 44100}))["audio"]
        self.assertEqual(a["sample_rate"], 44100)

    def test_12bit_audio(self):
        a = scan(clip(2, False, audio={"rate": 32000, "twelve": True}))["audio"]
        self.assertEqual((a["sample_rate"], a["bits"], a["channels"]), (32000, 12, 4))

    def test_aspect(self):
        self.assertEqual(scan(clip(1, False, disp=0))["aspect"], "4:3")
        self.assertEqual(scan(clip(1, False, disp=1))["aspect"], "14:9")
        self.assertEqual(scan(clip(1, False, disp=2))["aspect"], "16:9")

    def test_audio_samples_from_aaux_pack(self):
        out = scan(clip(3, True, audio={"rate": 48000, "af": 24}))
        self.assertEqual(out["audio_samples"], 3 * 1920)
        out = scan(clip(2, False, audio={"rate": 44100, "af": 14}))
        self.assertEqual(out["audio_samples"], 2 * 1471)

    def test_recorded_year(self):
        out = scan(clip(1, False, date=(2009, 3, 1), time=(9, 30, 0)))
        self.assertEqual(out["recorded"], "2009-03-01T09:30:00")
        out = scan(clip(1, False, date=(1999, 12, 31), time=(23, 59, 58)))
        self.assertEqual(out["recorded"], "1999-12-31T23:59:58")

    def test_trailing_bytes(self):
        out = scan(clip(2, False) + clip(1, False)[:5000])
        self.assertEqual((out["frames"], out["trailing_bytes"]), (2, 5000))

    def test_dv50(self):
        ch0 = frame(False)
        ch1 = bytearray(ch0)
        for i in range(1, len(ch1), 80):
            ch1[i] |= 0x08  # FSC: second DIF channel
        out = scan((ch0 + bytes(ch1)) * 3)
        self.assertEqual((out["frames"], out["bitrate_mbps"]), (3, 50))
