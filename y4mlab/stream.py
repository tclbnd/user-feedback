"""YUV4MPEG2 header, plane layout and frames."""


class Y4mError(ValueError):
    pass


def header(data):
    """Parse the YUV4MPEG2 header line.

    Returns ``(params, x_params, offset)``:

    * ``params`` maps the single-letter header tags (W, H, F, I, A, C, ...);
    * ``x_params`` maps every X-extension tag (key without the leading X,
      e.g. ``COLORRANGE``); repeated tags keep the last occurrence, which is
      the effective one per the YUV4MPEG2 convention;
    * ``offset`` is the byte position right after the header line.
    """
    end = data.find(b"\n")
    if end < 0 or not data.startswith(b"YUV4MPEG2"):
        raise Y4mError("not a YUV4MPEG2 stream")
    tokens = data[:end].decode("ascii", "replace").split(" ")
    if tokens[0] != "YUV4MPEG2":
        raise Y4mError("not a YUV4MPEG2 stream")
    params, x_params = {}, {}
    for token in tokens[1:]:
        if not token:
            continue
        if token[0] == "X":
            key, sep, value = token[1:].partition("=")
            if key:
                x_params[key] = value if sep else ""
        else:
            params[token[0]] = token[1:]
    if "W" not in params or "H" not in params:
        raise Y4mError("missing frame size")
    return params, x_params, end + 1


def colorspace_info(colorspace):
    """Split a C tag into ``(bit_depth, sampling, has_alpha)``.

    ``sampling`` is one of ``mono``, ``411``, ``420``, ``422``, ``444``.
    The jpeg/mpeg/paldv siting variants share 420 geometry. Depth suffixes
    are ``p9..p16`` for colour tags and ``10..16`` for mono tags; alpha,
    when present, always comes last (e.g. ``422p10alpha``).
    """
    tag = (colorspace or "420jpeg").lower()
    has_alpha = tag.endswith("alpha")
    if has_alpha:
        tag = tag[:-len("alpha")]
    depth = 8
    stripped = False
    for bits in (16, 14, 12, 10, 9):
        suffix = "p" + str(bits)
        if tag.endswith(suffix):
            depth = bits
            tag = tag[:-len(suffix)]
            stripped = True
            break
    if not stripped and tag.startswith("mono") and tag[4:].isdigit():
        depth = int(tag[4:])
        tag = "mono"
    return depth, tag, has_alpha


def bit_depth(colorspace):
    """Sample bit depth of a C tag (8, 9, 10, 12, 14 or 16)."""
    return colorspace_info(colorspace)[0]


def sample_bytes(colorspace):
    """Bytes per sample; depths above 8 use little-endian 16-bit words."""
    return 2 if bit_depth(colorspace) > 8 else 1


def planes(width, height, colorspace):
    """Plane dimensions of one frame: luma, chroma planes, optional alpha.

    Chroma dimensions are rounded up, so odd frame sizes work (a 33x19
    420 frame has 17x10 chroma planes).
    """
    _, sampling, has_alpha = colorspace_info(colorspace)
    if sampling == "mono":
        out = [(width, height)]
    else:
        if sampling == "444":
            cw, ch = width, height
        elif sampling == "422":
            cw, ch = (width + 1) // 2, height
        elif sampling == "411":
            cw, ch = (width + 3) // 4, (height + 1) // 2
        else:  # 420, including 420jpeg / 420mpeg / 420paldv siting variants
            cw, ch = (width + 1) // 2, (height + 1) // 2
        out = [(width, height), (cw, ch), (cw, ch)]
    if has_alpha:
        out.append((width, height))
    return out


def frame_size(width, height, colorspace):
    """Raw byte size of one frame for the given format and bit depth."""
    bps = sample_bytes(colorspace)
    return sum(w * h for w, h in planes(width, height, colorspace)) * bps


def iter_frames(data, start, width, height, colorspace):
    """Yield the raw plane bytes of every frame.

    FRAME lines may carry per-frame parameters (``FRAME I T ...``); the
    whole line is skipped instead of assuming a fixed 6-byte marker.
    """
    size = frame_size(width, height, colorspace)
    pos, end_pos = start, len(data)
    while pos < end_pos:
        line_end = data.find(b"\n", pos)
        if line_end < 0:
            raise Y4mError(f"bad frame marker at {pos}")
        line = data[pos:line_end]
        if line != b"FRAME" and not line.startswith(b"FRAME "):
            raise Y4mError(f"bad frame marker at {pos}")
        pos = line_end + 1
        if pos + size > end_pos:
            raise Y4mError("truncated frame data")
        yield data[pos:pos + size]
        pos += size


def frames(data, start, width, height, colorspace):
    """Raw plane bytes of every frame."""
    return list(iter_frames(data, start, width, height, colorspace))


def extract(data, start, width, height, colorspace, index):
    """Raw planes of frame ``index`` (0-based), walking FRAME lines."""
    if index < 0:
        raise Y4mError(f"no frame {index}")
    for i, raw in enumerate(iter_frames(data, start, width, height, colorspace)):
        if i == index:
            return raw
    raise Y4mError(f"no frame {index}")


def luma_mean(raw, width, height, colorspace):
    """Mean of the luma plane in sample units (0..2**bit_depth-1)."""
    bps = sample_bytes(colorspace)
    count = width * height
    plane = raw[:count * bps]
    if bps == 1:
        total = sum(plane)
    else:
        total = sum(
            int.from_bytes(plane[i:i + 2], "little")
            for i in range(0, len(plane), 2)
        )
    return round(total / count, 3)
