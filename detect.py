#!/usr/bin/env python3
"""Frame-accurate black / freeze detector.

The detector works entirely on the decoded frame timeline supplied in the
input file.  Frame durations are taken from neighbouring presentation
timestamps (pts), falling back to the nominal ``frame_ms`` for the final
frame.  This keeps events correct for variable-frame-rate material and for
streams containing dropped frames (a large positive pts step represents the
previous frame being held until the next decoded frame).

Processing is incremental: :class:`Detector` keeps its run state across
``feed`` calls and only emits events when closed, so slicing the input into
different read blocks cannot duplicate or drop events.

Output shape (see README.md):
    {"events": [...], "playable": [...], "skipped": [...]}
"""

import bisect
import json
import sys


# Optional "already marked black" annotation.  The documented input only has
# pts/luma/diff, so this support is inert unless present.  A frame or a whole
# pts range can be declared intentionally black (director slate, reserved
# black, ...).  Such material is not a defect, so it is reported as neither
# signal loss nor a freeze alarm; it simply stays playable.
_MARK_FRAME_KEYS = (
    "marked_black",
    "intentional_black",
    "reserved_black",
    "expected_black",
)
_MARK_RANGE_KEYS = (
    "marked_black",
    "intentional_black",
    "reserved_black",
    "expected_black",
    "black_ranges",
    "slates",
)


def _normalise_ranges(items):
    """Parse and coalesce marked-black ranges into sorted non-overlapping form."""
    parsed = []
    for item in items or ():
        if isinstance(item, dict):
            start, end = item["start"], item["end"]
        else:
            start, end = item[0], item[1]
        if end > start:
            parsed.append((start, end))

    parsed.sort()
    merged = []
    for start, end in parsed:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _parse_marked_ranges(data):
    items = []
    for key in _MARK_RANGE_KEYS:
        value = data.get(key)
        if isinstance(value, (list, tuple)):
            items.extend(value)
    return _normalise_ranges(items)


def _is_marked_frame(frame):
    return any(frame.get(key) is True for key in _MARK_FRAME_KEYS)



def _frame_end(kept, pos, frame_ms):
    """Timeline end of the kept frame at *pos*.

    A frame stays on screen until the next decoded frame, so a dropped frame
    (large pts step) naturally lengthens the previous frame's hold interval.
    The final frame falls back to the nominal frame duration.  ``kept``
    contains only frames whose pts did not move backwards.
    """
    if pos + 1 < len(kept):
        return kept[pos + 1]["pts"]
    return kept[pos]["pts"] + frame_ms


def _classify(frame, is_first, windows, marked_black=False):
    """Return 'black', 'freeze', 'both' or None for one frame.

    Threshold comparisons are strict (``<``) as specified, so a value exactly
    on a window boundary is not flagged.  The stream's first frame has
    diff == 0 by definition but is not a freeze unless it is also black.

    A frame marked intentionally black (see :func:`_is_marked_black`) is not
    a defect and therefore not an alarm.  It is static black by design, so
    skipping both classes avoids reporting expected slates or reserved black
    as either signal loss or a frozen picture.
    """
    if marked_black:
        return None

    is_black = frame["luma"] < windows["black_luma"]

    # diff == 0 is documented as frozen.  Keeping that explicit also makes
    # the rule well defined if freeze_diff is zero or negative.
    is_freeze = (not is_first) and (
        frame["diff"] == 0 or frame["diff"] < windows["freeze_diff"]
    )

    if is_black and is_freeze:
        return "both"
    if is_black:
        return "black"
    if is_freeze:
        return "freeze"
    return None


def _merge_runs(runs, kept, windows, frame_ms):
    """Merge adjacent same-kind runs separated by less than ``gap_ms``.

    Only neighbouring runs in the run list are candidates.  A run of the
    other kind in between means the two runs are not adjacent, so they are
    never merged across flagged material (the documented gap must be made of
    *unflagged* frames).

    Merging runs *before* the minimum-duration test is what keeps a long
    frozen picture with occasional one-frame noise spikes a single event
    instead of a burst of short alarms.  The boundary is strict exactly as
    documented: ``gap < gap_ms`` merges, ``gap == gap_ms`` does not.
    """
    merged = []

    for run in runs:
        run_start = kept[run["first_pos"]]["pts"]
        run_end = _frame_end(kept, run["last_pos"], frame_ms)

        if merged:
            prev = merged[-1]
            if prev["kind"] == run["kind"]:
                gap = run_start - prev["end"]
                if gap < windows["gap_ms"]:
                    prev["end"] = run_end
                    prev["last_pos"] = run["last_pos"]
                    prev["both"] = prev["both"] or run["both"]
                    continue

        merged.append(
            {
                "kind": run["kind"],
                "start": run_start,
                "end": run_end,
                "first_pos": run["first_pos"],
                "last_pos": run["last_pos"],
                "both": run["both"],
            }
        )

    return merged


class Detector:
    """Incremental black/freeze detector.

    Feed frames in decode order with :meth:`feed` (or :meth:`push`) and call
    :meth:`close` to get the result.  Open runs live in the instance state,
    so splitting the input into different read blocks cannot duplicate or
    drop events: an event is only emitted once, when its run closes.
    """

    def __init__(self, windows, frame_ms, marked_ranges=()):
        self.windows = windows
        self.frame_ms = frame_ms
        # Sorted, non-overlapping (start, end) pts ranges.
        self.marked_ranges = _normalise_ranges(marked_ranges)
        self._range_starts = [start for start, _ in self.marked_ranges]
        self.kept = []
        self.skipped = []
        self._runs = []
        self._current = None
        self._last_pts = None
        self._input_index = 0
        self._closed = False

    def _in_marked_range(self, pts):
        if not self._range_starts:
            return False
        index = bisect.bisect_right(self._range_starts, pts) - 1
        if index < 0:
            return False
        start, end = self.marked_ranges[index]
        return start <= pts < end

    def feed(self, frames):
        for frame in frames:
            self.push(frame)
        return self

    def push(self, frame):
        if self._closed:
            raise RuntimeError("detector is already closed")

        index = self._input_index
        self._input_index += 1
        pts = frame["pts"]

        # pts must be non-decreasing.  A backwards step is a decode-order
        # discontinuity: drop the frame and report its input index instead of
        # letting it distort run durations or create a duplicate alarm.
        if self._last_pts is not None and pts < self._last_pts:
            self.skipped.append(index)
            return self

        pos = len(self.kept)
        self.kept.append(frame)
        self._last_pts = pts

        kind = _classify(
            frame,
            pos == 0,
            self.windows,
            marked_black=(
                _is_marked_frame(frame) or self._in_marked_range(pts)
            ),
        )
        current = self._current

        if kind is None:
            self._close_current()
            return self

        if current is not None:
            # A `both` frame counts toward whichever run it joins.
            if kind == "both" or kind == current["kind"]:
                current["last_pos"] = pos
                current["both"] = current["both"] or kind == "both"
                return
            self._close_current()

        # Opening a run.  A `both` frame joins nothing yet; black is tested
        # first, so it opens a black run (reason stays "both").
        self._current = {
            "kind": "black" if kind == "both" else kind,
            "first_pos": pos,
            "last_pos": pos,
            "both": kind == "both",
        }
        return self

    def _close_current(self):
        if self._current is not None:
            self._runs.append(self._current)
            self._current = None

    def close(self):
        if self._closed:
            raise RuntimeError("detector is already closed")
        self._closed = True
        self._close_current()

        merged = _merge_runs(
            self._runs, self.kept, self.windows, self.frame_ms
        )

        indexed_events = []
        for run in merged:
            # Guard against degenerate zero-length intervals (possible when
            # several frames share a pts or a window is configured as zero).
            if run["end"] <= run["start"]:
                continue
            minimum = (
                self.windows["min_black_ms"]
                if run["kind"] == "black"
                else self.windows["min_freeze_ms"]
            )
            # "reaches min_*_ms": equality qualifies.
            if run["end"] - run["start"] < minimum:
                continue
            indexed_events.append(run)

        indexed_events.sort(key=lambda run: (run["start"], run["first_pos"]))

        events = [
            {
                "kind": run["kind"],
                "start": run["start"],
                "end": run["end"],
                # Frames covered by the reported interval.  A merged event
                # therefore includes the short unflagged gaps it absorbed,
                # keeping end/start and frames consistent for CFR input.
                "frames": run["last_pos"] - run["first_pos"] + 1,
                "reason": "both" if run["both"] else run["kind"],
            }
            for run in indexed_events
        ]

        return {
            "events": events,
            "playable": self._playable(indexed_events),
            "skipped": list(self.skipped),
        }

    def _playable(self, indexed_events):
        """Frame ranges not covered by any emitted event.

        Frame positions (not wall-clock arithmetic) drive the ranges, so
        dropped/duplicate timestamps cannot create phantom coverage.  Frames
        of runs too short to become events remain playable.
        """
        playable = []
        cursor = 0
        total = len(self.kept)

        def emit(first, last):
            if first > last:
                return
            playable.append(
                {
                    "kind": "playable",
                    "start": self.kept[first]["pts"],
                    "end": _frame_end(self.kept, last, self.frame_ms),
                    "frames": last - first + 1,
                    "reason": "playable",
                }
            )

        for run in sorted(indexed_events, key=lambda item: item["first_pos"]):
            if run["first_pos"] > cursor:
                emit(cursor, run["first_pos"] - 1)
            cursor = max(cursor, run["last_pos"] + 1)

        if cursor < total:
            emit(cursor, total - 1)

        return playable


def detect(data):
    """Run the detector over a whole input document."""
    detector = Detector(
        data["windows"], data["frame_ms"], _parse_marked_ranges(data)
    )
    detector.feed(data.get("frames", []))
    return detector.close()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    chunk = None
    positional = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in ("--chunk", "-c"):
            if index + 1 >= len(argv):
                raise SystemExit("--chunk requires a positive frame count")
            chunk = int(argv[index + 1])
            if chunk <= 0:
                raise SystemExit("--chunk requires a positive frame count")
            index += 2
            continue
        if arg.startswith("--chunk="):
            chunk = int(arg.split("=", 1)[1])
            if chunk <= 0:
                raise SystemExit("--chunk requires a positive frame count")
            index += 1
            continue
        positional.append(arg)
        index += 1

    if len(positional) != 2:
        raise SystemExit(
            "usage: detect.py [--chunk N] input.json output.json"
        )

    with open(positional[0], encoding="utf-8") as handle:
        data = json.load(handle)

    detector = Detector(
        data["windows"], data["frame_ms"], _parse_marked_ranges(data)
    )
    frames = data.get("frames", [])

    if chunk is None:
        detector.feed(frames)
    else:
        # Reading the same frames in a different block size must not change
        # the result; the state machine carries open runs across blocks.
        for start in range(0, len(frames), chunk):
            detector.feed(frames[start:start + chunk])

    result = detector.close()

    with open(positional[1], "w", encoding="utf-8") as handle:
        json.dump(result, handle)


if __name__ == "__main__":
    main()
