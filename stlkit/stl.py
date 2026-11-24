"""EBU STL subtitle files (EBU Tech 3264): GSI block, TTI blocks, extension blocks, character tables."""
from fractions import Fraction

TABLES = {"00": "latin", "01": "cyrillic", "02": "arabic", "03": "greek", "04": "hebrew"}
JUSTIFY = {0: "unchanged", 1: "left", 2: "centre", 3: "right"}


class StlError(ValueError):
    pass


def text_field(raw, table):
    return [raw.replace(b"\x8f", b"").decode("latin-1").strip()]


def tc(b):
    return "%02d:%02d:%02d:%02d" % tuple(b)


def parse(data):
    if len(data) < 1024 or data[3:6] != b"STL":
        raise StlError("not an EBU STL file")
    g = data[:1024]
    fps = 25
    table = g[12:14].decode("latin-1")
    s = lambda a, b: g[a:b].decode("latin-1").strip() or None
    out = {"frame_rate": fps, "code_page": s(0, 3), "character_table": TABLES.get(table, "table_" + table),
           "language": s(14, 16), "programme": s(16, 48), "episode": s(48, 80),
           "declared_blocks": int(s(238, 243) or 0), "declared_subtitles": int(s(243, 248) or 0),
           "start_timecode": "%s:%s:%s:%s" % (g[256:258].decode(), g[258:260].decode(), g[260:262].decode(), g[262:264].decode()),
           "tti_blocks": (len(data) - 1024) // 128, "comments": 0, "user_data_blocks": 0, "subtitles": []}
    for k in range(out["tti_blocks"]):
        t = data[1024 + 128 * k:1024 + 128 * (k + 1)]
        sn = t[1] | t[2] << 8
        ebn = t[3]
        if ebn == 0xFE:
            out["user_data_blocks"] += 1
            continue
        tci, tco = t[5:9], t[9:13]
        fi = ((tci[0] * 60 + tci[1]) * 60 + tci[2]) * fps + tci[3]
        fo = ((tco[0] * 60 + tco[1]) * 60 + tco[2]) * fps + tco[3]
        out["subtitles"].append({"number": sn, "in": tc(tci), "out": tc(tco), "vertical": t[13],
                                 "justification": JUSTIFY.get(t[14], "code_%d" % t[14]),
                                 "duration": str(Fraction(fo - fi, fps)),
                                 "lines": [l for l in text_field(t[16:128], table) if l]})
    return out
