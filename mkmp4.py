#!/usr/bin/env python3
"""
mkmp4.py - build tiny hand-crafted fragmented MP4 files for acceptance.

The files deliberately stress every rule in the task:
  * interleaved video (id=1, timescale 1000) and audio (id=2, 48000)
    tracks, each traf containing TWO trun runs;
  * B-frame style reordering through signed (trun v1) composition
    offsets, including negative cts so PTS < DTS;
  * tfhd default duration/size/flags and trex fallback defaults, with some
    truns omitting per-sample fields entirely;
  * both default-base-is-moof (0x020000) and explicit base-data-offset
    (0x000001) anchor modes;
  * a gap on the audio timeline, plus seamless cross-fragment continuation;
  * a variant whose final mdat is cut inside a video sample.
"""
import struct

VIDEO_TID = 1
AUDIO_TID = 2
VIDEO_TS = 1000
AUDIO_TS = 48000
SYNC = 0x00000000
NONSYNC = 0x00010000

# (duration, size, cts, sample_flags or None) -- None => use defaults.
FRAG1_VIDEO = [
    # run 0: only signed cts per sample; duration/size/flags come from the
    # tfhd default sample fields (size/duration) and first_sample_flags
    ([(None, None, 1000, None),
      (None, None, -1000, None)],
     {"first_sample_flags": SYNC}),
    # run 1: every per-sample field present
    ([(1000, 36, 1000, NONSYNC),
      (1000, 44, -2000, SYNC)], {}),
]
FRAG1_AUDIO = [
    # run 0: last sample gives neither duration nor size -> trex defaults
    # (duration 1024, size 20) must be applied by the indexer
    ([(1024, 24, None, None), (1024, 28, None, None), (None, None, None, None)],
     {}),
    ([(1024, 26, None, None), (1024, 30, None, None)], {}),
]
FRAG2_VIDEO = [
    # both samples carry explicit flags, so the trun writes the full
    # sample-flags column (sync P-like anchor, then a NONSYNC B-frame)
    ([(1000, 40, 1000, SYNC), (1000, 40, -1000, NONSYNC)], {}),
]
FRAG2_AUDIO = [
    ([(1024, 25, None, None), (1024, 27, None, None)], {}),
]
FRAG3_VIDEO = [
    ([(1000, 40, 1000, None)], {"first_sample_flags": SYNC}),
    ([(1000, 40, -1000, NONSYNC)], {}),  # second sample gets truncated
]
FRAG3_AUDIO = [
    ([(1024, 26, None, None)], {}),
    ([(1024, 26, None, None)], {}),
]

# tfdt base media decode times (timescale units)
TFDT = {
    1: {VIDEO_TID: 0, AUDIO_TID: 0},
    2: {VIDEO_TID: 4000, AUDIO_TID: 6144},   # audio gap: 5120 -> 6144
    3: {VIDEO_TID: 6000, AUDIO_TID: 8192},   # seamless after fragment 2
}


def u16(v):
    return struct.pack(">H", v)


def u32(v):
    return struct.pack(">I", v)


def u64(v):
    return struct.pack(">Q", v)


def i32(v):
    return struct.pack(">i", v)


def full(version, flags):
    return bytes([version]) + struct.pack(">I", flags)[1:]


def box(btype, payload):
    return u32(8 + len(payload)) + btype + payload


def ftyp():
    return box(b"ftyp", b"iso5" + u32(1) + b"iso5iso6")


def tkhd(tid):
    p = full(0, 0x000007)
    p += u32(0) + u32(0)            # creation / modification
    p += u32(tid) + u32(0)          # track id / reserved
    p += u32(0)                     # duration
    p += b"\x00" * 8                # reserved
    p += u16(0) + u16(0)            # layer / alternate group
    p += u16(0x0100) + u16(0)       # volume / reserved
    p += struct.pack(">9I", 0x00010000, 0, 0, 0, 0x00010000, 0,
                     0, 0, 0x40000000)  # matrix
    p += u32(320 << 16) + u32(240 << 16)
    return box(b"tkhd", p)


def mdhd(timescale):
    p = full(0, 0)
    p += u32(0) + u32(0)            # creation / modification
    p += u32(timescale) + u32(0)    # timescale / duration
    p += u16(0x55C4) + u16(0)       # language 'und' / pre-defined
    return box(b"mdhd", p)


def hdlr(handler):
    p = full(0, 0) + u32(0) + handler + b"\x00" * 12 + b"\x00"
    return box(b"hdlr", p)


def trak(tid, timescale, handler):
    mdia = box(b"mdia",
               mdhd(timescale) + hdlr(handler) + box(b"minf", b""))
    return box(b"trak", tkhd(tid) + mdia)


def trex(tid, dur, size):
    p = full(0, 0) + u32(tid) + u32(1) + u32(dur) + u32(size) + u32(0)
    return box(b"trex", p)


def moov():
    body = trak(VIDEO_TID, VIDEO_TS, b"vide")
    body += trak(AUDIO_TID, AUDIO_TS, b"soun")
    body += box(b"mvex",
                trex(VIDEO_TID, 1000, 64) + trex(AUDIO_TID, 1024, 20))
    return box(b"moov", body)


def trun_box(run_idx, samples, version, data_offset, first_sample_flags=None):
    flags = 0x000001  # data-offset-present
    if first_sample_flags is not None:
        flags |= 0x000004
    # A field column is emitted only when EVERY sample supplies it; a None
    # inside an omitted column means the indexer must take the tfhd/trex
    # default. Mixed presence inside one trun run is not representable.
    use_dur = all(s[0] is not None for s in samples)
    use_size = all(s[1] is not None for s in samples)
    use_cts = all(s[2] is not None for s in samples)
    use_sflags = all(s[3] is not None for s in samples)
    if use_dur:
        flags |= 0x000100
    if use_size:
        flags |= 0x000200
    if use_sflags:
        flags |= 0x000400
    if use_cts:
        flags |= 0x000800
    p = full(version, flags) + u32(len(samples)) + i32(data_offset)
    if first_sample_flags is not None:
        p += u32(first_sample_flags)
    for dur, size, cts, sflags in samples:
        if use_dur:
            p += u32(dur)
        if use_size:
            p += u32(size)
        if use_sflags:
            p += u32(sflags)
        if use_cts:
            p += (i32(cts) if version == 1 else u32(cts))
    return box(b"trun", p), flags


def tfhd_box(tid, anchor_mode, moof_origin, defaults=None):
    """anchor_mode: 'moof' (flag 0x020000) or 'explicit' (base-data-offset)."""
    flags = 0x020000 if anchor_mode == "moof" else 0x000001
    p = full(0, flags) + u32(tid)
    if anchor_mode == "explicit":
        p += u64(moof_origin)
    if defaults:
        # default-description-index(0x02), -duration(0x08), -size(0x10),
        # -flags(0x20)
        p = full(0, flags | 0x02 | 0x08 | 0x10 | 0x20) + u32(tid)
        if anchor_mode == "explicit":
            p += u64(moof_origin)
        p += u32(1)
        p += u32(defaults["duration"]) + u32(defaults["size"])
        p += u32(defaults["flags"])
    return box(b"tfhd", p)


def tfdt_box(base, version):
    p = full(version, 0) + (u64(base) if version == 1 else u32(base))
    return box(b"tfdt", p)


def payload(label, size):
    label = label.encode("ascii")
    assert len(label) < size
    return label + b"\x00" * (size - len(label))


def fragment(seq, video_runs, audio_runs, anchor_mode, moof_origin,
             video_defaults=None):
    """Return (moof_bytes, mdat_bytes, run_locations).

    run_locations: list of (tid, trun_index, [absolute sample offsets...])
    computed with the two-pass moof assembly below.
    """
    # sizes used when a trun omits per-sample sizes: tfhd default (video
    # fragment 1 only) or the trex default declared in moov.
    track_default_size = {VIDEO_TID: 64, AUDIO_TID: 20}
    if video_defaults is not None:
        track_default_size[VIDEO_TID] = video_defaults["size"]

    # ---- mdat payload layout (interleaved per run): v runs then a runs
    layout = []  # (tid, trun_idx, samples, label_prefix)
    for i, (samples, _opts) in enumerate(video_runs):
        layout.append((VIDEO_TID, i, samples, "v"))
    for i, (samples, _opts) in enumerate(audio_runs):
        layout.append((AUDIO_TID, i, samples, "a"))

    def build(offset_guess):
        # flatten run payloads in fixed interleaved order
        run_payloads = []
        cursor = offset_guess
        locations = []
        mdat = b""
        for tid, ri, samples, prefix in layout:
            offsets = []
            for si, (_dur, size, _cts, _fl) in enumerate(samples):
                real_size = size if size is not None \
                    else track_default_size[tid]
                blob = payload("%s%dr%ds%d" % (prefix, seq, ri, si), real_size)
                offsets.append(cursor)
                cursor += len(blob)
                mdat += blob
            run_payloads.append((tid, ri, offsets))
            locations.append((tid, ri, offsets))

        def traf(tid, runs, tfdt_version, tfdt_base, defaults):
            children = tfhd_box(tid, anchor_mode, moof_origin, defaults)
            children += tfdt_box(tfdt_base, tfdt_version)
            for ri, (samples, opts) in enumerate(runs):
                # map this track's run index to its payload offsets
                offs = next(loc for t, r, loc in run_payloads
                            if t == tid and r == ri)
                # data_offset is relative to the tfhd base (moof origin),
                # so it spans the whole moof plus the 8-byte mdat header
                rel = offs[0] - moof_origin
                version = 1 if tid == VIDEO_TID else 0
                tb, _ = trun_box(ri, samples, version, rel,
                                 opts.get("first_sample_flags"))
                children += tb
            return box(b"traf", children)

        v_traf = traf(VIDEO_TID, video_runs, 1,
                      TFDT[seq][VIDEO_TID], video_defaults)
        a_traf = traf(AUDIO_TID, audio_runs, 0,
                      TFDT[seq][AUDIO_TID], None)
        moof = box(b"moof",
                   box(b"mfhd", full(0, 0) + u32(seq)) + v_traf + a_traf)
        return moof, mdat, locations

    # pass 1: placeholder to learn the moof length; the mdat header is 8
    moof0, _, _ = build(0)
    first_payload = moof_origin + len(moof0) + 8
    # pass 2: real offsets (moof length is invariant between the passes)
    moof, mdat, locations = build(first_payload)
    assert len(moof) == len(moof0), "moof size changed between passes"
    return moof, box(b"mdat", mdat), locations


def build_file(anchor_modes=None, truncate=False):
    """Assemble the full acceptance file (or its truncated variant)."""
    if anchor_modes is None:
        anchor_modes = {1: "moof", 2: "explicit", 3: "moof"}
    video_defaults = {"duration": 1000, "size": 40, "flags": NONSYNC}
    parts = [ftyp(), moov()]
    spec = {}
    for seq, vruns, aruns in (
            (1, FRAG1_VIDEO, FRAG1_AUDIO),
            (2, FRAG2_VIDEO, FRAG2_AUDIO),
            (3, FRAG3_VIDEO, FRAG3_AUDIO)):
        origin = sum(len(p) for p in parts)
        moof, mdat, loc = fragment(
            seq, vruns, aruns, anchor_modes[seq], origin,
            video_defaults if seq == 1 else None)
        parts.append(moof)
        parts.append(mdat)
        spec[seq] = {"origin": origin, "locations": loc}
    data = b"".join(parts)
    if truncate:
        # cut at the start of fragment-3 video run-1 sample 0, so the
        # truncated sample contributes zero usable bytes
        f3 = spec[3]
        cut_offset = next(offs for tid, ri, offs in f3["locations"]
                          if tid == VIDEO_TID and ri == 1)[0]
        data = data[:cut_offset]
        spec["_cut_offset"] = cut_offset
    return data, spec


if __name__ == "__main__":
    import os
    outdir = os.path.join(os.path.dirname(__file__), "out")
    os.makedirs(outdir, exist_ok=True)
    full_data, spec = build_file()
    trunc_data, tspec = build_file(truncate=True)
    with open(os.path.join(outdir, "full.fmp4"), "wb") as f:
        f.write(full_data)
    with open(os.path.join(outdir, "truncated.fmp4"), "wb") as f:
        f.write(trunc_data)
    print("wrote", len(full_data), "and", len(trunc_data), "bytes;",
          "cut at", tspec["_cut_offset"])
