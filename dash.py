#!/usr/bin/env python3
"""Build a real-time segment map for a multi-period DASH local MPD.

The input subset is described in README.md: ``periods`` in play order, each
with an optional ``duration`` in seconds, and tracks whose ``timeline`` is a
SegmentTimeline subset ({t?, d, r?}) expressed in ``timescale`` ticks with a
``presentationTimeOffset``.

Rules implemented here:

* media ticks map to real seconds as ``period_start + (m - pto) / timescale``;
* ``t`` defaults to the running end of the previous entry, and to 0 for the
  first entry;
* ``r >= 0`` means ``r + 1`` segments, ``r == -1`` repeats until the next
  entry's ``t`` boundary or, when last, the period's own duration; repeats
  are limited by segment *starts* staying below that bound, so the final
  segment may overrun it;
* a period's start is the running sum of earlier durations; a period with no
  duration extends to its last segment end;
* segments are clipped by start only: a segment may overrun the period end as
  long as it started before it;
* segment intervals are half-open ``[start, end)``; a seek takes the segment
  with the greatest start that contains the time, so a shared boundary lands
  on the next period rather than replaying the previous tail, and reports a
  gap when the owning track has no segment covering the time.
"""
import json
import math
import sys


def r6(value):
    """Round to the 6 decimals required by the output contract."""
    v = round(value, 6)
    return 0.0 if v == 0 else v


def _ceil_div(a, b):
    """Smallest integer >= a/b for positive b, tolerant of float fuzz."""
    if isinstance(a, int) and isinstance(b, int):
        return -((-a) // b)
    # 3.6000000000000005 / 1.2 is 3.0000000000000004 in binary floating point;
    # the tiny tolerance keeps such exact divisions from gaining a segment.
    return int(math.ceil(a / b - 1e-9))


def _repeat_count(t, d, bound):
    """Number of segments for ``r == -1`` starting at media tick ``t``.

    A segment is emitted while its start stays strictly below ``bound``; the
    last one is allowed to run past it.  With no usable bound the entry is
    emitted once, since there is nothing to repeat against.
    """
    if bound is None or d <= 0:
        return 1
    span = bound - t
    if span <= 0:
        return 0
    return max(1, _ceil_div(span, d))


def expand(timeline, timescale, pto, pstart, pend):
    """Expand a SegmentTimeline into real-time half-open segment intervals.

    ``pstart`` and ``pend`` are real seconds, ``pend`` being None for a
    period with an open tail.
    """
    if timescale <= 0:
        timescale = 1
    segs = []
    cur = 0
    last = len(timeline) - 1
    for i, e in enumerate(timeline):
        t = e["t"] if e.get("t") is not None else cur
        d = e["d"]
        r = e.get("r")
        if r is None:
            r = 0

        if r == -1:
            # Repeat until the next entry's t boundary.  A next entry without
            # t starts at this repeat's running end and defines no boundary,
            # so the repeat collapses to a single segment; for the last entry
            # the period's own duration is the boundary.
            nxt = timeline[i + 1] if i < last else None
            if nxt is not None:
                bound = nxt.get("t")
            elif pend is not None:
                # The period end expressed in this track's media ticks.
                bound = pto + (pend - pstart) * timescale
            else:
                bound = None
            count = _repeat_count(t, d, bound)
        elif r < -1:
            # Documented as invalid input that will not appear; treat it as
            # the single segment form rather than looping forever.
            count = 1
        else:
            count = r + 1

        # The running end for the next defaulted ``t`` is defined by the
        # entry itself, independent of the period-end clipping below.
        cur = t + count * d

        if pend is not None and d > 0:
            # A segment survives only when its start is below the period end,
            # so there is no point materialising later ones (this also bounds
            # huge repeat counts).  The extra slot keeps the overrun segment.
            limit = pto + (pend - pstart) * timescale
            if limit <= t:
                count = 0
            else:
                count = min(count, max(1, _ceil_div(limit - t, d)))

        for k in range(count):
            m0 = t + k * d
            m1 = m0 + d
            start = r6(pstart + (m0 - pto) / timescale)
            end = r6(pstart + (m1 - pto) / timescale)
            # Clip by start only: an overrunning segment is kept when it
            # starts before the period end.  The comparison uses the rounded
            # value so the emitted map never has a start at/after the end.
            if pend is not None and start >= pend:
                continue
            segs.append({"n": 0, "start": start, "end": end})
    return segs


def resolve(segments, t):
    """Index of the segment whose half-open interval contains ``t``.

    Where two intervals overlap (a period's last segment may overrun into
    the next period's first one), the segment with the greatest start wins,
    so a seek exactly on the shared boundary lands on the following period's
    segment instead of replaying the previous period's tail.
    """
    hit = None
    best = None
    for seg in segments:
        if seg["start"] <= t < seg["end"]:
            key = (seg["start"], seg["n"])
            if best is None or key >= best:
                best = key
                hit = seg["n"]
    return hit


def main():
    with open(sys.argv[1]) as f:
        data = json.load(f)

    tracks = {}
    periods = []
    pstart = 0.0

    for p in data["periods"]:
        duration = p.get("duration")
        pend = r6(pstart + duration) if duration is not None else None

        period_end = pstart
        for tr in p["tracks"]:
            segs = expand(tr["timeline"], tr["timescale"], tr["pto"],
                          pstart, pend)
            entry = tracks.setdefault(tr["id"], {"segments": []})
            for seg in segs:
                seg["n"] = len(entry["segments"])
                entry["segments"].append(seg)
                if seg["end"] > period_end:
                    period_end = seg["end"]

        if pend is not None:
            period_end = pend
        period_end = r6(period_end)
        periods.append({"id": p["id"], "start": r6(pstart), "end": period_end})
        # Chain the rounded end so consecutive periods share an exact
        # boundary instead of drifting by accumulated float error.
        pstart = period_end

    seeks = []
    for s in data.get("seeks", []):
        track = tracks.get(s["track"])
        n = resolve(track["segments"], s["t"]) if track is not None else None
        if n is None:
            # Time outside every segment of the requested track is a gap and
            # is reported in place of an index.
            seeks.append({"t": s["t"], "track": s["track"], "gap": True})
        else:
            seeks.append({"t": s["t"], "track": s["track"], "n": n})

    with open(sys.argv[2], "w") as f:
        json.dump({"tracks": tracks, "periods": periods, "seeks": seeks},
                  f, indent=1)


main()


