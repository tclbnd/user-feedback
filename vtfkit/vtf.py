"""Valve texture files (.vtf): header, thumbnail, resources and the layout of the high-resolution image data."""
import struct

FORMATS = ["rgba8888", "abgr8888", "rgb888", "bgr888", "rgb565", "i8", "ia88", "p8", "a8",
           "rgb888_bluescreen", "bgr888_bluescreen", "argb8888", "bgra8888", "dxt1", "dxt3", "dxt5",
           "bgrx8888", "bgr565", "bgrx5551", "bgra4444", "dxt1_onebitalpha", "bgra5551", "uv88",
           "uvwq8888", "rgba16161616f", "rgba16161616", "uvlx8888"]
BYTES = {"rgba8888": 4, "abgr8888": 4, "rgb888": 3, "bgr888": 3, "rgb565": 2, "i8": 1, "ia88": 2, "p8": 1, "a8": 1,
         "rgb888_bluescreen": 3, "bgr888_bluescreen": 3, "argb8888": 4, "bgra8888": 4, "bgrx8888": 4, "bgr565": 2,
         "bgrx5551": 2, "bgra4444": 2, "bgra5551": 2, "uv88": 2, "uvwq8888": 4, "rgba16161616f": 8,
         "rgba16161616": 8, "uvlx8888": 4}
BLOCK = {"dxt1": 8, "dxt1_onebitalpha": 8, "dxt3": 16, "dxt5": 16}
ENVMAP = 0x4000


class VtfError(ValueError):
    pass


def fmt_name(v):
    if v == 0xFFFFFFFF or v == -1:
        return None
    if not 0 <= v < len(FORMATS):
        raise VtfError("unknown image format %d" % v)
    return FORMATS[v]


def image_bytes(fmt, w, h):
    if fmt in BLOCK:
        return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * BLOCK[fmt]
    return w * h * BYTES[fmt]


def parse(data):
    if len(data) < 64 or data[:4] != b"VTF\0":
        raise VtfError("not a VTF file")
    major, minor, header_size = struct.unpack_from("<3I", data, 4)
    if major != 7 or minor > 5:
        raise VtfError("unsupported version %d.%d" % (major, minor))
    width, height, flags, frames, first_frame = struct.unpack_from("<HHIHH", data, 16)
    high_fmt = fmt_name(struct.unpack_from("<I", data, 52)[0])
    mips = data[56]
    low_fmt = fmt_name(struct.unpack_from("<I", data, 57)[0])
    low_w, low_h = data[61], data[62]
    depth = struct.unpack_from("<H", data, 63)[0] or 1
    frames = frames or 1
    faces = 6 if flags & ENVMAP else 1
    thumb = image_bytes(high_fmt, low_w, low_h) if low_fmt and low_w and low_h else 0

    def size(m):
        return image_bytes(high_fmt, max(1, width >> m), max(1, height >> m)) * max(1, depth >> m)

    high_total = sum(size(m) for m in range(mips)) * frames * faces
    high_start = header_size + thumb
    largest = high_start
    return {"version": "%d.%d" % (major, minor), "width": width, "height": height, "depth": depth,
            "format": high_fmt, "mipmaps": mips, "frames": frames, "faces": faces,
            "thumbnail": [low_w, low_h] if thumb else None, "thumbnail_format": low_fmt if thumb else None,
            "image_bytes": high_total, "largest_mip_offset": largest,
            "complete": high_start + high_total <= len(data)}
