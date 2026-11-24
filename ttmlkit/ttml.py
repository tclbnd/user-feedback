"""TTML subtitle documents (W3C TTML1 / IMSC1): parameters, time expressions, timing hierarchy, paragraphs."""
import re
import xml.etree.ElementTree as ET
from fractions import Fraction

TT = "http://www.w3.org/ns/ttml"
TTP = "http://www.w3.org/ns/ttml#parameter"
CLOCK = re.compile(r"^(\d{2,}):(\d{2}):(\d{2})(?:(\.\d+)|:(\d{2,})(?:\.(\d+))?)?$")
OFFSET = re.compile(r"^(\d+(?:\.\d+)?)(h|m|s|ms|f|t)$")


class TtmlError(ValueError):
    pass


def local(tag):
    return tag.rsplit("}", 1)[-1]


class Params:
    def __init__(self, root):
        g = lambda name, default=None: root.get("{%s}%s" % (TTP, name), default)
        self.frame_rate = int(g("frameRate", "30"))
        self.effective = Fraction(self.frame_rate)
        self.sub_frame_rate = int(g("subFrameRate", "1"))
        tick = g("tickRate")
        if tick:
            self.tick_rate = Fraction(int(tick))
        else:
            self.tick_rate = Fraction(1)

    def seconds(self, expr):
        expr = expr.strip()
        m = CLOCK.match(expr)
        if m:
            h, mi, s, frac, frames, sub = m.groups()
            t = Fraction(int(h) * 3600 + int(mi) * 60 + int(s))
            if frac:
                t += Fraction(frac)
            if frames:
                t += Fraction(int(frames), 100)
            return t
        m = OFFSET.match(expr)
        if m:
            v, unit = Fraction(m.group(1)), m.group(2)
            return {"h": v * 3600, "m": v * 60, "s": v, "ms": v, "f": v / self.effective,
                    "t": v / self.tick_rate}[unit]
        raise TtmlError("bad time expression %r" % expr)


def text_of(el):
    lines, cur = [], []

    def walk(e):
        if e.text:
            cur.append(e.text)
        for c in e:
            walk(c)
            if c.tail:
                cur.append(c.tail)
    walk(el)
    lines.append("".join(cur))
    return [" ".join(l.split()) for l in lines if l.strip()]


def parse(text):
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError as exc:
        raise TtmlError("not XML: %s" % exc)
    if local(root.tag) != "tt":
        raise TtmlError("not a TTML document")
    prm = Params(root)
    out = {"frame_rate": str(prm.effective), "tick_rate": str(prm.tick_rate),
           "language": root.get("{http://www.w3.org/XML/1998/namespace}lang"), "paragraphs": []}

    def interval(el, start, parent_end, prev_end, container):
        """Absolute (begin, end) of an element; `start` is where the element's time begins (the parent's begin,
        or the end of the previous sibling in a seq container)."""
        b = el.get("begin")
        e = el.get("end")
        d = el.get("dur")
        begin = prm.seconds(b) if b else start
        end = None
        if e:
            end = prm.seconds(e)
        if end is None:
            end = parent_end if container == "par" else begin
        if parent_end is not None and end > parent_end:
            end = parent_end
        return begin, end

    def visit(el, start, parent_end, container):
        prev_end = start
        kind = el.get("timeContainer", "par")
        for c in el:
            name = local(c.tag)
            if name not in ("body", "div", "p", "span"):
                continue
            b, e = interval(c, start, parent_end, prev_end, container)
            if name == "p":
                out["paragraphs"].append({"begin": str(b), "end": str(e) if e is not None else None,
                                          "lines": text_of(c), "region": c.get("region")})
            else:
                visit(c, b, e, c.get("timeContainer", "par"))
            prev_end = e if e is not None else b

    body = next((c for c in root if local(c.tag) == "body"), None)
    if body is None:
        raise TtmlError("document without a body")
    b, e = interval(body, Fraction(0), None, Fraction(0), "par")
    visit(body, b, e, body.get("timeContainer", "par"))
    out["paragraphs"].sort(key=lambda p: Fraction(p["begin"]))
    out["duration"] = str(max((Fraction(p["end"]) for p in out["paragraphs"] if p["end"]), default=Fraction(0)))
    return out
