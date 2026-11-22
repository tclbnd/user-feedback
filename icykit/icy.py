"""Shoutcast/Icecast stream captures: response headers, ICY metadata interleaved with audio."""
import re


class IcyError(ValueError):
    pass


def split_headers(data):
    end = data.find(b"\r\n\r\n")
    sep = 4
    if end < 0:
        raise IcyError("no end of response headers")
    lines = data[:end].split(b"\r\n")
    status = lines[0].decode("latin-1").strip()
    m = re.match(r"(ICY|HTTP/1\.[01]) (\d{3})", status)
    if not m:
        raise IcyError("not an HTTP or ICY response")
    headers = {}
    for line in lines[1:]:
        if b":" not in line:
            continue
        k, v = line.split(b":", 1)
        headers[k.decode("latin-1").strip()] = v.decode("latin-1").strip()
    return m.group(1), int(m.group(2)), headers, data[end + sep:]


def dechunk(body):
    out, pos = b"", 0
    while pos < len(body):
        eol = body.find(b"\r\n", pos)
        if eol < 0:
            break
        size = int(body[pos:eol].split(b";")[0].strip() or b"0", 16)
        if size == 0:
            break
        out += body[eol + 2:eol + 2 + size]
        pos = eol + 2 + size + 2
    return out


def _text(raw):
    raw = raw.rstrip(b"\0")
    return raw.decode("utf-8")


def fields(meta):
    """StreamTitle='...';StreamUrl='...';"""
    out = {}
    for part in meta.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.split("'")[1] if "'" in v else v
    return out


def scan(data):
    proto, code, headers, body = split_headers(data)
    metaint = int(headers["icy-metaint"]) if headers.get("icy-metaint", "").isdigit() else 0
    audio, blocks, titles, truncated = 0, 0, [], False
    if metaint <= 0:
        audio = len(body)
    else:
        pos = 0
        while pos < len(body):
            take = min(metaint, len(body) - pos)
            audio += take
            pos += take
            if pos >= len(body):
                break
            n = body[pos]
            pos += 1
            if pos + n > len(body):
                truncated = True
                break
            blocks += 1
            f = fields(_text(body[pos:pos + n]))
            titles.append({"at_audio_byte": audio, "title": f.get("StreamTitle", ""), "url": f.get("StreamUrl")})
            pos += n
    br = headers.get("icy-br", "")
    return {"protocol": proto, "status": code, "name": headers.get("icy-name"), "genre": headers.get("icy-genre"),
            "bitrate": int(br.split(",")[0]) if br.split(",")[0].isdigit() else None,
            "content_type": headers.get("content-type"), "metaint": metaint or None,
            "audio_bytes": audio, "metadata_blocks": blocks, "titles": titles, "truncated": truncated}
