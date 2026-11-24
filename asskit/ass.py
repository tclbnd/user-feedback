"""SubStation Alpha (SSA v4) and Advanced SubStation (ASS v4+) scripts: sections, styles, events, override tags."""
import re
from fractions import Fraction

TIME = re.compile(r"^(\d+):(\d{1,2}):(\d{1,2})(?:\.(\d+))?$")
# SSA v4 alignment -> numpad: 1-3 bottom, 5-7 top, 9-11 middle (4 and 8 are unused).
LEGACY = {1: 1, 2: 2, 3: 3, 5: 7, 6: 8, 7: 9, 9: 4, 10: 5, 11: 6}
BLOCK = re.compile(r"\{([^}]*)\}")
TAG = re.compile(r"\\([A-Za-z]+)([^\\]*)")

STYLE_FORMAT = {
    "ssa": ["name", "fontname", "fontsize", "primarycolour", "secondarycolour", "tertiarycolour",
            "backcolour", "bold", "italic", "borderstyle", "outline", "shadow", "alignment",
            "marginl", "marginr", "marginv", "alphalevel", "encoding"],
    "ass": ["name", "fontname", "fontsize", "primarycolour", "secondarycolour", "outlinecolour",
            "backcolour", "bold", "italic", "underline", "strikeout", "scalex", "scaley", "spacing",
            "angle", "borderstyle", "outline", "shadow", "alignment", "marginl", "marginr",
            "marginv", "encoding"],
}
EVENT_FORMAT = {
    "ssa": ["marked", "start", "end", "style", "name", "marginl", "marginr", "marginv", "effect", "text"],
    "ass": ["layer", "start", "end", "style", "name", "marginl", "marginr", "marginv", "effect", "text"],
}
STYLE_SECTIONS = {"v4 styles": "ssa", "v4+ styles": "ass", "v4 styles+": "ass"}


class AssError(ValueError):
    pass


def fields(fmt, value):
    """Split a Style/Dialogue line by `fmt`, keeping every comma in the text field."""
    parts = value.split(",", len(fmt) - 1)
    return {name: (parts[i] if i < len(parts) else "") if name == "text"
            else (parts[i] if i < len(parts) else "").strip()
            for i, name in enumerate(fmt)}


def seconds(t):
    """`H:MM:SS.cc`: the fraction is hundredths of a second, not milliseconds."""
    m = TIME.match(t.strip())
    if not m:
        raise AssError("bad time %r" % t)
    h, mi, s, frac = m.groups()
    value = Fraction((int(h) * 60 + int(mi)) * 60 + int(s))
    return value + Fraction(int(frac), 10 ** len(frac)) if frac else value


def colour(v):
    """`&HAABBGGRR` / SSA `&HBBGGRR` -> (`#rrggbb`, alpha byte)."""
    v = v.strip().rstrip("&")
    n = int(v[2:], 16) if v[:2].upper() == "&H" else int(v or "0")
    a, b, g, r = n >> 24 & 255, n >> 16 & 255, n >> 8 & 255, n & 255
    return "#%02x%02x%02x" % (r, g, b), a


def integer(v, default=0):
    """First integer in a field (`Marked=0`, `Layer: 2`, ` 3 `)."""
    m = re.search(r"-?\d+", v or "")
    return int(m.group(0)) if m else default


def numpad(v, default=2):
    n = integer(str(v), 0)
    return n if 1 <= n <= 9 else default


def legacy_alignment(v):
    """SSA v4 alignment number (1-3, 5-7, 9-11) -> numpad position, else None."""
    return LEGACY.get(integer(str(v), 0))


def fields(fmt, value):
    """Split a Style/Dialogue line by `fmt`, keeping every comma in the text field."""
    parts = value.split(",", len(fmt) - 1)
    out = {}
    for i, name in enumerate(fmt):
        part = parts[i] if i < len(parts) else ""
        out[name] = part if name == "text" else part.strip()
    return out


def text_info(raw, wrap_style):
    info = {"alignment": None, "legacy": None, "position": None, "fade": None, "karaoke": 0}
    drawing = False
    out = []
    pos = 0
    for m in BLOCK.finditer(raw):
        if not drawing:
            out.append(raw[pos:m.start()])
        for name, arg in TAG.findall(m.group(1)):
            arg = arg.strip()
            if name == "an" and arg.isdigit():
                info["alignment"] = numpad(arg)
            elif name == "a" and arg.isdigit():
                info["legacy"] = legacy_alignment(arg)
            elif name == "pos":
                x, y = arg.strip("()").split(",")[:2]
                info["position"] = [float(x), float(y)]
            elif name == "fad":
                fi, fo = arg.strip("()").split(",")[:2]
                info["fade"] = [int(fi), int(fo)]
            elif name in ("k", "kf", "ko", "K") and arg.isdigit():
                info["karaoke"] += int(arg)
            elif name == "p" and arg.isdigit():
                drawing = int(arg) > 0
        pos = m.end()
    if not drawing:
        out.append(raw[pos:])
    text = "".join(out).replace("\\h", " ")
    text = text.replace("\\n", "\n" if wrap_style == 2 else " ")
    lines = [" ".join(p.split()) for l in text.split("\\N") for p in l.split("\n")]
    return lines, info


def parse(text):
    section, info, styles, events, comments = None, {}, {}, [], 0
    formats = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if section == "script info":
            info[key.lower()] = value.strip()
        elif section in STYLE_SECTIONS:
            kind = STYLE_SECTIONS[section]
            if key == "Format":
                formats[section] = [f.strip().lower() for f in value.split(",")]
            elif key == "Style":
                d = fields(formats.get(section) or STYLE_FORMAT[kind], value)
                col, alpha = colour(d.get("primarycolour") or "&H00FFFFFF")
                if kind == "ssa":
                    alignment = legacy_alignment(d.get("alignment", "2")) or 2
                else:
                    alignment = numpad(d.get("alignment", "2"))
                styles[d.get("name") or "Default"] = {
                    "font": d.get("fontname"), "size": float(d.get("fontsize") or 0),
                    "color": col, "opacity": 255 - alpha, "alignment": alignment}
        elif section == "events":
            if key == "Format":
                formats[section] = [f.strip().lower() for f in value.split(",")]
            elif key == "Comment":
                comments += 1
            elif key == "Dialogue":
                if not formats.get(section):
                    raise AssError("no [Events] Format line")
                d = fields(formats[section], value)
                lines, tags = text_info(d.get("text", ""), integer(info.get("wrapstyle", "0")))
                style = (d.get("style") or "Default").strip().lstrip("*")
                base = styles.get(style, {}).get("alignment", 2)
                al = tags["alignment"] or tags["legacy"] or base
                events.append({"start": seconds(d.get("start", "")), "end": seconds(d.get("end", "")),
                               "style": style, "layer": integer(d.get("layer") or d.get("marked") or "0"),
                               "lines": lines, "alignment": al, "position": tags["position"],
                               "fade": tags["fade"],
                               "karaoke": str(Fraction(tags["karaoke"], 100)) if tags["karaoke"] else None})
    if "events" not in formats:
        raise AssError("no [Events] format line")
    res = [int(info[k]) if info.get(k, "").isdigit() else None for k in ("playresx", "playresy")]
    return {"script_type": info.get("scripttype"), "play_res": res, "styles": styles,
            "events": [dict(e, start=str(e["start"]), end=str(e["end"])) for e in events],
            "comments": comments, "duration": str(max((e["end"] for e in events), default=Fraction(0)))}
