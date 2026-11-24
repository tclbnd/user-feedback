"""VobSub index files (.idx): header settings, palette, languages, timestamps with delays."""
import re
from fractions import Fraction

TIMESTAMP = re.compile(r"^timestamp:\s*(-?)(\d+):(\d+):(\d+):(\d+),\s*filepos:\s*([0-9A-Fa-f]+)\s*$")
DELAY = re.compile(r"^delay:\s*()(\d+):(\d+):(\d+):(\d+)\s*$")


class IdxError(ValueError):
    pass


def ms(sign, h, m, s, f):
    v = ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(f) * 10
    return -v if sign else v


def parse(text):
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines or not lines[0].startswith("# VobSub index file"):
        raise IdxError("not a VobSub index file")
    head = lines[0]
    m = re.search(r"v(\d+)", head)
    out = {"version": int(m.group(1)) if m else None, "size": None, "origin": [0, 0], "scale": [100, 100],
           "alpha": 100, "palette": [], "custom_colors": None, "forced": False, "default_language": None, "languages": []}
    cur = None
    delay = 0
    offset = 0
    for raw in lines[1:]:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "size":
            w, h = value.lower().split("x")
            out["size"] = [int(w), int(h)]
        elif key == "org":
            out["origin"] = [int(v) for v in value.split(",")]
        elif key == "scale":
            out["scale"] = [int(v.strip().rstrip("%")) for v in value.split(",")]
        elif key == "alpha":
            out["alpha"] = int(value.rstrip("%"))
        elif key == "palette":
            out["palette"] = ["#" + v.strip().lower().zfill(6) for v in value.split(",")]
        elif key == "custom colors":
            first = value.split(",")[0].strip().upper()
            if first in ("ON", "OFF"):
                cols = re.search(r"colors:\s*([0-9A-Fa-f,\s]+)", value)
                out["custom_colors"] = ["#" + c.strip().lower().zfill(6) for c in cols.group(1).split(",") if c.strip()] if cols else []
        elif key == "time offset":
            offset = int(value)                                  # milliseconds added to every timestamp
        elif key == "forced subs":
            out["forced"] = value.upper() == "ON"
        elif key == "langidx":
            out["default_language"] = int(value)
        elif key == "id":
            code, _, rest = value.partition(",")
            idx = re.search(r"index:\s*(\d+)", rest)
            cur = {"id": code.strip(), "index": len(out["languages"]),
                   "subtitles": 0, "first": None, "last": None, "delays": 0, "backwards": 0}
            out["languages"].append(cur)
        elif key == "delay":
            d = DELAY.match(line)
            if not d:
                raise IdxError("bad delay line %r" % line[:40])
            pass
            if cur is not None:
                cur["delays"] += 1
        elif key == "timestamp":
            t = TIMESTAMP.match(line)
            if not t or cur is None:
                raise IdxError("bad timestamp line %r" % line[:40])
            sign, h, mi, s, f, _pos = t.groups()
            at = ms(sign, h, mi, s, f) + delay + offset
            if cur["last"] is not None and at < cur["last"]:
                cur["backwards"] += 1
            cur["subtitles"] += 1
            cur["first"] = at if cur["first"] is None else cur["first"]
            cur["last"] = at
    for lang in out["languages"]:
        for k in ("first", "last"):
            if lang[k] is not None:
                lang[k] = str(Fraction(lang[k], 1000))
    return out
