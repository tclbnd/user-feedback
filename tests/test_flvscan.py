import struct
import unittest

from flvscan import amf
from flvscan.summary import summarise
from flvscan.tags import FlvError, header, iter_tags, tags


def u24(n):
    return n.to_bytes(3, "big")


def s24(n):
    return (n + (1 << 24) if n < 0 else n).to_bytes(3, "big")


def flv_header(data_offset=9, flags=5):
    out = b"FLV" + bytes([1, flags]) + data_offset.to_bytes(4, "big")
    return out + b"\x00" * (data_offset - 9)


def make_tag(kind, body, ts=0):
    ts &= 0xFFFFFFFF
    head = bytes([kind]) + u24(len(body)) + u24(ts & 0xFFFFFF) + bytes([ts >> 24]) + b"\x00\x00\x00"
    return head + body


def video(frame_type, codec, pkt, cts=0, payload=b"\x01\x02", enh=False, ts=0):
    if enh:
        # ExVideoTag: IsEx | ExFrameType (key=1, inter=0) | 0xF marker.
        ex_frame_type = 1 if frame_type == 1 else 0
        b0 = 0x80 | (ex_frame_type << 4) | 0x0F
        fourcc = codec if isinstance(codec, bytes) else b"hvc1"
        body = bytes([b0]) + fourcc + bytes([pkt]) + s24(cts) + payload
    else:
        body = bytes([(frame_type << 4) | codec, pkt]) + s24(cts) + payload
    return make_tag(9, body, ts)


def audio(b0, rest=b"", ts=0):
    return make_tag(8, bytes([b0]) + rest, ts)


def build(parts, data_offset=9):
    out = flv_header(data_offset) + b"\x00\x00\x00\x00"
    for t in parts:
        out += t + len(t).to_bytes(4, "big")
    return out


AVC = 7
HEVC = 12


class HeaderTests(unittest.TestCase):
    def test_standard_header(self):
        info, off = header(build([video(1, AVC, 0, 0)]))
        self.assertEqual(off, 9)
        self.assertTrue(info["has_audio"])
        self.assertTrue(info["has_video"])

    def test_long_header_data_offset(self):
        # A 13-byte header: extra bytes between the fixed header and the
        # first PreviousTagSize0 must not hide the first video tag.
        data = build([video(1, AVC, 0, 0), video(1, AVC, 1, 0)], data_offset=13)
        info, off = header(data)
        self.assertEqual(off, 13)
        _, tag_list = tags(data)
        self.assertEqual(len(tag_list), 2)
        self.assertEqual(summarise(data)["video_frames"], 1)

    def test_not_flv(self):
        with self.assertRaises(FlvError):
            header(b"not an flv file at all here")


class TimestampTests(unittest.TestCase):
    def test_full_32bit_timestamp(self):
        # Timestamps beyond 0xFFFFFF must use the Timestamp Extended byte.
        data = build([
            video(1, AVC, 1, 0, ts=16777215),
            video(2, AVC, 1, 0, ts=16777248),
            video(2, AVC, 1, 0, ts=17000000),
        ])
        _, tag_list = tags(data)
        self.assertEqual([t.timestamp for t in tag_list],
                         [16777215, 16777248, 17000000])
        self.assertEqual(summarise(data)["max_timestamp"], 17000000)

    def test_negative_cts_is_signed_24bit(self):
        # B-frame offset 0xFFFFD8 is -40 signed, not 16777176.
        data = build([
            video(1, AVC, 0, 0),
            video(1, AVC, 1, 0, ts=0),
            video(2, AVC, 1, -40, ts=40),
            video(2, AVC, 1, 40, ts=40),
        ])
        self.assertEqual(summarise(data)["first_pts"], [0, 0, 80])


class CountingTests(unittest.TestCase):
    def test_sequence_headers_and_command_frames_excluded(self):
        data = build([
            video(1, AVC, 0, 0),       # AVC sequence header
            video(1, HEVC, 0, 0),      # HEVC sequence header (different codec)
            video(5, AVC, 1, 0),       # video info / command frame
            video(1, AVC, 1, 0),       # keyframe
            video(2, AVC, 1, 20),      # inter frame
            video(2, AVC, 2, 0),       # end of sequence
        ])
        s = summarise(data)
        self.assertEqual(s["video_frames"], 2)
        self.assertEqual(s["keyframes"], 1)

    def test_aac_sequence_header_not_an_audio_tag(self):
        data = build([
            audio(0xAF, b"\x00"),          # AAC sequence header
            audio(0xAF, b"\x01abc"),       # AAC raw
            audio(0x2A, b"\xff\xfb"),      # MP3 frame
            audio(0xAF, b"\x01def"),       # AAC raw
        ])
        s = summarise(data)
        self.assertEqual(s["audio_tags"], 3)
        self.assertEqual(s["audio_codec"], "aac")

    def test_truncated_final_tag_skipped(self):
        complete = build([
            video(1, AVC, 0, 0),
            video(1, AVC, 1, 0),
            video(2, AVC, 1, 0),
        ])
        # Keep the first two whole tags, plus 14 bytes of the third tag's
        # header/body so its body and trailing PreviousTagSize are missing.
        cut = 9 + 4 + 2 * (11 + 7 + 4) + 14
        data = complete[:cut]
        _, tag_list = tags(data)
        self.assertEqual(len(tag_list), 2)
        s = summarise(data)
        self.assertEqual(s["video_frames"], 1)
        self.assertEqual(s["keyframes"], 1)

    def test_trailing_garbage_ignored(self):
        data = build([video(1, AVC, 1, 0)]) + b"\x09\x00\x00"
        self.assertEqual(summarise(data)["video_frames"], 1)


class EnhancedRtmpTests(unittest.TestCase):
    def test_enhanced_hevc(self):
        data = build([
            video(1, b"hvc1", 0, 0, enh=True),     # sequence start
            video(1, b"hvc1", 1, 0, enh=True),     # keyframe
            video(2, b"hvc1", 1, -33, enh=True, ts=33),  # inter, negative CTS
            video(2, b"hvc1", 3, 100, enh=True, ts=0),   # coded frames X
        ])
        s = summarise(data)
        self.assertEqual(s["video_codec"], "hevc")
        self.assertEqual(s["video_frames"], 3)
        self.assertEqual(s["keyframes"], 1)
        self.assertEqual(s["first_pts"], [0, 0, 100])

    def test_enhanced_wire_markers(self):
        # The exact marker bytes produced by Enhanced RTMP encoders.
        data = build([
            make_tag(9, b"\x9Fhvc1\x00\x00\x00\x00cfg"),        # key, seq start
            make_tag(9, b"\x9Fhvc1\x01\x00\x00\x00nal"),        # key, coded
            make_tag(9, b"\x8Fhvc1\x01\x00\x00\x21nal"),        # inter, cts +33
        ])
        s = summarise(data)
        self.assertEqual(s["video_codec"], "hevc")
        self.assertEqual(s["video_frames"], 2)
        self.assertEqual(s["keyframes"], 1)
        self.assertEqual(s["first_pts"], [0, 33])

    def test_enhanced_audio_aac(self):
        data = build([
            audio(0x90, b"mp4a" + b"\x00cfg"),     # sequence start
            audio(0x90, b"mp4a" + b"\x01frame"),   # coded frame
        ])
        s = summarise(data)
        self.assertEqual(s["audio_codec"], "aac")
        self.assertEqual(s["audio_tags"], 1)


class PreviousTagSizeTests(unittest.TestCase):
    def test_previous_tag_size_chain(self):
        data = build([
            video(1, AVC, 0, 0),
            video(2, AVC, 1, 0),
        ])
        parsed = list(iter_tags(data))
        sizes = [p.previous_size for p in parsed]
        body_lens = [len(p.tag.body) for p in parsed]
        self.assertEqual(sizes, [0, 11 + body_lens[0]])


class AmfTests(unittest.TestCase):
    def test_long_string_and_strict_array_metadata(self):
        body = (
            b"\x02\x00\x0aonMetaData"
            b"\x08\x00\x00\x00\x02"                       # ECMA array, 2 entries
            b"\x00\x07encoder\x0c\x00\x00\x00\x09obs 30.1 "
            b"\x00\x05times\x0a\x00\x00\x00\x02"          # strict array
            + b"\x00" + struct.pack(">d", 0.0)
            + b"\x00" + struct.pack(">d", 2.0)
            + b"\x00\x00\x09"                             # empty key + object end
        )
        self.assertEqual(body[-3:], b"\x00\x00\x09")
        name, p = amf.value(body, 0)
        self.assertEqual(name, "onMetaData")
        meta, _ = amf.value(body, p)
        self.assertEqual(meta, {"encoder": "obs 30.1 ", "times": [0.0, 2.0]})

    def test_common_scalar_types(self):
        # null, undefined, boolean, date, plain object
        body = (
            b"\x03"
            b"\x00\x01n\x05"
            b"\x00\x01u\x06"
            b"\x00\x01b\x01\x01"
            b"\x00\x01d\x0b" + struct.pack(">d", 1000.0) + b"\x00\x00"
            b"\x00\x00\x09"
        )
        val, _ = amf.value(body, 0)
        self.assertEqual(val, {"n": None, "u": None, "b": True, "d": 1000.0})

    def test_truncated_amf_raises(self):
        with self.assertRaises(amf.AmfError):
            amf.value(b"\x00\x01\x02", 0)


if __name__ == "__main__":
    unittest.main()
