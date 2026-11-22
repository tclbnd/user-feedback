import unittest

from mp3kit.mp3 import scan

from frames import frame, stream


class BasicTests(unittest.TestCase):
    def test_cbr_frames(self):
        out = scan(stream(10))
        self.assertEqual((out["frames"], out["sample_rate"], out["bitrate_mode"]), (10, 44100, "CBR"))

    def test_duration(self):
        out = scan(stream(25, rate=48000))
        self.assertEqual(out["duration"], "3/5")

    def test_padding_bit_does_not_drift(self):
        out = scan(stream(40, pads=range(0, 40, 3)))
        self.assertEqual(out["frames"], 40)

    def test_mpeg2_layer3_samples_per_frame(self):
        out = scan(stream(30, version="2", rate=22050, kbps=64, mode="mono"))
        self.assertEqual((out["frames"], out["duration"]), (30, "192/245"))

    def test_mpeg2_5_sample_rates(self):
        out = scan(stream(12, version="2.5", rate=11025, kbps=32, mode="mono"))
        self.assertEqual((out["version"], out["sample_rate"], out["frames"]), ("2.5", 11025, 12))

    def test_id3v2_tag_is_skipped(self):
        tag = b"ID3\x04\x00\x00" + bytes([0, 0, 0, 20]) + bytes(20)
        out = scan(tag + stream(5))
        self.assertEqual((out["id3v2_bytes"], out["frames"], out["audio_bytes"]),
                         (30, 5, sum(len(frame(seed=i)) for i in range(5))))

    def test_mono_xing_at_mono_side_info_offset(self):
        # MPEG-2 mono frames carry 9 bytes of side info before the tag.
        body = bytes(9) + b"Xing" + (1).to_bytes(4, "big") + (7).to_bytes(4, "big")
        first = frame(version="2", rate=22050, kbps=64, mode="mono", body=body)
        out = scan(first + stream(7, version="2", rate=22050, kbps=64, mode="mono"))
        self.assertEqual((out["vbr_header"], out["frames"], out["bitrate_mode"], out["header_frames"]),
                         ("Xing", 7, "VBR", 7))

    def test_info_header_is_cbr_and_not_audio(self):
        body = bytes(32) + b"Info" + (1).to_bytes(4, "big") + (7).to_bytes(4, "big")
        first = frame(body=body)
        out = scan(first + stream(7))
        self.assertEqual((out["vbr_header"], out["bitrate_mode"], out["frames"]),
                         ("Info", "CBR", 7))

    def test_lame_gapless_fields(self):
        # LAME tag: 9-byte version, 12 bytes, then 3 bytes delay/padding.
        lame = b"LAME3.100" + bytes(12) + bytes([0x05, 0x00, 0x0B]) + bytes(50)
        body = bytes(32) + b"Xing" + (1).to_bytes(4, "big") + (5).to_bytes(4, "big") + lame
        first = frame(body=body)
        out = scan(first + stream(5))
        self.assertEqual((out["encoder_delay"], out["encoder_padding"]), (0x50, 0x0B))
        self.assertEqual(out["gapless_samples"], 5 * 1152 - 0x50 - 0x0B)

    def test_crc_shifts_xing_header(self):
        # Protection bit clear adds a 2-byte CRC after the header.
        body = bytes(2) + bytes(32) + b"Xing" + (1).to_bytes(4, "big") + (3).to_bytes(4, "big")
        raw = bytearray(frame(body=body))
        raw[1] &= 0xFE
        out = scan(bytes(raw) + stream(3))
        self.assertEqual((out["crc"], out["vbr_header"], out["frames"]), (True, "Xing", 3))

    def test_vbri_header(self):
        # VBRI: version(2) delay(2) quality(2) bytes(4) frames(4).
        vbri = (b"VBRI" + bytes(2) + (576).to_bytes(2, "big") + bytes(2)
                + bytes(4) + (4).to_bytes(4, "big"))
        first = frame(body=bytes(32) + vbri)
        out = scan(first + stream(4))
        self.assertEqual((out["vbr_header"], out["bitrate_mode"], out["frames"],
                          out["encoder_delay"]),
                         ("VBRI", "VBR", 4, 576))

    def test_id3v1_tag_is_not_audio(self):
        out = scan(stream(6) + b"TAG" + bytes(125))
        self.assertEqual(out["frames"], 6)
