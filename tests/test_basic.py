import unittest

from roqkit.roq import RoqError, parse

from movies import empty, movie, rich, truncated
from movies import CODEBOOK, INFO, JPEG, MONO, PACKET, STEREO, VQ


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(empty())
        self.assertEqual((out["fps"], out["frames"], out["audio_channels"]), (30, 0, None))
        self.assertEqual(out["video_duration"], "0")
        self.assertEqual(out["audio_duration"], "0")

    def test_not_roq(self):
        with self.assertRaises(RoqError):
            parse(b"RIFF" + bytes(20))

    def test_fps_comes_from_header(self):
        self.assertEqual(parse(movie(15, []))["fps"], 15)
        self.assertEqual(parse(movie(24, []))["fps"], 24)

    def test_info_and_following_chunks_align(self):
        out = parse(movie(15, [(INFO, 8, 0), (VQ, 4, 0), (JPEG, 4, 0)]))
        self.assertEqual((out["width"], out["height"]), (0, 0))
        self.assertEqual(out["frames"], 2)
        self.assertEqual(out["jpeg_frames"], 1)
        self.assertEqual(out["unknown_chunks"], 0)

    def test_codebook_zero_means_256_units(self):
        out = parse(movie(30, [(CODEBOOK, 0, 0x0000), (CODEBOOK, 0, 0x0408)]))
        self.assertEqual(out["codebooks"], 2)
        self.assertEqual(out["cells_2x2"], 256 + 4)
        self.assertEqual(out["cells_4x4"], 256 + 8)

    def test_stereo_samples_are_per_channel(self):
        out = parse(movie(30, [(STEREO, 14, 0xFC02), (STEREO, 6, 0x0000)]))
        self.assertEqual(out["audio_channels"], 2)
        self.assertEqual(out["audio_samples"], 7 + 3)
        self.assertEqual(out["audio_duration"], "1/2205")
        self.assertEqual(out["audio_predictors"], [-4, 2])

    def test_mono_predictor_is_signed_16bit(self):
        out = parse(movie(30, [(MONO, 5, 0xF800)]))
        self.assertEqual(out["audio_predictors"], [-2048])
        self.assertEqual(out["audio_samples"], 5)

    def test_jpeg_frames_count_as_frames(self):
        out = parse(movie(30, [(VQ, 0, 0), (JPEG, 0, 0), (JPEG, 0, 0)]))
        self.assertEqual(out["frames"], 3)
        self.assertEqual(out["jpeg_frames"], 2)

    def test_packets_and_unknown_chunks(self):
        out = parse(movie(30, [(PACKET, 8, 0), (0x1099, 2, 0)]))
        self.assertEqual(out["packets"], 1)
        self.assertEqual(out["unknown_chunks"], 1)

    def test_rich_movie(self):
        out = parse(rich())
        self.assertEqual(out["fps"], 15)
        self.assertEqual(out["frames"], 2)
        self.assertEqual(out["jpeg_frames"], 1)
        self.assertEqual(out["codebooks"], 2)
        self.assertEqual((out["cells_2x2"], out["cells_4x4"]), (260, 264))
        self.assertEqual(out["audio_channels"], 2)
        self.assertEqual(out["audio_samples"], 12)
        self.assertEqual(out["audio_predictors"], [-4, 2])
        self.assertEqual(out["packets"], 1)
        self.assertFalse(out["truncated"])

    def test_truncated_chunk(self):
        out = parse(truncated())
        self.assertTrue(out["truncated"])


if __name__ == "__main__":
    unittest.main()

