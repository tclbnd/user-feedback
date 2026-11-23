import unittest

from edlkit.edl import parse

from lists import cut, dissolve, edl


class BasicTests(unittest.TestCase):
    def test_title(self):
        self.assertEqual(parse(edl([]))["title"], "TEST")

    def test_cut(self):
        out = parse(edl([cut(1, "R1", "V", "00:00:00:00", "00:00:01:00", "01:00:00:00", "01:00:01:00")]))
        self.assertEqual((out["events"][0]["duration"], out["record_frames"]), (30, 30))

    def test_drop_frame(self):
        out = parse(edl([cut(1, "R1", "V", "00:00:00;00", "00:00:01;00", "01:00:00;00", "01:00:01;00")],
                        fcm="DROP FRAME"))
        e = out["events"][0]
        # One hour of drop-frame timecode is 108 frames short of 30 * 3600.
        self.assertEqual((e["record_in"], e["record_out"], e["duration"]), (107892, 107922, 30))
        self.assertEqual(out["record_frames"], 30)

    def test_fps_25(self):
        out = parse(edl([cut(1, "R1", "V", "00:00:00:00", "00:00:01:00", "01:00:00:00", "01:00:01:00")]),
                    fps=25)
        e = out["events"][0]
        self.assertEqual((e["record_in"], e["duration"]), (90000, 25))

    def test_dissolve_merges_to_one_event(self):
        lines = dissolve(1, "R1", "R2", 24, "00:00:10:00", "00:00:20:00", "01:00:00:00", "01:00:10:00")
        lines += ["* FROM CLIP NAME: OLD.MOV", "* TO CLIP NAME: NEW.MOV"]
        out = parse(edl(lines))
        self.assertEqual(len(out["events"]), 1)
        e = out["events"][0]
        self.assertEqual((e["reel"], e["from_reel"], e["transition"], e["transition_frames"]),
                         ("R2", "R1", "D", 24))
        self.assertEqual((e["clip"], e["from_clip"]), ("NEW.MOV", "OLD.MOV"))
        self.assertEqual((e["record_in"], e["record_out"], e["duration"]), (108000, 108300, 300))
        self.assertEqual(out["video_overlaps"], [])

    def test_wipe(self):
        out = parse(edl(["001  R1       V     C        00:00:00:00 00:00:00:00 01:00:00:00 01:00:00:00",
                         "001  R2       V     W022  016 00:00:05:00 00:00:07:00 01:00:00:00 01:00:02:00"]))
        e = out["events"][0]
        self.assertEqual((e["transition"], e["transition_frames"], e["from_reel"]), ("W", 16, "R1"))
        self.assertEqual(e["duration"], 60)

    def test_tracks_b_and_aa(self):
        out = parse(edl([cut(1, "R1", "B", "00:00:00:00", "00:00:01:00", "01:00:00:00", "01:00:01:00"),
                         cut(2, "R2", "AA", "00:00:00:00", "00:00:01:00", "01:00:00:00", "01:00:01:00")]))
        self.assertEqual(out["events"][0]["tracks"], ["V", "A"])
        self.assertEqual(out["events"][1]["tracks"], ["A", "A2"])
        self.assertEqual(out["tracks"], {"A": 2, "A2": 1, "V": 1})
        # The B event is picture: no hole between it and a following video event.
        self.assertEqual(out["video_gaps"], [])

    def test_clip_names(self):
        out = parse(edl([cut(1, "R1", "V", "00:00:00:00", "00:00:01:00", "01:00:00:00", "01:00:01:00"),
                         "* FROM CLIP NAME: SHOT.MOV"]))
        self.assertEqual(out["events"][0]["clip"], "SHOT.MOV")

    def test_motion_effect(self):
        out = parse(edl([cut(1, "R1", "V", "00:00:40:00", "00:00:43:00", "01:00:00:00", "01:00:03:00"),
                         "M2   R1       015.0 00:00:40:00"]))
        e = out["events"][0]
        self.assertEqual(e["speed"], "15")
        self.assertEqual(e["duration"], 90)
        self.assertEqual(e["source_frames"], 45)

    def test_reels_and_black(self):
        out = parse(edl(dissolve(1, "BL", "R1", 10, "00:00:00:00", "00:00:01:00",
                                 "01:00:00:00", "01:00:01:00")
                        + [cut(2, "AX", "V", "00:00:00:00", "00:00:01:00", "01:00:01:00", "01:00:02:00")]))
        self.assertEqual(out["reels"], ["R1"])
        self.assertEqual(out["black_events"], [1])


if __name__ == "__main__":
    unittest.main()
