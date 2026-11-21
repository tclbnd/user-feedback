import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from latmkit.latm import LatmError, scan  # noqa: E402
from streams import asc, au, frame, smc, stream  # noqa: E402

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


class BasicTest(unittest.TestCase):
    def test_aac_lc_stereo(self):
        out = scan(stream(smc(asc(rate=48000, chconf=2)), count=4, size=100))
        self.assertEqual(out["object_type"], 2)
        self.assertEqual(out["sample_rate"], 48000)
        self.assertEqual(out["channels"], 2)
        self.assertEqual(out["access_units"], 4)
        self.assertEqual(out["payload_bytes"], 400)
        self.assertEqual(out["duration"], "32/375")

    def test_same_stream_mux(self):
        out = scan(stream(smc(asc(rate=44100, chconf=1)), count=6, size=50))
        self.assertEqual(out["configs"], 1)
        self.assertEqual(out["loas_frames"], 6)

    def test_no_config(self):
        with self.assertRaises(LatmError):
            scan(b"\x00" * 16)


class LeadingGarbageTest(unittest.TestCase):
    """Recorder captures often begin mid-stream or with junk bytes."""

    def test_garbage_prefix_skipped(self):
        good = stream(smc(asc(rate=48000, chconf=2)), count=4, size=100)
        out = scan(b"\x00\x11\x22\x33\x44" + good)
        self.assertEqual(out["sample_rate"], 48000)
        self.assertEqual(out["loas_frames"], 4)
        self.assertEqual(out["access_units"], 4)
        self.assertEqual(out["payload_bytes"], 400)

    def test_prefix_that_looks_like_a_syncword(self):
        # 0x56 0xE1 starts like AudioSyncStream but its length runs past the file.
        bad = bytes([0x56, 0xE1, 0xFF, 0xAA, 0xBB])
        good = stream(smc(asc(rate=48000, chconf=2)), count=3, size=60)
        out = scan(bad + good)
        self.assertEqual(out["loas_frames"], 3)
        self.assertEqual(out["payload_bytes"], 180)

    def test_frames_before_first_config_are_not_counted(self):
        cfg = smc(asc(rate=48000, chconf=2))
        data = frame([au(50)]) + frame([au(50)], cfg) + frame([au(50)])
        out = scan(data)
        self.assertEqual(out["loas_frames"], 2)
        self.assertEqual(out["access_units"], 2)
        self.assertEqual(out["configs"], 1)
        self.assertEqual(out["payload_bytes"], 100)

    def test_config_appears_mid_file(self):
        cfg = smc(asc(rate=48000, chconf=2))
        data = b"\xAA" * 7 + frame([au(40)], cfg) + frame([au(40)])
        out = scan(data)
        self.assertEqual(out["loas_frames"], 2)
        self.assertEqual(out["payload_bytes"], 80)


class ObjectTypeTest(unittest.TestCase):
    """HE-AAC, both hierarchically and by sync extension."""

    def test_explicit_sbr_reports_core_object_type(self):
        out = scan(stream(smc(asc(aot=2, rate=24000, chconf=2, sbr=48000)), count=2, size=50))
        self.assertEqual(out["object_type"], 2)
        self.assertEqual(out["sample_rate"], 24000)
        self.assertEqual(out["ext_sample_rate"], 48000)
        self.assertFalse(out["ps"])

    def test_explicit_ps(self):
        out = scan(stream(smc(asc(aot=2, rate=24000, chconf=2, sbr=48000, sbr_aot=29)),
                          count=2, size=50))
        self.assertEqual(out["object_type"], 2)
        self.assertEqual(out["ext_sample_rate"], 48000)
        self.assertTrue(out["ps"])

    def test_implicit_sbr_sync_extension(self):
        out = scan(stream(smc(asc(rate=44100, chconf=2, sbr=48000, sbr_sync=True)),
                          count=2, size=50))
        self.assertEqual(out["object_type"], 2)
        self.assertEqual(out["sample_rate"], 44100)
        self.assertEqual(out["ext_sample_rate"], 48000)

    def test_implicit_ps_sync_extension(self):
        out = scan(stream(smc(asc(rate=24000, chconf=1, sbr=48000,
                                  sbr_sync=True, ps_sync=True)), count=2, size=50))
        self.assertEqual(out["ext_sample_rate"], 48000)
        self.assertTrue(out["ps"])

    def test_plain_aac_has_no_sbr_rate(self):
        out = scan(stream(smc(asc(rate=32000, chconf=2)), count=2, size=50))
        self.assertIsNone(out["ext_sample_rate"])
        self.assertFalse(out["ps"])

    def test_frame_length_960(self):
        out = scan(stream(smc(asc(rate=48000, chconf=2, frame960=True)), count=4, size=25))
        self.assertEqual(out["frame_length"], 960)
        self.assertEqual(out["duration"], "2/25")

    def test_channel_configuration_71(self):
        out = scan(stream(smc(asc(rate=48000, chconf=7)), count=2, size=20))
        self.assertEqual(out["channels"], 8)

    def test_explicit_sampling_frequency(self):
        out = scan(stream(smc(asc(rate=12345, chconf=2)), count=2, size=20))
        self.assertEqual(out["sample_rate"], 12345)


class AccessUnitLengthTest(unittest.TestCase):
    """PayloadLengthInfo accumulates 255-byte chunks."""

    def check(self, size, count=4):
        out = scan(stream(smc(asc(rate=48000, chconf=2)), count=count, size=size))
        self.assertEqual(out["access_units"], count)
        self.assertEqual(out["payload_bytes"], size * count)
        return out

    def test_under_255(self):
        self.check(254)

    def test_exactly_255(self):
        self.check(255)

    def test_256(self):
        self.check(256)

    def test_510(self):
        self.check(510)

    def test_multi_chunk(self):
        self.check(900, count=2)

    def test_later_frames_stay_aligned(self):
        # A wrong length in an early frame shifts every following frame.
        out = scan(stream(smc(asc(rate=48000, chconf=2)), count=5, size=600))
        self.assertEqual(out["loas_frames"], 5)
        self.assertEqual(out["payload_bytes"], 3000)


class AudioMuxVersionTest(unittest.TestCase):
    """audioMuxVersion 1 (used by ATSC and by DRM encoders)."""

    def test_version_1(self):
        out = scan(stream(smc(asc(rate=48000, chconf=2), version=1), count=4, size=100))
        self.assertEqual(out["mux_version"], 1)
        self.assertEqual(out["sample_rate"], 48000)
        self.assertEqual(out["access_units"], 4)
        self.assertEqual(out["payload_bytes"], 400)

    def test_version_1_with_asc_padding(self):
        # ascLen covers the ASC plus alignment padding bits.
        out = scan(stream(smc(asc(rate=24000, chconf=2, sbr=48000), version=1, asc_pad=9),
                          count=3, size=80))
        self.assertEqual(out["object_type"], 2)
        self.assertEqual(out["ext_sample_rate"], 48000)
        self.assertEqual(out["payload_bytes"], 240)

    def test_version_1_long_payloads(self):
        out = scan(stream(smc(asc(rate=48000, chconf=2), version=1), count=3, size=700))
        self.assertEqual(out["payload_bytes"], 2100)

    def test_version_1_crc(self):
        out = scan(stream(smc(asc(rate=48000, chconf=2), version=1, crc=0x3C),
                          count=3, size=100))
        self.assertEqual(out["payload_bytes"], 300)


class CrcTest(unittest.TestCase):
    """crcCheckPresent adds crcCheckSum after otherData."""

    def test_crc_config(self):
        out = scan(stream(smc(asc(rate=48000, chconf=2), crc=0xA5), count=4, size=120))
        self.assertEqual(out["access_units"], 4)
        self.assertEqual(out["payload_bytes"], 480)

    def test_crc_with_other_data(self):
        out = scan(stream(smc(asc(rate=48000, chconf=2), other=12,
                              other_payload=0x5A5, crc=0x11), count=3, size=90))
        self.assertEqual(out["loas_frames"], 3)
        self.assertEqual(out["payload_bytes"], 270)

    def test_crc_with_repeated_config(self):
        out = scan(stream(smc(asc(rate=48000, chconf=2), crc=0x7E),
                          count=6, size=100, every=2))
        self.assertEqual(out["configs"], 3)
        self.assertEqual(out["loas_frames"], 6)
        self.assertEqual(out["payload_bytes"], 600)


class OtherDataTest(unittest.TestCase):
    def test_length_only(self):
        # Some encoders write otherDataLenBits but no payload bits.
        out = scan(stream(smc(asc(rate=48000, chconf=2), other=8), count=3, size=100))
        self.assertEqual(out["payload_bytes"], 300)

    def test_length_and_payload(self):
        out = scan(stream(smc(asc(rate=48000, chconf=2), other=8, other_payload=0x33),
                          count=3, size=100))
        self.assertEqual(out["payload_bytes"], 300)

    def test_version_1_length_and_payload(self):
        out = scan(stream(smc(asc(rate=48000, chconf=2), version=1, other=8,
                              other_payload=0x33), count=3, size=100))
        self.assertEqual(out["payload_bytes"], 300)


class SubFrameTest(unittest.TestCase):
    def test_multiple_subframes(self):
        out = scan(stream(smc(asc(rate=48000, chconf=2), subframes=3),
                          count=2, size=40, subframes=3))
        self.assertEqual(out["loas_frames"], 2)
        self.assertEqual(out["access_units"], 6)
        self.assertEqual(out["payload_bytes"], 246)

    def test_subframes_with_long_payloads(self):
        # AUs are size and size+1, so each frame carries 601 bytes.
        out = scan(stream(smc(asc(rate=48000, chconf=2), subframes=2),
                          count=2, size=300, subframes=2))
        self.assertEqual(out["access_units"], 4)
        self.assertEqual(out["payload_bytes"], 1202)


class ExampleTest(unittest.TestCase):
    """The checked-in captures."""

    def test_studio_latm(self):
        out = scan((EXAMPLES / "studio.latm").read_bytes())
        self.assertEqual(out["object_type"], 2)
        self.assertEqual(out["sample_rate"], 48000)
        self.assertEqual(out["ext_sample_rate"], None)
        self.assertEqual(out["channels"], 2)
        self.assertEqual(out["loas_frames"], 12)
        self.assertEqual(out["access_units"], 12)
        self.assertEqual(out["configs"], 2)
        self.assertEqual(out["payload_bytes"], 4560)

    def test_broadcast_loas(self):
        out = scan((EXAMPLES / "broadcast.loas").read_bytes())
        self.assertEqual(out["mux_version"], 1)
        self.assertEqual(out["object_type"], 2)
        self.assertEqual(out["sample_rate"], 24000)
        self.assertEqual(out["ext_sample_rate"], 48000)
        self.assertEqual(out["loas_frames"], 10)
        self.assertEqual(out["access_units"], 10)
        self.assertEqual(out["configs"], 2)
        self.assertEqual(out["payload_bytes"], 1800)


if __name__ == "__main__":
    unittest.main()
