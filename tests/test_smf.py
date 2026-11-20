"""Regression tests for SMF parsing and tick-to-seconds conversion."""
import unittest

from midiclock.smf import Event, MidiError, track_events, tracks
from midiclock.timing import analyse


def vlq(value):
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(out))


def meta(kind, payload=b""):
    return b"\xff" + bytes([kind]) + vlq(len(payload)) + payload


def trk(payload):
    return (b"MTrk", payload)


def end():
    return b"\x00\xff\x2f\x00"


def smf(*chunks, fmt=1, division=480):
    track_count = len([c for c in chunks if c[0] == b"MTrk"])
    head = (b"MThd" + (6).to_bytes(4, "big") + bytes([0, fmt])
            + track_count.to_bytes(2, "big") + division.to_bytes(2, "big"))
    body = b"".join(cid + len(payload).to_bytes(4, "big") + payload for cid, payload in chunks)
    return head + body


class RunningStatusTests(unittest.TestCase):
    def test_running_status_notes(self):
        # One 0x90, then note-off and a second note-on under running status.
        body = bytes([0x00, 0x90, 60, 100, 0x83, 0x60, 60, 0,
                      0x00, 64, 90, 0x83, 0x60, 64, 0])
        events = track_events(body)
        self.assertEqual([(e.tick, e.kind, bytes(e.data)) for e in events],
                         [(0, ("channel", 9, 0), b"\x3c\x64"),
                          (480, ("channel", 9, 0), b"\x3c\x00"),
                          (480, ("channel", 9, 0), b"\x40\x5a"),
                          (960, ("channel", 9, 0), b"\x40\x00")])

    def test_running_status_without_previous_status(self):
        with self.assertRaises(MidiError):
            track_events(bytes([0x00, 60, 100]))

    def test_two_byte_message_running_status(self):
        # Program change (0xC0) uses one data byte.
        body = bytes([0x00, 0xC0, 5, 0x00, 6]) + end()
        self.assertEqual([e.data for e in track_events(body)], [b"\x05", b"\x06", b""])


class MetaAndSysexTests(unittest.TestCase):
    def test_running_status_survives_meta_event(self):
        # Karaoke tracks interleave lyric meta events with running-status
        # note events.  Meta events do not set a new running status, so the
        # channel status from before the lyric is still in force.
        body = (bytes([0x00, 0x90, 60, 100, 0x00]) + meta(0x05, b"la")
                + bytes([0x83, 0x60, 60, 0]) + end())
        out = analyse(smf(trk(body)))
        self.assertEqual([(n["pitch"], n["start"], n["end"]) for n in out["notes"]],
                         [(60, 0.0, 0.5)])
        self.assertEqual(out["lyrics"], [{"seconds": 0.0, "text": "la"}])

    def test_long_meta_payload(self):
        text = "x" * 300
        body = (bytes([0x00, 0x90, 60, 100, 0x00]) + meta(0x05, text.encode())
                + bytes([0x60, 0x80, 60, 0]) + end())
        out = analyse(smf(trk(body)))
        self.assertEqual(out["lyrics"], [{"seconds": 0.0, "text": text}])

    def test_f0_and_f7_sysex(self):
        body = bytes([0x00, 0xF0, 0x03, 0x7E, 0x7F, 0xF7,
                      0x00, 0xF7, 0x02, 0x01, 0x02,
                      0x00, 0x90, 60, 100, 0x60, 0x80, 60, 0]) + end()
        events = track_events(body)
        self.assertEqual([e.kind for e in events],
                         [("sysex",), ("sysex",), ("channel", 9, 0),
                          ("channel", 8, 0), ("meta", 47)])
        self.assertEqual([n["pitch"] for n in analyse(smf(trk(body)))["notes"]], [60])

    def test_sysex_keeps_running_status(self):
        # System events never become running status themselves.  For
        # tolerance with real-world karaoke files the previous channel
        # status stays in force, so a following data byte still parses.
        body = bytes([0x00, 0x90, 60, 100, 0x00, 0xF0, 0x01, 0x7F,
                      0x00, 60, 0]) + end()
        self.assertEqual([e.kind for e in track_events(body)],
                         [("channel", 9, 0), ("sysex",), ("channel", 9, 0),
                          ("meta", 47)])

    def test_end_of_track_stops_parsing(self):
        body = end() + b"\x00\x90\x3c\x64"
        self.assertEqual(track_events(body), [Event(0, ("meta", 47), b"")])

    def test_lyrics_are_utf8_when_possible(self):
        body = (bytes([0x00]) + meta(0x05, "月亮".encode("utf-8"))
                + bytes([0x00]) + meta(0x05, b"caf\xe9") + end())
        self.assertEqual([l["text"] for l in analyse(smf(trk(body)))["lyrics"]],
                         ["月亮", "café"])

    def test_truncated_meta_payload(self):
        with self.assertRaises(MidiError):
            track_events(bytes([0x00, 0xFF, 0x01, 0x10, 0x41]))

    def test_truncated_sysex_payload(self):
        with self.assertRaises(MidiError):
            track_events(bytes([0x00, 0xF0, 0x10, 0x41]))


class NotePairingTests(unittest.TestCase):
    def test_velocity_zero_note_on_is_note_off(self):
        body = bytes([0x00, 0x90, 60, 100, 0x83, 0x60, 60, 0]) + end()
        notes = analyse(smf(trk(body)))["notes"]
        self.assertEqual([(n["pitch"], n["start"], n["end"]) for n in notes],
                         [(60, 0.0, 0.5)])

    def test_note_off(self):
        body = bytes([0x00, 0x90, 60, 100, 0x83, 0x60, 0x80, 60, 64]) + end()
        notes = analyse(smf(trk(body)))["notes"]
        self.assertEqual([(n["velocity"], n["end"]) for n in notes], [(100, 0.5)])

    def test_overlapping_same_pitch_is_first_in_first_out(self):
        body = bytes([0x00, 0x90, 60, 10, 0x60, 0x90, 60, 20,
                      0x60, 0x80, 60, 0, 0x60, 0x80, 60, 0]) + end()
        notes = analyse(smf(trk(body)))["notes"]
        self.assertEqual([(n["velocity"], n["start"], n["end"]) for n in notes],
                         [(10, 0.0, 0.2), (20, 0.1, 0.3)])

    def test_note_still_open_ends_with_track(self):
        body = bytes([0x00, 0x90, 60, 100, 0x83, 0x60]) + meta(0x01, b"x") + end()
        notes = analyse(smf(trk(body)))["notes"]
        self.assertEqual([(n["start"], n["end"]) for n in notes], [(0.0, 0.5)])

    def test_note_off_without_note_on_is_ignored(self):
        body = bytes([0x00, 0x80, 60, 0, 0x00, 0x90, 61, 10, 0x60, 0x80, 61, 0]) + end()
        self.assertEqual([n["pitch"] for n in analyse(smf(trk(body)))["notes"]], [61])

    def test_pitches_and_channels_are_kept_apart(self):
        body = bytes([0x00, 0x90, 60, 10, 0x00, 0x91, 60, 20,
                      0x60, 0x80, 60, 0, 0x60, 0x81, 60, 0]) + end()
        notes = analyse(smf(trk(body)))["notes"]
        self.assertEqual([(n["channel"], n["end"]) for n in notes], [(0, 0.1), (1, 0.2)])


class TempoTests(unittest.TestCase):
    def test_format1_tempo_track_applies_to_all_tracks(self):
        conductor = bytes([0x00]) + meta(0x51, (1000000).to_bytes(3, "big")) + end()
        melody = bytes([0x00, 0x90, 60, 100, 0x83, 0x60, 0x80, 60, 0]) + end()
        out = analyse(smf(trk(conductor), trk(melody), fmt=1))
        self.assertEqual([(n["start"], n["end"]) for n in out["notes"]], [(0.0, 1.0)])

    def test_tempo_change_affects_only_later_ticks(self):
        body = (bytes([0x00]) + meta(0x51, (1000000).to_bytes(3, "big"))
                + bytes([0x83, 0x60]) + meta(0x51, (250000).to_bytes(3, "big"))
                + bytes([0x00, 0x90, 60, 100, 0x83, 0x60, 0x80, 60, 0]) + end())
        out = analyse(smf(trk(body)))
        self.assertEqual([(t["tick"], t["us_per_quarter"], t["seconds"])
                          for t in out["tempo_map"]],
                         [(0, 1000000, 0.0), (480, 250000, 1.0)])
        self.assertEqual([(n["start"], n["end"]) for n in out["notes"]], [(1.0, 1.25)])

    def test_lyrics_follow_tempo_changes(self):
        body = (bytes([0x00]) + meta(0x51, (1000000).to_bytes(3, "big"))
                + bytes([0x83, 0x60]) + meta(0x51, (250000).to_bytes(3, "big"))
                + bytes([0x83, 0x60]) + meta(0x05, b"la") + end())
        self.assertEqual(analyse(smf(trk(body)))["lyrics"],
                         [{"seconds": 1.25, "text": "la"}])

    def test_no_tempo_event_defaults_to_120_bpm(self):
        body = bytes([0x00, 0x90, 60, 100, 0x83, 0x60, 0x80, 60, 0]) + end()
        out = analyse(smf(trk(body)))
        self.assertEqual(out["tempo_map"],
                         [{"tick": 0, "us_per_quarter": 500000, "seconds": 0.0}])
        self.assertEqual([(n["start"], n["end"]) for n in out["notes"]], [(0.0, 0.5)])

    def test_format0(self):
        body = (bytes([0x00]) + meta(0x51, (600000).to_bytes(3, "big"))
                + bytes([0x00, 0x90, 60, 100, 0x87, 0x40, 0x80, 60, 0]) + end())
        out = analyse(smf(trk(body), fmt=0))
        self.assertEqual(out["format"], 0)
        self.assertEqual([(n["start"], n["end"]) for n in out["notes"]], [(0.0, 1.2)])

    def test_format2_tracks_have_independent_tempo_maps(self):
        slow = (bytes([0x00]) + meta(0x51, (1000000).to_bytes(3, "big"))
                + bytes([0x00, 0x90, 60, 100, 0x83, 0x60, 0x80, 60, 0]) + end())
        fast = (bytes([0x00]) + meta(0x51, (250000).to_bytes(3, "big"))
                + bytes([0x00, 0x90, 62, 100, 0x83, 0x60, 0x80, 62, 0]) + end())
        out = analyse(smf(trk(slow), trk(fast), fmt=2))
        self.assertEqual([(n["track"], n["start"], n["end"]) for n in out["notes"]],
                         [(0, 0.0, 1.0), (1, 0.0, 0.25)])
        self.assertEqual([(t["track"], t["us_per_quarter"]) for t in out["tempo_map"]],
                         [(0, 1000000), (1, 250000)])

    def test_tempo_event_not_at_start(self):
        body = (bytes([0x00, 0x90, 60, 100, 0x83, 0x60])
                + meta(0x51, (1000000).to_bytes(3, "big"))
                + bytes([0x83, 0x60, 0x80, 60, 0]) + end())
        out = analyse(smf(trk(body)))
        self.assertEqual([(t["tick"], t["seconds"]) for t in out["tempo_map"]],
                         [(0, 0.0), (480, 0.5)])
        self.assertEqual([(n["start"], n["end"]) for n in out["notes"]], [(0.0, 1.5)])

    def test_zero_tempo_is_ignored(self):
        body = (bytes([0x00]) + meta(0x51, b"\x00\x00\x00")
                + bytes([0x00, 0x90, 60, 100, 0x83, 0x60, 0x80, 60, 0]) + end())
        self.assertEqual([(n["start"], n["end"]) for n in
                          analyse(smf(trk(body)))["notes"]], [(0.0, 0.5)])


class TimebaseTests(unittest.TestCase):
    def test_smpte_25fps(self):
        division = 0xE700 | 40  # -25 fps, 40 ticks per frame => 1000 ticks/s
        body = bytes([0x00, 0x90, 60, 100, 0x87, 0x68, 0x80, 60, 0]) + end()
        out = analyse(smf(trk(body), fmt=0, division=division))
        self.assertEqual(out["division"], division)
        self.assertEqual([(n["start"], n["end"]) for n in out["notes"]], [(0.0, 1.0)])

    def test_smpte_ignores_tempo_events(self):
        division = 0xE700 | 40
        body = (bytes([0x00]) + meta(0x51, (1000000).to_bytes(3, "big"))
                + bytes([0x00, 0x90, 60, 100, 0x87, 0x68, 0x80, 60, 0]) + end())
        out = analyse(smf(trk(body), fmt=0, division=division))
        self.assertEqual([(n["start"], n["end"]) for n in out["notes"]], [(0.0, 1.0)])

    def test_smpte_2997_drop_frame(self):
        division = 0xE300 | 80
        body = bytes([0x00, 0x90, 60, 100, 0xFF, 0x7F, 0x80, 60, 0]) + end()
        out = analyse(smf(trk(body), fmt=0, division=division))
        self.assertEqual(out["notes"][0]["end"], round(16383 / (30000 / 1001 * 80), 6))

    def test_smpte_30fps(self):
        division = 0xE200 | 100  # 3000 ticks/s
        body = bytes([0x00, 0x90, 60, 100, 0x97, 0x38, 0x80, 60, 0]) + end()
        out = analyse(smf(trk(body), fmt=0, division=division))
        self.assertEqual([(n["start"], n["end"]) for n in out["notes"]], [(0.0, 1.0)])

    def test_unsupported_smpte_rate(self):
        with self.assertRaises(MidiError):
            analyse(smf(trk(end()), fmt=0, division=0xE100 | 40))

    def test_zero_division(self):
        with self.assertRaises(MidiError):
            analyse(smf(trk(end()), fmt=0, division=0))


class DeltaTests(unittest.TestCase):
    def test_three_and_four_byte_delta(self):
        delta = 0x0FFFFFFF
        body = (vlq(delta) + bytes([0x90, 60, 100])
                + vlq(delta) + bytes([0x80, 60, 0]) + end())
        self.assertEqual([e.tick for e in track_events(body)], [delta, 2 * delta, 2 * delta])
        out = analyse(smf(trk(body)))
        self.assertEqual(out["notes"][0]["start"], round(delta / 960, 6))

    def test_vlq_round_trip(self):
        from midiclock.smf import vlq as read_vlq
        for value in (0, 0x40, 0x7F, 0x80, 0x2000, 0x3FFF, 0x4000, 0x100000, 0x0FFFFFFF):
            encoded = vlq(value)
            self.assertEqual(read_vlq(encoded, 0), (value, len(encoded)), value)

    def test_vlq_longer_than_four_bytes(self):
        with self.assertRaises(MidiError):
            track_events(bytes([0xFF, 0xFF, 0xFF, 0xFF, 0x7F, 0x90, 60, 100]))

    def test_truncated_vlq(self):
        with self.assertRaises(MidiError):
            track_events(bytes([0x81]))


class ChunkTests(unittest.TestCase):
    def test_unknown_chunk_is_skipped(self):
        data = smf(trk(end()),
                   (b"Xtra", b"\x00" * 7),
                   trk(bytes([0x00, 0x90, 60, 100, 0x60, 0x80, 60, 0]) + end()))
        fmt, division, trks = tracks(data)
        self.assertEqual((fmt, division, len(trks)), (1, 480, 2))
        self.assertEqual([n["pitch"] for n in analyse(data)["notes"]], [60])

    def test_tracks_beyond_declared_count(self):
        data = bytearray(smf(trk(end()),
                             trk(bytes([0x00, 0x90, 60, 100,
                                        0x60, 0x80, 60, 0]) + end())))
        data[10:12] = (1).to_bytes(2, "big")
        self.assertEqual(len(tracks(bytes(data))[2]), 2)

    def test_truncated_chunk(self):
        with self.assertRaises(MidiError):
            tracks(smf(trk(end()))[:-1])

    def test_trailing_padding_after_last_chunk_is_ignored(self):
        data = smf(trk(bytes([0x00, 0x90, 60, 100, 0x60, 0x80, 60, 0]) + end()))
        self.assertEqual([n["pitch"] for n in analyse(data + b"\x00\x00\x00")["notes"]], [60])

    def test_truncated_header(self):
        with self.assertRaises(MidiError):
            tracks(b"MThd" + (6).to_bytes(4, "big") + b"\x00\x00")

    def test_not_a_midi_file(self):
        with self.assertRaises(MidiError):
            tracks(b"RIFF" + b"\x00" * 20)

    def test_rmid_wrapper_is_unwrapped(self):
        inner = smf(trk(bytes([0x00, 0x90, 60, 100, 0x60, 0x80, 60, 0]) + end()))
        riff = (b"RIFF" + (4 + 8 + len(inner)).to_bytes(4, "big") + b"RMID"
                + b"data" + len(inner).to_bytes(4, "big") + inner)
        self.assertEqual([n["pitch"] for n in analyse(riff)["notes"]], [60])

    def test_unsupported_format(self):
        data = smf(trk(end()), fmt=0)
        data = bytearray(data)
        data[9] = 3
        with self.assertRaises(MidiError):
            tracks(bytes(data))

    def test_status_byte_in_channel_data(self):
        with self.assertRaises(MidiError):
            track_events(bytes([0x00, 0x90, 60, 0x90]) + end())

    def test_undefined_status_byte(self):
        with self.assertRaises(MidiError):
            track_events(bytes([0x00, 0xF1, 0x00]) + end())


if __name__ == "__main__":
    unittest.main()
