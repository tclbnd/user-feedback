import unittest

from midiclock import scoring
from midiclock.timing import analyse


def smf(*tracks, division=480):
    head = b"MThd" + (6).to_bytes(4, "big") + (1).to_bytes(2, "big") + len(tracks).to_bytes(2, "big") + division.to_bytes(2, "big")
    return head + b"".join(b"MTrk" + len(t).to_bytes(4, "big") + t for t in tracks)


class BasicTests(unittest.TestCase):
    def test_one_note(self):
        t = bytes([0x00, 0x90, 60, 100, 0x83, 0x60, 0x80, 60, 0, 0x00, 0xFF, 0x2F, 0x00])
        out = analyse(smf(t))
        self.assertEqual([(n["pitch"], n["start"], n["end"]) for n in out["notes"]], [(60, 0.0, 0.5)])

    def test_score(self):
        notes = [{"start": 0, "end": 1, "pitch": 60}]
        self.assertEqual(scoring.score(notes, [(0.1, 72), (0.5, 61), (0.9, None)]), 0.6667)


if __name__ == "__main__":
    unittest.main()
