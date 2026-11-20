"""WebVTT parsing."""
import re

_TS = re.compile(r"^(\d{2}):(\d{2}):(\d{2})\.(\d{3})$")
_TAG = re.compile(r"<[^>]+>")


class VttError(ValueError):
    pass


def timestamp(text):
    m = _TS.match(text.strip())
    if not m:
        raise VttError(f"bad timestamp {text!r}")
    h, mi, s, ms = map(int, m.groups())
    return ((h * 60 + mi) * 60 + s) * 1000 + ms


def settings(text):
    out = {}
    for item in text.split():
        name, _, value = item.partition(":")
        out[name] = value
    return out


def cue_text(raw):
    return _TAG.sub("", raw)


def parse(text):
    lines = text.split("\n")
    if not lines or lines[0].strip() != "WEBVTT":
        raise VttError("missing WEBVTT header")
    blocks, cur = [], []
    for line in lines[1:]:
        if line == "":
            if cur:
                blocks.append(cur)
                cur = []
        else:
            cur.append(line)
    if cur:
        blocks.append(cur)
    cues = []
    for block in blocks:
        ident = None
        if "-->" not in block[0]:
            ident, block = block[0], block[1:]
        if not block:
            continue
        left, _, right = block[0].partition("-->")
        end, _, rest = right.strip().partition(" ")
        cues.append({
            "id": ident,
            "start": timestamp(left),
            "end": timestamp(end),
            "settings": settings(rest),
            "voice": None,
            "text": cue_text("\n".join(block[1:])),
        })
    return cues
