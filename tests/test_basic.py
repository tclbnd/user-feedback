import unittest

from muskit.mus import MusError, parse

from lumps import full_lump, lump, vlq, wrap


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(lump())
        self.assertEqual((out["primary_channels"], out["instruments"], out["score_end"]), (1, [30], True))

    def test_ticks(self):
        self.assertEqual(parse(lump())["ticks"], 20)


class FullStreamTests(unittest.TestCase):
    def setUp(self):
        self.out = parse(full_lump())

    def test_controllers_named_and_unknown(self):
        self.assertEqual(self.out["controllers"],
                         {"program": 1, "volume": 1, "controller_20": 1})

    def test_system_events_named_and_unknown(self):
        self.assertEqual(self.out["system_events"], {"sounds_off": 1, "system_15": 1})

    def test_pitch_bends_and_measures(self):
        self.assertEqual(self.out["pitch_bends"], 1)
        self.assertEqual(self.out["measures"], 1)

    def test_notes_and_percussion_channel_15(self):
        self.assertEqual(self.out["notes"], 2)
        self.assertEqual(self.out["channels"], [0, 15])
        self.assertTrue(self.out["percussion"])

    def test_variable_length_delays_and_140hz_timing(self):
        # delays: 128 + 272 + 5 + 7 = 412 MUS ticks at 140 ticks per second
        self.assertEqual(self.out["ticks"], 412)
        self.assertEqual(self.out["duration"], "103/35")

    def test_score_end(self):
        self.assertTrue(self.out["score_end"])

    def test_only_header_score_bytes_are_music(self):
        data = full_lump()
        body = data[18:]
        body_len = len(body)
        # a stray note-on event and zero padding after the declared score must be ignored
        padded = wrap(body + b"\x90\x3c\x60" + b"\x00" * 4, score_length=body_len)
        self.assertEqual(parse(padded), self.out)


class ErrorTests(unittest.TestCase):
    def test_truncated_delay(self):
        # delay flag set but no bytes left, and a multi-byte delay cut off mid stream
        with self.assertRaises(MusError):
            parse(wrap(b"\x80\x3c"))
        with self.assertRaises(MusError):
            parse(wrap(b"\x80\x3c\x80"))

    def test_truncated_note_volume(self):
        # note byte asks for a volume byte but none follows
        with self.assertRaises(MusError):
            parse(wrap(b"\x90\xc0"))

    def test_truncated_controller_event(self):
        with self.assertRaises(MusError):
            parse(wrap(b"\x30\x00"))

    def test_unknown_event_type(self):
        with self.assertRaises(MusError):
            parse(wrap(b"\x70"))

    def test_vlq_encoding_roundtrip(self):
        self.assertEqual(vlq(127), b"\x7f")
        self.assertEqual(vlq(128), b"\x81\x00")
        self.assertEqual(vlq(272), b"\x82\x10")
        self.assertEqual(vlq(16384), b"\x81\x80\x00")
