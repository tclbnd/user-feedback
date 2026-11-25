"""Downloadable Sounds banks (.dls, DLS level 1 and 2): instruments, regions, wave pool."""
import struct


class DlsError(ValueError):
    pass


def chunks(data, start, end):
    # RIFF chunks with an odd body size are followed by one padding byte so
    # that the next chunk starts on a word boundary.
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
        p = body + size + (size & 1)


LOOP_TYPES = {0: "forward", 1: "release"}

# The bank field of an insh chunk packs three MIDI values into one DWORD:
# bit 31 marks a drum kit, bits 16-22 hold the bank select MSB and
# bits 24-30 the bank select LSB.
DRUM_BIT = 1 << 31


def sample_info(data, b):
    """unity note, gain in dB and the first loop of a wsmp chunk."""
    size, unity, _fine, gain, _opts, loops = struct.unpack_from("<IHhiII", data, b)
    loop = None
    if loops:
        # The loop table starts directly after the 20-byte wsmp header.
        _ls, ltype, start, length = struct.unpack_from("<IIII", data, b + 20)
        loop = {"type": LOOP_TYPES.get(ltype, "type_%d" % ltype), "start": start, "length": length}
    return {"unity_note": unity, "gain_db": "%.2f" % (gain / 65536), "loop": loop}


NULL_INFO = {"unity_note": None, "gain_db": None, "loop": None}


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
    cues = []
    wave_offsets, wave_frames, wave_wsmp = [], [], []
    insts = []
    for cid, kind, b, e in chunks(data, 12, end):
        if cid == b"vers":
            ms, ls = struct.unpack_from("<II", data, b)
            out["version"] = "%d.%d.%d.%d" % (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
        elif cid == b"ptbl":
            _size, count = struct.unpack_from("<II", data, b)
            # Some tools leave the ptbl size at its 8-byte header value even
            # though cues follow, so derive the count from the chunk's real
            # extent and never read past it.
            count = min(count, (e - b - 8) // 4)
            cues = list(struct.unpack_from("<%dI" % count, data, b + 8))
        elif cid == b"LIST" and kind == b"INFO":
            out["name"] = info_name(data, b, e)
        elif cid == b"LIST" and kind == b"wvpl":
            # Pool table cues are offsets of each wave's LIST chunk measured
            # from the start of the pool's wave data: eight bytes past the
            # pool body (its opening wave's LIST/size header precedes the
            # first "wave" label). A wave's own header sits at wb-4.
            pool_origin = b + 8
            for wcid, wkind, wb, we in chunks(data, b, e):
                if wkind != b"wave":
                    continue
                wave_offsets.append((wb - 4) - pool_origin)
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
    # Pool table cue (wave offset) -> index of the wave in the pool.
    by_offset = {off: i for i, off in enumerate(wave_offsets)}
    for ib, ie in insts:
        inst = {"name": None, "bank_msb": None, "bank_lsb": None, "program": None, "drums": False, "regions": []}
        for cid, kind, b, e in chunks(data, ib, ie):
            if cid == b"insh":
                _count, bank, program = struct.unpack_from("<III", data, b)
                inst.update(
                    bank_msb=(bank >> 16) & 0x7F,
                    bank_lsb=(bank >> 24) & 0x7F,
                    program=program & 0x7F,
                    drums=bool(bank & DRUM_BIT),
                )
            elif cid == b"LIST" and kind == b"INFO":
                inst["name"] = info_name(data, b, e)
            elif cid == b"LIST" and kind == b"lrgn":
                # DLS Level 1 names regions "rgn ", Level 2 names them "rgn2".
                for rcid, rkind, rb, re_ in chunks(data, b, e):
                    if rkind not in (b"rgn ", b"rgn2"):
                        continue
                    reg = {"keys": None, "wave": None, "looped": False}
                    own = None
                    link = None
                    for c2, _, cb, ce in chunks(data, rb, re_):
                        if c2 == b"rgnh":
                            lo, hi = struct.unpack_from("<HH", data, cb)
                            reg["keys"] = [lo, hi]
                        elif c2 == b"wlnk":
                            # The final DWORD of the wlnk body is the pool
                            # table index naming the entry whose cue locates
                            # the wave this region plays.
                            link, = struct.unpack_from("<I", data, cb + 8)
                        elif c2 == b"wsmp":
                            own = sample_info(data, cb)
                    # The link names an entry of the pool table, whose cue
                    # locates the wave the region actually plays.
                    if link is not None and link < len(cues):
                        reg["wave"] = by_offset.get(cues[link])
                    # Sample information on the region overrides that of the
                    # wave it plays; without a region wsmp the wave's applies.
                    si = own
                    if si is None and reg["wave"] is not None:
                        si = wave_wsmp[reg["wave"]]
                    if si is None:
                        si = NULL_INFO
                    reg.update(si)
                    reg["looped"] = si["loop"] is not None
                    inst["regions"].append(reg)
        out["instruments"].append(inst)
    return out
