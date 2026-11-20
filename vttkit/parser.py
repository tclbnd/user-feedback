"""WebVTT parsing following the W3C WebVTT specification.

The parser is lenient at the cue level: anything the spec says must be
ignored (NOTE/STYLE/REGION blocks, unknown cue settings, malformed cues)
is dropped or repaired while parsing continues.  Only a file that does not
start with a valid WebVTT file header raises :class:`VttError`.
"""
import re

# A WebVTT timestamp is either mm:ss.ttt or hh:mm:ss.ttt (hours may have
# more than two digits).  Minutes and seconds are 00-59.
_TS_FULL = re.compile(r"^(\d+):(\d{2}):(\d{2})\.(\d{3})$")
_TS_SHORT = re.compile(r"^(\d{2}):(\d{2})\.(\d{3})$")

_TAG = re.compile(r"<[^>\s][^>]*>")
_ENTITY = re.compile(r"&(?:#x[0-9A-Fa-f]+|#[0-9]+|[A-Za-z][A-Za-z0-9]+);")

_NAMED_ENTITIES = {
    "amp": "&",
    "lt": "<",
    "gt": ">",
    "nbsp": " ",
}

# Cue setting value grammars (WebVTT cue setting syntax).
_RE_VERTICAL = re.compile(r"^(?:rl|lr)$")
_RE_LINE = re.compile(r"^(?:-?[0-9]+|[0-9]+%(?:,(?:start|center|end))?)$")
_RE_POSITION = re.compile(r"^[0-9]+%(?:,(?:start|center|end|line-left|line-right))?$")
_RE_SIZE = re.compile(r"^[0-9]+%$")
_RE_ALIGN = re.compile(r"^(?:start|center|end|left|right)$")
_RE_REGION = re.compile(r"^[^\s\x00-\x1f]+$")

_SETTING_VALIDATORS = {
    "vertical": _RE_VERTICAL,
    "line": _RE_LINE,
    "position": _RE_POSITION,
    "size": _RE_SIZE,
    "align": _RE_ALIGN,
    "region": _RE_REGION,
}


class VttError(ValueError):
    pass


def timestamp(text):
    """Parse a WebVTT timestamp into milliseconds."""
    text = text.strip()
    m = _TS_FULL.match(text)
    if m:
        h, mi, s, ms = (int(g) for g in m.groups())
    else:
        m = _TS_SHORT.match(text)
        if not m:
            raise VttError(f"bad timestamp {text!r}")
        mi, s, ms = (int(g) for g in m.groups())
        h = 0
    if mi > 59 or s > 59:
        raise VttError(f"bad timestamp {text!r}")
    return ((h * 60 + mi) * 60 + s) * 1000 + ms


def _valid_setting(name, value):
    if name == "line":
        if not _RE_LINE.match(value):
            return False
        if value.endswith("%"):
            return int(value[: value.index("%")]) <= 100
        return True
    if name == "position":
        if not _RE_POSITION.match(value):
            return False
        return int(value[: value.index("%")]) <= 100
    if name == "size":
        if not _RE_SIZE.match(value):
            return False
        return int(value[:-1]) <= 100
    if name == "region":
        return bool(_RE_REGION.match(value)) and "-->" not in value
    validator = _SETTING_VALIDATORS.get(name)
    return bool(validator and validator.match(value))


def settings(text):
    """Parse cue settings, dropping items that are not WebVTT-valid.

    Valid items are kept verbatim (original spelling and order); an unknown
    keyword or an out-of-range value is discarded.  A repeated keyword keeps
    its first occurrence, matching the spec's cue-settings parser.
    """
    out = {}
    for item in text.split():
        name, sep, value = item.partition(":")
        if not sep or not value:
            continue
        if name in out:
            continue
        if _valid_setting(name, value):
            out[name] = value
    return out


def _decode_entities(text):
    def repl(match):
        token = match.group(0)
        body = token[1:-1]
        if body.startswith("#x"):
            try:
                codepoint = int(body[2:], 16)
            except ValueError:
                return token
        elif body.startswith("#"):
            try:
                codepoint = int(body[1:])
            except ValueError:
                return token
        else:
            return _NAMED_ENTITIES.get(body, token)
        if codepoint == 0 or codepoint > 0x10FFFF or 0xD800 <= codepoint <= 0xDFFF:
            return "�"
        try:
            return chr(codepoint)
        except (ValueError, OverflowError):
            return "�"

    return _ENTITY.sub(repl, text)


def _strip_tags(raw):
    """Return (plain text, voice label).

    Tags are removed and the remaining text is entity-decoded.  The voice
    (speaker) of the first ``<v ...>`` / ``<v.class ...>`` class cue span is
    captured.
    """
    voice = None
    plain_parts = []
    pos = 0
    for match in _TAG.finditer(raw):
        plain_parts.append(raw[pos:match.start()])
        inner = match.group(0)[1:-1].strip()
        head = inner.split(None, 1)[0] if inner.split() else ""
        tag_name = head.split(".", 1)[0]
        if voice is None and tag_name == "v":
            label = inner[len(head):].strip()
            if label:
                voice = label
        pos = match.end()
    plain_parts.append(raw[pos:])
    return _decode_entities("".join(plain_parts)), voice


def cue_text(raw):
    """Plain text of a cue payload: tags removed, entities decoded."""
    return _strip_tags(raw)[0]


def _split_blocks(text):
    blocks, cur = [], []
    for line in text.split("\n"):
        if line == "":
            if cur:
                blocks.append(cur)
                cur = []
        else:
            cur.append(line)
    if cur:
        blocks.append(cur)
    return blocks


def _parse_cue(block):
    """Parse one block as a cue, or return None when it must be discarded."""
    ident = None
    if "-->" not in block[0]:
        ident, block = block[0], block[1:]
        if not block or "-->" in ident:
            return None
    if not block:
        return None
    timing = block[0]
    if timing.count("-->") != 1:
        return None
    left, _, right = timing.partition("-->")
    tokens = right.split()
    if not tokens:
        return None
    try:
        start = timestamp(left)
        end = timestamp(tokens[0])
    except VttError:
        return None
    text, voice = _strip_tags("\n".join(block[1:]))
    return {
        "id": ident,
        "start": start,
        "end": end,
        "settings": settings(" ".join(tokens[1:])),
        "voice": voice,
        "text": text,
    }


def parse(text):
    """Parse a WebVTT document into a list of cue dictionaries.

    Raises :class:`VttError` only for a missing/invalid file header.
    Individual bad cues are silently discarded.
    """
    if text.startswith("﻿"):
        text = text[1:]
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if not lines or not re.fullmatch(r"WEBVTT(?:[ \t].*)?", lines[0]):
        raise VttError("missing WEBVTT header")

    cues = []
    for block in _split_blocks("\n".join(lines[1:])):
        first = block[0]
        if first in ("STYLE", "REGION"):
            continue
        if first == "NOTE" or first.startswith("NOTE ") or first.startswith("NOTE\t"):
            continue
        cue = _parse_cue(block)
        if cue is not None:
            cues.append(cue)
    return cues
