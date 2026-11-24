"""SubStation Alpha (SSA v4) and Advanced SubStation (ASS v4+) scripts: sections, styles, events, override tags."""
import re
from fractions import Fraction

TIME = re.compile(r"^(\d+):(\d{2}):(\d{2})\.(\d{2})$")
LEGACY = {1: 1, 2: 2, 3: 3, 5: 7, 6: 8, 7: 9, 9: 4, 10: 5, 11: 6}     # SSA alignment -> numpad
BLOCK = re.compile(r"\{([^}]*)\}")


class AssError(ValueError):
    pass


def seconds(t):
    m = TIME.match(t.strip())
    if not m:
        raise AssError("bad time %r" % t)
    h, mi, s, cs = (int(x) for x in m.groups())
    return Fraction(((h * 60 + mi) * 60 + s) * 1000 + cs, 1000)


def colour(v):
    v = v.strip()
    n = int(v[2:].rstrip("&"), 16) if v.upper().startswith("&H") else int(v)
    a, r, g, b = n >> 24 & 255, n >> 16 & 255, n >> 8 & 255, n & 255
    return "#%02x%02x%02x" % (r, g, b), 255 - a


def text_info(raw, wrap_style):
    info = {"alignment": None, "legacy": None, "position": None, "fade": None, "karaoke": 0}
    drawing = False
    out = []
    pos = 0
    for m in BLOCK.finditer(raw):
        if not drawing:
            out.append(raw[pos:m.start()])
        for tag in re.findall(r"\\([a-z]+)([^\\]*)", m.group(1)):
            name, arg = tag[0], tag[1].strip()
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
            elif name in ("k", "kf", "ko", "K") and arg.isdigit():
                info["karaoke"] += int(arg)
            elif name == "p" and arg.isdigit():
                drawing = int(arg) > 0
        pos = m.end()
    if not drawing:
        out.append(raw[pos:])
    text = "".join(out).replace("\\h", " ")
    text = text.replace("\\n", "\n" if wrap_style == 2 else " ")
    lines = [" ".join(l.split()) for l in text.split("\\N")]
    lines = [p for l in lines for p in l.split("\n")]
    return lines, info


def parse(text):
    section, info, styles, events, comments = None, {}, {}, [], 0
    fmt = {}
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
        elif section in ("v4+ styles", "v4 styles", "v4 styles+"):
            if key == "Format":
                fmt["style"] = [f.strip().lower() for f in value.split(",")]
            elif key == "Style":
                f = fmt.get("style") or []
                vals = [v.strip() for v in value.split(",", len(f) - 1)]
                d = dict(zip(f, vals))
                al = int(d.get("alignment", "2"))
                col, alpha = colour(d.get("primarycolour", "&H00FFFFFF"))
                styles[d.get("name", "Default")] = {"font": d.get("fontname"), "size": float(d.get("fontsize", "0")),
                                                     "color": col, "opacity": alpha, "alignment": al}
        elif section == "events":
            if key == "Format":
                fmt["event"] = [f.strip().lower() for f in value.split(",")]
            elif key == "Comment":
                comments += 1
            elif key == "Dialogue":
                f = fmt.get("event") or []
                f = ["layer", "start", "end", "style", "name", "marginl", "marginr", "marginv", "effect", "text"]
                vals = value.split(",")
                d = dict(zip(f, [v.strip() if i < len(f) - 1 else v for i, v in enumerate(vals)]))
                lines, tags = text_info(d.get("text", ""), int(info.get("wrapstyle", "0") or 0))
                style = d.get("style", "Default").lstrip("*")
                base = styles.get(style, {"alignment": 2})["alignment"]
                al = tags["alignment"] or (LEGACY.get(tags["legacy"]) if tags["legacy"] else None) or base
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
