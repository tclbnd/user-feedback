import json
import os
import unittest

from amrfile import mailbox
from amrfile.rtp import depacketize
from amrfile.summary import summarise

EXAMPLES = os.path.join(os.path.dirname(__file__), os.pardir, "examples")


def example(name):
    with open(os.path.join(EXAMPLES, name), "rb") as f:
        return f.read()


class BasicTests(unittest.TestCase):
    def test_nb_122(self):
        frame = bytes([0x3C]) + bytes(31)
        out = summarise(b"#!AMR\n" + frame * 50)
        self.assertEqual((out["frames"], out["duration_ms"], out["modes"]), (50, 1000, {"12.2": 50}))

    def test_quota(self):
        self.assertEqual(mailbox.usage([{"duration_ms": 1000000}, {"duration_ms": 900000}])["over_quota"], True)


def frame_bytes(ft, size, quality=True):
    return bytes([(ft << 3) | (int(quality) << 2)]) + bytes(size - 1)


class FrameSizeTests(unittest.TestCase):
    def test_nb_sizes_match_spec(self):
        # TS 26.101: 95, 103, 118, 134, 148, 159, 204 and 244 bits, plus a
        # 39-bit SID frame, each behind a one-octet header.
        sizes = [13, 14, 16, 18, 20, 21, 27, 32, 6]
        frame = b"".join(frame_bytes(ft, size) for ft, size in enumerate(sizes))
        out = summarise(b"#!AMR\n" + frame)
        self.assertEqual(out["frames"], 9)
        self.assertEqual(out["modes"], {"4.75": 1, "5.15": 1, "5.9": 1, "6.7": 1,
                                        "7.4": 1, "7.95": 1, "10.2": 1, "12.2": 1})

    def test_nb_795_does_not_shift_later_frames(self):
        # 7.95 kbit/s frames are 159 bits: 21 octets with the header, not 20.
        data = b"#!AMR\n" + frame_bytes(5, 21) * 4 + frame_bytes(7, 32)
        out = summarise(data)
        self.assertEqual((out["frames"], out["modes"]), (5, {"7.95": 4, "12.2": 1}))

    def test_wb_sizes_match_spec(self):
        # TS 26.201: 132, 177, 253, 285, 317, 365, 397, 461, 477 and 40 bits.
        sizes = [18, 24, 33, 37, 41, 47, 51, 59, 61, 6]
        frame = b"".join(frame_bytes(ft, size) for ft, size in enumerate(sizes))
        out = summarise(b"#!AMR-WB\n" + frame)
        self.assertEqual(out["codec"], "amr-wb")
        self.assertEqual(out["sample_rate"], 16000)
        self.assertEqual(out["frames"], 10)
        self.assertEqual(out["modes"], {"6.6": 1, "8.85": 1, "12.65": 1, "14.25": 1,
                                        "15.85": 1, "18.25": 1, "19.85": 1,
                                        "23.05": 1, "23.85": 1})

    def test_wb_modes_from_file(self):
        out = summarise(example("msg0002.awb"))
        self.assertEqual((out["codec"], out["frames"], out["duration_ms"]),
                         ("amr-wb", 319, 6380))
        self.assertEqual(out["modes"], {"12.65": 200, "23.85": 101})


class FrameKindTests(unittest.TestCase):
    def test_sid_is_not_speech(self):
        data = b"#!AMR\n" + frame_bytes(7, 32) + frame_bytes(8, 6)
        out = summarise(data)
        self.assertEqual((out["speech_frames"], out["sid_frames"], out["modes"]),
                         (1, 1, {"12.2": 1}))

    def test_wb_sid_is_not_speech(self):
        data = b"#!AMR-WB\n" + frame_bytes(9, 6)
        out = summarise(data)
        self.assertEqual((out["speech_frames"], out["sid_frames"], out["modes"]), (0, 1, {}))

    def test_no_data_frame_is_not_an_error(self):
        data = b"#!AMR\n" + frame_bytes(7, 32) + bytes([0x7C]) * 3
        out = summarise(data)
        self.assertEqual((out["frames"], out["speech_frames"], out["no_data_frames"],
                          out["duration_ms"]), (4, 1, 3, 80))

    def test_wb_no_data_frame(self):
        out = summarise(b"#!AMR-WB\n" + bytes([0x7C]) * 2)
        self.assertEqual((out["frames"], out["no_data_frames"]), (2, 2))

    def test_damaged_frames_are_counted(self):
        # A 7.95 kbit/s frame flagged Q=0, and AMR-WB speech lost (type 14).
        out = summarise(b"#!AMR\n" + frame_bytes(5, 21, quality=False))
        self.assertEqual((out["bad_frames"], out["speech_frames"], out["no_data_frames"]),
                         (1, 0, 0))
        out = summarise(b"#!AMR-WB\n" + bytes([0x70]) + frame_bytes(2, 33))
        self.assertEqual((out["bad_frames"], out["speech_frames"]), (1, 1))

    def test_damaged_frames_from_file(self):
        self.assertEqual(summarise(example("msg0001.amr"))["bad_frames"], 2)
        self.assertEqual(summarise(example("msg0002.awb"))["bad_frames"], 2)

    def test_trailing_partial_frame_is_not_counted(self):
        frame = bytes([0x3C]) + bytes(31)
        out = summarise(b"#!AMR\n" + frame * 2 + frame[:10])
        self.assertEqual((out["frames"], out["duration_ms"]), (2, 40))


def pack_bits(text):
    text += "0" * (-len(text) % 8)
    return bytes(int(text[i:i + 8], 2) for i in range(0, len(text), 8))


def wb_frame_bits(ft):
    from amrfile.frames import frame_bits
    return frame_bits(ft, "amr-wb")


class PayloadFormatTests(unittest.TestCase):
    def leg(self, name):
        return json.loads(example(name))

    def convert(self, name):
        leg = self.leg(name)
        return depacketize([bytes.fromhex(p) for p in leg["payloads"]],
                           leg["codec"], leg["mode"])

    def test_octet_aligned_and_bandwidth_efficient_agree(self):
        self.assertEqual(self.convert("gateway_leg_oa.json"),
                         self.convert("gateway_leg_be.json"))

    def test_payloads_convert_to_wellformed_storage_file(self):
        out = summarise(self.convert("gateway_leg_oa.json"))
        self.assertEqual((out["codec"], out["frames"], out["duration_ms"]),
                         ("amr-nb", 30, 600))
        self.assertEqual(out["modes"], {"4.75": 3, "5.9": 3, "7.4": 3, "12.2": 9})

    def test_wb_bandwidth_efficient(self):
        # One 23.85 kbit/s AMR-WB frame: CMR, then F=0, FT=8, Q=1, then the
        # 477 speech bits with no octet alignment.
        nbits = wb_frame_bits(8)
        speech = sum(1 << (nbits - 1 - i) for i in range(nbits) if i % 2)
        payload = pack_bits("1111" + "0" + "{:04b}".format(8) + "1" +
                            "{:0{}b}".format(speech, nbits))
        out = depacketize([payload], "amr-wb", "bandwidth-efficient")
        self.assertEqual(out, b"#!AMR-WB\n" + bytes([0x44]) +
                         (speech << (-nbits % 8)).to_bytes((nbits + 7) // 8, "big"))

    def test_octet_aligned_ignores_toc_padding(self):
        # A 7.4 kbit/s frame (FT=4) is 148 bits, so 19 octets of speech after
        # the header; its last nibble is padded out to the octet boundary.
        speech = (1 << 148) - 1
        payload = bytes([0xF0, 0x24]) + (speech << 4).to_bytes(19, "big")
        out = depacketize([payload], "amr-nb", "octet-aligned")
        self.assertEqual(out, b"#!AMR\n" + bytes([0x24]) +
                         (speech << 4).to_bytes(19, "big"))

    def test_cmr_only_payload_is_ignored(self):
        payload = bytes([0xF0]) + frame_bytes(7, 32)
        self.assertEqual(depacketize([b"\xF0", payload]), depacketize([payload]))


if __name__ == "__main__":
    unittest.main()
