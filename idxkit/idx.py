"""VobSub index files (.idx): header settings, palette, languages, timestamps with delays."""
import re
from fractions import Fraction

# The final timestamp field is milliseconds (normally three digits, 000-999).
TIMESTAMP = re.compile(
    r"^timestamp:\s*([+-]?)(\d+):(\d+):(\d+):(\d+)\s*,\s*"
    r"filepos:\s*(?:0x)?([0-9a-f]+)\s*$",
    re.IGNORECASE,
)
DELAY = re.compile(
    r"^delay:\s*([+-]?)(\d+):(\d+):(\d+):(\d+)\s*$",
    re.IGNORECASE,
)
ID_INDEX = re.compile(r"index:\s*(\d+)", re.IGNORECASE)
CUSTOM_COLORS = re.compile(r"colors:\s*([0-9a-f,\s]+?)\s*$", re.IGNORECASE)


class IdxError(ValueError):
    pass


def to_ms(sign, hours, minutes, seconds, millis):
    """Convert a VobSub ``hh:mm:ss:mmm`` field tuple to milliseconds.

    The last field is already in milliseconds, so it is taken as-is. A leading
    minus sign negates the whole value (used by ``delay`` lines).
    """
    value = ((int(hours) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(millis)
    return -value if sign == "-" else value


def parse(text):
    # Tolerate a UTF-8 BOM (some Windows rippers save one) and any line ending.
    text = text.lstrip("\ufeff")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines or not lines[0].strip().startswith("# VobSub index file"):
        raise IdxError("not a VobSub index file")
    head = lines[0]
    m = re.search(r"v(\d+)", head)
    out = {"version": int(m.group(1)) if m else None, "size": None, "origin": [0, 0], "scale": [100, 100],
           "alpha": 100, "palette": [], "custom_colors": None, "forced": False, "default_language": None, "languages": []}
    cur = None
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
            # Only an explicit ON switches custom colours on; OFF (or anything
            # unrecognised) leaves custom_colors as None.
            if value.split(",")[0].strip().upper() == "ON":
                cols = CUSTOM_COLORS.search(value)
                out["custom_colors"] = (
                    ["#" + c.strip().lower().zfill(6)
                     for c in cols.group(1).split(",") if c.strip()]
                    if cols else []
                )
        elif key == "time offset":
            offset = int(value)                                  # milliseconds added to every timestamp
        elif key == "forced subs":
            out["forced"] = value.upper() == "ON"
        elif key == "langidx":
            out["default_language"] = int(value)
        elif key == "id":
            code, _, rest = value.partition(",")
            idx = ID_INDEX.search(rest)
            cur = {"id": code.strip(),
                   "index": int(idx.group(1)) if idx else len(out["languages"]),
                   "subtitles": 0, "first": None, "last": None,
                   "delays": 0, "backwards": 0, "_delay": 0}
            out["languages"].append(cur)
        elif key == "delay":
            d = DELAY.match(line)
            if not d:
                raise IdxError("bad delay line %r" % line[:40])
            if cur is not None:
                # Delays accumulate within one language stream and shift every
                # timestamp that follows them (negative delays included).
                cur["_delay"] += to_ms(*d.groups())
                cur["delays"] += 1
        elif key == "timestamp":
            t = TIMESTAMP.match(line)
            if not t or cur is None:
                raise IdxError("bad timestamp line %r" % line[:40])
            sign, h, mi, s, millis, _pos = t.groups()
            at = to_ms(sign, h, mi, s, millis) + cur["_delay"] + offset
            if cur["last"] is not None and at < cur["last"]:
                cur["backwards"] += 1
            cur["subtitles"] += 1
            cur["first"] = at if cur["first"] is None else cur["first"]
            cur["last"] = at
    for lang in out["languages"]:
        lang.pop("_delay", None)
        for k in ("first", "last"):
            if lang[k] is not None:
                lang[k] = str(Fraction(lang[k], 1000))
    return out
