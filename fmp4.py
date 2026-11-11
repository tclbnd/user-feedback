"""Pure-Python fragmented MP4 (ISO/IEC 14496-12) parsing and writing.

The recorder receives single-track fMP4 captures (one ``moof`` + ``mdat`` per
fragment file) and republishes dual-track HLS fMP4 segments.  Only the boxes
exercised by those two paths are modelled; compressed sample payloads are
always treated as opaque bytes so they are preserved bit-for-bit.
"""

import struct
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Low level box helpers
# ---------------------------------------------------------------------------

def iter_boxes(data: bytes, start: int = 0, end: Optional[int] = None):
    """Yield ``(offset, size, kind, body)`` for every top level box."""
    if end is None:
        end = len(data)
    offset = start
    while offset + 8 <= end:
        size, kind = struct.unpack_from(">I4s", data, offset)
        header = 8
        if size == 1:
            size = struct.unpack_from(">Q", data, offset + 8)[0]
            header = 16
        elif size == 0:
            size = end - offset
        if size < header or offset + size > end:
            raise ValueError("invalid MP4 box size")
        yield offset, size, kind, data[offset + header:offset + size]
        offset += size


def find_box(data: bytes, path: List[bytes], start: int = 0,
             end: Optional[int] = None) -> Optional[Tuple[int, int, bytes]]:
    """Find a box by type path, e.g. ``[b'moof', b'traf', b'tfhd']``."""
    if end is None:
        end = len(data)
    wanted = path[0]
    for offset, size, kind, body in iter_boxes(data, start, end):
        if kind != wanted:
            continue
        if len(path) == 1:
            return offset, size, body
        header = size - len(body)
        found = find_box(data, path[1:], offset + header, offset + size)
        if found is not None:
            return found
    return None


def box(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(body), kind) + body


def full_box(kind: bytes, version: int, flags: int, body: bytes) -> bytes:
    return box(kind, bytes([version]) + struct.pack(">I", flags)[1:] + body)


# ---------------------------------------------------------------------------
# Parsing captured (single track) fragments and init sections
# ---------------------------------------------------------------------------

@dataclass
class Sample:
    size: int
    cto: int                       # composition time offset, track ticks
    duration: int                  # track ticks
    is_sync: bool
    payload: bytes                 # exact compressed sample bytes
    sample_flags: int = 0          # raw MP4 sample flags (sync bit etc.)

    @property
    def digest(self) -> str:
        import hashlib
        return hashlib.sha256(self.payload).hexdigest()


@dataclass
class Fragment:
    base_media_decode_time: int
    default_duration: int
    default_flags: int
    samples: List[Sample]
    mdat_payload: bytes

    @property
    def duration(self) -> int:
        return sum(s.duration for s in self.samples)

    @property
    def starts_with_sync(self) -> bool:
        return bool(self.samples) and self.samples[0].is_sync


def _parse_tfhd(body: bytes):
    flags = struct.unpack(">I", b"\x00" + body[1:4])[0]
    pos = 4
    track_id = struct.unpack_from(">I", body, pos)[0]
    pos += 4
    base_data_offset = None
    description_index = None
    default_duration = None
    default_size = None
    default_flags = None
    if flags & 0x000001:
        base_data_offset = struct.unpack_from(">Q", body, pos)[0]
        pos += 8
    if flags & 0x000002:
        description_index = struct.unpack_from(">I", body, pos)[0]
        pos += 4
    if flags & 0x000008:
        default_duration = struct.unpack_from(">I", body, pos)[0]
        pos += 4
    if flags & 0x000020:
        default_size = struct.unpack_from(">I", body, pos)[0]
        pos += 4
    if flags & 0x000010:
        default_flags = struct.unpack_from(">I", body, pos)[0]
        pos += 4
    return {
        "track_id": track_id,
        "base_data_offset": base_data_offset,
        "description_index": description_index,
        "default_duration": default_duration,
        "default_size": default_size,
        "default_flags": default_flags,
    }


def _parse_trun(body: bytes):
    version = body[0]
    flags = struct.unpack(">I", b"\x00" + body[1:4])[0]
    count = struct.unpack_from(">I", body, 4)[0]
    pos = 8
    data_offset = None
    first_sample_flags = None
    if flags & 0x000001:
        data_offset = struct.unpack_from(">i", body, pos)[0]
        pos += 4
    if flags & 0x000004:
        first_sample_flags = struct.unpack_from(">I", body, pos)[0]
        pos += 4
    rows = []
    for _ in range(count):
        duration = struct.unpack_from(">I", body, pos)[0] if flags & 0x000100 else None
        if flags & 0x000100:
            pos += 4
        size = struct.unpack_from(">I", body, pos)[0] if flags & 0x000200 else None
        if flags & 0x000200:
            pos += 4
        sample_flags = struct.unpack_from(">I", body, pos)[0] if flags & 0x000400 else None
        if flags & 0x000400:
            pos += 4
        if flags & 0x000800:
            cto = (struct.unpack_from(">i", body, pos)[0] if version == 1
                   else struct.unpack_from(">I", body, pos)[0])
            pos += 4
        else:
            cto = 0
        rows.append((duration, size, sample_flags, cto))
    return flags, data_offset, first_sample_flags, rows


def _parse_tfdt(body: bytes) -> int:
    version = body[0]
    if version == 1:
        return struct.unpack_from(">Q", body, 4)[0]
    return struct.unpack_from(">I", body, 4)[0]


def _h264_sample_is_sync(payload: bytes) -> bool:
    """True when an AVCC length-prefixed sample carries an IDR slice (NAL 5)."""
    pos = 0
    while pos + 4 <= len(payload):
        length = struct.unpack_from(">I", payload, pos)[0]
        if length <= 0 or pos + 4 + length > len(payload):
            break
        nal_type = payload[pos + 4] & 0x1F
        if nal_type == 5:
            return True
        pos += 4 + length
    return False


def trex_defaults(init_bytes: bytes) -> Dict[int, dict]:
    """Map track id -> trex default fields from an init section."""
    result = {}
    moov = find_box(init_bytes, [b"moov"])
    if moov is None:
        return result
    offset, size, _ = moov
    for off, sz, kind, body in iter_boxes(init_bytes, offset + 8, offset + size):
        if kind != b"mvex":
            continue
        inner_start = off + (sz - len(body))
        for _, _, tkind, tbody in iter_boxes(init_bytes, inner_start, off + sz):
            if tkind != b"trex":
                continue
            track_id, desc_idx, def_dur, def_size, def_flags = struct.unpack_from(
                ">IIIII", tbody, 4)
            result[track_id] = {
                "description_index": desc_idx,
                "default_duration": def_dur,
                "default_size": def_size,
                "default_flags": def_flags,
            }
    return result


def read_fragment(data: bytes, is_video: bool,
                  trex: Optional[dict] = None) -> Fragment:
    """Parse a single-track ``moof`` + ``mdat`` capture fragment."""
    moof = find_box(data, [b"moof"])
    mdat = find_box(data, [b"mdat"])
    if moof is None or mdat is None:
        raise ValueError("fragment must contain moof and mdat")
    moof_off, moof_size, _ = moof
    tfhd = find_box(data, [b"moof", b"traf", b"tfhd"])
    trun = find_box(data, [b"moof", b"traf", b"trun"])
    tfdt = find_box(data, [b"moof", b"traf", b"tfdt"])
    if tfhd is None or trun is None or tfdt is None:
        raise ValueError("fragment traf is missing tfhd/trun/tfdt")
    header = tfhd[1] - len(tfhd[2])
    tfhd_info = _parse_tfhd(tfhd[2])
    flags, data_offset, first_sample_flags, rows = _parse_trun(trun[2])
    base_time = _parse_tfdt(tfdt[2])
    mdat_off, mdat_size, mdat_body = mdat
    mdat_header = mdat_size - len(mdat_body)
    if data_offset is not None:
        payload_start = moof_off + data_offset
    else:
        payload_start = mdat_off + mdat_header
    trex = trex or {}
    fallback_duration = (tfhd_info["default_duration"]
                         if tfhd_info["default_duration"] is not None
                         else trex.get("default_duration", 0))
    fallback_size = tfhd_info["default_size"]
    fallback_flags = (tfhd_info["default_flags"]
                      if tfhd_info["default_flags"] is not None
                      else trex.get("default_flags", 0))

    samples: List[Sample] = []
    cursor = payload_start
    for index, (trun_dur, trun_size, trun_flags, cto) in enumerate(rows):
        sample_size = trun_size if trun_size is not None else fallback_size
        if sample_size is None:
            raise ValueError("sample size not declared in trun/tfhd")
        duration = trun_dur if trun_dur is not None else fallback_duration
        if index == 0 and first_sample_flags is not None:
            sample_flags = first_sample_flags
        elif trun_flags is not None:
            sample_flags = trun_flags
        else:
            sample_flags = fallback_flags
        payload = data[cursor:cursor + sample_size]
        if len(payload) != sample_size:
            raise ValueError("truncated fragment: sample extends past mdat")
        if is_video:
            is_sync = _h264_sample_is_sync(payload)
        else:
            # AAC: every frame is an independently decodable sync sample.
            is_sync = (sample_flags & 0x00010000) == 0
        samples.append(Sample(sample_size, cto, duration, is_sync, payload,
                              sample_flags))
        cursor += sample_size
    mdat_payload = data[mdat_off + mdat_header:mdat_off + mdat_size]
    return Fragment(base_time, fallback_duration, fallback_flags, samples,
                    mdat_payload)


# ---------------------------------------------------------------------------
# Writing dual-track init sections and segments
# ---------------------------------------------------------------------------

def _rewrite_trak(trak_bytes: bytes, new_track_id: int) -> bytes:
    """Clone a standalone trak box with a new track id in its tkhd child."""
    rebuilt = bytearray(trak_bytes[:8])  # keep the trak container header
    for offset, size, kind, body in iter_boxes(trak_bytes, 8, len(trak_bytes)):
        if kind != b"tkhd":
            rebuilt += trak_bytes[offset:offset + size]
            continue
        version = body[0]
        # v0: full header(8) + ver/flags(4) + two 32-bit times(8) -> id @20;
        # v1 has 64-bit times -> id @28 (offset within the tkhd box itself).
        edited = bytearray(trak_bytes[offset:offset + size])
        struct.pack_into(">I", edited, 20 if version == 0 else 28,
                         new_track_id)
        rebuilt += edited
    return bytes(rebuilt)


def _rewrite_trex(trex_body: bytes, new_track_id: int,
                  default_duration: int) -> bytes:
    """Rebuild a trex with the track id and sample duration we publish."""
    desc_idx = struct.unpack_from(">I", trex_body, 8)[0]
    default_size = struct.unpack_from(">I", trex_body, 16)[0]
    default_flags = struct.unpack_from(">I", trex_body, 20)[0]
    return struct.pack(">IIIII", new_track_id, desc_idx, default_duration,
                       default_size, default_flags)


def build_dual_init(video_init: bytes, audio_init: bytes) -> bytes:
    """Combine two single-track init sections into one dual-track init."""
    ftyp = find_box(video_init, [b"ftyp"])
    v_moov = find_box(video_init, [b"moov"])
    a_moov = find_box(audio_init, [b"moov"])
    if ftyp is None or v_moov is None or a_moov is None:
        raise ValueError("init sections must contain ftyp/moov boxes")
    ftyp_bytes = video_init[ftyp[0]:ftyp[0] + ftyp[1]]

    def trak_of(moov_bytes: bytes, moov_box):
        off, size, _ = moov_box
        header = size - len(moov_box[2])
        for t_off, t_size, kind, _ in iter_boxes(moov_bytes,
                                                 off + header, off + size):
            if kind == b"trak":
                return moov_bytes[t_off:t_off + t_size]
        raise ValueError("moov without trak")

    video_trak = _rewrite_trak(trak_of(video_init, v_moov), 1)
    audio_trak = _rewrite_trak(trak_of(audio_init, a_moov), 2)

    def trex_of(moov_bytes: bytes, moov_box):
        off, size, _ = moov_box
        found = find_box(moov_bytes, [b"moov", b"mvex", b"trex"])
        if found is None:
            raise ValueError("moov without mvex/trex")
        return found[2]

    video_trex = trex_of(video_init, v_moov)
    audio_trex = trex_of(audio_init, a_moov)
    # Each capture fragment declares its real default duration in tfhd; keep
    # the init-level trex duration at zero like a standard fMP4 publisher.
    mvex_body = (
        full_box(b"trex", 0, 0, _rewrite_trex(video_trex, 1, 0))
        + full_box(b"trex", 0, 0, _rewrite_trex(audio_trex, 2, 0))
    )
    # mvhd: version 0, movie timescale 1000, duration supplied by fragments.
    mvhd_body = b"".join([
        struct.pack(">II", 0, 0),              # creation / modification time
        struct.pack(">I", 1000),               # timescale
        struct.pack(">I", 0),                  # duration (unknown)
        struct.pack(">I", 0x00010000),         # rate 1.0
        struct.pack(">H", 0x0100),             # volume 1.0
        b"\x00" * 10,                          # reserved
        struct.pack(">9I", 0x00010000, 0, 0, 0, 0x00010000, 0, 0, 0, 0x40000000),
        b"\x00" * 24,                          # pre-defined
        struct.pack(">II", 2, 2),              # next track id / reserved
    ])
    moov_body = (
        full_box(b"mvhd", 0, 0, mvhd_body)
        + video_trak
        + audio_trak
        + box(b"mvex", mvex_body)
    )
    return ftyp_bytes + box(b"moov", moov_body)


def _build_traf(track_id: int, base_time: int, default_duration: int,
                samples: List[Sample], with_cto: bool, moof_size: int,
                mdat_offset: int) -> bytes:
    # tfhd: default-base-is-moof (0x020000) + default-sample-duration (0x8).
    tfhd_flags = 0x020000 | 0x000008
    tfhd_body = struct.pack(">I", track_id)
    tfhd_body += struct.pack(">I", default_duration)
    tfhd = full_box(b"tfhd", 0, tfhd_flags, tfhd_body)
    tfdt = full_box(b"tfdt", 1, 0, struct.pack(">Q", base_time))
    # trun: data-offset(0x1) + per-sample duration(0x100) + size(0x200)
    # + flags(0x400); video additionally carries composition offset(0x800).
    # Per-sample durations are required because a merged segment can contain
    # samples from source fragments with different tfhd defaults (e.g. the
    # shorter final AAC frame).
    trun_flags = 0x000001 | 0x000100 | 0x000200 | 0x000400
    if with_cto:
        trun_flags |= 0x000800
    # trun layout (full box header already added by full_box):
    # sample_count(4), data_offset(4, flag 0x1), then per-sample fields.
    trun_body = struct.pack(">I", len(samples))
    trun_body += struct.pack(">i", moof_size + mdat_offset)
    for sample in samples:
        trun_body += struct.pack(">I", sample.duration)
        trun_body += struct.pack(">I", len(sample.payload))
        trun_body += struct.pack(">I", sample.sample_flags)
        if with_cto:
            trun_body += struct.pack(">i", sample.cto)
    trun = full_box(b"trun", 0, trun_flags, trun_body)
    return box(b"traf", tfhd + tfdt + trun)


def build_segment(sequence: int, video_base_time: int,
                  video_samples: List[Sample], video_default_duration: int,
                  audio_base_time: Optional[int],
                  audio_samples: List[Sample], audio_default_duration: int
                  ) -> bytes:
    """Build one dual-track fMP4 segment (moof with two trafs + mdat)."""
    video_payload = b"".join(s.payload for s in video_samples)
    audio_payload = b"".join(s.payload for s in audio_samples)
    mdat = box(b"mdat", video_payload + audio_payload)
    mdat_header = 8
    # First pass: measure moof with placeholder offsets.
    v_traf = _build_traf(1, video_base_time, video_default_duration,
                         video_samples, True, 0, mdat_header)
    a_traf = b""
    if audio_samples:
        a_traf = _build_traf(2, audio_base_time, audio_default_duration,
                             audio_samples, False, 0,
                             mdat_header + len(video_payload))
    mfhd = full_box(b"mfhd", 0, 0, struct.pack(">I", sequence))
    moof_size = 8 + len(mfhd) + len(v_traf) + len(a_traf)
    # Second pass with the real data offset (relative to moof start).
    v_traf = _build_traf(1, video_base_time, video_default_duration,
                         video_samples, True, moof_size, mdat_header)
    if audio_samples:
        a_traf = _build_traf(2, audio_base_time, audio_default_duration,
                             audio_samples, False, moof_size,
                             mdat_header + len(video_payload))
    moof = box(b"moof", mfhd + v_traf + a_traf)
    return moof + mdat


# ---------------------------------------------------------------------------
# Parsing published dual-track segments (used by the acceptance verifier)
# ---------------------------------------------------------------------------

@dataclass
class TrackRun:
    track_id: int
    base_media_decode_time: int
    default_duration: int
    samples: List[Sample]


def read_track_runs(data: bytes) -> Dict[int, TrackRun]:
    """Return every traf in a segment keyed by track id."""
    runs: Dict[int, TrackRun] = {}
    moof = find_box(data, [b"moof"])
    if moof is None:
        raise ValueError("segment without moof")
    moof_off, moof_size, _ = moof
    for off, size, kind, _ in iter_boxes(data, moof_off + 8, moof_off + moof_size):
        if kind != b"traf":
            continue
        tfhd = find_box(data, [b"traf", b"tfhd"], off, off + size)
        trun = find_box(data, [b"traf", b"trun"], off, off + size)
        tfdt = find_box(data, [b"traf", b"tfdt"], off, off + size)
        if tfhd is None or trun is None or tfdt is None:
            raise ValueError("published traf missing boxes")
        info = _parse_tfhd(tfhd[2])
        trun_flags, data_offset, first_flags, rows = _parse_trun(trun[2])
        base_time = _parse_tfdt(tfdt[2])
        if data_offset is None:
            mdat = find_box(data, [b"mdat"])
            cursor = mdat[0] + (mdat[1] - len(mdat[2]))
        else:
            cursor = moof_off + data_offset
        samples: List[Sample] = []
        for index, (trun_dur, trun_size, trun_flags_row, cto) in enumerate(rows):
            sample_size = (trun_size if trun_size is not None
                           else info["default_size"])
            if sample_size is None:
                raise ValueError("published sample without size")
            duration = (trun_dur if trun_dur is not None
                        else info["default_duration"] or 0)
            if index == 0 and first_flags is not None:
                sample_flags = first_flags
            elif trun_flags_row is not None:
                sample_flags = trun_flags_row
            else:
                sample_flags = info["default_flags"] or 0
            payload = data[cursor:cursor + sample_size]
            is_sync = ((sample_flags & 0x00010000) == 0
                       or (_h264_sample_is_sync(payload)
                           if info["track_id"] == 1 else True))
            samples.append(Sample(sample_size, cto, duration, is_sync, payload))
            cursor += sample_size
        runs[info["track_id"]] = TrackRun(
            info["track_id"], base_time,
            info["default_duration"] or 0, samples)
    return runs
