import struct
import unittest

from cmfkit.cmf import parse


def build(events, version=(1, 1), strings=(), instruments=0, tempo=120, tpq=96, tps=96):
    """Build a file: header, optional title/composer/remarks, then music data."""
    major, minor = version
    hsize = 0x28 if (major, minor) >= (1, 1) else 0x25
    strdata = bytearray()
    offsets = []
    for text in strings:
        offsets.append(hsize + len(strdata) if text is not None else 0)
        if text is not None:
            strdata += text.encode("latin-1") + b"\0"
    offsets += [0] * (3 - len(offsets))
    data = bytearray(hsize)
    data[:4] = b"CTMF"
    data[4], data[5] = minor, major
    struct.pack_into("<7H", data, 6, 0, hsize + len(strdata), tpq, tps, *offsets)
    if (major, minor) >= (1, 1):
        struct.pack_into("<HH", data, 0x24, instruments, tempo)
    else:
        data[0x24] = instruments
    return bytes(data) + bytes(strdata) + events


def minimal():
    h = bytearray(0x28)
    h[:4] = b"CTMF"
    h[4], h[5] = 1, 1
    struct.pack_into("<7H", h, 6, 0x28, 0x28, 96, 96, 0, 0, 0)
    struct.pack_into("<HH", h, 0x24, 0, 120)
    return bytes(h) + b"\x00\xff\x2f\x00"


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(minimal())
        self.assertEqual((out["version"], out["ticks_per_quarter"], out["end_of_track"]), ("1.1", 96, True))

    def test_empty(self):
        self.assertEqual(parse(minimal())["notes"], 0)


class HeaderTests(unittest.TestCase):
    def test_strings(self):
        out = parse(build(b"\x00\xff\x2f\x00", strings=("Dune Theme", "S. Chiron", "Arranged")))
        self.assertEqual((out["title"], out["composer"], out["remarks"]), ("Dune Theme", "S. Chiron", "Arranged"))

    def test_missing_strings(self):
        out = parse(minimal())
        self.assertEqual((out["title"], out["composer"], out["remarks"]), (None, None, None))

    def test_version_1_0_instrument_count_single_byte(self):
        out = parse(build(b"\x00\xff\x2f\x00", version=(1, 0), instruments=4))
        self.assertEqual((out["version"], out["instruments"], out["tempo"]), ("1.0", 4, None))

    def test_version_1_1_instrument_count_word(self):
        out = parse(build(b"\x00\xff\x2f\x00", instruments=300, tempo=80))
        self.assertEqual((out["instruments"], out["tempo"]), (300, 80))

    def test_channels_used(self):
        head = bytearray(minimal())
        head[0x14] = 1
        head[0x1B] = 1
        self.assertEqual(parse(bytes(head))["channels_used"], [0, 7])


class EventTests(unittest.TestCase):
    def test_running_status(self):
        events = b"\x00\x90\x3c\x64" b"\x00\x3c\x00" b"\x00\xff\x2f\x00"
        out = parse(build(events))
        self.assertEqual((out["notes"], out["end_of_track"]), (1, True))

    def test_running_status_after_meta_is_not_allowed(self):
        events = b"\x00\x90\x3c\x64" b"\x00\xff\x01\x00" b"\x00\x3c\x00" b"\x00\xff\x2f\x00"
        with self.assertRaises(Exception):
            parse(build(events))

    def test_data_byte_without_running_status(self):
        with self.assertRaises(Exception):
            parse(build(b"\x00\x3c\x64" b"\x00\xff\x2f\x00"))

    def test_multibyte_delta(self):
        events = b"\x83\x74\x90\x3c\x64" b"\x00\xff\x2f\x00"
        self.assertEqual(parse(build(events))["ticks"], 500)

    def test_zero_velocity_counts_as_note_off(self):
        events = b"\x00\x90\x3c\x40" b"\x00\x90\x3c\x00" b"\x00\x90\x3e\x7f" b"\x00\xff\x2f\x00"
        self.assertEqual(parse(build(events))["notes"], 2)

    def test_duration_uses_ticks_per_second(self):
        out = parse(build(b"\x83\x74\x90\x3c\x64" b"\x00\xff\x2f\x00", tpq=96, tps=60))
        self.assertEqual(out["duration"], "25/3")

    def test_system_exclusive_is_skipped(self):
        events = b"\x00\xf0\x03\x01\x02\xf7" b"\x00\x90\x3c\x64" b"\x00\xff\x2f\x00"
        self.assertEqual(parse(build(events))["notes"], 1)

    def test_system_exclusive_without_f7_terminator(self):
        events = b"\x00\xf0\x03\x01\x02\xf7" b"\x00\xf7\x02\x01\x02" b"\x00\x90\x3c\x64" b"\x00\xff\x2f\x00"
        self.assertEqual(parse(build(events))["notes"], 1)

    def test_short_message_lengths(self):
        events = (b"\x00\xc0\x05" b"\x00\xd0\x20"
                  b"\x00\x80\x3c\x00" b"\x00\xa0\x3c\x10" b"\x00\xe0\x00\x40"
                  b"\x00\xff\x2f\x00")
        out = parse(build(events))
        self.assertEqual((out["notes"], out["end_of_track"], out["ticks"]), (0, True, 0))

    def test_meta_event_does_not_end(self):
        events = b"\x00\xff\x01\x02ab" b"\x00\x90\x3c\x64" b"\x00\xff\x2f\x00"
        out = parse(build(events))
        self.assertEqual((out["notes"], out["end_of_track"]), (1, True))

    def test_missing_end_of_track(self):
        self.assertFalse(parse(build(b"\x00\x90\x3c\x64"))["end_of_track"])


class ControllerTests(unittest.TestCase):
    def test_markers(self):
        events = b"\x00\xb0\x66\x01" b"\x00\x66\x03" b"\x00\xff\x2f\x00"
        self.assertEqual(parse(build(events))["markers"], 2)

    def test_rhythm_mode(self):
        events = b"\x00\xb0\x67\x01" b"\x00\xb0\x67\x00" b"\x00\xb0\x67\x01" b"\x00\xff\x2f\x00"
        self.assertEqual(parse(build(events))["rhythm_mode_on"], 2)

    def test_transpose(self):
        events = (b"\x00\xb0\x68\x02" b"\x00\xb0\x68\x00" b"\x00\xb0\x69\x03"
                  b"\x00\xb0\x68\x05" b"\x00\xff\x2f\x00")
        self.assertEqual(parse(build(events))["transpose"], [2, 0, -3, 5])

    def test_other_controllers_ignored(self):
        events = b"\x00\xb0\x07\x7f" b"\x00\xb0\x65\x00" b"\x00\xff\x2f\x00"
        out = parse(build(events))
        self.assertEqual((out["markers"], out["rhythm_mode_on"], out["transpose"]), (0, 0, []))


class ErrorTests(unittest.TestCase):
    def test_not_a_cmf(self):
        with self.assertRaises(Exception):
            parse(b"NOPE" + b"\0" * 0x30)

    def test_truncated_file(self):
        with self.assertRaises(Exception):
            parse(b"CTMF" + b"\0" * 4)

    def test_truncated_note(self):
        with self.assertRaises(Exception):
            parse(build(b"\x00\x90\x3c"))

    def test_string_offset_out_of_range(self):
        head = bytearray(minimal())
        struct.pack_into("<H", head, 0x10, 0xFFFF)
        with self.assertRaises(Exception):
            parse(bytes(head))

