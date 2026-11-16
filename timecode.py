#!/usr/bin/env python3
"""SMPTE timecode <-> frame index conversion.

Frame rates are exact rationals (``fps_num / fps_den``).  The nominal label
rate is ``round(fps_num / fps_den)`` and is the number of frame numbers per
second (30 for 29.97, 60 for 59.94, ...).

Non-drop numbering uses every nominal frame number.  Drop-frame numbering
skips the first ``drop`` frame numbers of every minute whose minute number is
not a multiple of ten (2 for 29.97, 4 for 59.94).  A label that names a
skipped number is invalid, as is a label that uses the separator of the other
convention.

A frame index is a position on the 24-hour media timeline.  ``source_start_index``
is the index that carries the label 00:00:00:00; indices wrap modulo one
24-hour day.
"""

import json
import re
import sys

LABEL_RE = re.compile(r"\A(\d{2}):(\d{2}):(\d{2})([:;])(\d{2})\Z")

DAY_MINUTES = 24 * 60
TENTH_MINUTES = DAY_MINUTES // 10


class TimecodeError(ValueError):
    """Raised when a value cannot be interpreted as a timecode."""


def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def as_int(value):
    """Return an integer, tolerating JSON floats that are whole numbers.

    Rate fields are written by many tools as ``30000.0``; accepting those
    costs nothing as long as the value really is integral.
    """
    if is_int(value):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise TimecodeError("expected an integer")


def parse_bool(value, name):
    """Accept a real boolean (or 0/1) and reject ambiguous values.

    A string such as ``"false"`` is truthy in Python; treating it as True
    would silently flip the numbering convention.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise TimecodeError("%s must be a boolean" % name)


def round_half_up(num, den):
    """Round the non-negative fraction num/den, exact halves upward."""
    return (2 * num + den) // (2 * den)


def round_nearest(num, den):
    """Round the non-negative fraction num/den with exact halves to even.

    This mirrors the usual ``round(fps_num / fps_den)`` definition of a
    nominal rate without introducing floating point error.
    """
    quotient, remainder = divmod(num, den)
    doubled = 2 * remainder
    if doubled > den or (doubled == den and quotient % 2):
        return quotient + 1
    return quotient


def nominal_fps(num, den):
    """Nominal (label) frame rate, e.g. 30 for 30000/1001."""
    num = as_int(num)
    den = as_int(den)
    if num <= 0 or den <= 0:
        raise TimecodeError("invalid frame rate")
    return round_nearest(num, den)


def dropped_per_minute(nominal):
    """Labels skipped at each non-tenth minute, by convention."""
    if nominal == 30:
        return 2
    if nominal == 60:
        return 4
    raise TimecodeError("drop-frame numbering is undefined for this rate")


class Rate:
    """A rational frame rate and its label-numbering convention."""

    def __init__(self, num, den, drop):
        self.num = as_int(num)
        self.den = as_int(den)
        if self.num <= 0 or self.den <= 0:
            raise TimecodeError("invalid frame rate")
        self.nominal = nominal_fps(self.num, self.den)
        if self.nominal < 1:
            raise TimecodeError("frame rate has no nominal label rate")
        self.drop = parse_bool(drop, "drop_frame")
        self.dropped = dropped_per_minute(self.nominal) if self.drop else 0
        # A 24-hour day has 1440 minutes, 144 of which are tenth minutes, so
        # labels are dropped at 1296 minute boundaries.
        self.day_frames = self.nominal * 86400 - self.dropped * (DAY_MINUTES - TENTH_MINUTES)


def parse_label(label, rate):
    """Return the label's frame position within its 24-hour day.

    Field ranges, the numbering separator and dropped frame numbers are
    validated here.  ``source_start_index`` is applied by the task, not here.
    """
    if not isinstance(label, str):
        raise TimecodeError("label is not a string")
    match = LABEL_RE.match(label)
    if match is None:
        raise TimecodeError("malformed label")
    hh, mm, ss, sep, ff = match.groups()
    hh, mm, ss, ff = int(hh), int(mm), int(ss), int(ff)

    if hh > 23 or mm > 59 or ss > 59 or ff >= rate.nominal:
        raise TimecodeError("label field out of range")

    # The separator carries the numbering convention.  Accepting the other
    # one silently reinterprets material recorded under a different
    # convention, which is the drift this converter must not introduce.
    if rate.drop and sep != ";":
        raise TimecodeError("drop-frame label must use ';'")
    if not rate.drop and sep != ":":
        raise TimecodeError("non-drop label must use ':'")

    if rate.drop and ss == 0 and mm % 10 != 0 and ff < rate.dropped:
        raise TimecodeError("label names a skipped frame number")

    total_minutes = hh * 60 + mm
    position = (total_minutes * 60 + ss) * rate.nominal + ff
    if rate.drop:
        # Every minute except each tenth minute has already lost `dropped`
        # labels by the time this label is reached.
        position -= rate.dropped * (total_minutes - total_minutes // 10)
    return position


def format_label(position, rate):
    """Format a frame position in [0, rate.day_frames) as a label."""
    if position < 0 or position >= rate.day_frames:
        raise TimecodeError("frame position out of range")

    if rate.drop:
        frames_per_minute = rate.nominal * 60 - rate.dropped
        frames_per_ten_minutes = rate.nominal * 600 - rate.dropped * 9

        tenths, rest = divmod(position, frames_per_ten_minutes)
        label_position = position + rate.dropped * 9 * tenths
        if rest >= rate.dropped:
            label_position += rate.dropped * ((rest - rate.dropped) // frames_per_minute)
    else:
        label_position = position

    ff = label_position % rate.nominal
    total_seconds = label_position // rate.nominal
    ss = total_seconds % 60
    total_minutes = total_seconds // 60
    mm = total_minutes % 60
    hh = total_minutes // 60

    sep = ";" if rate.drop else ":"
    return "%02d:%02d:%02d%s%02d" % (hh, mm, ss, sep, ff)


class Task:
    """One source clip: a rate, a numbering convention and an index origin."""

    def __init__(self, spec):
        self.id = spec.get("id")
        self.rate = Rate(spec["fps_num"], spec["fps_den"], spec.get("drop_frame", False))
        start = spec.get("source_start_index", 0)
        if not is_int(start):
            raise TimecodeError("source_start_index must be an integer")
        self.start = start

    def index_from_position(self, position):
        return (self.start + position) % self.rate.day_frames

    def position_from_index(self, index):
        if not is_int(index):
            raise TimecodeError("frame index must be an integer")
        return (index - self.start) % self.rate.day_frames

    def label_to_index(self, label):
        return self.index_from_position(parse_label(label, self.rate))

    def index_to_label(self, index):
        return format_label(self.position_from_index(index), self.rate)

    def convert(self, label, target):
        """Map a source label to the target rate's nearest frame by media time.

        Media time is ``position / source_rate`` seconds; the target position
        is that time in target frames, rounded to the nearest frame with exact
        halves rounded up.
        """
        source_position = parse_label(label, self.rate)
        numerator = source_position * target.num * self.rate.den
        denominator = target.den * self.rate.num
        target_position = round_half_up(numerator, denominator) % target.day_frames
        return {
            "valid": True,
            "label": format_label(target_position, target),
            "index": self.index_from_position(source_position),
        }


def invalid():
    return {"valid": False, "label": None, "index": None}


def run_op(op, task):
    direction = op.get("dir")

    if direction == "label_to_index":
        return {
            "valid": True,
            "label": op.get("label"),
            "index": task.label_to_index(op.get("label")),
        }

    if direction == "index_to_label":
        index = op.get("index")
        return {
            "valid": True,
            "label": task.index_to_label(index),
            "index": index,
        }

    if direction == "convert":
        target = Rate(op["to_fps_num"], op["to_fps_den"], op.get("to_drop", False))
        return task.convert(op.get("label"), target)

    raise TimecodeError("unknown operation")


def main(argv):
    if len(argv) != 3:
        sys.stderr.write("usage: timecode.py INPUT.json OUTPUT.json\n")
        return 2

    with open(argv[1], "r", encoding="utf-8") as stream:
        data = json.load(stream)

    specs = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(specs, list):
        specs = []

    results = []
    for task_spec in specs:
        if not isinstance(task_spec, dict):
            task_spec = {}
        try:
            task = Task(task_spec)
        except Exception:
            task = None

        raw_ops = task_spec.get("ops")
        if not isinstance(raw_ops, list):
            raw_ops = []

        ops = []
        for op in raw_ops:
            try:
                if task is None or not isinstance(op, dict):
                    raise TimecodeError("invalid operation")
                ops.append(run_op(op, task))
            except Exception:
                ops.append(invalid())
        results.append({"id": task_spec.get("id"), "ops": ops})

    with open(argv[2], "w", encoding="utf-8") as stream:
        json.dump({"tasks": results}, stream)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
