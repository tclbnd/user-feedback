"""APE tags (APEv1 and APEv2) at the end of an audio file."""
import struct

PREAMBLE = b"APETAGEX"
FOOTER_SIZE = 32
HEADER_SIZE = 32
ID3V1_SIZE = 128

# Tag-level flags (footer/header flags field).
TAG_FLAG_READ_ONLY = 0x00000001
TAG_FLAG_IS_HEADER = 0x20000000  # set in the header copy only
TAG_FLAG_NO_FOOTER = 0x40000000
TAG_FLAG_HAS_HEADER = 0x80000000

# Item-level flags.
ITEM_FLAG_READ_ONLY = 0x00000001
ITEM_TYPE_SHIFT = 1
ITEM_TYPES = ("text", "binary", "locator", "reserved")

# Keys forbidden by the APE specification (compared case-insensitively).
RESERVED_KEYS = frozenset(["id3", "tag", "oggs", "mp+"])

COVER_ART_PREFIX = "cover art ("


class ApeError(ValueError):
    pass


def _valid_key(raw_key):
    """An APE item key is 2..255 printable ASCII chars (0x20-0x7E)
    and none of the reserved keys ID3, TAG, OggS, MP+."""
    if not 2 <= len(raw_key) <= 255:
        return False
    if any(c < 0x20 or c > 0x7E for c in raw_key):
        return False
    return raw_key.decode("ascii").lower() not in RESERVED_KEYS


def _strip_id3v1(data, end):
    """Return (new_end, has_id3v1): an ID3v1 tag sits in the last 128 bytes."""
    if end >= ID3V1_SIZE and data[end - ID3V1_SIZE:end - ID3V1_SIZE + 3] == b"TAG":
        return end - ID3V1_SIZE, True
    return end, False


def _strip_lyrics3(data, end):
    """Bytes of a Lyrics3 v2.00 block ending at ``end`` (0 if none).

    Layout: "LYRICSBEGIN" + <fields> + 6 decimal size digits + "LYRICS200",
    where the size counts the field bytes only.
    """
    if end >= 15 and data[end - 9:end] == b"LYRICS200":
        digits = data[end - 15:end - 9]
        if digits.isdigit():
            total = 11 + int(digits) + 15
            if total <= end and data[end - total:end - total + 11] == b"LYRICSBEGIN":
                return total
    return 0


def _parse_item(data, p):
    """Parse one item at offset p; return (item_dict_or_None, next_offset).

    Items whose key is not a valid APE item key yield None.
    """
    vsize, iflags = struct.unpack_from("<II", data, p)
    k_end = data.index(b"\0", p + 8)
    raw_key = data[p + 8:k_end]
    value = data[k_end + 1:k_end + 1 + vsize]
    next_p = k_end + 1 + vsize

    try:
        key = raw_key.decode("ascii")
    except UnicodeDecodeError:
        return None, next_p
    if not _valid_key(raw_key):
        return None, next_p

    item = {
        "key": key,
        "type": ITEM_TYPES[(iflags >> ITEM_TYPE_SHIFT) & 0x3],
        "read_only": bool(iflags & ITEM_FLAG_READ_ONLY),
    }
    if item["type"] == "text":
        # Multi-value text items separate values with NUL bytes.
        item["values"] = [v.decode("utf-8") for v in value.split(b"\0")]
    elif item["type"] == "binary" and key.lower().startswith(COVER_ART_PREFIX):
        # Cover art: NUL-terminated filename, then the image data.
        name, _, image = value.partition(b"\0")
        item["filename"] = name.decode("utf-8", "replace")
        item["bytes"] = len(image)
    else:
        item["bytes"] = len(value)
    return item, next_p


def parse(data):
    end = len(data)
    end, id3v1 = _strip_id3v1(data, end)
    lyrics3_bytes = _strip_lyrics3(data, end)
    end -= lyrics3_bytes

    pos = end - FOOTER_SIZE
    if pos < 0 or data[pos:pos + 8] != PREAMBLE:
        raise ApeError("no APE tag footer")
    version, size, count, flags = struct.unpack_from("<IIII", data, pos + 8)

    # The size field covers items + footer; a header adds another 32 bytes.
    has_header = bool(flags & TAG_FLAG_HAS_HEADER)
    tag_bytes = size + (HEADER_SIZE if has_header else 0)
    start = end - tag_bytes
    if start < 0:
        raise ApeError("APE tag is larger than the file")

    items, invalid_keys = [], 0
    p = start + (HEADER_SIZE if has_header else 0)
    for _ in range(count):
        item, p = _parse_item(data, p)
        if item is None:
            invalid_keys += 1
        else:
            items.append(item)

    return {
        "version": "%d.%03d" % (version // 1000, version % 1000),
        "has_header": has_header,
        "read_only": bool(flags & TAG_FLAG_READ_ONLY),
        "id3v1": id3v1,
        "lyrics3_bytes": lyrics3_bytes,
        "tag_bytes": tag_bytes,
        "audio_bytes": start,
        "invalid_keys": invalid_keys,
        "items": items,
    }
