"""Decoding of the individual metadata blocks."""


def streaminfo(b):
    bits = int.from_bytes(b[10:18], "big")
    return {
        "min_blocksize": int.from_bytes(b[0:2], "big"),
        "max_blocksize": int.from_bytes(b[2:4], "big"),
        "sample_rate": bits >> 44,
        "channels": (bits >> 41) & 7,
        "bits_per_sample": (bits >> 36) & 31,
        "total_samples": bits & 0xFFFFFFFF,
        "md5": b[18:34].hex(),
    }


def vorbis_comment(b):
    n = int.from_bytes(b[0:4], "big")
    vendor = b[4:4 + n].decode("utf-8", "replace")
    pos = 4 + n
    count = int.from_bytes(b[pos:pos + 4], "big")
    pos += 4
    tags = {}
    for _ in range(count):
        size = int.from_bytes(b[pos:pos + 4], "big")
        text = b[pos + 4:pos + 4 + size].decode("utf-8", "replace")
        pos += 4 + size
        parts = text.split("=")
        tags[parts[0]] = parts[1] if len(parts) > 1 else ""
    return vendor, tags


def seektable(b):
    points = []
    for i in range(0, len(b) - len(b) % 18, 18):
        points.append((int.from_bytes(b[i:i + 8], "big"), int.from_bytes(b[i + 8:i + 16], "big"),
                       int.from_bytes(b[i + 16:i + 18], "big")))
    return points


def picture(b):
    def u32(p):
        return int.from_bytes(b[p:p + 4], "little")
    kind = int.from_bytes(b[0:4], "big")
    n = u32(4)
    mime = b[8:8 + n].decode("ascii", "replace")
    pos = 8 + n
    d = u32(pos)
    desc = b[pos + 4:pos + 4 + d].decode("utf-8", "replace")
    pos += 4 + d
    width, height, depth, colors, size = (u32(pos + 4 * i) for i in range(5))
    return {"type": kind, "mime": mime, "description": desc, "width": width, "height": height,
            "depth": depth, "size": size}
