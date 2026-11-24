"""EBU STL subtitle files (EBU Tech 3264): GSI block, TTI blocks, extension blocks, character tables."""
from fractions import Fraction
import unicodedata

TABLES = {"00": "latin", "01": "cyrillic", "02": "arabic", "03": "greek", "04": "hebrew"}
JUSTIFY = {0: "unchanged", 1: "left", 2: "centre", 3: "right"}

# EBU Latin uses ISO 6937-style non-spacing marks before their base letter.
LATIN_MARKS = {
    0xC1: "̀",  # grave
    0xC2: "́",  # acute
    0xC3: "̂",  # circumflex
    0xC4: "̃",  # tilde
    0xC5: "̄",  # macron
    0xC6: "̆",  # breve
    0xC7: "̇",  # dot above
    0xC8: "̈",  # diaeresis/umlaut
    0xC9: "̉",  # hook above
    0xCA: "̊",  # ring above
    0xCB: "̧",  # cedilla
    0xCC: "̋",  # double acute
    0xCD: "̨",  # ogonek
    0xCE: "̌",  # caron
}

TABLE_CODECS = {
    "cyrillic": "iso8859-5",
    "arabic": "iso8859-6",
    "greek": "iso8859-7",
    "hebrew": "iso8859-8",
}


class StlError(ValueError):
    pass


def _decode_latin(raw):
    chars = []
    i = 0
    while i < len(raw):
        byte = raw[i]

        # 0x8A is the STL row break; other C0/C1 bytes are display controls
        # or fill and do not contribute visible text.
        if byte in (0x8A, 0x0A):
            chars.append("\n")
            i += 1
        elif byte == 0x0D:
            i += 1
        elif byte < 0x20 or byte == 0x7F or 0x80 <= byte <= 0x9F:
            i += 1
        elif byte in LATIN_MARKS and i + 1 < len(raw):
            base = raw[i + 1]
            if 0x40 <= base <= 0x7E:
                chars.append(unicodedata.normalize("NFC", chr(base) + LATIN_MARKS[byte]))
                i += 2
            else:
                i += 1
        else:
            chars.append(chr(byte))
            i += 1
    return "".join(chars)


def text_field(raw, table):
    """Decode a TTI text field and return its non-empty rows."""
    if isinstance(table, bytes):
        table = table.decode("ascii", "replace")
    table_name = TABLES.get(table, table)
    codec = TABLE_CODECS.get(table_name)

    if table == "00" or table_name == "latin":
        text = _decode_latin(raw)
    elif codec:
        text = raw.decode(codec, errors="replace").replace("\x8a", "\n")
    else:
        text = raw.decode("latin-1", errors="replace").replace("\x8a", "\n")

    lines = []
    for line in text.split("\n"):
        line = "".join(ch for ch in line if unicodedata.category(ch) not in ("Cc", "Cf")).strip()
        if line:
            lines.append(line)
    return lines


def tc(b):
    return "%02d:%02d:%02d:%02d" % tuple(b)


def parse(data):
    data = bytes(data)
    if len(data) < 1024 or data[3:6] != b"STL":
        raise StlError("not an EBU STL file")
    g = data[:1024]
    try:
        fps = int(g[6:8])
    except ValueError:
        fps = 25
    table = g[12:14].decode("latin-1")
    s = lambda a, b: g[a:b].decode("latin-1").strip() or None
    out = {"frame_rate": fps, "code_page": s(0, 3), "character_table": TABLES.get(table, "table_" + table),
           "language": s(14, 16), "programme": s(16, 48), "episode": s(48, 80),
           "declared_blocks": int(s(238, 243) or 0), "declared_subtitles": int(s(243, 248) or 0),
           "start_timecode": "%s:%s:%s:%s" % (g[256:258].decode(), g[258:260].decode(), g[260:262].decode(), g[262:264].decode()),
           "tti_blocks": (len(data) - 1024) // 128, "comments": 0, "user_data_blocks": 0, "subtitles": []}

    groups = {}
    order = []
    for k in range(out["tti_blocks"]):
        t = data[1024 + 128 * k:1024 + 128 * (k + 1)]
        sn = t[1] | t[2] << 8
        ebn = t[3]
        if ebn == 0xFE:
            out["user_data_blocks"] += 1
            continue

        group = groups.get(sn)
        if group is None:
            group = {"first": t, "text": bytearray(), "comment": False}
            groups[sn] = group
            order.append(sn)

        # Extension blocks belonging to one subtitle are concatenated before
        # rows are decoded.
        group["text"].extend(t[16:128])
        group["comment"] = group["comment"] or t[15] == 1

    for sn in order:
        group = groups[sn]
        if group["comment"]:
            out["comments"] += 1
            continue

        t = group["first"]
        tci, tco = t[5:9], t[9:13]
        fi = ((tci[0] * 60 + tci[1]) * 60 + tci[2]) * fps + tci[3]
        fo = ((tco[0] * 60 + tco[1]) * 60 + tco[2]) * fps + tco[3]
        out["subtitles"].append({"number": sn, "in": tc(tci), "out": tc(tco), "vertical": t[13],
                                 "justification": JUSTIFY.get(t[14], "code_%d" % t[14]),
                                 "duration": str(Fraction(fo - fi, fps)),
                                 "lines": text_field(bytes(group["text"]), table)})
    return out
