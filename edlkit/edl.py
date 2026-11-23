"""CMX 3600 edit decision lists: events, transitions, motion effects, timecode."""
import re
from fractions import Fraction

TC = re.compile(r"^(\d{2}):(\d{2}):(\d{2})([:;.,])(\d{2})$")

CLIP_NAME = re.compile(r"^\*\s*(?:(FROM|TO)\s+)?CLIP\s+NAME:\s*(\S.*?)\s*$", re.IGNORECASE)

BLACK = frozenset(("BL", "BLK", "BLACK"))
AUX = frozenset(("AX",))

# CMX 3600 track codes and the tracks each event sits on.
TRACKS = {
    "V": ["V"],
    "A": ["A"],
    "A2": ["A2"],
    "A3": ["A3"],
    "A4": ["A4"],
    "AA": ["A", "A2"],
    "B": ["V", "A"],
    "AA/V": ["V", "A", "A2"],
}


class EdlError(ValueError):
    pass


def frames(tc, fps=30, drop=False):
    """Frame count from 00:00:00:00 for a timecode, honouring the FCM mode."""
    m = TC.match(tc)
    if not m:
        raise EdlError("bad timecode %r" % tc)
    hh, mm, ss, ff = int(m[1]), int(m[2]), int(m[3]), int(m[5])
    if mm > 59 or ss > 59 or ff >= fps:
        raise EdlError("bad timecode %r" % tc)
    total = ((hh * 60 + mm) * 60 + ss) * fps + ff
    if drop and fps == 30:
        # Drop frame: frame numbers 00 and 01 are skipped every minute,
        # except on every tenth minute.
        minutes = hh * 60 + mm
        total -= 2 * (minutes - minutes // 10)
    return total


def _event(fields, fps, drop):
    """One event line: number, reel, track, transition, four timecodes."""
    trans = fields[3].upper()
    kind = trans[0]
    if kind not in ("C", "D", "W"):
        raise EdlError("bad transition %r" % fields[3])
    tframes = 0
    tcs = fields[4:8]
    if kind == "D" and trans[1:].isdigit():  # D030 style
        tframes = int(trans[1:])
    elif kind in ("D", "W"):  # D 030 / W022 030 style (Wnnn carries the wipe code)
        if len(fields) > 4 and fields[4].isdigit():
            tframes = int(fields[4])
            tcs = fields[5:9]
    if len(tcs) != 4:
        raise EdlError("bad event line %r" % " ".join(fields))
    si, so, ri, ro = (frames(x, fps, drop) for x in tcs)
    track = fields[2].upper()
    return {"event": int(fields[0]), "reel": fields[1],
            "tracks": TRACKS.get(track, [track]), "transition": kind,
            "transition_frames": tframes, "source_in": si, "source_out": so,
            "record_in": ri, "record_out": ro, "clip": None, "speed": None,
            "source_frames": so - si, "duration": ro - ri,
            "from_reel": None, "from_clip": None,
            "_names": {"from": None, "to": None}}


def _merge(raw):
    """Fold the two lines of a dissolve or wipe into a single event."""
    events = []
    for e in raw:
        if (events and e["transition"] in ("D", "W")
                and e["event"] == events[-1]["event"]
                and events[-1]["from_reel"] is None):
            out = events[-1]
            inc, outn = e["_names"], out["_names"]
            e["from_reel"] = out["reel"]
            e["from_clip"] = (inc["from"] if inc["to"] else None) or outn["from"]
            e["clip"] = inc["to"] or inc["from"] or outn["to"]
            events[-1] = e
        else:
            if e["transition"] == "C":
                e["clip"] = e["_names"]["from"] or e["_names"]["to"]
            events.append(e)
    for e in events:
        del e["_names"]
    return events


def parse(text, fps=30):
    rate = Fraction(30000, 1001) if fps == 30 else Fraction(fps)
    drop = False
    title = None
    raw = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = line.strip()
        if not line:
            continue
        upper = line.upper()
        if upper.startswith("FCM:"):
            drop = "DROP" in upper and "NON-DROP" not in upper
            continue
        if upper.startswith("TITLE:"):
            title = line[6:].strip() or None
            continue
        m = CLIP_NAME.match(line)
        if m:
            if raw:
                raw[-1]["_names"][(m.group(1) or "from").lower()] = m.group(2)
            continue
        if line.startswith("*"):
            continue
        fields = line.split()
        if upper.startswith("M2"):
            if raw and len(fields) >= 3:
                try:
                    raw[-1]["speed"] = Fraction(fields[2])
                except (ValueError, ZeroDivisionError):
                    raise EdlError("bad motion effect %r" % line)
            continue
        if not fields[0].isdigit():
            continue
        raw.append(_event(fields, fps, drop))
    events = _merge(raw)
    for e in events:
        if e["speed"] is not None:
            # Source frames the motion effect consumes at its own rate.
            e["source_frames"] = int(round(e["duration"] * e["speed"] / rate))
            e["speed"] = str(e["speed"])
    events.sort(key=lambda e: e["record_in"])
    video = [e for e in events if "V" in e["tracks"]]
    gaps, overlaps = [], []
    for a, b in zip(video, video[1:]):
        if b["record_in"] > a["record_out"]:
            gaps.append([a["record_out"], b["record_in"]])
        elif b["record_in"] < a["record_out"]:
            overlaps.append([b["record_in"], a["record_out"]])
    counts = {}
    for e in events:
        for t in e["tracks"]:
            counts[t] = counts.get(t, 0) + 1
    reels = sorted({r for e in events for r in (e["reel"], e["from_reel"])
                    if r and r.upper() not in BLACK and r.upper() not in AUX})
    black = [e["event"] for e in events
             if e["reel"].upper() in BLACK or (e["from_reel"] or "").upper() in BLACK]
    start = min((e["record_in"] for e in events), default=0)
    end = max((e["record_out"] for e in events), default=0)
    return {"title": title, "events": events, "tracks": dict(sorted(counts.items())),
            "video_gaps": gaps, "video_overlaps": overlaps, "reels": reels,
            "black_events": black, "record_frames": end - start,
            "record_seconds": str(Fraction(end - start) / rate)}
