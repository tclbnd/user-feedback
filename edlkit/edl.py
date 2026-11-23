"""CMX 3600 edit decision lists: events, transitions, motion effects, timecode."""
import re
from fractions import Fraction

TC = re.compile(r"^(\d{2}):(\d{2}):(\d{2})([:;.,])(\d{2})$")


class EdlError(ValueError):
    pass


def frames(tc):
    m = TC.match(tc)
    if not m:
        raise EdlError("bad timecode %r" % tc)
    hh, mm, ss, ff = int(m[1]), int(m[2]), int(m[3]), int(m[5])
    return ((hh * 60 + mm) * 60 + ss) * 30 + ff


def parse(text, fps=30):
    rate = Fraction(30000, 1001) if fps == 30 else Fraction(fps)
    title = None
    events = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line or line.startswith("*") or line.startswith("FCM:") or line.startswith("M2"):
            continue
        if line.startswith("TITLE:"):
            title = line[6:].strip() or None
            continue
        f = line.split()
        if not re.match(r"^\d{3}", f[0]):
            continue
        si, so, ri, ro = (frames(x) for x in f[4:8])
        events.append({"event": int(f[0]), "reel": f[1], "tracks": [f[2]], "transition": f[3][0],
                       "transition_frames": 0, "source_in": si, "source_out": so, "record_in": ri,
                       "record_out": ro, "clip": None, "speed": None, "source_frames": so - si,
                       "duration": ro - ri, "from_reel": None, "from_clip": None})
    events.sort(key=lambda e: e["record_in"])
    gaps = []
    video = [e for e in events if "V" in e["tracks"]]
    for a, b in zip(video, video[1:]):
        if b["record_in"] > a["record_out"]:
            gaps.append([a["record_out"], b["record_in"]])
    counts = {}
    for e in events:
        for t in e["tracks"]:
            counts[t] = counts.get(t, 0) + 1
    start = min((e["record_in"] for e in events), default=0)
    end = max((e["record_out"] for e in events), default=0)
    return {"title": title, "events": events, "tracks": dict(sorted(counts.items())), "video_gaps": gaps,
            "record_frames": end - start, "record_seconds": str((end - start) / rate)}
