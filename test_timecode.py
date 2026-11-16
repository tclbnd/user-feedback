#!/usr/bin/env python3
"""Acceptance tests for the timecode converter.

Run with: python3 -m unittest -v test_timecode
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

import timecode

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "timecode.py")

DF2997 = (30000, 1001)
DF5994 = (60000, 1001)
NDF2997 = (30000, 1001)


def spec(rate, drop, start=0, ops=None):
    return {
        "id": "t",
        "fps_num": rate[0],
        "fps_den": rate[1],
        "drop_frame": drop,
        "source_start_index": start,
        "ops": ops or [],
    }


def run(specification, op):
    """Run one op, turning malformed input into the invalid result shape."""
    try:
        task = timecode.Task(specification)
        return timecode.run_op(op, task)
    except Exception:
        return {"valid": False, "label": None, "index": None}


def label_to_index(rate, drop, label, start=0):
    result = run(
        spec(rate, drop, start), {"dir": "label_to_index", "label": label}
    )
    assert result["valid"], (label, result)
    return result["index"]


def index_to_label(rate, drop, index, start=0):
    result = run(spec(rate, drop, start), {"dir": "index_to_label", "index": index})
    assert result["valid"], (index, result)
    return result["label"]


def convert(source_rate, source_drop, label, target_rate, target_drop, start=0):
    result = run(
        spec(source_rate, source_drop, start),
        {
            "dir": "convert",
            "label": label,
            "to_fps_num": target_rate[0],
            "to_fps_den": target_rate[1],
            "to_drop": target_drop,
        },
    )
    return result


class NonDrop29_97(unittest.TestCase):
    def test_known_labels(self):
        rate = NDF2997
        cases = {
            "00:00:00:00": 0,
            "00:00:00:01": 1,
            "00:00:59:29": 1799,
            "00:01:00:00": 1800,
            "00:09:59:29": 17999,
            "00:10:00:00": 18000,
            "00:59:59:29": 107999,
            "01:00:00:00": 108000,
            "23:59:59:29": 2591999,
        }
        for label, expected in cases.items():
            self.assertEqual(label_to_index(rate, False, label), expected)
            self.assertEqual(index_to_label(rate, False, expected), label)

    def test_day_wrap(self):
        self.assertEqual(index_to_label(NDF2997, False, 2592000), "00:00:00:00")
        self.assertEqual(index_to_label(NDF2997, False, 2592007), "00:00:00:07")
        self.assertEqual(label_to_index(NDF2997, False, "00:00:00:00"), 0)

    def test_round_trip_boundaries(self):
        indexes = [
            0,
            1799,
            1800,
            17999,
            18000,
            107999,
            108000,
            2591999,
            2592000 - 1,
        ]
        for index in indexes:
            label = index_to_label(NDF2997, False, index)
            self.assertEqual(label_to_index(NDF2997, False, label), index % 2592000)

    def test_mismatched_separator_rejected(self):
        result = run(
            spec(NDF2997, False),
            {"dir": "label_to_index", "label": "00:00:00;00"},
        )
        self.assertFalse(result["valid"])
        self.assertEqual(index_to_label(NDF2997, False, 0), "00:00:00:00")


class Drop29_97(unittest.TestCase):
    def test_known_labels(self):
        rate = DF2997
        cases = {
            "00:00:00;00": 0,
            "00:00:59;29": 1799,
            "00:01:00;02": 1800,
            "00:09:59;29": 17981,
            "00:10:00;00": 17982,
            "00:10:00;02": 17984,
            "00:59:59;29": 107891,
            "01:00:00;00": 107892,
            "10:00:00;00": 1078920,
            "23:59:59;29": 2589407,
        }
        for label, expected in cases.items():
            self.assertEqual(label_to_index(rate, True, label), expected)
            self.assertEqual(index_to_label(rate, True, expected), label)

    def test_skipped_numbers_are_invalid(self):
        for minute in (1, 2, 7, 9, 11, 59, 1439):
            for frame in (0, 1):
                label = "00:%02d:00;%02d" % (minute, frame)
                if minute >= 60:
                    label = "%02d:%02d:00;%02d" % (minute // 60, minute % 60, frame)
                result = run(
                    spec(DF2997, True), {"dir": "label_to_index", "label": label}
                )
                self.assertFalse(result["valid"], label)

    def test_tenth_minutes_are_continuous(self):
        for minute in (10, 20, 1430):
            hh, mm = divmod(minute, 60)
            label = "%02d:%02d:00;00" % (hh, mm)
            position = label_to_index(DF2997, True, label)
            previous = index_to_label(DF2997, True, position - 1)
            self.assertEqual(
                previous,
                "%02d:%02d:59;29" % ((minute - 1) // 60, (minute - 1) % 60),
            )

    def test_hour_and_day_boundaries(self):
        self.assertEqual(label_to_index(DF2997, True, "00:59:59;29"), 107891)
        self.assertEqual(label_to_index(DF2997, True, "01:00:00;00"), 107892)
        self.assertEqual(label_to_index(DF2997, True, "23:59:59;29"), 2589407)
        self.assertEqual(index_to_label(DF2997, True, 2589408), "00:00:00;00")
        self.assertEqual(index_to_label(DF2997, True, 0), "00:00:00;00")

    def test_round_trip_every_minute(self):
        # Sample the whole day, including the first frame after every minute
        # boundary.
        for minute in range(0, 24 * 60):
            hh, mm = divmod(minute, 60)
            first = "%02d:%02d:00;%s" % (
                hh,
                mm,
                "00" if mm % 10 == 0 else "02",
            )
            index = label_to_index(DF2997, True, first)
            self.assertEqual(index_to_label(DF2997, True, index), first)
            self.assertEqual(index_to_label(DF2997, True, index - 1)[-2:], "29")
        for index in range(0, 2589408, 1798):
            label = index_to_label(DF2997, True, index)
            self.assertEqual(label_to_index(DF2997, True, label), index)

    def test_mismatched_separator_rejected(self):
        # A drop-frame task rejects the non-drop marker on the same numbers.
        result = run(
            spec(DF2997, True), {"dir": "label_to_index", "label": "00:01:00:02"}
        )
        self.assertFalse(result["valid"])
        result = run(
            spec(DF2997, True), {"dir": "label_to_index", "label": "00:01:00;02"}
        )
        self.assertEqual(result["index"], 1800)
        self.assertEqual(index_to_label(DF2997, True, 1800), "00:01:00;02")


class Drop59_94(unittest.TestCase):
    def test_known_labels(self):
        rate = DF5994
        cases = {
            "00:00:00;00": 0,
            "00:00:59;59": 3599,
            "00:01:00;04": 3600,
            "00:09:59;59": 35963,
            "00:10:00;00": 35964,
            "01:00:00;00": 215784,
            "23:59:59;59": 5178815,
        }
        for label, expected in cases.items():
            self.assertEqual(label_to_index(rate, True, label), expected)
            self.assertEqual(index_to_label(rate, True, expected), label)

    def test_skipped_numbers_are_invalid(self):
        for frame in range(4):
            result = run(
                spec(DF5994, True),
                {"dir": "label_to_index", "label": "00:01:00;%02d" % frame},
            )
            self.assertFalse(result["valid"])
        result = run(
            spec(DF5994, True),
            {"dir": "label_to_index", "label": "00:01:00;04"},
        )
        self.assertTrue(result["valid"])

    def test_tenth_minute_is_continuous(self):
        self.assertEqual(
            index_to_label(DF5994, True, label_to_index(DF5994, True, "00:10:00;00") - 1),
            "00:09:59;59",
        )

    def test_round_trip(self):
        for index in range(0, 5178816, 3596):
            label = index_to_label(DF5994, True, index)
            self.assertEqual(label_to_index(DF5994, True, label), index)
        for index in (0, 3599, 3600, 35963, 35964, 215784, 5178815):
            self.assertEqual(
                label_to_index(DF5994, True, index_to_label(DF5994, True, index)),
                index,
            )


class SourceStart(unittest.TestCase):
    def test_zero_label_maps_to_start(self):
        for rate, drop in ((NDF2997, False), (DF2997, True), (DF5994, True)):
            position = timecode.parse_label(
                "00:00:00" + (";" if drop else ":") + "00",
                timecode.Rate(rate[0], rate[1], drop),
            )
            self.assertEqual(position, 0)
            for start in (0, 1, 1000, -5, 1234567):
                task = timecode.Task(spec(rate, drop, start))
                self.assertEqual(task.index_from_position(0), start % task.rate.day_frames)
                self.assertEqual(task.position_from_index(start), 0)
                self.assertEqual(
                    index_to_label(rate, drop, start, start), "00:00:00" + (";" if drop else ":") + "00"
                )
                self.assertEqual(label_to_index(rate, drop, "00:00:00" + (";" if drop else ":") + "00", start), start % task.rate.day_frames)

    def test_local_edit_range_round_trip(self):
        # A subrange deep inside a long programme still round-trips exactly.
        for rate, drop in ((NDF2997, False), (DF2997, True), (DF5994, True)):
            start = 1000000
            day = timecode.Rate(rate[0], rate[1], drop).day_frames
            for offset in (0, 1, 1799, 1800, 107891, 107892, day - 1):
                index = (start + offset) % day
                label = index_to_label(rate, drop, index, start)
                self.assertEqual(label_to_index(rate, drop, label, start), index)

    def test_wrap_through_day(self):
        start = 1000
        label = index_to_label(DF2997, True, 999, start)
        self.assertEqual(label, "23:59:59;29")
        self.assertEqual(label_to_index(DF2997, True, label, start), 999)


class Convert(unittest.TestCase):
    def test_29_97_drop_to_59_94_drop(self):
        result = convert(DF2997, True, "00:00:00;00", DF5994, True)
        self.assertEqual(result["label"], "00:00:00;00")
        self.assertEqual(result["index"], 0)

        result = convert(DF2997, True, "00:01:00;02", DF5994, True)
        self.assertEqual(result["label"], "00:01:00;04")
        self.assertEqual(result["index"], 1800)

        result = convert(DF2997, True, "00:10:00;00", DF5994, True)
        self.assertEqual(result["label"], "00:10:00;00")

        result = convert(DF2997, True, "23:59:59;29", DF5994, True)
        self.assertEqual(result["label"], "23:59:59;58")

    def test_59_94_drop_to_29_97_drop(self):
        self.assertEqual(
            convert(DF5994, True, "00:01:00;04", DF2997, True)["label"],
            "00:01:00;02",
        )
        # The last 59.94 frame of the minute lands exactly between two 29.97
        # frames; the tie rounds up into the next minute.
        self.assertEqual(
            convert(DF5994, True, "00:09:59;59", DF2997, True)["label"],
            "00:10:00;00",
        )
        self.assertEqual(
            convert(DF5994, True, "00:10:00;00", DF2997, True)["label"],
            "00:10:00;00",
        )
        self.assertEqual(
            convert(DF5994, True, "10:00:00;00", DF2997, True)["label"],
            "10:00:00;00",
        )

    def test_non_drop_to_drop(self):
        # Same frame, different label: 1800 frames into the day.
        result = convert(NDF2997, False, "00:01:00:00", DF2997, True)
        self.assertEqual(result["label"], "00:01:00;02")
        self.assertEqual(result["index"], 1800)

    def test_drop_to_non_drop(self):
        # Frame 107892 is the 1-hour drop-frame label 01:00:00;00, but one
        # hour of real time is 00:59:56:12 in non-drop numbering.
        result = convert(DF2997, True, "01:00:00;00", NDF2997, False)
        self.assertEqual(result["label"], "00:59:56:12")
        self.assertEqual(result["index"], 107892)

    def test_half_ties_round_up(self):
        # 3 frames at 24 fps = 0.125 s = 4.5 frames at 36 fps -> 5.
        result = convert((24, 1), False, "00:00:00:03", (36, 1), False)
        self.assertEqual(result["label"], "00:00:00:05")
        # 1 frame at 24 fps -> 1.5 frames at 36 fps -> 2.
        result = convert((24, 1), False, "00:00:00:01", (36, 1), False)
        self.assertEqual(result["label"], "00:00:00:02")
        # 1 frame at 30 fps -> 1.5 frames at 45 fps -> 2.
        result = convert((30, 1), False, "00:00:00:01", (45, 1), False)
        self.assertEqual(result["label"], "00:00:00:02")
        # Just below a tie rounds down: 1 * 35 / 24 = 1.458 -> 1.
        result = convert((24, 1), False, "00:00:00:01", (35, 1), False)
        self.assertEqual(result["label"], "00:00:00:01")
        # Just above a tie rounds up: 1 * 37 / 24 = 1.541 -> 2.
        result = convert((24, 1), False, "00:00:00:01", (37, 1), False)
        self.assertEqual(result["label"], "00:00:00:02")

    def test_source_offset_preserved(self):
        result = convert(DF2997, True, "00:01:00;02", DF5994, True, start=1000)
        self.assertEqual(result["index"], 2800)
        # The target label is still derived from media time, not the index.
        self.assertEqual(result["label"], "00:01:00;04")

    def test_invalid_source_label(self):
        self.assertFalse(convert(DF2997, True, "00:01:00;00", DF5994, True)["valid"])

    def test_unsupported_target_drop_rate(self):
        result = convert(DF2997, True, "00:00:00;00", (25, 1), True)
        self.assertFalse(result["valid"])


class Rates(unittest.TestCase):
    def test_nominal_matches_python_round(self):
        for num, den in (
            (30000, 1001),
            (60000, 1001),
            (24000, 1001),
            (25000, 1001),
            (24, 1),
            (25, 1),
            (50, 1),
            (60, 1),
            (24000, 1000),
            (48, 2),
            (25, 2),
            (37, 2),
            (1, 3),
            (2, 3),
        ):
            self.assertEqual(
                timecode.nominal_fps(num, den), round(num / den), (num, den)
            )

    def test_day_lengths(self):
        self.assertEqual(timecode.Rate(30000, 1001, True).day_frames, 2589408)
        self.assertEqual(timecode.Rate(30000, 1001, False).day_frames, 2592000)
        self.assertEqual(timecode.Rate(60000, 1001, True).day_frames, 5178816)
        self.assertEqual(timecode.Rate(60000, 1001, False).day_frames, 5184000)
        # 1440 minutes, 1296 of them dropping labels.
        self.assertEqual(30 * 86400 - 2 * 1296, 2589408)
        self.assertEqual(60 * 86400 - 4 * 1296, 5178816)

    def test_dropped_labels_are_the_expected_count(self):
        # Independently derive the day length and the label->index map from
        # the drop rule, rather than from the shipped closed-form arithmetic.
        for nominal, dropped, rate in ((30, 2, DF2997), (60, 4, DF5994)):
            position = 0
            checked = 0
            for minute in range(24 * 60):
                hh, mm = divmod(minute, 60)
                for ss in range(60):
                    for ff in range(nominal):
                        skipped = ss == 0 and mm % 10 != 0 and ff < dropped
                        if skipped:
                            result = run(
                                spec(rate, True),
                                {
                                    "dir": "label_to_index",
                                    "label": "%02d:%02d:%02d;%02d" % (hh, mm, ss, ff),
                                },
                            )
                            self.assertFalse(result["valid"])
                            continue
                        if position % 5003 == 0:
                            label = "%02d:%02d:%02d;%02d" % (hh, mm, ss, ff)
                            self.assertEqual(label_to_index(rate, True, label), position)
                            self.assertEqual(index_to_label(rate, True, position), label)
                            checked += 1
                        position += 1
            self.assertEqual(position, nominal * 86400 - dropped * 1296)
            self.assertGreater(checked, 100)


class InvalidInput(unittest.TestCase):
    def test_malformed_labels(self):
        bad = [
            "",
            "00:00:00",
            "00:00:00:",
            "aa:bb:cc:dd",
            "00:60:00:00",
            "00:00:60:00",
            "00:00:00:30",
            "24:00:00:00",
            "-1:00:00:00",
            "00:00:00:00:00",
            "00:00:00;0",
            "0:00:00;00",
            "00:0:00;00",
            None,
            1234,
        ]
        for label in bad:
            result = run(spec(NDF2997, False), {"dir": "label_to_index", "label": label})
            self.assertFalse(result["valid"], repr(label))

    def test_bad_index(self):
        for index in (1.5, "5", None, True, [1]):
            result = run(spec(NDF2997, False), {"dir": "index_to_label", "index": index})
            self.assertFalse(result["valid"], repr(index))

    def test_unknown_direction(self):
        result = run(spec(NDF2997, False), {"dir": "nope"})
        self.assertFalse(result["valid"])

    def test_bad_rate(self):
        for rate in ((0, 1), (30000, 0), (-30000, 1001), (30000.5, 1001)):
            with self.assertRaises(Exception):
                timecode.Task(spec(rate, False))

    def test_whole_float_rate_fields_accepted(self):
        task = timecode.Task(
            {
                "fps_num": 30000.0,
                "fps_den": 1001.0,
                "drop_frame": True,
                "source_start_index": 0,
            }
        )
        self.assertEqual(task.rate.nominal, 30)
        self.assertEqual(task.label_to_index("00:01:00;02"), 1800)

    def test_drop_without_defined_convention(self):
        with self.assertRaises(Exception):
            timecode.Task(spec((25, 1), True))

    def test_ambiguous_boolean_rejected(self):
        for value in ("false", "true", None, 2, -1):
            with self.assertRaises(Exception):
                timecode.Task(spec(NDF2997, value))
        # Integers 0/1 are accepted as booleans, matching JSON habits.
        self.assertFalse(timecode.Task(spec(NDF2997, 0)).rate.drop)
        self.assertTrue(timecode.Task(spec(DF2997, 1)).rate.drop)


class CommandLine(unittest.TestCase):
    def test_end_to_end(self):
        payload = {
            "tasks": [
                {
                    "id": "a",
                    "fps_num": 30000,
                    "fps_den": 1001,
                    "drop_frame": True,
                    "source_start_index": 0,
                    "ops": [
                        {"dir": "label_to_index", "label": "00:01:00;02"},
                        {"dir": "label_to_index", "label": "00:01:00;00"},
                        {"dir": "index_to_label", "index": 17982},
                        {"dir": "convert", "label": "01:00:00;00",
                         "to_fps_num": 60000, "to_fps_den": 1001, "to_drop": True},
                        {"dir": "bogus"},
                    ],
                },
                {
                    "id": "b",
                    "fps_num": 30000,
                    "fps_den": 1001,
                    "drop_frame": False,
                    "source_start_index": 1000,
                    "ops": [
                        {"dir": "label_to_index", "label": "01:00:00:00"},
                        {"dir": "index_to_label", "index": 1000},
                    ],
                },
            ]
        }
        with tempfile.TemporaryDirectory(dir=HERE) as directory:
            source = os.path.join(directory, "in.json")
            target = os.path.join(directory, "out.json")
            with open(source, "w", encoding="utf-8") as stream:
                json.dump(payload, stream)
            subprocess.run(
                [sys.executable, SCRIPT, source, target],
                check=True,
                cwd=HERE,
            )
            with open(target, "r", encoding="utf-8") as stream:
                output = json.load(stream)

        self.assertEqual([task["id"] for task in output["tasks"]], ["a", "b"])
        ops = output["tasks"][0]["ops"]
        self.assertEqual(ops[0], {"valid": True, "label": "00:01:00;02", "index": 1800})
        self.assertEqual(ops[1], {"valid": False, "label": None, "index": None})
        self.assertEqual(ops[2], {"valid": True, "label": "00:10:00;00", "index": 17982})
        self.assertEqual(ops[3], {"valid": True, "label": "01:00:00;00", "index": 107892})
        self.assertEqual(ops[4], {"valid": False, "label": None, "index": None})

        ops = output["tasks"][1]["ops"]
        self.assertEqual(ops[0], {"valid": True, "label": "01:00:00:00", "index": 109000})
        self.assertEqual(ops[1], {"valid": True, "label": "00:00:00:00", "index": 1000})


class Reference(unittest.TestCase):
    """Cross-check the implementation against a brute-force enumerator.

    The reference walks the 24-hour label space frame by frame using the
    definition ("does this label exist?"), which is independent of the
    closed-form arithmetic in the converter.
    """

    @staticmethod
    def enumerate_rate(nominal, dropped):
        """Yield (label, position) for every label that exists in a day."""
        position = 0
        for minute in range(24 * 60):
            remaining = 60 * nominal
            if dropped and minute % 10 != 0:
                remaining -= dropped
            for offset in range(remaining):
                ff = offset + (dropped if (dropped and minute % 10 != 0) else 0)
                yield minute, ff, position
                position += 1

    def check(self, nominal, dropped, rate, stride):
        expected = 0
        seen = 0
        for minute, ff, position in self.enumerate_rate(nominal, dropped):
            self.assertEqual(position, expected)
            expected += 1
            if position % stride == 0:
                label = "%02d:%02d:%02d%s%02d" % (
                    minute // 60,
                    minute % 60,
                    ff // nominal,
                    ";" if dropped else ":",
                    ff % nominal,
                )
                self.assertEqual(
                    label_to_index(rate, bool(dropped), label), position, label
                )
                self.assertEqual(
                    index_to_label(rate, bool(dropped), position), label
                )
                seen += 1
        self.assertGreater(seen, 100)
        self.assertEqual(expected, timecode.Rate(rate[0], rate[1], bool(dropped)).day_frames)

    def test_29_97_drop(self):
        self.check(30, 2, DF2997, 997)

    def test_59_94_drop(self):
        self.check(60, 4, DF5994, 997)

    def test_29_97_non_drop(self):
        self.check(30, 0, NDF2997, 997)


    def test_convert_round_trip_over_the_day(self):
        # Every 29.97 drop-frame label maps to a 59.94 label that maps back
        # to the same frame; the mapping is frame-exact, not label-exact.
        source = timecode.Task(spec(DF2997, True))
        target_rate = timecode.Rate(DF5994[0], DF5994[1], True)
        reverse = timecode.Task(spec(DF5994, True))
        for index in range(0, source.rate.day_frames, 1793):
            label = source.index_to_label(index)
            forward = source.convert(label, target_rate)
            back_index = reverse.label_to_index(forward["label"])
            expected = timecode.round_half_up(
                index * target_rate.num * source.rate.den,
                target_rate.den * source.rate.num,
            ) % target_rate.day_frames
            self.assertEqual(back_index, expected, label)
        self.assertEqual(
            source.convert("23:59:59;29", target_rate)["label"], "23:59:59;58"
        )

    def test_convert_hits_hour_boundary_on_the_target(self):
        # 01:00:00;00 drop-frame is one hour of media time in both rates.
        self.assertEqual(
            convert(DF2997, True, "01:00:00;00", DF5994, True)["label"],
            "01:00:00;00",
        )
        self.assertEqual(
            convert(DF2997, True, "00:59:59;29", DF5994, True)["label"],
            "00:59:59;58",
        )
        self.assertEqual(
            convert(DF5994, True, "01:00:00;00", DF2997, True)["label"],
            "01:00:00;00",
        )


class CommandLineRobustness(unittest.TestCase):
    def invoke(self, payload):
        with tempfile.TemporaryDirectory(dir=HERE) as directory:
            source = os.path.join(directory, "in.json")
            target = os.path.join(directory, "out.json")
            with open(source, "w", encoding="utf-8") as stream:
                if isinstance(payload, str):
                    stream.write(payload)
                else:
                    json.dump(payload, stream)
            subprocess.run(
                [sys.executable, SCRIPT, source, target], check=True, cwd=HERE
            )
            with open(target, "r", encoding="utf-8") as stream:
                return json.load(stream)

    def test_task_level_failure_invalidates_all_ops(self):
        output = self.invoke(
            {
                "tasks": [
                    {
                        "id": "bad-rate",
                        "fps_num": 0,
                        "fps_den": 1,
                        "drop_frame": False,
                        "ops": [{"dir": "label_to_index", "label": "00:00:00:00"}],
                    },
                    {
                        "id": "bad-start",
                        "fps_num": 30000,
                        "fps_den": 1001,
                        "drop_frame": False,
                        "source_start_index": 1.5,
                        "ops": [{"dir": "index_to_label", "index": 0}],
                    },
                ]
            }
        )
        for task in output["tasks"]:
            self.assertEqual(
                task["ops"], [{"valid": False, "label": None, "index": None}]
            )

    def test_missing_ops_and_empty_tasks(self):
        output = self.invoke({"tasks": [{"id": "empty"}, {"id": "no-ops", "ops": None}]})
        self.assertEqual(output, {"tasks": [{"id": "empty", "ops": []}, {"id": "no-ops", "ops": []}]})

        output = self.invoke({})
        self.assertEqual(output, {"tasks": []})


class ConvertReference(unittest.TestCase):
    """Check convert against an independent Fraction-based calculation."""

    def test_against_fraction(self):
        import fractions
        import random

        rates = [
            ((30000, 1001), True),
            ((30000, 1001), False),
            ((60000, 1001), True),
            ((24000, 1001), False),
            ((25, 1), False),
        ]
        rng = random.Random(20261117)
        for source_rate, source_drop in rates:
            for target_rate, target_drop in rates:
                source = timecode.Task(spec(source_rate, source_drop))
                # Generating a valid source label is easiest via the
                # formatter, then the reference recomputes the target exactly.
                for _ in range(200):
                    index = rng.randrange(source.rate.day_frames)
                    label = source.index_to_label(index)
                    result = source.convert(
                        label, timecode.Rate(target_rate[0], target_rate[1], target_drop)
                    )
                    exact = fractions.Fraction(
                        index * target_rate[0] * source_rate[1],
                        target_rate[1] * source_rate[0],
                    )
                    floor = exact.numerator // exact.denominator
                    remainder = exact - floor
                    expected = floor + (1 if remainder >= fractions.Fraction(1, 2) else 0)
                    expected %= timecode.Rate(
                        target_rate[0], target_rate[1], target_drop
                    ).day_frames
                    self.assertTrue(result["valid"])
                    self.assertEqual(
                        result["label"],
                        timecode.format_label(
                            expected,
                            timecode.Rate(target_rate[0], target_rate[1], target_drop),
                        ),
                        (source_rate, source_drop, target_rate, target_drop, label),
                    )


class EchoOfInputBasis(unittest.TestCase):
    """Results keep the caller's own values so an edit range round-trips."""

    def test_label_echoed_verbatim(self):
        result = run(
            spec(DF2997, True, 500),
            {"dir": "label_to_index", "label": "01:01:00;02"},
        )
        self.assertEqual(result["label"], "01:01:00;02")
        self.assertEqual(result["index"], 500 + 109692)

    def test_index_echoed_verbatim(self):
        # Out-of-range indices wrap for label purposes but are reported as
        # given, so a caller's timeline arithmetic is not silently rewritten.
        for index in (2589408, -1, 10 ** 9):
            result = run(
                spec(DF2997, True), {"dir": "index_to_label", "index": index}
            )
            self.assertEqual(result["index"], index)
        self.assertEqual(
            index_to_label(DF2997, True, 2589408), "00:00:00;00"
        )
        self.assertEqual(
            index_to_label(DF2997, True, -1), "23:59:59;29"
        )

    def test_convert_echoes_source_index(self):
        result = convert(DF2997, True, "01:01:00;02", DF5994, True, start=500)
        self.assertEqual(result["index"], 500 + 109692)
        # The same media time is the doubled frame number at 59.94.
        self.assertEqual(result["label"], "01:01:00;04")


if __name__ == "__main__":
    unittest.main()