import unittest

from midiclock import scoring
from midiclock.smf import MidiError
from midiclock.timing import analyse


def smf(*tracks, division=480, fmt=1):
    head = b"MThd" + (6).to_bytes(4, "big") + fmt.to_bytes(2, "big") + len(tracks).to_bytes(2, "big") + division.to_bytes(2, "big")
    return head + b"".join(b"MTrk" + len(t).to_bytes(4, "big") + t for t in tracks)


def vlq(value):
    groups = [value & 0x7F]
    value >>= 7
    while value:
        groups.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(groups))


def tempo(us_per_quarter, delta=0):
    return vlq(delta) + b"\xFF\x51\x03" + us_per_quarter.to_bytes(3, "big")


def lyrics(text, delta=0):
    data = text.encode("latin-1")
    return vlq(delta) + b"\xFF\x05" + vlq(len(data)) + data

END = b"\x00\xFF\x2F\x00"


class BasicTests(unittest.TestCase):
    def test_one_note(self):
        t = bytes([0x00, 0x90, 60, 100, 0x83, 0x60, 0x80, 60, 0, 0x00, 0xFF, 0x2F, 0x00])
        out = analyse(smf(t))
        self.assertEqual([(n["pitch"], n["start"], n["end"]) for n in out["notes"]], [(60, 0.0, 0.5)])

    def test_score(self):
        notes = [{"start": 0, "end": 1, "pitch": 60}]
        self.assertEqual(scoring.score(notes, [(0.1, 72), (0.5, 61), (0.9, None)]), 0.6667)

    def test_running_status(self):
        # Status byte only on the first note; the rest reuse it.
        t = bytes([0x00, 0x90, 60, 100, 0x78, 62, 100, 0x78, 64, 100,
                   0x78, 60, 0, 0x00, 62, 0, 0x00, 64, 0]) + END
        out = analyse(smf(t))
        self.assertEqual([(n["pitch"], n["start"], n["end"]) for n in out["notes"]],
                         [(60, 0.0, 0.375), (62, 0.125, 0.375), (64, 0.25, 0.375)])

    def test_velocity_zero_is_note_off(self):
        t = bytes([0x00, 0x90, 60, 100, 0x83, 0x60, 0x90, 60, 0]) + END
        out = analyse(smf(t))
        self.assertEqual([(n["pitch"], n["start"], n["end"], n["velocity"]) for n in out["notes"]],
                         [(60, 0.0, 0.5, 100)])

    def test_format1_tempo_on_conductor_track(self):
        conductor = tempo(1000000) + tempo(250000, 480) + END
        melody = bytes([0x00, 0x90, 60, 100, 0x83, 0x60, 0x80, 60, 0, 0x00,
                        0x90, 62, 100, 0x83, 0x60, 0x80, 62, 0]) + END
        out = analyse(smf(conductor, melody))
        # 480 ticks at 1 s/quarter, then 480 ticks at 0.25 s/quarter.
        self.assertEqual([(n["pitch"], n["start"], n["end"]) for n in out["notes"]],
                         [(60, 0.0, 1.0), (62, 1.0, 1.25)])
        self.assertEqual([(e["tick"], e["seconds"]) for e in out["tempo_map"]],
                         [(0, 0.0), (480, 1.0)])

    def test_format0_multi_digit_delta(self):
        # B0 40 is the two-byte VLQ 32 * 128 + 64 = 4160 ticks.
        self.assertEqual(vlq(4160), b"\xA0\x40")
        t = b"\x00\x90\x3C\x64" + b"\x83\x60\x80\x3C\x00" + b"\xA0\x40\x90\x3E\x64" + b"\x83\x60\x80\x3E\x00" + END
        out = analyse(smf(t, fmt=0))
        self.assertEqual([(n["pitch"], n["start"], n["end"]) for n in out["notes"]],
                         [(60, 0.0, 0.5), (62, 4.833333, 5.333333)])

    def test_long_delta_over_two_bytes(self):
        # A four-byte VLQ delta: 0x87 0xC0 0x80 0x00 == 0xF00000 ticks.
        delta = vlq(0xF00000)
        self.assertEqual(delta, b"\x87\xC0\x80\x00")
        t = b"\x00\x90\x3C\x64" + vlq(0xF00000) + b"\x80\x3C\x00" + END
        out = analyse(smf(t, fmt=0))
        self.assertEqual(out["notes"][0]["end"], round(0xF00000 / 480 * 0.5, 6))

    def test_lyrics_follow_tempo(self):
        conductor = tempo(1000000) + tempo(250000, 480) + END
        melody = lyrics("one") + lyrics("two", 480) + lyrics("three", 480) + END
        out = analyse(smf(conductor, melody))
        self.assertEqual([(l["seconds"], l["text"]) for l in out["lyrics"]],
                         [(0.0, "one"), (1.0, "two"), (1.25, "three")])

    def test_lyrics_sorted_by_time(self):
        conductor = tempo(1000000) + END
        first = lyrics("late", 480) + END
        second = lyrics("early") + END
        out = analyse(smf(conductor, first, second))
        self.assertEqual([l["text"] for l in out["lyrics"]], ["early", "late"])

    def test_smpte_time_base(self):
        # -25 fps, 40 ticks per frame => 1000 ticks per second.
        division = ((-25 & 0xFF) << 8) | 40
        t = bytes([0x00, 0x90, 60, 100]) + vlq(2500) + bytes([0x80, 60, 0]) + END
        out = analyse(smf(t, division=division, fmt=0))
        self.assertEqual([(n["pitch"], n["start"], n["end"]) for n in out["notes"]], [(60, 0.0, 2.5)])

    def test_running_status_cancelled_by_meta(self):
        # An unterminated note 60 before the meta stays open; 62 after it
        # must not reuse status 0x90 as if it were data.
        t = bytes([0x00, 0x90, 60, 100, 0x00, 0xFF, 0x05, 0x01, 0x41,
                   0x00, 0x90, 62, 100, 0x83, 0x60, 0x80, 62, 0]) + END
        out = analyse(smf(t, fmt=0))
        self.assertEqual([(n["pitch"], n["start"], n["end"]) for n in out["notes"]],
                         [(60, 0.0, 0.5), (62, 0.0, 0.5)])

    def test_hanging_note_closed_at_track_end(self):
        t = bytes([0x00, 0x90, 60, 100, 0x83, 0x60, 0x90, 62, 100]) + END
        out = analyse(smf(t, fmt=0))
        self.assertEqual([(n["pitch"], n["start"], n["end"]) for n in out["notes"]],
                         [(60, 0.0, 0.5), (62, 0.5, 0.5)])

    def test_conductor_track_wins(self):
        # Track 0 owns the tempo map; tempo events on other tracks are ignored.
        conductor = tempo(1000000) + END
        melody = tempo(250000) + bytes([0x00, 0x90, 60, 100, 0x83, 0x60, 0x80, 60, 0]) + END
        out = analyse(smf(conductor, melody))
        self.assertEqual(out["notes"][0]["end"], 1.0)

    def test_fallback_tempo_when_conductor_has_none(self):
        melody = tempo(1000000) + bytes([0x00, 0x90, 60, 100, 0x83, 0x60, 0x80, 60, 0]) + END
        out = analyse(smf(b"\x00\xFF\x2F\x00", melody))
        self.assertEqual(out["notes"][0]["end"], 1.0)

    def test_format2_tracks_have_own_tempo(self):
        slow = tempo(1000000) + bytes([0x00, 0x90, 60, 100, 0x83, 0x60, 0x80, 60, 0]) + END
        fast = tempo(250000) + bytes([0x00, 0x90, 62, 100, 0x83, 0x60, 0x80, 62, 0]) + END
        out = analyse(smf(slow, fast, fmt=2))
        self.assertEqual([(n["pitch"], n["start"], n["end"]) for n in out["notes"]],
                         [(60, 0.0, 1.0), (62, 0.0, 0.25)])

    def test_smpte_drop_frame(self):
        # -29 (29.97 fps), 80 ticks per frame => 2400000/1001 ticks per second.
        division = ((-29 & 0xFF) << 8) | 80
        t = bytes([0x00, 0x90, 60, 100]) + vlq(240000) + bytes([0x80, 60, 0]) + END
        out = analyse(smf(t, division=division, fmt=0))
        self.assertEqual(out["notes"][0]["end"], 100.1)

    def test_smpte_ignores_tempo(self):
        # Tempo events must not alter time-code based time.
        division = ((-25 & 0xFF) << 8) | 40
        t = tempo(250000) + bytes([0x00, 0x90, 60, 100]) + vlq(500) + bytes([0x80, 60, 0]) + END
        out = analyse(smf(t, division=division, fmt=0))
        self.assertEqual(out["notes"][0]["end"], 0.5)

    def test_note_off_before_note_on_same_tick(self):
        # Zero-length note is still reported, not dropped.
        t = bytes([0x00, 0x90, 60, 100, 0x00, 0x80, 60, 0]) + END
        out = analyse(smf(t, fmt=0))
        self.assertEqual([(n["pitch"], n["start"], n["end"]) for n in out["notes"]], [(60, 0.0, 0.0)])

    def test_repeated_pitch_overlap(self):
        # Two overlapping notes of the same pitch: each note-off closes one.
        t = bytes([0x00, 0x90, 60, 100, 0x78, 0x90, 60, 90,
                   0x78, 0x80, 60, 0, 0x78, 0x80, 60, 0]) + END
        out = analyse(smf(t, fmt=0))
        self.assertEqual([(n["start"], n["end"]) for n in out["notes"]], [(0.0, 0.25), (0.125, 0.375)])


if __name__ == "__main__":
    unittest.main()
