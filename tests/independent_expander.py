# -*- coding: utf-8 -*-
"""Independent interval expander used only by the acceptance tests.

Deliberately self contained: it imports neither vttcutter nor cut_vtt, and its
parsing/expansion logic is written independently.  It produces the expected
cue intervals (in milliseconds) and expected inline marker times by applying
the same published rules with exact rational arithmetic:

  * source frame f covers [f*T, (f+1)*T), T = 1001/30000 s
  * output time = clip.out_start + (src - clip.src_start) / speed
  * intersection of every cue with every clip of the same source
  * half-up millisecond rounding; zero length cues after quantisation drop
  * inline markers survive only when strictly inside the quantised cue and
    remain strictly increasing (first one wins on a tie)
"""
import json
import re
from fractions import Fraction
from pathlib import Path

TICK = Fraction(1001, 30000)
TS_HOUR = re.compile(r"^(\d+):(\d{1,2}):(\d{2})\.(\d{3})$")
TS_MIN = re.compile(r"^(\d{1,2}):(\d{2})\.(\d{3})$")
TIMING = re.compile(
    r"((?:\d+:)?\d{1,2}:\d{2}\.\d{3})\s*-->\s*"
    r"((?:\d+:)?\d{1,2}:\d{2}\.\d{3})(?:[ \t](.*))?$"
)
MARK = re.compile(r"<((?:\d+:)?\d{1,2}:\d{2}\.\d{3})>")


def ts(text):
    mh = TS_HOUR.match(text)
    if mh is not None:
        h, mi, se, ms = (int(mh.group(1)), int(mh.group(2)),
                         int(mh.group(3)), int(mh.group(4)))
    else:
        mm = TS_MIN.match(text)
        h, mi, se, ms = 0, int(mm.group(1)), int(mm.group(2)), \
            int(mm.group(3))
    return Fraction(h * 3600 + mi * 60 + se) + Fraction(ms, 1000)


def ms(value):
    z = value * 1000 + Fraction(1, 2)
    return z.numerator // z.denominator


def parse_cues(path):
    """Return list of dicts: id, line, start, end, settings, markers, body."""
    raw = Path(path).read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    blocks = re.split(r"\n[ \t]*\n", raw.strip())
    cues = []
    line_cursor = 1
    for block in blocks:
        rows = block.split("\n")
        timing_at = None
        for i, row in enumerate(rows):
            if TIMING.match(row.strip()):
                timing_at = i
                break
        if timing_at is None:
            line_cursor += len(rows) + 1
            continue
        ident = rows[timing_at - 1].strip() if timing_at > 0 else None
        tm = TIMING.match(rows[timing_at].strip())
        body = "\n".join(rows[timing_at + 1:])
        markers = [ts(x.group(1)) for x in MARK.finditer(body)]
        # find the 1-based line number of the timing row
        pos = raw.index(block)
        line = raw[:pos].count("\n") + 1 + timing_at
        cues.append(dict(
            id=ident,
            line=line,
            start=ts(tm.group(1)),
            end=ts(tm.group(2)),
            settings=tm.group(3) or "",
            markers=markers,
            body=body,
        ))
        line_cursor += len(rows) + 1
    return cues


def expand(edl_path):
    edl = json.loads(Path(edl_path).read_text(encoding="utf-8"))
    base = Path(edl_path).parent
    by_source = {}
    for label, rel in edl["sources"].items():
        by_source[label] = parse_cues(base / rel)

    clips = []
    cursor = Fraction(0)
    for raw in edl["clips"]:
        speed = Fraction(*raw.get("speed", [1, 1]))
        s0 = raw["start_frame"] * TICK
        s1 = raw["end_frame"] * TICK
        o0 = cursor
        o1 = cursor + (s1 - s0) / speed
        clips.append(dict(
            id=raw["id"], source=raw["source"],
            sf=raw["start_frame"], ef=raw["end_frame"],
            speed=speed, s0=s0, s1=s1, o0=o0, o1=o1,
        ))
        cursor = o1

    expected = []
    for clip in clips:
        def out_t(t):
            return clip["o0"] + (t - clip["s0"]) / clip["speed"]

        for cue in by_source[clip["source"]]:
            lo = max(cue["start"], clip["s0"])
            hi = min(cue["end"], clip["s1"])
            if lo >= hi:
                continue
            a = ms(out_t(lo))
            b = ms(out_t(hi))
            if a >= b:
                continue
            markers = []
            for t in cue["markers"]:
                if not (lo < t < hi):
                    continue
                m = ms(out_t(t))
                if not (a < m < b):
                    continue
                if markers and m == markers[-1]:
                    continue
                markers.append(m)
            expected.append(dict(
                clip=clip["id"], source=clip["source"],
                source_id=cue["id"], line=cue["line"],
                start_ms=a, end_ms=b, markers=markers,
            ))
    expected.sort(key=lambda r: (r["start_ms"],
                                 [c["id"] for c in clips].index(r["clip"]),
                                 r["line"]))
    return clips, expected


def visible_at(clips, expected, play_ms):
    """Half open visibility [start_ms, end_ms) at a playback instant."""
    return [r for r in expected
            if r["start_ms"] <= play_ms < r["end_ms"]]


def frame_instant(clips, clip_serial, offset_frames):
    """Playback time of frame (clip start frame + offset) in ms, half up."""
    clip = clips[clip_serial]
    t = clip["o0"] + offset_frames * TICK / clip["speed"]
    return ms(t)
