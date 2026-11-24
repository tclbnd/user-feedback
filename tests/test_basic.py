import unittest

from muskit.mus import MusError, parse

from lumps import build, lump


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(lump())
        self.assertEqual((out["primary_channels"], out["instruments"], out["score_end"]), (1, [30], True))

    def test_ticks(self):
        self.assertEqual(parse(lump())["ticks"], 20)

    def test_duration_is_at_140_ticks_per_second(self):
        self.assertEqual(parse(lump())["duration"], "1/7")      # 20 / 140


class EventTests(unittest.TestCase):
    def test_variable_length_delay(self):
        # delay 200 = 0x81 0x48 (7 bits per byte, high bit = continue)
        body = bytes([0x80, 60, 0x81, 0x48, 0x60])
        self.assertEqual(parse(build(body))["ticks"], 200)

    def test_long_delay_does_not_swallow_events(self):
        body = bytes([0x80, 60, 0x81, 0x48, 0x90, 62, 0x85, 0x00, 0x60])
        out = parse(build(body))
        self.assertEqual((out["ticks"], out["notes"]), (200 + 640, 1))

    def test_play_note_with_volume(self):
        # bit 7 of the note byte announces a velocity byte; the next event must stay aligned
        body = bytes([0x10, 0xBC, 100, 0x11, 0xC0, 101, 0x60])   # notes 60, 64 with volumes
        out = parse(build(body))
        self.assertEqual((out["notes"], out["ticks"], out["channels"]), (2, 0, [0, 1]))

    def test_controller_consumes_number_and_value(self):
        # controller 3 (volume) = 100, then a note on the same channel
        body = bytes([0x40, 3, 100, 0x10, 60, 0x60])
        out = parse(build(body))
        self.assertEqual((out["controllers"], out["notes"]), ({"volume": 1}, 1))

    def test_percussion_is_channel_15(self):
        body = bytes([0x1F, 36, 0x60])
        self.assertTrue(parse(build(body))["percussion"])
        body = bytes([0x19, 36, 0x60])
        self.assertFalse(parse(build(body))["percussion"])

    def test_score_end_stops_at_header_length(self):
        body = bytes([0x10, 60, 0x60])
        out = parse(build(body, tail=bytes([0x10, 64, 0x10, 62, 0x60])))
        self.assertEqual(out["notes"], 1)

    def test_truncated_event_raises(self):
        with self.assertRaises(MusError):
            parse(build(bytes([0x40, 3])))

