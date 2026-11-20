"""Decoding of the text, comment (COMM) and user-defined text (TXXX) frames.

The four ID3v2 text encodings are:

    0 - ISO-8859-1, terminated by a single 0x00 byte
    1 - UTF-16 with BOM, terminated by two 0x00 bytes (BOM picks endianness)
    2 - UTF-16BE without BOM, terminated by two 0x00 bytes
    3 - UTF-8, terminated by a single 0x00 byte (2.3/2.4 only)

Multi-value text frames use null separators in 2.4 and "/" separators in
2.2/2.3 (per the respective frame specifications).
"""

from .id3 import TagError

# ID3v1 genre names referenced from TCON as "(number)"; the order is the
# official index list from the ID3v2 frame specification.
ID3V1_GENRES = [
    "Blues", "Classic Rock", "Country", "Dance", "Disco", "Funk", "Grunge",
    "Hip-Hop", "Jazz", "Metal", "New Age", "Oldies", "Other", "Pop", "R&B",
    "Rap", "Reggae", "Rock", "Techno", "Industrial", "Alternative", "Ska",
    "Death Metal", "Pranks", "Soundtrack", "Euro-Techno", "Ambient",
    "Trip-Hop", "Vocal", "Jazz+Funk", "Fusion", "Trance", "Classical",
    "Instrumental", "Acid", "House", "Game", "Sound Clip", "Gospel", "Noise",
    "AlternRock", "Bass", "Soul", "Punk", "Space", "Meditative",
    "Instrumental Pop", "Instrumental Rock", "Ethnic", "Gothic", "Darkwave",
    "Techno-Industrial", "Electronic", "Pop-Folk", "Eurodance", "Dream",
    "Southern Rock", "Comedy", "Cult", "Gangsta", "Top 40", "Christian Rap",
    "Pop/Funk", "Jungle", "Native American", "Cabaret", "New Wave",
    "Psychadelic", "Rave", "Showtunes", "Trailer", "Lo-Fi", "Tribal",
    "Acid Punk", "Acid Jazz", "Polka", "Retro", "Musical", "Rock & Roll",
    "Hard Rock",
    # Winamp 1.91 extensions
    "Folk", "Folk-Rock", "National Folk", "Swing", "Fast Fusion", "Bebob",
    "Latin", "Revival", "Celtic", "Bluegrass", "Avantgarde", "Gothic Rock",
    "Progressive Rock", "Psychedelic Rock", "Symphonic Rock", "Slow Rock",
    "Big Band", "Chorus", "Easy Listening", "Acoustic", "Humour", "Speech",
    "Chanson", "Opera", "Chamber Music", "Sonata", "Symphony", "Booty Bass",
    "Primus", "Porn Groove", "Satire", "Slow Jam", "Club", "Tango", "Samba",
    "Folklore", "Ballad", "Power Ballad", "Rhythmic Soul", "Freestyle",
    "Duet", "Punk Rock", "Drum Solo", "A capella", "Euro-House", "Dance Hall",
    # Winamp 5 extensions
    "Goa", "Drum & Bass", "Club-House", "Hardcore Techno", "Terror", "Indie",
    "BritPop", "Negerpunk", "Polsk Punk", "Beat", "Christian Gangsta Rap",
    "Heavy Metal", "Black Metal", "Crossover", "Contemporary Christian",
    "Christian Rock", "Merengue", "Salsa", "Thrash Metal", "Anime", "JPop",
    "Synthpop",
]

# TCON refinement tokens defined by the ID3v2.3 frame specification.
_TCON_REFINEMENTS = {"RX": "Remix", "CR": "Cover"}


class FrameDataError(TagError):
    """Raised when a frame payload cannot be decoded."""


def _utf16_codec(raw):
    """Pick a UTF-16 codec from the BOM (encoding 1); default is big-endian."""
    if raw[:2] == b"\xff\xfe":
        return "utf-16-le"
    if raw[:2] == b"\xfe\xff":
        return "utf-16-be"
    return "utf-16-be"


def _find_terminator(raw, encoding):
    """Return the offset of the first string terminator, len(raw) if none."""
    if encoding == 1:
        # Two null bytes; the first code unit follows the BOM (even offset).
        start = 2 if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else 0
        for i in range(start, len(raw) - 1, 2):
            if raw[i] == 0 and raw[i + 1] == 0:
                return i
        return len(raw)
    for i, b in enumerate(raw):
        if b == 0:
            return i
    return len(raw)


def _decode(raw, encoding, utf16_codec=None):
    """Decode a raw string fragment and strip any leading BOM character."""
    if encoding == 1:
        text = raw.decode(utf16_codec or _utf16_codec(raw), "replace")
    elif encoding == 2:
        text = raw.decode("utf-16-be", "replace")
    elif encoding == 3:
        text = raw.decode("utf-8", "replace")
    else:
        text = raw.decode("latin-1", "replace")
    if text[:1] == "﻿":
        text = text[1:]
    return text


def _split_fields(raw, encoding):
    """Split a (description, value) payload at its terminator (byte level)."""
    term = _find_terminator(raw, encoding)
    first = raw[:term]
    if encoding == 1:
        codec = _utf16_codec(first)
        rest = raw[term + 2:]
    elif encoding == 2:
        codec = "utf-16-be"
        rest = raw[term + 2:]
    else:
        codec = None
        rest = raw[term + 1:]
    return first, rest, codec


def _strip_trailing_nulls(raw, encoding):
    """Remove terminating null code units; return the trimmed bytes only."""
    if encoding in (1, 2):
        while len(raw) >= 2 and raw[-2:] == b"\x00\x00":
            raw = raw[:-2]
    else:
        raw = raw.rstrip(b"\x00")
    return raw


def _split_multi_values(raw, encoding, major):
    """Split one text frame payload into its individual values."""
    raw = _strip_trailing_nulls(raw, encoding)
    if not raw:
        return []
    if encoding in (1, 2):
        codec = "utf-16-be" if encoding == 2 else _utf16_codec(raw)
        # Skip an initial BOM before walking code units.
        start = 2 if encoding == 1 and raw[:2] in (b"\xff\xfe", b"\xfe\xff") else 0
        parts = []
        piece_start = 0
        for i in range(start, len(raw) - 1, 2):
            if raw[i] == 0 and raw[i + 1] == 0:
                parts.append(raw[piece_start:i])
                piece_start = i + 2
        parts.append(raw[piece_start:])
        # codec is fixed from the initial BOM; later pieces carry no BOM.
        return [_decode(p, encoding, codec) for p in parts if p]
    text = _decode(raw, encoding)
    if major == 4:
        # 2.4 separates values with null bytes (already byte-stripped above).
        values = text.split("\x00")
    else:
        # 2.2/2.3 use "/" as the multi-value separator.
        values = text.split("/")
    return [v for v in values if v != ""]


def text(body, major):
    """Decode a T... text frame as a list of values."""
    if not body:
        return []
    encoding = body[0]
    if encoding not in (0, 1, 2, 3):
        raise FrameDataError("unknown text encoding " + str(encoding))
    return _split_multi_values(body[1:], encoding, major)


def comment(body):
    """Decode a COMM frame: text encoding, 3-letter language, desc, text."""
    if len(body) < 4:
        raise FrameDataError("truncated COMM frame")
    encoding = body[0]
    if encoding not in (0, 1, 2, 3):
        raise FrameDataError("unknown text encoding " + str(encoding))
    lang = body[1:4].decode("latin-1")
    desc_raw, value_raw, codec = _split_fields(body[4:], encoding)
    desc = _decode(desc_raw, encoding, codec)
    value_raw = _strip_trailing_nulls(value_raw, encoding)
    if encoding == 1:
        value_codec = codec  # value shares the descriptor BOM endianness
    elif encoding == 2:
        value_codec = "utf-16-be"
    else:
        value_codec = None
    return {
        "lang": lang,
        "desc": desc,
        "text": _decode(value_raw, encoding, value_codec),
    }


def user_text(body):
    """Decode a TXXX frame into a (description, value) pair."""
    if not body:
        raise FrameDataError("empty TXXX frame")
    encoding = body[0]
    if encoding not in (0, 1, 2, 3):
        raise FrameDataError("unknown text encoding " + str(encoding))
    desc_raw, value_raw, codec = _split_fields(body[1:], encoding)
    desc = _decode(desc_raw, encoding, codec)
    value_raw = _strip_trailing_nulls(value_raw, encoding)
    if encoding == 1:
        value_codec = codec
    elif encoding == 2:
        value_codec = "utf-16-be"
    else:
        value_codec = None
    return desc, _decode(value_raw, encoding, value_codec)


def _parse_tcon_token(token):
    """Resolve one TCON reference/string (ID3v2.3 grammar, reused in 2.4)."""
    values = []
    i = 0
    n = len(token)
    while i < n:
        if token.startswith("((", i):
            # Escaped parenthesis: one literal '(' starts a free-text run.
            j = token.find("(", i + 2)
            if j == -1:
                values.append("(" + token[i + 2:])
                break
            values.append("(" + token[i + 2:j])
            i = j
            continue
        if token[i] == "(":
            close = token.find(")", i + 1)
            if close == -1:
                values.append(token[i:])
                break
            inside = token[i + 1:close]
            if inside.isdigit():
                index = int(inside)
                if 0 <= index < len(ID3V1_GENRES):
                    values.append(ID3V1_GENRES[index])
                else:
                    values.append("(" + inside + ")")
            elif inside in _TCON_REFINEMENTS:
                values.append(_TCON_REFINEMENTS[inside])
            elif inside == "":
                values.append("()")
            else:
                # Parenthesised free text stays parenthesised.
                values.append("(" + inside + ")")
            i = close + 1
        else:
            j = token.find("(", i)
            if j == -1:
                values.append(token[i:])
                break
            values.append(token[i:j])
            i = j
    return values


def genre(body, major):
    """Decode a TCON frame, resolving numeric references and refinements."""
    resolved = []
    for value in text(body, major):
        resolved.extend(_parse_tcon_token(value))
    return resolved
