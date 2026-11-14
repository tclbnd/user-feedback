import struct
import sys
from pathlib import Path

CONTAINERS = {"moov", "trak", "mdia", "minf", "stbl", "edts", "dinf", "mvex", "moof", "traf", "udta", "ilst"}

FULL_HEX = {"mdhd", "mvhd", "tkhd", "elst", "stts", "ctts", "stss", "stsc", "stco", "co64", "stsz", "stsd", "avcC", "esds", "sgpd", "sbgp", "hdlr", "vmhd", "smhd", "dref", "dinf"}


def boxes(data, start=0, end=None):
    if end is None:
        end = len(data)
    pos = start
    while pos + 8 <= end:
        size = struct.unpack(">I", data[pos:pos + 4])[0]
        typ = data[pos + 4:pos + 8].decode("latin1")
        hdr = 8
        if size == 1:
            size = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
            hdr = 16
        elif size == 0:
            size = end - pos
        if size < hdr or pos + size > end:
            break
        yield typ, pos, pos + hdr, pos + size
        pos += size


def find(data, typ, start=0, end=None):
    if end is None:
        end = len(data)
    for t, p, hs, pe in boxes(data, start, end):
        if t == typ:
            yield p, hs, pe
        if t in CONTAINERS:
            extra = 4 if t == "meta" else 0
            yield from find(data, typ, hs + extra, pe)


def walk(data, start=0, end=None, depth=0):
    if end is None:
        end = len(data)
    for typ, p, hs, pe in boxes(data, start, end):
        print("  " * depth + "%s @%d size=%d" % (typ, p, pe - p))
        if typ in CONTAINERS:
            extra = 4 if typ == "meta" else 0
            walk(data, hs + extra, pe, depth + 1)


def parse_full(path):
    data = Path(path).read_bytes()
    walk(data)
    return data


def dump_tables(path):
    data = Path(path).read_bytes()
    names = ["elst", "stts", "ctts", "stss", "stsz", "stsc", "stco", "sgpd", "sbgp", "mdhd", "mvhd", "tkhd", "stsd"]
    for typ in names:
        print("===", typ)
        for p, hs, pe in find(data, typ):
            print(data[hs:pe].hex(), "len", pe - hs)


def u32(b, o):
    return struct.unpack_from(">I", b, o)[0]


def s32(b, o):
    return struct.unpack_from(">i", b, o)[0]


def u16(b, o):
    return struct.unpack_from(">H", b, o)[0]


def parse_track(data, trak_hs, trak_pe):
    info = {}
    mdia = list(boxes(data, trak_hs, trak_pe))
    elst = []
    for p, hs, pe in find(data, "elst", trak_hs, trak_pe):
        count = u32(data, hs + 4)
        off = hs + 8
        for _ in range(count):
            if u32(data, hs) == 1:
                dur = struct.unpack_from(">Q", data, off)[0]
                mt = struct.unpack_from(">q", data, off + 8)[0]
                off += 20
            else:
                dur = u32(data, off)
                mt = s32(data, off + 4)
                off += 12
            elst.append((dur, mt))
    info["elst"] = elst
    for p, hs, pe in find(data, "mdhd", trak_hs, trak_pe):
        ver = data[hs]
        if ver == 1:
            ts = u32(data, hs + 20)
            dur = struct.unpack_from(">Q", data, hs + 24)[0]
        else:
            ts = u32(data, hs + 12)
            dur = u32(data, hs + 16)
        info["timescale"] = ts
        info["duration"] = dur
    for p, hs, pe in find(data, "hdlr", trak_hs, trak_pe):
        info["handler"] = data[hs + 8:hs + 12].decode("latin1")
    for p, hs, pe in find(data, "stsd", trak_hs, trak_pe):
        count = u32(data, hs + 4)
        off = hs + 8
        for _ in range(count):
            size = u32(data, off)
            fmt = data[off + 4:off + 8].decode("latin1")
            info["codec"] = fmt
            info["stsd_entry"] = data[off:off + size]
            if fmt in ("avc1", "avc3"):
                w = u16(data, off + 8 + 24)
                h = u16(data, off + 8 + 26)
                info["width"] = w
                info["height"] = h
                # find avcC inside sample entry
                for t2, p2, hs2, pe2 in boxes(data, off + 8 + 78, off + size):
                    if t2 == "avcC":
                        info["avcC"] = data[hs2:pe2]
            elif fmt == "mp4a":
                info["channels"] = u16(data, off + 8 + 16)
                info["sample_rate"] = u32(data, off + 8 + 24) >> 16
                for t2, p2, hs2, pe2 in boxes(data, off + 8 + 28, off + size):
                    if t2 == "esds":
                        info["esds"] = data[hs2:pe2]
            off += size
    # tables
    stts = []
    for p, hs, pe in find(data, "stts", trak_hs, trak_pe):
        count = u32(data, hs + 4)
        off = hs + 8
        for _ in range(count):
            stts.append((u32(data, off), u32(data, off + 4)))
            off += 8
    info["stts"] = stts
    ctts = []
    for p, hs, pe in find(data, "ctts", trak_hs, trak_pe):
        ver = data[hs]
        count = u32(data, hs + 4)
        off = hs + 8
        for _ in range(count):
            if ver == 1:
                ctts.append((u32(data, off), s32(data, off + 4)))
            else:
                ctts.append((u32(data, off), u32(data, off + 4)))
            off += 8
    info["ctts"] = ctts
    stss = None
    for p, hs, pe in find(data, "stss", trak_hs, trak_pe):
        count = u32(data, hs + 4)
        stss = [u32(data, hs + 8 + 4 * i) for i in range(count)]
    info["stss"] = stss
    stsz = []
    for p, hs, pe in find(data, "stsz", trak_hs, trak_pe):
        sample_size = u32(data, hs + 4)
        count = u32(data, hs + 8)
        if sample_size:
            stsz = [sample_size] * count
        else:
            off = hs + 12
            for _ in range(count):
                stsz.append(u32(data, off))
                off += 4
    info["stsz"] = stsz
    stsc = []
    for p, hs, pe in find(data, "stsc", trak_hs, trak_pe):
        count = u32(data, hs + 4)
        off = hs + 8
        for _ in range(count):
            stsc.append((u32(data, off), u32(data, off + 4), u32(data, off + 8)))
            off += 12
    info["stsc"] = stsc
    stco = []
    for p, hs, pe in list(find(data, "stco", trak_hs, trak_pe)) + list(find(data, "co64", trak_hs, trak_pe)):
        is64 = data[p+4:p+8] == b"co64"
        count = u32(data, hs + 4)
        off = hs + 8
        for _ in range(count):
            stco.append(struct.unpack_from(">Q", data, off)[0] if is64 else u32(data, off))
            off += 8 if is64 else 4
    info["stco"] = stco
    return info


def track_infos(path):
    data = Path(path).read_bytes()
    out = []
    for typ, p, hs, pe in boxes(data):
        if typ == "moov":
            for t2, p2, hs2, pe2 in boxes(data, hs, pe):
                if t2 == "trak":
                    out.append(parse_track(data, hs2, pe2))
    return data, out


def samples_from_info(data, info):
    st = info["stts"]
    dts = 0
    durations = []
    for count, delta in st:
        for _ in range(count):
            durations.append(delta)
    ctts = info["ctts"]
    coffsets = []
    for count, off in ctts:
        for _ in range(count):
            coffsets.append(off)
    n = len(info["stsz"])
    if len(durations) != n:
        print("WARN durations", len(durations), n)
    if len(coffsets) != n:
        print("WARN ctts", len(coffsets), n)
    # sample offsets
    chunks = info["stco"]
    stsc = info["stsc"]
    offsets = []
    si = 0
    for ci, chunk_off in enumerate(chunks):
        # find applicable stsc entry
        entry = stsc[0]
        for e in stsc:
            if e[0] - 1 <= ci:
                entry = e
        per = entry[1]
        for j in range(per):
            if si >= n:
                break
            offsets.append(chunk_off + sum(info["stsz"][si - j:si]) if j else chunk_off)
            # simpler: cumulative within chunk
            if j:
                offsets[-1] = offsets[-2] + info["stsz"][si - 1]
            si += 1
    if len(offsets) != n:
        print("WARN offsets", len(offsets), n)
    # correct offsets using cumulative sizes within chunk
    offsets = []
    si = 0
    for ci, chunk_off in enumerate(chunks):
        entry = stsc[0]
        for e in stsc:
            if e[0] - 1 <= ci:
                entry = e
        per = entry[1]
        pos = chunk_off
        for j in range(per):
            if si >= n:
                break
            offsets.append(pos)
            pos += info["stsz"][si]
            si += 1
    samples = []
    for i in range(n):
        samples.append({
            "i": i,
            "dts": dts + sum(durations[:i]),
            "pts": dts + sum(durations[:i]) + (coffsets[i] if i < len(coffsets) else 0),
            "duration": durations[i] if i < len(durations) else 0,
            "size": info["stsz"][i],
            "offset": offsets[i],
            "key": (i + 1 in info["stss"]) if info["stss"] is not None else True,
        })
    return samples


def show_extradata(path):
    data, infos = track_infos(path)
    for info in infos:
        print("====", path, info.get("handler"), info.get("codec"))
        if "avcC" in info:
            print("avcC", info["avcC"].hex())
        if "esds" in info:
            print("esds", info["esds"].hex())


if __name__ == "__main__":
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    for p in args:
        print("====", p)
        if "--extradata" in sys.argv:
            show_extradata(p)
        elif "--tables" in sys.argv:
            dump_tables(p)
        elif "--samples" in sys.argv:
            data, infos = track_infos(p)
            for info in infos:
                print("track", info.get("handler"), info.get("codec"), "ts", info.get("timescale"), "dur", info.get("duration"), "elst", info.get("elst"), "stss", info.get("stss"))
                if info.get("handler") == "vide":
                    for s in samples_from_info(data, info):
                        print(s["i"], "key" if s["key"] else "   ", "dts", s["dts"], "pts", s["pts"], "dur", s["duration"], "size", s["size"])
        else:
            parse_full(p)

