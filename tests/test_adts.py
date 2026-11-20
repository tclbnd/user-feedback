"""ADTS header parsing, resync and probe behaviour.

Frames are synthesised from the ISO/IEC 13818-7 / 14496-3 ADTS header layout
so the suite is independent of the captured example files.
"""
import os
import unittest

from aacpack import adts
from aacpack.adts import AdtsError, parse, frames, skip_id3v2
from aacpack.probe import audio_specific_config, probe

HERE = os.path.dirname(__file__)
EXAMPLES = os.path.join(HERE, "..", "examples")


def build_frame(*, mpeg4=True, profile=1, freq_index=4, channel_config=2,
                raw_blocks=1, crc=False, payload_size=20, fill=0x11):
    """Return one ADTS frame; payload_size excludes header and CRC."""
    header_size = 7 + (2 if crc else 0)
    length = header_size + payload_size
    b1 = 0xF0 | ((0 if mpeg4 else 1) << 3) | (0 if crc else 1)
    b2 = ((profile & 3) << 6) | ((freq_index & 0xF) << 2) | (channel_config >> 2)
    b3 = ((channel_config & 3) << 6) | ((length >> 11) & 3)
    b4 = (length >> 3) & 0xFF
    b5 = (length & 7) << 5
    b6 = (raw_blocks - 1) & 3
    header = bytes([0xFF, b1, b2, b3, b4, b5, b6])
    if crc:
        header += b"\xAB\xCD"
    return header + bytes([fill]) * payload_size


def build_stream(count, **kwargs):
    return b"".join(build_frame(**kwargs) for _ in range(count))


def asc_bits(hexstr):
    value = int(hexstr, 16)
    width = len(hexstr) * 4
    bits = bin(value)[2:].zfill(width)
    pos = 0

    def take(n):
        nonlocal pos
        out = int(bits[pos:pos + n], 2)
        pos += n
        return out

    aot = take(5)
    if aot == 31:
        aot = 32 + take(6)
    freq = take(4)
    if freq == 0xF:
        rate = take(24)
    else:
        rate = adts.RATES[freq]
    chan = take(4)
    return aot, freq, rate, chan


class HeaderTests(unittest.TestCase):
    def test_iso_example_header(self):
        f = parse(bytes([0xFF, 0xF1, 0x50, 0x80, 0x2E, 0x7F, 0xFC]))
        self.assertEqual((f.sample_rate, f.channel_config, f.length, f.crc),
                         (44100, 2, 371, False))
        self.assertEqual(f.header_size, 7)
        self.assertEqual(f.raw_blocks, 1)

    def test_mpeg2_and_mpeg4_identification(self):
        f4 = parse(build_frame(mpeg4=True)[:7])
        f2 = parse(build_frame(mpeg4=False)[:7])
        self.assertEqual(f4.mpeg_version, 4)
        self.assertEqual(f2.mpeg_version, 2)

    def test_all_sample_rate_indices(self):
        for index, rate in enumerate(adts.RATES):
            if index in (13, 14):
                with self.assertRaises(AdtsError):
                    parse(build_frame(freq_index=index)[:7])
                continue
            f = parse(build_frame(freq_index=index)[:7])
            self.assertEqual(f.freq_index, index)
            if index == 15:
                self.assertIsNone(f.sample_rate)
            else:
                self.assertEqual(f.sample_rate, rate)

    def test_channel_configurations(self):
        for config, count in enumerate(adts.CHANNEL_COUNTS):
            f = parse(build_frame(channel_config=config)[:7])
            self.assertEqual(f.channel_config, config)
            self.assertEqual(f.channels, count)

    def test_seven_one_is_eight_channels(self):
        f = parse(build_frame(channel_config=7)[:7])
        self.assertEqual(f.channels, 8)
        self.assertEqual(f.channel_config, 7)

    def test_raw_data_block_count(self):
        for blocks in (1, 2, 3, 4):
            f = parse(build_frame(raw_blocks=blocks)[:7])
            self.assertEqual(f.raw_blocks, blocks)

    def test_crc_extends_header_by_two_bytes(self):
        no_crc = parse(build_frame(crc=False)[:7])
        with_crc = parse(build_frame(crc=True)[:7])
        self.assertFalse(no_crc.crc)
        self.assertEqual(no_crc.header_size, 7)
        self.assertTrue(with_crc.crc)
        self.assertEqual(with_crc.header_size, 9)

    def test_frame_length_covers_header_crc_and_payload(self):
        f = parse(build_frame(crc=True, payload_size=40)[:7])
        self.assertEqual(f.length, 9 + 40)
        self.assertEqual(f.length - f.header_size, 40)

    def test_rejects_bad_sync_and_short_buffer(self):
        with self.assertRaises(AdtsError):
            parse(b"\x00\xF1\x50\x80\x2E\x7F\xFC")
        with self.assertRaises(AdtsError):
            parse(b"\xFF\xE1\x50\x80\x2E\x7F\xFC")
        with self.assertRaises(AdtsError):
            parse(b"\xFF\xF1\x50")

    def test_rejects_frame_shorter_than_header(self):
        frame = bytearray(build_frame(crc=True))
        frame[3] &= 0x3F
        frame[4] = 0
        frame[5] = 0x60  # length field becomes 3, smaller than 9 byte header
        with self.assertRaises(AdtsError):
            parse(bytes(frame[:7]))


class Id3Tests(unittest.TestCase):
    def test_id3v2_syncsafe_size(self):
        tag = b"ID3\x04\x00\x00\x00\x00\x00\x7F" + b"\x00" * 127
        self.assertEqual(skip_id3v2(tag), 137)

    def test_id3v2_footer_adds_ten_bytes(self):
        tag = b"ID3\x04\x00\x10\x00\x00\x00\x05" + b"\x00" * 5
        self.assertEqual(skip_id3v2(tag), 25)

    def test_no_tag_returns_offset(self):
        self.assertEqual(skip_id3v2(b"\xFF\xF1xx"), 0)

    def test_probe_skips_leading_id3(self):
        tag = b"ID3\x04\x00\x00\x00\x00\x00\x0A" + b"\x00" * 10
        got = list(frames(tag + build_stream(3)))
        self.assertEqual(len(got), 3)
        self.assertEqual(got[0].offset, len(tag))


class ResyncTests(unittest.TestCase):
    def test_leading_garbage_is_skipped(self):
        got = list(frames(b"\x01\x02\x03" + build_stream(2)))
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0].offset, 3)

    def test_bad_bytes_between_frames_resync(self):
        stream = build_frame() + b"\xAA\xBB\xCC" + build_frame()
        stream += b"\xDE\xAD" + build_frame()
        got = list(frames(stream))
        self.assertEqual([f.offset for f in got], [0, 30, 59])
        self.assertEqual(len(got), 3)

    def test_truncated_trailing_frame_still_reads_complete_ones(self):
        stream = build_stream(2) + build_frame()[:10]
        self.assertEqual(len(list(frames(stream))), 2)

    def test_truncated_header_only(self):
        stream = build_stream(1) + b"\xFF\xF1\x50"
        self.assertEqual(len(list(frames(stream))), 1)

    def test_all_garbage_yields_no_frames(self):
        self.assertEqual(list(frames(b"\x00" * 50)), [])

    def test_reserved_index_frame_is_skipped_not_fatal(self):
        stream = build_frame() + bytes(bytearray(build_frame(freq_index=13)))
        stream += build_frame()
        self.assertEqual(len(list(frames(stream))), 2)


class ProbeTests(unittest.TestCase):
    def test_basic_stereo_lc_summary(self):
        info = probe(build_stream(5, payload_size=30))
        self.assertEqual(info["mpeg_version"], 4)
        self.assertEqual(info["object_type"], 2)  # ADTS profile 1 -> AOT 2 (LC)
        self.assertEqual(info["sample_rate"], 44100)
        self.assertEqual(info["channels"], 2)
        self.assertFalse(info["crc"])
        self.assertEqual(info["frames"], 5)
        self.assertEqual(info["raw_blocks"], 5)
        self.assertEqual(info["samples"], 5 * 1024)
        self.assertEqual(info["payload_bytes"], 5 * 30)
        self.assertEqual(info["duration"], "256/2205")

    def test_multiple_raw_blocks_per_frame_count_samples(self):
        info = probe(build_stream(10, raw_blocks=3))
        self.assertEqual(info["frames"], 10)
        self.assertEqual(info["raw_blocks"], 30)
        self.assertEqual(info["samples"], 30 * 1024)
        info24 = probe(build_stream(10, raw_blocks=3, freq_index=6))
        self.assertEqual(info24["samples"], 30720)
        self.assertEqual(info24["duration"], "32/25")

    def test_seven_one_programme_reports_eight_channels(self):
        info = probe(build_stream(2, channel_config=7))
        self.assertEqual(info["channels"], 8)
        self.assertEqual(info["channel_config"], 7)

    def test_crc_payload_excludes_header_and_crc(self):
        info = probe(build_stream(4, crc=True, payload_size=25))
        self.assertTrue(info["crc"])
        self.assertEqual(info["payload_bytes"], 4 * 25)
        self.assertEqual(len(build_frame(crc=True, payload_size=25)), 25 + 9)

    def test_audio_specific_config_object_type_is_profile_plus_one(self):
        for profile, aot in ((0, 1), (1, 2), (2, 3), (3, 4)):
            f = parse(build_frame(profile=profile)[:7])
            aot_got, freq, rate, chan = asc_bits(audio_specific_config(f))
            self.assertEqual(aot_got, aot)
            self.assertEqual(freq, 4)
            self.assertEqual(rate, 44100)
            self.assertEqual(chan, 2)

    def test_lc_is_not_main(self):
        f = parse(build_frame(profile=1)[:7])
        self.assertEqual(audio_specific_config(f), "1210")  # AOT 2, 44100, stereo

    def test_audio_specific_config_matches_frame_fields(self):
        f = parse(build_frame(profile=1, freq_index=6, channel_config=1)[:7])
        self.assertEqual(audio_specific_config(f), "1308")
        self.assertEqual(asc_bits(audio_specific_config(f)), (2, 6, 24000, 1))

    def test_mpeg2_stream_summary(self):
        info = probe(build_stream(3, mpeg4=False, freq_index=6, channel_config=1,
                                  raw_blocks=2, payload_size=180))
        self.assertEqual(info["mpeg_version"], 2)
        self.assertEqual(info["frames"], 3)
        self.assertEqual(info["raw_blocks"], 6)
        self.assertEqual(info["samples"], 6144)
        self.assertEqual(info["payload_bytes"], 3 * 180)
        self.assertEqual(info["duration"], "32/125")

    def test_truncated_and_dirty_stream_summary(self):
        stream = build_frame(payload_size=10) + b"\x99" * 4
        stream += build_frame(payload_size=10) + build_frame(payload_size=10)[:6]
        info = probe(stream)
        self.assertEqual(info["frames"], 2)
        self.assertEqual(info["raw_blocks"], 2)
        self.assertEqual(info["samples"], 2048)
        self.assertEqual(info["payload_bytes"], 20)

    def test_empty_and_junk_only_raise(self):
        with self.assertRaises(AdtsError):
            probe(b"")
        with self.assertRaises(AdtsError):
            probe(b"\x00" * 64)


class ExampleFileTests(unittest.TestCase):
    def test_station_a_id3_prefixed_mpeg4(self):
        with open(os.path.join(EXAMPLES, "station_a.aac"), "rb") as fh:
            data = fh.read()
        self.assertTrue(data.startswith(b"ID3"))
        info = probe(data)
        self.assertEqual(info["mpeg_version"], 4)
        self.assertEqual(info["object_type"], 2)
        self.assertEqual(info["sample_rate"], 44100)
        self.assertEqual(info["channels"], 2)
        self.assertEqual(info["frames"], 400)
        self.assertEqual(info["raw_blocks"], 400)
        self.assertEqual(info["samples"], 409600)
        self.assertEqual(info["audio_specific_config"], "1210")
        self.assertEqual(info["payload_bytes"],
                         sum(f.length - f.header_size for f in frames(data)))

    def test_station_b_multi_block_mpeg2(self):
        with open(os.path.join(EXAMPLES, "station_b.aac"), "rb") as fh:
            data = fh.read()
        info = probe(data)
        self.assertEqual(info["mpeg_version"], 2)
        self.assertEqual(info["sample_rate"], 24000)
        self.assertEqual(info["channels"], 1)
        self.assertEqual(info["frames"], 300)
        self.assertEqual(info["raw_blocks"], 600)
        self.assertEqual(info["samples"], 614400)
        self.assertEqual(info["duration"], "128/5")
        self.assertEqual(info["audio_specific_config"], "1308")


if __name__ == "__main__":
    unittest.main()
