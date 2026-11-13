"""Temporary container inspection helper (not part of the deliverable)."""
import sys

NAMES = {
    0x1A45DFA3: "EBML", 0x18538067: "Segment", 0x114D9B74: "SeekHead",
    0x1549A966: "Info", 0x1654AE6B: "Tracks", 0x1F43B675: "Cluster",
    0x1C53BB6B: "Cues", 0x1254C367: "Tags", 0x1043A770: "Chapters",
    0xEC: "Void", 0xBF: "CRC-32", 0xE7: "Timestamp", 0xA3: "SimpleBlock",
    0xA0: "BlockGroup", 0xA1: "Block", 0xFB: "ReferenceBlock",
    0x9B: "BlockDuration", 0x2AD7B1: "TimestampScale", 0x4489: "Duration",
    0x73A4: "SegmentUID", 0x4D80: "MuxingApp", 0x5741: "WritingApp",
    0xAE: "TrackEntry", 0xD7: "TrackNumber", 0x73C5: "TrackUID",
    0x83: "TrackType", 0x86: "CodecID", 0x63A2: "CodecPrivate",
    0x22B59C: "Language", 0x536E: "Name", 0xE1: "Audio",
    0xE0: "Video", 0xB0: "PixelWidth", 0xBA: "PixelHeight",
    0x55B0: "Colour", 0x55D0: "MasteringMetadata",
}


def read_id(data, off):
    n = 0
    first = data[off]
    for i in range(1, 9):
        if first & (1 << (8 - i)):
            n = i
            break
    return n, int.from_bytes(data[off:off + n], "big")


def size_vint(data, off):
    n = 0
    first = data[off]
    for i in range(1, 9):
        if first & (1 << (8 - i)):
            n = i
            break
    raw = int.from_bytes(data[off:off + n], "big")
    value = raw & ((1 << (7 * n)) - 1)
    unknown = value == (1 << (7 * n)) - 1
    return n, value, unknown


def read_track_vint(data, off):
    n = 0
    first = data[off]
    for i in range(1, 9):
        if first & (1 << (8 - i)):
            n = i
            break
    raw = int.from_bytes(data[off:off + n], "big")
    return n, raw & ((1 << (7 * n)) - 1)


def parse_block_payload(data, start, end, cluster_ts):
    n, track = read_track_vint(data, start)
    rel = int.from_bytes(data[start + n:start + n + 2], "big", signed=True)
    flags = data[start + n + 2]
    pos = start + n + 3
    lacing = (flags >> 1) & 3
    frames = []
    if lacing == 0:
        frames = [(pos, end)]
        sizes = [end - pos]
    elif lacing == 1:  # Xiph
        count = data[pos] + 1
        pos += 1
        sizes = []
        for _ in range(count - 1):
            size = 0
            while True:
                b = data[pos]
                pos += 1
                size += b
                if b != 255:
                    break
            sizes.append(size)
        sizes.append(end - pos - sum(sizes))
        for size in sizes:
            frames.append((pos, pos + size))
            pos += size
    elif lacing == 3:  # EBML
        count = data[pos] + 1
        pos += 1
        sizes = []
        for _ in range(count - 1):
            sn, sz, _ = size_vint(data, pos)
            sizes.append(sz)
            pos += sn
        sizes.append(end - pos - sum(sizes))
        for size in sizes:
            frames.append((pos, pos + size))
            pos += size
    else:  # fixed
        count = data[pos] + 1
        pos += 1
        each = (end - pos) // count
        sizes = [each] * count
        for _ in range(count):
            frames.append((pos, pos + each))
            pos += each
    key = bool(flags & 0x80)
    inv = bool(flags & 0x08)
    disc = bool(flags & 0x01)
    return track, rel, lacing, key, inv, disc, sizes


def dump_cluster(data, start, end, out, depth):
    off = start
    ts = 0
    while off < end:
        n, eid = read_id(data, off)
        n2, size, unk = size_vint(data, off + n)
        payload = off + n + n2
        if eid == 0xE7:
            ts = int.from_bytes(data[payload:payload + size], "big")
            task = "ts"
        elif eid == 0xA3:
            task = "block"
        elif eid == 0xA0:
            task = "group"
        else:
            task = "other"
        if task in ("block", "group"):
            block_off = payload
            block_end = payload + size
            if task == "group":
                p = payload
                be = payload + size
                while p < be:
                    bn, bid = read_id(data, p)
                    bs_n, bsz, bunk = size_vint(data, p + bn)
                    if bid == 0xA1:
                        block_off = p + bn + bs_n
                        block_end = block_off + bsz
                        break
                    p += bn + bs_n + bsz
            track, rel, lacing, key, inv, disc, sizes = parse_block_payload(data, block_off, block_end, ts)
            print("%s%8d %-6s track=%d rel=%d time=%d lac=%d key=%s frames=%d sizes=%s"
                  % ("  " * depth, off, task, track, rel, ts + rel, lacing, key,
                     len(sizes), sizes))
        else:
            print("%s%8d id=%#x %s size=%d" % ("  " * depth, off, eid, NAMES.get(eid, "?"), size))
        if unk:
            print("unknown size cluster")
            return
        off = payload + size


def walk(data, start, end, depth):
    off = start
    while off < end:
        n, eid = read_id(data, off)
        n2, size, unk = size_vint(data, off + n)
        print("%s%8d id=%#x %-10s size=%d unk=%s hdr=%d"
              % ("  " * depth, off, eid, NAMES.get(eid, "?"), size, unk, n + n2))
        payload = off + n + n2
        if eid == 0x1F43B675:
            dump_cluster(data, payload, payload + size if not unk else end, None, depth + 1)
        elif eid in (0x18538067, 0x114D9B74, 0x1549A966, 0x1654AE6B, 0x1C53BB6B):
            walk(data, payload, payload + size if not unk else end, depth + 1)
        off = payload + (size if not unk else end)


def main(path):
    data = open(path, "rb").read()
    print("size", len(data), "head", data[:32].hex())
    walk(data, 0, len(data), 0)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "demo.mkv")