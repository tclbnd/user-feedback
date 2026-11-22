import unittest

from smkkit.smk import SmkError, frame_rate, parse

from videos import rate_word, smk


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(smk(frames=4, width=640, height=480, tracks=()))
        self.assertEqual((out["version"], out["width"], out["height"], out["ring_frame"]), ("SMK2", 640, 480, False))

    def test_version(self):
        self.assertEqual(parse(smk(frames=2, tracks=(), version=b"SMK4"))["version"], "SMK4")


class FrameRateTests(unittest.TestCase):
    def test_positive_word_is_milliseconds_per_frame(self):
        self.assertEqual(str(frame_rate(66)), "500/33")
        self.assertEqual(str(frame_rate(100)), "10")
        out = parse(smk(frames=30, rate=66, tracks=()))
        self.assertEqual(out["fps"], "500/33")
        self.assertEqual(out["duration"], "99/50")

    def test_negative_word_is_frames_per_second(self):
        self.assertEqual(str(frame_rate(-15)), "15")
        out = parse(smk(frames=15, rate=-15, tracks=()))
        self.assertEqual(out["fps"], "15")
        self.assertEqual(out["duration"], "1")


class RingFrameTests(unittest.TestCase):
    def test_ring_frame_excluded_from_statistics(self):
        ringed = parse(smk(frames=30, ring=True))
        plain = parse(smk(frames=30, ring=False))
        self.assertTrue(ringed["ring_frame"])
        self.assertEqual(ringed["frames"], 30)
        self.assertEqual(ringed["keyframes"], 6)
        self.assertEqual(ringed["palette_frames"], 6)
        self.assertEqual(ringed["video_bytes"], plain["video_bytes"])
        self.assertEqual(ringed["audio"][0]["bytes"], plain["audio"][0]["bytes"])
        # payload of frame i is 100 + i, ring frame i=30 must not be counted
        self.assertEqual(ringed["audio"][0]["bytes"], sum(100 + i for i in range(30)))


class AudioTests(unittest.TestCase):
    def test_info_word_smk2(self):
        word = rate_word(rate=44100, bits=8, channels=1, codec="pcm", version=b"SMK2")
        out = parse(smk(frames=4, tracks=(word,)))
        self.assertEqual(out["audio"], [{"track": 0, "sample_rate": 44100, "bits": 8,
                                         "channels": 1, "codec": "pcm",
                                         "bytes": sum(100 + i for i in range(4))}])

    def test_info_word_stereo_16bit(self):
        word = rate_word(rate=11025, bits=16, channels=2, codec="smacker", version=b"SMK2")
        a = parse(smk(frames=4, tracks=(word,)))["audio"][0]
        self.assertEqual((a["sample_rate"], a["bits"], a["channels"], a["codec"]),
                         (11025, 16, 2, "smacker"))

    def test_smk4_codecs(self):
        for codec in ("pcm", "smacker", "bink-rdft", "bink-dct"):
            word = rate_word(rate=22050, codec=codec, version=b"SMK4")
            out = parse(smk(frames=4, tracks=(word,), version=b"SMK4"))
            self.assertEqual(out["audio"][0]["codec"], codec)

    def test_audio_survives_palette_frames(self):
        out = parse(smk(frames=10))
        self.assertEqual(out["palette_frames"], 2)
        self.assertEqual(out["audio"][0]["bytes"], sum(100 + i for i in range(10)))

    def test_tracks_without_data_omitted(self):
        self.assertEqual(parse(smk(frames=4, tracks=()))["audio"], [])


class FrameWalkTests(unittest.TestCase):
    def test_truncated_frame_is_an_error(self):
        with self.assertRaises(SmkError):
            parse(smk(frames=10)[:-10])

    def test_interlaced_and_doubled_height(self):
        self.assertEqual(parse(smk(frames=2, height=200, interlaced=True, tracks=()))["height"], 400)
        self.assertEqual(parse(smk(frames=2, height=200, doubled=True, tracks=()))["height"], 400)
        self.assertEqual(parse(smk(frames=2, height=200, tracks=()))["height"], 200)

    def test_video_bytes_exclude_audio_and_palette(self):
        out = parse(smk(frames=10, tracks=()))
        self.assertGreater(out["video_bytes"], 0)
        self.assertEqual(out["audio"], [])
