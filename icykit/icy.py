"""Shoutcast/Icecast stream captures: response headers, ICY metadata interleaved with audio."""
import re


class IcyError(ValueError):
    pass


class Headers(dict):
    """Header map with case-insensitive lookups; the first occurrence wins.

    Keys keep their original spelling, but ``headers["Icy-Name"]`` and
    ``headers["icy-name"]`` address the same value.
    """

    def __init__(self):
        super().__init__()
        self._names = {}

    def add(self, key, value):
        low = key.lower()
        if low in self._names:
            return
        self._names[low] = key
        super().__setitem__(key, value)

    def __getitem__(self, key):
        return super().__getitem__(self._names[key.lower()])

    def __contains__(self, key):
        return key.lower() in self._names

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default


def split_headers(data):
    """Split a captured response into (protocol, status, headers, body).

    Header names are case-insensitive and the first of a repeated name wins.  A
    line starting with whitespace continues the header above it.
    """
    m = re.search(rb"\r\n\r\n|\n\n", data)
    if not m:
        raise IcyError("no end of response headers")
    head = data[:m.start()].replace(b"\r\n", b"\n")
    body = data[m.end():]

    lines = head.split(b"\n")
    status = lines[0].decode("latin-1").strip()
    sm = re.match(r"(ICY|HTTP/1\.[01])\s+(\d{3})\b", status)
    if not sm:
        raise IcyError("not an HTTP or ICY response")

    raw = []
    for line in lines[1:]:
        if not line.strip():
            continue
        if line[:1] in (b" ", b"\t") and raw:
            raw[-1][1] += " " + line.strip().decode("latin-1")
            continue
        if b":" not in line:
            continue
        key, value = line.split(b":", 1)
        raw.append([key.decode("latin-1").strip(), value.decode("latin-1").strip()])

    headers = Headers()
    for key, value in raw:
        headers.add(key, value)
    return sm.group(1), int(sm.group(2)), headers, body


def _last_coding(value):
    parts = [p.strip().lower() for p in (value or "").split(",") if p.strip()]
    return parts[-1] if parts else ""


def _leading_int(value):
    m = re.match(r"\s*(\d+)", value or "")
    return int(m.group(1)) if m else None


def dechunk(body):
    """Decode an HTTP/1.1 chunked body (chunk extensions are ignored)."""
    out = bytearray()
    pos, end = 0, len(body)
    while pos < end:
        eol = body.find(b"\r\n", pos)
        if eol < 0:
            break
        try:
            size = int(body[pos:eol].split(b";", 1)[0].strip(), 16)
        except ValueError:
            break
        pos = eol + 2
        if size == 0:
            break
        out += body[pos:pos + size]
        pos += size
        if pos >= end:
            break
        if body[pos:pos + 2] == b"\r\n":
            pos += 2
        else:
            break
    return bytes(out)


def _text(raw):
    raw = raw.rstrip(b"\0")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def fields(meta):
    """Parse ``StreamTitle='...';StreamUrl='...';``.

    A quoted value may itself contain quotes and semicolons; it ends at the
    quote that is followed by a semicolon.  Unknown fields are ignored.
    """
    out = {}
    i, n = 0, len(meta)
    while i < n:
        while i < n and meta[i] in " \t\r\n\0;":
            i += 1
        if i >= n:
            break
        eq = meta.find("=", i)
        if eq < 0:
            break
        key = meta[i:eq].strip()
        i = eq + 1
        if i < n and meta[i] == "'":
            close = meta.find("';", i + 1)
            if close < 0:
                value = meta[i + 1:].rstrip("\0")
                if value.endswith("'"):
                    value = value[:-1]
                if key:
                    out[key] = value
                break
            if key:
                out[key] = meta[i + 1:close]
            i = close + 2
        else:
            close = meta.find(";", i)
            if close < 0:
                if key:
                    out[key] = meta[i:].rstrip("\0")
                break
            if key:
                out[key] = meta[i:close]
            i = close + 1
    return out


def scan(data):
    proto, code, headers, body = split_headers(data)

    if _last_coding(headers.get("transfer-encoding")) == "chunked":
        body = dechunk(body)
    else:
        cl = _leading_int(headers.get("content-length"))
        if cl is not None:
            body = body[:cl]

    metaint = _leading_int(headers.get("icy-metaint")) or 0
    audio, blocks, titles, truncated = 0, 0, [], False
    if metaint <= 0:
        audio = len(body)
    else:
        pos, last_title = 0, None
        while pos < len(body):
            take = min(metaint, len(body) - pos)
            audio += take
            pos += take
            if pos >= len(body):
                break
            size = body[pos] * 16
            pos += 1
            if size == 0:
                continue
            if pos + size > len(body):
                truncated = True
                break
            blocks += 1
            info = fields(_text(body[pos:pos + size]))
            pos += size
            title = info.get("StreamTitle")
            if title is not None and title != last_title:
                titles.append({"at_audio_byte": audio, "title": title, "url": info.get("StreamUrl")})
                last_title = title

    return {"protocol": proto, "status": code, "name": headers.get("icy-name"),
            "genre": headers.get("icy-genre"), "bitrate": _leading_int(headers.get("icy-br")),
            "content_type": headers.get("content-type"), "metaint": metaint or None,
            "audio_bytes": audio, "metadata_blocks": blocks, "titles": titles, "truncated": truncated}

