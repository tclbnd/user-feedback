"""Regression tests for MPEG-1/2/2.5 headers, ID3v2 and encoder info frames."""
import unittest
from fractions import Fraction

from mp3kit.mp3 import scan

RATES = {"1": (44100, 48000, 32000), "2": (22050, 24000, 16000), "2.5": (11025, 12000, 8000)}
KBPS = {"1": (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
        "2": (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
        "2.5": (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)}
VBITS = {"1": 3, "2": 2, "2.5": 0}
MODES = {"stereo": 0, "joint_stereo": 1, "dual_channel": 2, "mono": 3}
SIDE = {"1": (32, 17), "2": (17, 9), "2.5": (17, 9)}
SPF = {"1": 1152, "2": 576, "2.5": 576}


def frame_size(version, rate, kbps, pad=0):
    return (144 if version == "1" else 72) * kbps * 1000 // rate + pad


def frame(version="1", rate=44100, kbps=128, mode="joint_stereo", pad=0, seed=0, body=b""):
    b1 = 0xE0 | (VBITS[version] << 3) | (1 << 1) | 1
    b2 = (KBPS[version].index(kbps) << 4) | (RATES[version].index(rate) << 2) | (pad << 1)
    head = bytes([0xFF, b1, b2, MODES[mode] << 6])
    n = frame_size(version, rate, kbps, pad)
    fill = bytes(((i * 7 + seed * 13) & 0x7F) for i in range(n))
    return (head + body + fill[4 + len(body):])[:n]


def info_frame(kind="Xing", frames=10, delay=None, padding=None, toc=True,
               version="1", rate=44100, kbps=128, mode="joint_stereo"):
    """A Xing/Info/VBRI frame; the tag sits over the Layer III side info.

    Everything after the tag is zero, as encoders leave it.
    """
    side = SIDE[version][0 if mode != "mono" else 1]
    if kind == "VBRI":
        tag = b"VBRI" + (1).to_bytes(2, "big") + (delay or 0).to_bytes(2, "big")
        tag += (100).to_bytes(2, "big") + (2000).to_bytes(4, "big")
        tag += frames.to_bytes(4, "big")
        body = bytes(32) + tag
    else:
        flags = 1 | 2 | 8 | (4 if toc else 0)  # frames, bytes, quality, (TOC)
        body = bytes(side) + kind.encode() + flags.to_bytes(4, "big")
        body += frames.to_bytes(4, "big") + (4242).to_bytes(4, "big")
        if toc:
            body += bytes(100)
        body += (77).to_bytes(4, "big")
        if delay is not None:
            body += b"LAME3.100" + bytes(12)
            body += ((delay << 12) | padding).to_bytes(3, "big")
    b1 = 0xE0 | (VBITS[version] << 3) | (1 << 1) | 1
    b2 = (KBPS[version].index(kbps) << 4) | (RATES[version].index(rate) << 2)
    head = bytes([0xFF, b1, b2, MODES[mode] << 6])
    size = frame_size(version, rate, kbps) - 4
    return head + body + bytes(size - len(body))


def audio(n, first=0, pads=(), **kw):
    return b"".join(frame(pad=1 if i in pads else 0, seed=first + i, **kw) for i in range(n))


def id3v2(size=25, version=4):
    header = b"ID3" + bytes([version, 0, 0])
    header += bytes([(size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F])
    return header + bytes(size)


class InfoFrameTests(unittest.TestCase):
    def test_id3v2_is_skipped_not_counted(self):
        tag = id3v2()
        out = scan(tag + audio(7))
        self.assertEqual(out["id3v2_bytes"], len(tag))
        self.assertEqual(out["frames"], 7)

    def test_id3v2_v3_and_multiple_tags(self):
        tag = id3v2(40, version=3) + id3v2(10, version=2)
        out = scan(tag + audio(4))
        self.assertEqual(out["id3v2_bytes"], len(tag))
        self.assertEqual(out["frames"], 4)

    def test_mpeg2_samples_per_frame(self):
        data = info_frame(frames=60, delay=1105, padding=422,
                          version="2", rate=22050, kbps=64, mode="mono")
        out = scan(data + audio(60, version="2", rate=22050, kbps=64, mode="mono"))
        self.assertEqual(out["sample_rate"], 22050)
        self.assertEqual(out["frames"], 60)
        self.assertEqual(out["duration"], str(Fraction(60 * 576, 22050)))
        self.assertEqual(out["gapless_samples"], 60 * 576 - 1105 - 422)

    def test_mpeg2_frame_length_uses_72(self):
        # A wrong (144-based) frame length would derail the scan after frame one.
        data = audio(30, version="2", rate=22050, kbps=64)
        out = scan(data)
        self.assertEqual(out["frames"], 30)
        self.assertEqual(out["audio_bytes"], len(data))

    def test_mpeg25(self):
        data = audio(12, version="2.5", rate=8000, kbps=32,
                     mode="mono", pads=(3, 4))
        out = scan(data)
        self.assertEqual((out["version"], out["sample_rate"], out["frames"]), ("2.5", 8000, 12))
        self.assertEqual(out["duration"], str(Fraction(12 * 576, 8000)))

    def test_padding_bit_keeps_scan_in_sync(self):
        data = audio(40, pads=(0, 1, 2, 5, 8, 13, 21, 34))
        out = scan(data)
        self.assertEqual(out["frames"], 40)
        self.assertEqual(out["audio_bytes"], len(data))

    def test_mono_xing_after_side_info(self):
        info = info_frame(frames=25, mode="mono")
        out = scan(info + audio(25, mode="mono"))
        self.assertEqual(out["vbr_header"], "Xing")
        self.assertEqual(out["bitrate_mode"], "VBR")
        self.assertEqual(out["header_frames"], 25)
        self.assertEqual(out["frames"], 25)

    def test_mpeg25_mono_xing(self):
        info = info_frame(frames=11, version="2.5", rate=11025, kbps=32, mode="mono")
        out = scan(info + audio(11, version="2.5", rate=11025, kbps=32, mode="mono"))
        self.assertEqual(out["vbr_header"], "Xing")
        self.assertEqual(out["frames"], 11)
        self.assertEqual(out["duration"], str(Fraction(11 * 576, 11025)))

    def test_vbr_frames_of_varying_length(self):
        frames = [frame(kbps=kbps, seed=i) for i, kbps in enumerate(
            (128, 64, 192, 32, 320, 96, 160, 128, 256, 48))]
        out = scan(info_frame(frames=len(frames)) + b"".join(frames))
        self.assertEqual(out["frames"], len(frames))
        self.assertEqual(out["audio_bytes"], sum(map(len, frames)))

    def test_info_header_is_cbr(self):
        data = info_frame(kind="Info", frames=8)
        out = scan(data + audio(8))
        self.assertEqual(out["vbr_header"], "Info")
        self.assertEqual(out["bitrate_mode"], "CBR")
        self.assertEqual(out["frames"], 8)

    def test_vbri_header_is_vbr(self):
        data = info_frame(kind="VBRI", frames=9, delay=288)
        out = scan(data + audio(9))
        self.assertEqual(out["vbr_header"], "VBRI")
        self.assertEqual(out["bitrate_mode"], "VBR")
        self.assertEqual(out["header_frames"], 9)
        self.assertEqual(out["frames"], 9)
        self.assertEqual(out["encoder_delay"], 288)

    def test_lame_gapless(self):
        data = info_frame(frames=20, delay=576, padding=1236)
        out = scan(data + audio(20))
        self.assertEqual((out["encoder_delay"], out["encoder_padding"]), (576, 1236))
        self.assertEqual(out["gapless_samples"], 20 * 1152 - 576 - 1236)

    def test_missing_lame_tag_leaves_gapless_empty(self):
        out = scan(info_frame(frames=3) + audio(3))
        self.assertIsNone(out["encoder_delay"])
        self.assertIsNone(out["encoder_padding"])
        self.assertIsNone(out["gapless_samples"])

    def test_trailing_tags_are_not_audio(self):
        tail = b"TAG" + bytes(125)
        out = scan(audio(6) + tail)
        self.assertEqual(out["frames"], 6)
        self.assertEqual(out["audio_bytes"], len(audio(6)))

    def test_cbr_without_info_frame(self):
        out = scan(audio(5, rate=48000))
        self.assertIsNone(out["vbr_header"])
        self.assertIsNone(out["header_frames"])
        self.assertEqual(out["bitrate_mode"], "CBR")
        self.assertEqual(out["duration"], str(Fraction(5 * 1152, 48000)))


if __name__ == "__main__":
    unittest.main()
