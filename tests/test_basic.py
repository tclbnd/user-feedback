import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from flackit.flac import FlacError, crc16, scan  # noqa: E402
from streams import block, flac, frame, header, id3tag, streaminfo, vorbis  # noqa: E402


def corrupt(fr, at=6):
    b = bytearray(fr)
    b[at] ^= 0xFF
    return bytes(b)


class BasicTest(unittest.TestCase):
    def test_fixed_blocking(self):
        out = scan(flac([frame(i, seed=i) for i in range(6)], streaminfo(total=6 * 4096)))
        self.assertEqual(out["sample_rate"], 44100)
        self.assertEqual(out["total_samples"], 24576)
        self.assertEqual(out["frames"], 6)
        self.assertEqual(out["last_number"], 5)
        self.assertEqual(out["blocking"], "fixed")

    def test_vorbis_comment(self):
        out = scan(flac([frame(0)], streaminfo(), [block(4, vorbis(tags=["ARTIST=Mira"]))]))
        self.assertEqual(out["tags"], ["ARTIST=Mira"])
        self.assertEqual([b["type"] for b in out["metadata"]], ["STREAMINFO", "VORBIS_COMMENT"])

    def test_not_flac(self):
        with self.assertRaises(FlacError):
            scan(b"OggS" + bytes(60))


class ArchiveTest(unittest.TestCase):
    def test_id3v2_in_front(self):
        out = scan(flac([frame(0), frame(1)], streaminfo(total=8192), id3=id3tag(200)))
        self.assertEqual(out["id3v2_bytes"], 210)
        self.assertEqual(out["frames"], 2)

    def test_id3v2_footer(self):
        out = scan(flac([frame(0)], streaminfo(), id3=id3tag(300, footer=True)))
        self.assertEqual(out["id3v2_bytes"], 320)
        self.assertEqual(out["frames"], 1)

    def test_36_bit_total_samples(self):
        total = (1 << 36) - 1
        out = scan(flac([frame(0)], streaminfo(total=total)))
        self.assertEqual(out["total_samples"], total)

    def test_seektable_placeholders(self):
        from streams import seektable
        table = seektable([(0, 0, 4096), (8388608, 90000, 4096)], placeholders=3)
        out = scan(flac([frame(0)], streaminfo(), [block(3, table)]))
        self.assertEqual((out["seek_points"], out["placeholders"]), (2, 3))

    def test_16_bit_block_size(self):
        out = scan(flac([frame(0, blocksize=6000)], streaminfo(total=6000, max_block=6000)))
        self.assertEqual(out["frame_samples"], 6000)

    def test_channel_modes_are_stereo(self):
        for code, name in ((8, "left_side"), (9, "right_side"), (10, "mid_side")):
            out = scan(flac([frame(0, ch_code=code)], streaminfo(total=4096)))
            self.assertEqual(out["channel_modes"], {name: 1})
            self.assertEqual(out["channels"], 2)

    def test_sync_code_in_audio_data(self):
        fake = header(12345)                    # a header with a valid CRC-8
        body = bytearray(frame(0, seed=1))
        body[len(body) // 2:len(body) // 2 + len(fake)] = fake
        first = bytes(body) + crc16(bytes(body)).to_bytes(2, "big")
        out = scan(flac([first, frame(1, seed=2)], streaminfo(total=8192)))
        self.assertEqual((out["frames"], out["bad_frames"]), (2, 0))
        self.assertEqual(out["last_number"], 1)

    def test_bad_frame_counted_not_split(self):
        frames = [frame(i, seed=i) for i in range(5)]
        frames[2] = corrupt(frames[2])
        out = scan(flac(frames, streaminfo(total=5 * 4096)))
        self.assertEqual((out["frames"], out["bad_frames"]), (4, 1))
        self.assertEqual(out["first_number"], 0)
        self.assertEqual(out["last_number"], 4)


if __name__ == "__main__":
    unittest.main()
