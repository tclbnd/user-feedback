"""EBU STL subtitle files (EBU Tech 3264): GSI block, TTI blocks, extension blocks, character tables."""
from fractions import Fraction

# GSI character code table number (CCT) -> output name.
TABLES = {"00": "latin", "01": "cyrillic", "02": "arabic", "03": "greek", "04": "hebrew"}
JUSTIFY = {0: "unchanged", 1: "left", 2: "centre", 3: "right"}

# Disk Format Code (DFC, GSI bytes 3..10) -> frame rate, EBU Tech 3264 section 4.
FRAME_RATES = {"STL24.01": 24, "STL25.01": 25, "STL30.01": 30}

# Text control codes in TTI text fields (EBU Tech 3264, table 6).
LF_CODE = 0x8A            # boxing code: new line / new subtitle row
COMMENT_FLAG = 0x01       # CF byte: the subtitle is a comment, not on air
USER_DATA_EBN = 0xFE      # extension block number carrying user data

# EBU Latin national option character table (Tech 3264 table 3, CCT "00").
# Only bytes that differ from Latin-1 are listed; others fall back to Latin-1.
LATIN = {
    0xA1: "Ą", 0xA2: "Ł", 0xA3: "Ľ", 0xA5: "Ē", 0xA6: "Ċ",
    0xA7: "Š", 0xA8: "Ž", 0xA9: "Ť", 0xAA: "Ż", 0xAB: "Ś",
    0xAC: "Ŕ", 0xAD: "Ć", 0xAE: "Ç", 0xAF: "Ɓ", 0xB0: "Ñ",
    0xB1: "ĕ", 0xB2: "ł", 0xB4: "ē", 0xB5: "ċ", 0xB6: "ć",
    0xB7: "š", 0xB8: "ž", 0xB9: "ť", 0xBA: "ż", 0xBB: "ś",
    0xBC: "ŕ", 0xBD: "ñ", 0xBE: "ç", 0xBF: "œ", 0xC0: "–",
    0xC1: "À", 0xC2: "é", 0xC3: "è", 0xC4: "ê", 0xC5: "ë",
    0xC6: "ğ", 0xC7: "î", 0xC8: "ï", 0xC9: "™", 0xCA: "Â",
    0xCB: "â", 0xCC: "ā", 0xCD: "û", 0xCE: "ù", 0xCF: "ı",
    0xD0: "Ô", 0xD1: "ô", 0xD2: "č", 0xD3: "ú", 0xD4: "÷",
    0xD5: "Č", 0xD6: "Û", 0xD7: "ù", 0xD8: "ė", 0xD9: "É",
    0xDA: "À", 0xDB: "è", 0xDC: "←", 0xDD: "↑", 0xDE: "→",
    0xDF: "ß", 0xE0: "Ó", 0xE1: "ó", 0xE2: "Ò", 0xE3: "ò",
    0xE4: "ě", 0xE5: "ą", 0xE6: "ţ", 0xE7: "ú", 0xE8: "ù",
    0xE9: "Ý", 0xEA: "ý", 0xEB: "Ğ", 0xEC: "Ä", 0xED: "ä",
    0xEE: "ď", 0xEF: "ĺ", 0xF0: "Ë", 0xF1: "ë", 0xF2: "ň",
    0xF3: "ř", 0xF4: "Ĺ", 0xF5: "Ř", 0xF6: "Ü", 0xF7: "ü",
    0xF8: "á", 0xF9: "Á", 0xFA: "à", 0xFB: "ģ", 0xFC: "Ģ",
    0xFD: "Ķ", 0xFE: "ķ", 0xFF: "Ņ",
}

# EBU Cyrillic national option character table (Tech 3264 table 4, CCT "01").
CYRILLIC = {
    0xA1: "ђ", 0xA2: "ё", 0xA3: "є", 0xA4: "ѕ", 0xA5: "і",
    0xA6: "ї", 0xA7: "ј", 0xA8: "љ", 0xA9: "њ", 0xAA: "ћ",
    0xAB: "ќ", 0xAC: "ў", 0xAD: "џ", 0xAE: "ґ", 0xAF: "Ё",
}
for _i, _ch in enumerate("АБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ"):
    CYRILLIC[0xB0 + _i] = _ch
for _i, _ch in enumerate("абвгдежзийклмнопрстуфхцчшщъыьэюя"):
    CYRILLIC[0xD0 + _i] = _ch
for _i, _ch in enumerate("ЂЃЄЅІЇЈЉЊЋЌЎЏ"):
    CYRILLIC[0xF0 + _i] = _ch
for _i, _ch in enumerate("ђѓєѕіїјљњћќўџ"):
    CYRILLIC[0xFD - 13 + _i] = _ch
del _i, _ch

# EBU Greek national option character table (Tech 3264 table 5, CCT "03"),
# matching the Greek letter layout of ISO 8859-7.
GREEK = {0xA1: "‘", 0xA2: "’"}
for _i, _ch in enumerate("ΆΈΉΊΌΎΏΐΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡ"):
    GREEK[0xA3 + _i] = _ch
for _i, _ch in enumerate("ΣΤΥΦΧΨΩΪΫάέήίΰαβγδεζηθικλμνξοπρςστυφχψωϊϋόύώ"):
    GREEK[0xD3 + _i] = _ch
del _i, _ch

# EBU Arabic (Tech 3264 table 7, CCT "02") and Hebrew (table 8, CCT "04")
# follow the ISO 8859-6 / ISO 8859-8 layouts; undefined slots are skipped.
def _iso_table(name):
    table = {}
    for _code in range(0xA0, 0x100):
        try:
            table[_code] = bytes([_code]).decode(name)
        except UnicodeDecodeError:
            pass
    return table


ARABIC = _iso_table("iso8859-6")
HEBREW = _iso_table("iso8859-8")

CHAR_TABLES = {"00": LATIN, "01": CYRILLIC, "02": ARABIC, "03": GREEK, "04": HEBREW}


class StlError(ValueError):
    pass


def decode_text(raw, table):
    """Decode bytes using the file's EBU character table (Latin-1 fallback)."""
    mapping = CHAR_TABLES.get(table)
    if mapping is None:
        return raw.decode("latin-1")
    return "".join(mapping.get(byte, chr(byte)) for byte in raw)


def text_lines(raw, table):
    """Split a TTI text field into display rows; drop display control codes and empty rows."""
    lines = []
    for row in raw.split(bytes([LF_CODE])):
        visible = bytes(byte for byte in row if not 0x80 <= byte <= 0x9F)
        text = decode_text(visible, table).strip()
        if text:
            lines.append(text)
    return lines


def tc(b):
    return "%02d:%02d:%02d:%02d" % tuple(b)


def frame_count(tci, fps):
    return ((tci[0] * 60 + tci[1]) * 60 + tci[2]) * fps + tci[3]


def parse(data):
    if len(data) < 1024 or data[3:6] != b"STL":
        raise StlError("not an EBU STL file")
    g = data[:1024]
    dfc = g[3:11].decode("latin-1").strip()
    if dfc not in FRAME_RATES:
        raise StlError("unknown disk format code: %r" % dfc)
    fps = FRAME_RATES[dfc]
    table = g[12:14].decode("latin-1")
    s = lambda a, b: decode_text(g[a:b], table).strip() or None

    # Group TTI blocks by subtitle number: a subtitle split across blocks keeps
    # one number, and its blocks are contiguous in the file.
    groups = []
    user_data_blocks = 0
    for k in range((len(data) - 1024) // 128):
        t = data[1024 + 128 * k:1024 + 128 * (k + 1)]
        ebn = t[3]
        if ebn == USER_DATA_EBN:
            user_data_blocks += 1
            continue
        sn = t[1] | t[2] << 8
        if not groups or groups[-1]["number"] != sn:
            groups.append({"number": sn, "head": t, "parts": []})
        groups[-1]["parts"].append(t[16:128])

    subtitles = []
    comments = 0
    for group in groups:
        head = group["head"]
        if head[15] & COMMENT_FLAG:
            comments += 1
            continue
        tci, tco = head[5:9], head[9:13]
        fi, fo = frame_count(tci, fps), frame_count(tco, fps)
        subtitles.append({
            "number": group["number"],
            "in": tc(tci),
            "out": tc(tco),
            "vertical": head[13],
            "justification": JUSTIFY.get(head[14], "code_%d" % head[14]),
            "duration": str(Fraction(fo - fi, fps)),
            "lines": text_lines(b"".join(group["parts"]), table),
        })

    return {
        "frame_rate": fps,
        "code_page": s(0, 3),
        "character_table": TABLES.get(table, "table_" + table),
        "language": s(14, 16),
        "programme": s(16, 48),
        "episode": s(48, 80),
        "declared_blocks": int(s(238, 243) or 0),
        "declared_subtitles": int(s(243, 248) or 0),
        "start_timecode": "%s:%s:%s:%s" % (g[256:258].decode(), g[258:260].decode(),
                                           g[260:262].decode(), g[262:264].decode()),
        "tti_blocks": (len(data) - 1024) // 128,
        "comments": comments,
        "user_data_blocks": user_data_blocks,
        "subtitles": subtitles,
    }
