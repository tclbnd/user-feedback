"""YUV4MPEG2 header and frames."""


class Y4mError(ValueError):
    pass


def header(data):
    end = data.find(b"\n")
    if end < 0 or not data.startswith(b"YUV4MPEG2 "):
        raise Y4mError("not a YUV4MPEG2 stream")
    params = {}
    for token in data[10:end].decode("ascii").split(" "):
        params[token[0]] = token[1:]
    if "W" not in params or "H" not in params:
        raise Y4mError("missing frame size")
    return params, end + 1


def planes(width, height, colorspace):
    cw, ch = width // 2, height // 2
    if colorspace.startswith("422"):
        ch = height
    elif colorspace.startswith("444"):
        cw, ch = width, height
    return [(width, height), (cw, ch), (cw, ch)]


def frames(data, start, frame_bytes):
    pos = start
    out = []
    while pos < len(data):
        if data[pos:pos + 6] != b"FRAME\n":
            raise Y4mError(f"bad frame marker at {pos}")
        pos += 6
        out.append(data[pos:pos + frame_bytes])
        pos += frame_bytes
    return out


def extract(data, start, frame_bytes, index):
    """Raw planes of frame `index` (0-based)."""
    pos = start + index * (6 + frame_bytes)
    if pos + 6 + frame_bytes > len(data):
        raise Y4mError(f"no frame {index}")
    return data[pos + 6:pos + 6 + frame_bytes]
