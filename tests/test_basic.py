import unittest

from nsvkit.nsv import parse

from frames import file_header, plain_frame, sync_frame


class BasicTests(unittest.TestCase):
    def test_format(self):
        out = parse(sync_frame())
        self.assertEqual((out["video_format"], out["width"], out["height"], out["header"]), ("VP62", 320, 240, None))

    def test_payload(self):
        out = parse(sync_frame(100, 20) * 2)
        self.assertEqual((out["frames"], out["video_bytes"], out["audio_bytes"]), (2, 200, 40))

    def test_metadata_values_may_contain_spaces(self):
        head = file_header('Title="Late Night Session 14" Artist="DJ Kestrel" URL=http://radio.example/14')
        out = parse(head + sync_frame())
        self.assertEqual(out["header"]["metadata"],
                         {"Title": "Late Night Session 14", "Artist": "DJ Kestrel", "URL": "http://radio.example/14"})

    def test_fractional_frame_rate(self):
        out = parse(sync_frame(rate=0x81) * 90)
        self.assertEqual((out["fps"], out["duration"]), ("30000/1001", "3003/1000"))

    def test_plain_frames_are_counted(self):
        out = parse(sync_frame() + plain_frame() * 2)
        self.assertEqual((out["frames"], out["sync_frames"]), (3, 1))

    def test_twenty_bit_video_size(self):
        out = parse(sync_frame(9000) + plain_frame(1100))
        self.assertEqual((out["frames"], out["video_bytes"], out["skipped_bytes"]), (2, 10100, 0))

    def test_auxiliary_data_is_not_video(self):
        out = parse(sync_frame(100, 20, aux=[(b"SUB ", 30)]))
        self.assertEqual((out["video_bytes"], out["aux"], out["aux_bytes"], out["largest_video_frame"]),
                         (64, {"SUB ": 1}, 30, 64))

    def test_audio_frames(self):
        out = parse(sync_frame(100, 0) + plain_frame(100))
        self.assertEqual((out["audio_frames"], out["audio_bytes"]), (1, 20))

    def test_skipped_bytes(self):
        out = parse(sync_frame() + b"junk" + plain_frame())
        self.assertEqual((out["frames"], out["skipped_bytes"]), (2, 4))
