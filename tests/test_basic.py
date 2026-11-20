import unittest

from vttkit import qc, srt
from vttkit.parser import parse


class BasicTests(unittest.TestCase):
    def test_simple(self):
        cues = parse("WEBVTT\n\n00:00:01.000 --> 00:00:03.500\nHello there\n")
        self.assertEqual((cues[0]["start"], cues[0]["end"], cues[0]["text"]), (1000, 3500, "Hello there"))
        self.assertEqual(srt.export(cues), "1\n00:00:01,000 --> 00:00:03,500\nHello there\n")

    def test_qc(self):
        cues = [{"start": 0, "end": 1000, "text": "x" * 30}]
        self.assertEqual(qc.report(cues), [{"cue": 0, "issue": "too fast"}])


if __name__ == "__main__":
    unittest.main()
