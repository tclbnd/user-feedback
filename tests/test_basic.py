import struct
import unittest

from roqkit.roq import RoqError, parse

import movies


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(movies.empty())
        self.assertEqual((out["fps"], out["frames"], out["audio_channels"]), (30, 0, None))

    def test_not_roq(self):
        with self.assertRaises(RoqError):
            parse(b"RIFF" + bytes(20))

    def test_fps_comes_from_the_header(self):
        for fps in (10, 15, 24, 30):
            self.assertEqual(parse(movies.header(fps))["fps"], fps)
        self.assertEqual(parse(movies.header(15))["video_duration"], "0")

    def test_chunks_are_walked_with_eight_byte_preambles(self):
        data = movies.movie(15, movies.info(640, 480), movies.vq(), movies.vq(), movies.vq())
        out = parse(data)
        self.assertEqual((out["width"], out["height"], out["frames"]), (640, 480, 3))
        self.assertEqual((out["unknown_chunks"], out["truncated"]), (0, False))

    def test_info_without_info_chunk(self):
        out = parse(movies.movie(30, movies.vq()))
        self.assertEqual((out["width"], out["height"]), (None, None))

    def test_codebook_cell_counts(self):
        out = parse(movies.movie(30, movies.codebook(100, 200), movies.codebook(4, 6)))
        self.assertEqual((out["codebooks"], out["cells_2x2"], out["cells_4x4"]), (2, 104, 206))

    def test_full_codebook_counts_256(self):
        # A cell count of 256 is stored in a byte and wraps to zero.
        out = parse(movies.movie(30, movies.codebook(256, 256), movies.codebook(120, 256)))
        self.assertEqual((out["cells_2x2"], out["cells_4x4"]), (376, 512))

    def test_jpeg_frames_count_as_frames(self):
        out = parse(movies.movie(30, movies.vq(), movies.jpeg(), movies.jpeg()))
        self.assertEqual((out["frames"], out["jpeg_frames"]), (3, 2))

    def test_mono_audio(self):
        out = parse(movies.movie(30, movies.mono(735, 0xF800)))
        self.assertEqual((out["audio_channels"], out["audio_samples"]), (1, 735))
        self.assertEqual(out["audio_predictors"], [-2048])

    def test_stereo_audio_counts_sample_frames(self):
        out = parse(movies.movie(30, movies.stereo(735), movies.stereo(735)))
        self.assertEqual((out["audio_channels"], out["audio_samples"]), (2, 1470))

    def test_audio_predictors_come_from_the_first_audio_chunk(self):
        out = parse(movies.movie(30, movies.stereo(10, -4, 2), movies.stereo(10, 5, 6)))
        self.assertEqual(out["audio_predictors"], [-4, 2])

    def test_audio_duration_uses_22050_hz(self):
        out = parse(movies.movie(30, movies.stereo(22050)))
        self.assertEqual(out["audio_duration"], "1")

    def test_packet_and_unknown_chunks(self):
        data = movies.movie(30, movies.packet(b"\0" * 8), movies.chunk(0x1040, b"\0" * 4))
        out = parse(data)
        self.assertEqual((out["packets"], out["unknown_chunks"]), (1, 1))

    def test_truncated_last_chunk(self):
        data = movies.header(30) + struct.pack("<HIH", movies.VQ, 100, 0) + bytes(10)
        out = parse(data)
        self.assertTrue(out["truncated"])

    def test_examples(self):
        for name, expected in (
            ("intro.roq", {"fps": 15, "width": 640, "height": 480, "frames": 24, "jpeg_frames": 0,
                           "audio_channels": 2, "audio_samples": 35280, "audio_duration": "8/5"}),
            ("briefing.roq", {"fps": 30, "width": 512, "height": 256, "frames": 30, "jpeg_frames": 30,
                              "audio_channels": 1, "audio_samples": 22050, "audio_duration": "1"}),
        ):
            with open("examples/" + name, "rb") as fh:
                out = parse(fh.read())
            for key, value in expected.items():
                self.assertEqual(out[key], value, "%s %s" % (name, key))
            self.assertEqual((out["unknown_chunks"], out["truncated"]), (0, False))

