"""Quake WAD2 and Half-Life WAD3 directory, lumps, mip textures and palettes."""
import struct

PALETTE = 0x40
QPIC = 0x42
MIPTEX_WAD3 = 0x43
MIPTEX_WAD2 = 0x44
FONT_CODES = {0x45, 0x46}

# Keep the historical name for code that imported it.  Both miptex codes are
# accepted; WAD2 files conventionally use 0x44 and WAD3 files use 0x43.
TYPES = {
    PALETTE: "palette",
    QPIC: "qpic",
    MIPTEX_WAD3: "miptex",
    MIPTEX_WAD2: "miptex",
    0x45: "font",
    0x46: "font",
}


class WadError(ValueError):
    pass


def _hex(pal, i):
    if pal is None or i < 0 or i >= len(pal):
        return None
    colour = pal[i]
    if len(colour) != 3:
        return None
    return "#%02x%02x%02x" % tuple(colour)


def _type_name(typ):
    if typ == PALETTE:
        return "palette"
    if typ == QPIC:
        return "qpic"
    if typ == MIPTEX_WAD2:
        return "miptex"
    # WAD3's miptex code is 0x43.  Accept it in WAD2 as well: some tools use
    # the two values interchangeably for the same 40-byte mip header.
    if typ == MIPTEX_WAD3:
        return "miptex"
    if typ in FONT_CODES:
        return "font"
    return "other"


def _raw_palette(data, pos, disk_size):
    """Read a standalone 256-colour RGB palette."""
    if pos < 0 or disk_size < 768 or pos + 768 > len(data):
        return None
    return [tuple(data[pos + 3 * i:pos + 3 * i + 3]) for i in range(256)]


def _embedded_palette(data, start, lump_end):
    """Read the count-prefixed palette appended to a WAD3 image lump."""
    if start is None or start < 0 or start + 2 > lump_end:
        return None

    count = struct.unpack_from("<H", data, start)[0]
    pal_start = start + 2
    if 0 < count <= 256 and pal_start + 3 * count <= lump_end:
        return [
            tuple(data[pal_start + 3 * i:pal_start + 3 * i + 3])
            for i in range(count)
        ]

    # A few writers omit the colour-count word and append 768 RGB bytes.
    if start + 768 <= lump_end:
        return [tuple(data[start + 3 * i:start + 3 * i + 3]) for i in range(256)]
    return None


def _span(pos, lump_end, offset, size, minimum_offset=0):
    """Return the absolute start of an in-package pixel range, or None."""
    if pos is None or pos < 0 or offset < minimum_offset or size < 0:
        return None
    start = pos + offset
    end = start + size
    if start < pos or start > lump_end or end > lump_end:
        return None
    return start


def _animation(name):
    """Decode ``+nNAME`` frames and the separate ``+aNAME`` alternate set."""
    if len(name) < 2 or name[0] != "+":
        return None
    code = name[1]
    if "0" <= code <= "9":
        return {"frame": ord(code) - ord("0"), "alternate": False}
    if "A" <= code <= "J":
        return {"frame": ord(code) - ord("A"), "alternate": True}
    if "a" <= code <= "j":
        return {"frame": ord(code) - ord("a"), "alternate": True}
    return None


def _empty_item(name, typ):
    kind = _type_name(typ)
    item = {
        "name": name,
        "type": kind,
        "width": None,
        "height": None,
        "mip_sizes": None,
        "first_pixel": None,
        "smallest_pixel": None,
        "transparent": False,
        "transparent_pixels": 0,
        "animation": None,
    }
    if kind == "miptex":
        item["transparent"] = name.startswith("{")
        item["animation"] = _animation(name)
    return item


def parse(data):
    if len(data) < 12 or data[:4] not in (b"WAD2", b"WAD3"):
        raise WadError("not a WAD2 or WAD3 file")

    version = data[:4].decode("ascii")
    n, dirofs = struct.unpack_from("<2i", data, 4)
    if n < 0 or dirofs < 12:
        raise WadError("bad directory")

    complete = dirofs >= 0 and dirofs + 32 * n <= len(data)
    entries = []
    for i in range(n):
        entry_ofs = dirofs + 32 * i
        if entry_ofs < 0 or entry_ofs + 32 > len(data):
            complete = False
            break
        pos, disk, _uncompressed, typ, _compression = struct.unpack_from(
            "<3iBB", data, entry_ofs
        )
        name = data[entry_ofs + 16:entry_ofs + 32].split(b"\0", 1)[0].decode("latin-1")
        entries.append((name, pos, disk, typ))

    # Quake WAD2 uses one package-global PALETTE lump.
    shared_palette = None
    for name, pos, disk, typ in entries:
        if typ == PALETTE and name.upper() == "PALETTE":
            palette = _raw_palette(data, pos, disk)
            if palette is not None:
                shared_palette = palette

    out = []
    for name, pos, disk, typ in entries:
        item = _empty_item(name, typ)
        kind = item["type"]

        lump_inside = pos >= 0 and disk >= 0 and pos + disk <= len(data)
        if not lump_inside:
            complete = False

        if pos < 0 or disk < 0:
            out.append(item)
            continue
        lump_end = min(len(data), pos + disk)

        if kind == "miptex":
            if pos + 40 <= lump_end:
                w, h, off0, off1, off2, off3 = struct.unpack_from(
                    "<6I", data, pos + 16
                )
                sizes = [(w >> level) * (h >> level) for level in range(4)]
                item.update(width=w, height=h, mip_sizes=sizes)

                palette = shared_palette if version == "WAD2" else None
                start3 = _span(pos, lump_end, off3, sizes[3], minimum_offset=40)
                if version == "WAD3" and start3 is not None:
                    palette = _embedded_palette(
                        data, start3 + sizes[3], lump_end
                    )

                start0 = _span(pos, lump_end, off0, sizes[0], minimum_offset=40)
                if w > 0 and h > 0 and start0 is not None:
                    item["first_pixel"] = _hex(palette, data[start0])
                    if item["transparent"]:
                        pixels = data[start0:start0 + sizes[0]]
                        item["transparent_pixels"] = pixels.count(255)

                if sizes[3] > 0 and start3 is not None:
                    index = data[start3 + sizes[3] - 1]
                    item["smallest_pixel"] = _hex(palette, index)

        elif kind == "qpic":
            if pos + 8 <= lump_end:
                w, h = struct.unpack_from("<2I", data, pos)
                item.update(width=w, height=h)
                if w > 0 and h > 0:
                    size = w * h
                    pixel_start = pos + 8
                    if pixel_start + size <= lump_end:
                        palette = shared_palette
                        if version == "WAD3":
                            palette = _embedded_palette(
                                data, pixel_start + size, lump_end
                            )
                        item["first_pixel"] = _hex(palette, data[pixel_start])

        out.append(item)

    return {
        "version": version,
        "lump_count": n,
        "complete": complete,
        "lumps": out,
    }
