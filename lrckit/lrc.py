"""LRC lyric files: ID tags, time tags (several per line), offset, enhanced word timing."""
import re
from fractions import Fraction

TIME = re.compile(r"\[(\d+):(\d{1,2})(?:[.:](\d{1,3}))?\]")
WORD = re.compile(r"<(\d+):(\d{1,2})(?:[.:](\d{1,3}))?>")
ID = re.compile(r"^\[([A-Za-z#]+):(.*)\]$")


class LrcError(ValueError):
    pass


def seconds(mm, ss, frac):
    t = Fraction(int(mm) * 60 + int(ss))
    if frac:
        t += Fraction(int(frac), 10 ** len(frac))
    return t


def parse(text):
    tags = {}
    offset = 0
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        times, p = [], 0
        while True:
            m = TIME.match(line, p)
            if not m:
                break
            times.append(seconds(*m.groups()))
            p = m.end()
        if times:
            body = line[p:]
            words = [seconds(*w.groups()) for w in WORD.finditer(body)]
            plain = " ".join(WORD.sub(" ", body).split())
            for t in times:
                lines.append((t, plain, words))
            continue
        m = ID.match(line)
        if m:
            key, value = m[1].lower(), m[2].strip()
            if key == "offset":
                try:
                    offset = int(value)
                except ValueError:
                    raise LrcError("bad offset %r" % value)
            else:
                tags[key] = value or None
    if not lines:
        raise LrcError("no timed lyric lines")
    shift = Fraction(offset, 1000)
    lines.sort(key=lambda x: x[0])
    out_lines = [{"time": str(max(t - shift, 0)), "text": txt or None,
                  "words": [str(max(w - shift, 0)) for w in words]} for t, txt, words in lines]
    length = None
    if tags.get("length"):
        m = re.match(r"^(\d+):(\d{1,2})(?:\.(\d{1,3}))?$", tags["length"])
        length = str(seconds(m[1], m[2], m[3])) if m else None
    return {"title": tags.get("ti"), "artist": tags.get("ar"), "album": tags.get("al"), "author": tags.get("au"),
            "created_by": tags.get("by"), "length": length, "offset_ms": offset,
            "lines": out_lines, "blank_lines": sum(1 for l in out_lines if l["text"] is None),
            "enhanced": any(l["words"] for l in out_lines)}
