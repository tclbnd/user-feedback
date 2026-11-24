"""Downloadable Sounds banks (.dls, DLS level 1 and 2): instruments, regions, wave pool."""
import struct


class DlsError(ValueError):
    pass


def chunks(data, start, end):
    p = start
    while p + 8 <= end:
        cid, size = data[p:p + 4], struct.unpack_from("<I", data, p + 4)[0]
        body = p + 8
        if body + size > end:
            raise DlsError("chunk %r runs past its parent" % cid)
        if cid in (b"LIST", b"RIFF"):
            yield cid, data[body:body + 4], body + 4, body + size
        else:
            yield cid, None, body, body + size
        # RIFF chunks are padded to an even number of bytes; the pad byte
        # is not counted in the chunk size and must be skipped.
        p = body + size + (size & 1)


LOOP_TYPES = {0: "forward", 1: "release"}


def sample_info(data, b):
    """unity note, gain in dB and the first loop of a wsmp chunk."""
    size, unity, _fine, gain, _opts, loops = struct.unpack_from("<IHhiII", data, b)
    loop = None
    if loops:
        _ls, ltype, start, length = struct.unpack_from("<IIII", data, b + size)
        loop = {"type": LOOP_TYPES.get(ltype, "type_%d" % ltype), "start": start, "length": length}
    return {"unity_note": unity, "gain_db": "%.2f" % (gain / 65536), "loop": loop}


def info_name(data, b, e):
    for cid, _, cb, ce in chunks(data, b, e):
        if cid == b"INAM":
            return data[cb:ce].split(b"\0")[0].decode("latin-1") or None
    return None


def parse(data):
    if data[:4] != b"RIFF" or data[8:12] != b"DLS ":
        raise DlsError("not a DLS bank")
    end = min(len(data), 8 + struct.unpack_from("<I", data, 4)[0])
    out = {"version": None, "instruments": [], "waves": 0, "wave_frames": 0, "name": None}
    cues, wave_offsets, wave_frames, wave_wsmp = [], [], [], []
    insts = []
    for cid, kind, b, e in chunks(data, 12, end):
        if cid == b"vers":
            ms, ls = struct.unpack_from("<II", data, b)
            out["version"] = "%d.%d.%d.%d" % (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
        elif cid == b"ptbl":
            size, count = struct.unpack_from("<II", data, b)
            cues = list(struct.unpack_from("<%dI" % count, data, b + 8))
        elif cid == b"LIST" and kind == b"INFO":
            out["name"] = info_name(data, b, e)
        elif cid == b"LIST" and kind == b"wvpl":
            for wcid, wkind, wb, we in chunks(data, b, e):
                if wkind != b"wave":
                    continue
                # pool table offsets point at the wave's LIST chunk header,
                # measured from the start of the wave pool's content
                wave_offsets.append(wb - 4 - 8 - b)
                frames = 0
                align = 1
                ws = None
                for c2, _, cb, ce in chunks(data, wb, we):
                    if c2 == b"wsmp":
                        ws = sample_info(data, cb)
                    elif c2 == b"fmt ":
                        align = struct.unpack_from("<H", data, cb + 12)[0] or 1
                    elif c2 == b"data":
                        frames = (ce - cb) // align
                wave_frames.append(frames)
                wave_wsmp.append(ws)
        elif cid == b"LIST" and kind == b"lins":
            for icid, ikind, ib, ie in chunks(data, b, e):
                if ikind == b"ins ":
                    insts.append((ib, ie))
    out["waves"] = len(wave_frames)
    out["wave_frames"] = sum(wave_frames)
    by_offset = {off: i for i, off in enumerate(wave_offsets)}
    for ib, ie in insts:
        inst = {"name": None, "bank_msb": None, "bank_lsb": None, "program": None, "drums": False, "regions": []}
        for cid, kind, b, e in chunks(data, ib, ie):
            if cid == b"insh":
                _count, bank, program = struct.unpack_from("<III", data, b)
                # ulBank: bit 31 marks a drum kit, bits 8-15 the MSB, bits 0-7 the LSB
                inst.update(bank_msb=(bank >> 8) & 0x7F, bank_lsb=bank & 0x7F,
                            program=program & 0x7F, drums=bool(bank & 0x80000000))
            elif cid == b"LIST" and kind == b"INFO":
                inst["name"] = info_name(data, b, e)
            elif cid == b"LIST" and kind == b"lrgn":
                # DLS Level 1 uses "rgn " regions, Level 2 writes "rgn2"
                for rcid, rkind, rb, re_ in chunks(data, b, e):
                    if rkind not in (b"rgn ", b"rgn2"):
                        continue
                    reg = {"keys": None, "wave": None, "looped": False}
                    own = None
                    table_index = None
                    for c2, _, cb, ce in chunks(data, rb, re_):
                        if c2 == b"rgnh":
                            lo, hi = struct.unpack_from("<HH", data, cb)
                            reg["keys"] = [lo, hi]
                        elif c2 == b"wlnk":
                            table_index, = struct.unpack_from("<I", data, cb + 8)
                        elif c2 == b"wsmp":
                            own = sample_info(data, cb)
                    # the wave link indexes the pool table; the table entry
                    # is the wave's offset inside the wave pool
                    if table_index is not None and table_index < len(cues):
                        reg["wave"] = by_offset.get(cues[table_index])
                    # sample information: the region's own wsmp wins, otherwise
                    # the wsmp of the wave the region plays applies
                    si = own
                    if si is None and reg["wave"] is not None:
                        si = wave_wsmp[reg["wave"]]
                    if si is None:
                        si = {"unity_note": None, "gain_db": None, "loop": None}
                    reg.update(si)
                    reg["looped"] = si["loop"] is not None
                    inst["regions"].append(reg)
        out["instruments"].append(inst)
    return out
