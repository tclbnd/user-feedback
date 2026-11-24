"""SubStation Alpha (SSA v4) and Advanced SubStation (ASS v4+) scripts: sections, styles, events, override tags."""
import re
from fractions import Fraction

TIME = re.compile(r"^(\d+):(\d{2}):(\d{2})\.(\d+)$")
# SSA v4 alignment numbering -> ASS (numpad) numbering. 1-9 coincide with
# the numpad layout; 10 and 11 are the extra non-wrapping middle positions.
LEGACY = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8, 9: 9, 10: 4, 11: 5}
BLOCK = re.compile(r"\{([^}]*)\}")
TAG = re.compile(r"\\([a-zA-Z]+)([^\\]*)")
KARAOKE = {"k", "K", "kf", "ko"}


class AssError(ValueError):
    pass


def seconds(t):
    m = TIME.match(t.strip())
    if not m:
        raise AssError("bad time %r" % t)
    h, mi, s, frac = m.groups()
    # The spec uses centiseconds (two digits). Accept 1/3-digit fractions too,
    # scaling by their width instead of misreading them as centiseconds.
    scale = 10 ** len(frac)
    return Fraction(((int(h) * 60 + int(mi)) * 60 + int(s)) * scale + int(frac), scale)


def colour(v):
    v = v.strip()
    n = int(v[2:].rstrip("&"), 16) if v.upper().startswith("&H") else int(v)
    # SSA/ASS colours are &HAABBGGRR: the bytes are alpha, blue, green, red.
    a, b, g, r = n >> 24 & 255, n >> 16 & 255, n >> 8 & 255, n & 255
    return "#%02x%02x%02x" % (r, g, b), 255 - a


def split_fields(value, fields):
    """Split a Style/Dialogue line into a dict keyed by its Format names.

    Only the final field (the text) may contain commas, so it receives the
    remainder of the line; every other field is stripped of whitespace.
    """
    vals = value.split(",", len(fields) - 1) if fields else [value]
    return {name: (val.strip() if i < len(fields) - 1 else val)
            for i, (name, val) in enumerate(zip(fields, vals))}


def text_info(raw, wrap_style):
    info = {"alignment": None, "legacy": None, "position": None, "fade": None, "karaoke": 0}
    drawing = False
    out = []
    pos = 0
    for m in BLOCK.finditer(raw):
        if not drawing:
            out.append(raw[pos:m.start()])
        for tm in TAG.finditer(m.group(1)):
            name, arg = tm.group(1).lower(), tm.group(2).strip()
            if name == "an" and arg.isdigit():
                info["alignment"] = int(arg)
            elif name == "a" and arg.isdigit():
                info["legacy"] = int(arg)
            elif name == "pos":
                x, y = arg.strip("()").split(",")[:2]
                info["position"] = [float(x), float(y)]
            elif name == "fad":
                fi, fo = arg.strip("()").split(",")[:2]
                info["fade"] = [int(fi), int(fo)]
            elif name in KARAOKE and arg.isdigit():
                info["karaoke"] += int(arg)
            elif name == "p" and arg.isdigit():
                drawing = int(arg) > 0
        pos = m.end()
    if not drawing:
        out.append(raw[pos:])
    text = "".join(out).replace("\\h", " ")
    # \n is a line break only under WrapStyle 2, otherwise a space.
    text = text.replace("\\n", "\n" if wrap_style == 2 else " ")
    # Split at line breaks before collapsing runs, or str.split() would
    # swallow the newlines and join the lines back together with a space.
    lines = []
    for hard in text.split("\\N"):
        lines.extend(" ".join(run.split()) for run in hard.split("\n"))
    return lines, info


def parse(text):
    section, info, styles, events, comments = None, {}, {}, [], 0
    fmt = {}
    legacy_styles = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            legacy_styles = section == "v4 styles"
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if section == "script info":
            info[key.lower()] = value.strip()
        elif section in ("v4+ styles", "v4 styles"):
            if key == "Format":
                fmt["style"] = [f.strip().lower() for f in value.split(",")]
            elif key == "Style":
                # Keyed by the Format line, so reordered style fields still map
                # to the right meaning and only the text-like tail may keep commas.
                d = split_fields(value, fmt.get("style", []))
                al = int(d.get("alignment", "2"))
                if legacy_styles:
                    al = LEGACY.get(al, al)
                col, alpha = colour(d.get("primarycolour", "&H00FFFFFF"))
                styles[d.get("name", "Default")] = {"font": d.get("fontname"), "size": float(d.get("fontsize", "0")),
                                                     "color": col, "opacity": alpha, "alignment": al}
        elif section == "events":
            if key == "Format":
                fmt["event"] = [f.strip().lower() for f in value.split(",")]
            elif key == "Comment":
                comments += 1
            elif key == "Dialogue":
                # Keyed by the Format line, so non-standard field orders and
                # SSA's Marked field map correctly and the text keeps commas.
                d = split_fields(value, fmt.get("event", []))
                lines, tags = text_info(d.get("text", ""), int(info.get("wrapstyle", "0") or 0))
                style = d.get("style", "Default").lstrip("*")
                base = styles.get(style, {"alignment": 2})["alignment"]
                if tags["alignment"] is not None:
                    al = tags["alignment"]
                elif tags["legacy"] is not None:
                    al = LEGACY.get(tags["legacy"], base)
                else:
                    al = base
                events.append({"start": seconds(d["start"]), "end": seconds(d["end"]), "style": style,
                               "layer": int(d.get("layer", "0") or 0), "lines": lines, "alignment": al,
                               "position": tags["position"], "fade": tags["fade"],
                               "karaoke": str(Fraction(tags["karaoke"], 100)) if tags["karaoke"] else None})
    if "event" not in fmt:
        raise AssError("no [Events] format line")
    res = [int(info[k]) if info.get(k, "").isdigit() else None for k in ("playresx", "playresy")]
    return {"script_type": info.get("scripttype"), "play_res": res, "styles": styles,
            "events": [dict(e, start=str(e["start"]), end=str(e["end"])) for e in events],
            "comments": comments, "duration": str(max((e["end"] for e in events), default=Fraction(0)))}
