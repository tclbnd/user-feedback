"""APE tags at the end of an audio file."""
import struct


class ApeError(ValueError):
    pass


def parse(data):
    pos = len(data) - 32
    if pos < 0 or data[pos:pos + 8] != b"APETAGEX":
        raise ApeError("no APE tag footer")
    version, size, count, flags = struct.unpack_from("<IIII", data, pos + 8)
    has_header = bool(flags & 0x20000000)
    start = pos + 32 - size
    items, p = [], start
    for _ in range(count):
        vsize, iflags = struct.unpack_from("<II", data, p)
        k_end = data.index(b"\0", p + 8)
        key = data[p + 8:k_end].decode("ascii")
        value = data[k_end + 1:k_end + 1 + vsize]
        item = {"key": key, "type": "binary" if iflags & 1 else "text", "read_only": False}
        if item["type"] == "text":
            item["values"] = [value.decode("utf-8")]
        else:
            item["bytes"] = len(value)
        items.append(item)
        p = k_end + 1 + vsize
    return {
        "version": "%d.%03d" % (version // 1000, version % 1000),
        "has_header": has_header, "read_only": bool(flags & 1),
        "id3v1": False, "lyrics3_bytes": 0, "tag_bytes": size, "audio_bytes": start, "invalid_keys": 0,
        "items": items,
    }
