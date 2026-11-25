"""Build engine tile archives (TILES###.ART): header, tile sizes, picanm words, column-major pixels."""
import struct

ANIM = ["none", "oscillate", "forward", "backward"]
TRANSPARENT = 255


class ArtError(ValueError):
    pass


def signed8(v):
    """Interpret a byte as a signed 8-bit value (picanm offsets are signed)."""
    return v - 256 if v & 128 else v


def parse(data):
    if len(data) < 16:
        raise ArtError("not an ART file")
    version, _, start, end = struct.unpack_from("<4I", data, 0)
    if version != 1 or end < start:
        raise ArtError("not an ART file")
    # The header is inclusive: slots start..end, i.e. end - start + 1 tiles.
    n = end - start + 1
    p = 16
    sx = struct.unpack_from("<%dh" % n, data, p); p += 2 * n
    sy = struct.unpack_from("<%dh" % n, data, p); p += 2 * n
    anm = struct.unpack_from("<%dI" % n, data, p); p += 4 * n
    tiles, total, complete = [], 0, True
    for i in range(n):
        w, h, a = sx[i], sy[i], anm[i]
        size = w * h if w > 0 and h > 0 else 0
        px = data[p:p + size]
        if len(px) < size:
            complete = False
        p += size
        total += size
        if not size:
            continue

        def at(x, y):
            # Pixels are stored column by column: x is the outer index, y the inner.
            k = x * h + y
            return px[k] if k < len(px) else None

        tiles.append({"tile": start + i, "width": w, "height": h,
                      "frames": (a >> 2) & 63, "animation": ANIM[a & 3],
                      "offset": [signed8((a >> 8) & 255), signed8((a >> 16) & 255)],
                      "speed": (a >> 24) & 255,
                      "transparent": sum(1 for b in px if b == TRANSPARENT) if len(px) == size else None,
                      "top_right": at(w - 1, 0), "bottom_left": at(0, h - 1)})
    return {"first_tile": start, "last_tile": end, "tiles": n, "nonempty": len(tiles), "pixel_bytes": total,
            "complete": complete, "tile_list": tiles}
