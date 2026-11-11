"""Small ISO-BMFF (MP4) reader/writer used by the recorder.

The recorder never re-encodes media.  It needs to understand just enough of the
fragmented MP4 produced by the capture side to be able to:

* inspect the samples of every received fragment (timestamps, sizes, flags and
  composition offsets),
* rebuild a two-track initialisation segment from the two single-track ones,
* write deterministic, GOP-aligned HLS fMP4 segments that keep the original
  compressed payload bytes, decode order and presentation/decode timestamp
  relationship.

Only the boxes that are relevant for that task are interpreted; unknown boxes
are carried along when a ``trak`` is copied.
"""

import struct
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

CONTAINER_BOXES = {
    b"moov", b"trak", b"mdia", b"minf", b"stbl", b"mvex", b"moof", b"traf",
    b"edts", b"dinf", b"mfra", b"udta", b"meta", b"ilst",
}


@dataclass
class Box:
    kind: bytes
    start: int
    end: int
    header: int
    children: List["Box"] = field(default_factory=list)

    @property
    def payload_start(self) -> int:
        return self.start + self.header

    @property
    def size(self) -> int:
        return self.end - self.start


def _header_size(data: bytes, offset: int, end: int) -> Tuple[bytes, int, int]:
    if offset + 8 > end:
        raise ValueError("truncated MP4 box header")
    size, kind = struct.unpack_from(">I4s", data, offset)
    header = 8
    if size == 1:
        if offset + 16 > end:
            raise ValueError("truncated MP4 box header")
        size = struct.unpack_from(">Q", data, offset + 8)[0]
        header = 16
    elif size == 0:
        size = end - offset
    if kind == b"uuid":
        header += 16
    if size < header or offset + size > end:
        raise ValueError("invalid or truncated MP4 box")
    return kind, size, header


def parse_tree(data: bytes, start: int = 0, end: Optional[int] = None,
               depth: int = 0) -> List[Box]:
    """Parse a byte range into a shallow box tree.

    Container boxes are parsed recursively; leaf boxes are kept as ranges into
    ``data``.  ``depth`` only guards against pathological input.
    """
    if end is None:
        end = len(data)
    if depth > 12:
        raise ValueError("MP4 box nesting is too deep")
    boxes: List[Box] = []
    offset = start
    while offset < end:
        kind, size, header = _header_size(data, offset, end)
        box = Box(kind=kind, start=offset, end=offset + size, header=header)
        if kind in CONTAINER_BOXES and kind != b"meta":
            box.children = parse_tree(data, box.payload_start, box.end,
                                      depth + 1)
        boxes.append(box)
        offset += size
    return boxes


def find_box(boxes: Iterable[Box], kind: bytes) -> Optional[Box]:
    for box in boxes:
        if box.kind == kind:
            return box
    return None


def find_path(boxes: Iterable[Box], *kinds: bytes) -> Optional[Box]:
    current = list(boxes)
    found: Optional[Box] = None
    for kind in kinds:
        found = find_box(current, kind)
        if found is None:
            return None
        current = found.children
    return found


def box(kind: bytes, payload: bytes) -> bytes:
    body = struct.pack(">I4s", 8 + len(payload), kind) + payload
    return body


def full_box(kind: bytes, version: int, flags: int, payload: bytes) -> bytes:
    return box(kind, bytes([version]) + struct.pack(">I", flags)[1:] + payload)


def ftyp_like(kind: bytes, major: bytes, compatible: List[bytes]) -> bytes:
    return box(kind, major + struct.pack(">I", 0) + b"".join(compatible))


# ---------------------------------------------------------------------------
# Initialisation segment


def _tkhd_track_id(data: bytes, node: Box) -> int:
    version = data[node.payload_start]
    offset = node.payload_start + 4 + (16 if version == 1 else 8)
    if offset + 4 > node.end:
        raise ValueError("truncated tkhd box")
    return struct.unpack_from(">I", data, offset)[0]


def _mdhd_timescale(data: bytes, node: Box) -> int:
    version = data[node.payload_start]
    offset = node.payload_start + 4 + (16 if version == 1 else 8)
    if offset + 4 > node.end:
        raise ValueError("truncated mdhd box")
    return struct.unpack_from(">I", data, offset)[0]


def _set_tkhd_track_id(data: bytes, node: Box, track_id: int) -> bytes:
    version = data[node.payload_start]
    offset = node.payload_start + 4 + (16 if version == 1 else 8)
    return data[:offset] + struct.pack(">I", track_id) + data[offset + 4:]


def _parse_trex(data: bytes, node: Box) -> Dict[str, int]:
    payload = node.payload_start + 4  # version + flags
    if payload + 20 > node.end:
        raise ValueError("truncated trex box")
    track_id, desc_index, duration, size, flags = struct.unpack_from(
        ">IIIII", data, payload
    )
    return {
        "track_id": track_id,
        "description_index": desc_index,
        "duration": duration,
        "size": size,
        "flags": flags,
    }


def parse_init_tracks(data: bytes) -> List[dict]:
    """Track information of a (possibly multi-track) init segment."""
    boxes = parse_tree(data)
    moov = find_box(boxes, b"moov")
    if moov is None:
        raise ValueError("init segment has no moov box")
    result = []
    for trak in [box for box in moov.children if box.kind == b"trak"]:
        tkhd = find_box(trak.children, b"tkhd")
        mdhd = find_path(trak.children, b"mdia", b"mdhd")
        if tkhd is None or mdhd is None:
            raise ValueError("trak is missing tkhd/mdhd")
        result.append({
            "track_id": _tkhd_track_id(data, tkhd),
            "timescale": _mdhd_timescale(data, mdhd),
        })
    return result


def parse_init(data: bytes) -> dict:
    """Return the information needed from one single-track init segment."""
    boxes = parse_tree(data)
    moov = find_box(boxes, b"moov")
    if moov is None:
        raise ValueError("init segment has no moov box")
    traks = [box for box in moov.children if box.kind == b"trak"]
    if len(traks) != 1:
        raise ValueError("init segment must contain exactly one trak")
    trak = traks[0]
    tkhd = find_box(trak.children, b"tkhd")
    if tkhd is None:
        raise ValueError("trak has no tkhd box")
    mdhd = find_path(trak.children, b"mdia", b"mdhd")
    if mdhd is None:
        raise ValueError("trak has no mdhd box")
    track_id = _tkhd_track_id(data, tkhd)
    timescale = _mdhd_timescale(data, mdhd)
    if timescale <= 0:
        raise ValueError("invalid media timescale")
    trexs = []
    mvex = find_box(moov.children, b"mvex")
    if mvex is not None:
        trexs = [_parse_trex(data, box) for box in mvex.children
                 if box.kind == b"trex"]
    trex = next((item for item in trexs if item["track_id"] == track_id), None)
    ftyp = find_box(boxes, b"ftyp")
    return {
        "track_id": track_id,
        "timescale": timescale,
        "trak": data[trak.start:trak.end],
        "ftyp": data[ftyp.start:ftyp.end] if ftyp is not None else None,
        "trex": trex,
    }


def _make_mvhd(movie_timescale: int = 1000000, next_track_id: int = 3) -> bytes:
    matrix = (
        struct.pack(">I", 0x00010000) + struct.pack(">I", 0)
        + struct.pack(">I", 0) + struct.pack(">I", 0)
        + struct.pack(">I", 0x00010000) + struct.pack(">I", 0)
        + struct.pack(">I", 0) + struct.pack(">I", 0)
        + struct.pack(">I", 0x40000000)
    )
    payload = (
        struct.pack(">II", 0, 0)
        + struct.pack(">I", movie_timescale)
        + struct.pack(">I", 0)
        + struct.pack(">I", 0x00010000)       # rate 1.0
        + struct.pack(">H", 0x0100)            # volume 1.0
        + b"\x00\x00"
        + b"\x00" * 8
        + matrix
        + b"\x00" * 24
        + struct.pack(">I", next_track_id)
    )
    return full_box(b"mvhd", 0, 0, payload)


def _make_trex(track_id: int, trex: Optional[dict]) -> bytes:
    trex = trex or {}
    payload = struct.pack(
        ">IIIII",
        track_id,
        1,
        int(trex.get("duration", 0)),
        int(trex.get("size", 0)),
        int(trex.get("flags", 0)),
    )
    return full_box(b"trex", 0, 0, payload)


def merge_init(video: dict, audio: dict) -> bytes:
    """Combine two single-track init segments into one two-track init.

    The original ``trak`` boxes are copied verbatim apart from their track IDs;
    only a fresh movie header and ``mvex`` are generated.  This keeps the codec
    configuration (including SPS/PPS and AAC config) untouched.
    """
    video_trak = _patch_trak(video, 1)
    audio_trak = _patch_trak(audio, 2)
    # Use a standard fragmented-MP4 brand set regardless of the source
    # container (a QuickTime ftyp would make some HLS players unhappy).
    ftyp = ftyp_like(b"ftyp", b"iso5", [b"iso5", b"iso6", b"mp41"])
    mvex = box(b"mvex", _make_trex(1, video.get("trex"))
               + _make_trex(2, audio.get("trex")))
    moov = box(b"moov", _make_mvhd() + video_trak + audio_trak + mvex)
    return ftyp + moov


def _patch_trak(info: dict, new_track_id: int) -> bytes:
    """Prepare a source ``trak`` for a fragmented two-track init.

    The codec configuration and the ``stsd`` box are copied verbatim.  The
    track ID is renumbered and the duration/edit-list *lengths* are zeroed:
    in a fragmented movie the real duration comes from the fragments, and a
    stale duration from a non-fragmented source (or a movie-timescale edit list
    inherited from a different movie timescale) would otherwise confuse
    demuxers.  Edit-list ``media_time`` values are kept, so AAC priming
    compensation survives.
    """
    trak = info["trak"]
    boxes = parse_tree(trak)
    trak_box = find_box(boxes, b"trak")
    if trak_box is None:
        raise ValueError("trak bytes are not a trak box")
    tkhd = find_box(trak_box.children, b"tkhd")
    if tkhd is None:
        raise ValueError("trak has no tkhd box")
    mdhd = find_path(trak_box.children, b"mdia", b"mdhd")
    if mdhd is None:
        raise ValueError("trak has no mdhd box")

    patches = []
    version = trak[tkhd.payload_start]
    track_id_offset = tkhd.payload_start + 4 + (16 if version == 1 else 8)
    duration_size = 8 if version == 1 else 4
    patches.append((track_id_offset, struct.pack(">I", new_track_id)))
    patches.append((track_id_offset + 8, b"\x00" * duration_size))

    mdhd_version = trak[mdhd.payload_start]
    timescale_offset = mdhd.payload_start + 4 + (16 if mdhd_version == 1 else 8)
    mdhd_duration_size = 8 if mdhd_version == 1 else 4
    patches.append((timescale_offset + 4, b"\x00" * mdhd_duration_size))

    elst = find_path(trak_box.children, b"edts", b"elst")
    if elst is not None:
        elst_version = trak[elst.payload_start]
        count = struct.unpack_from(">I", trak, elst.payload_start + 4)[0]
        entry_size = 20 if elst_version == 1 else 12
        segment_size = 8 if elst_version == 1 else 4
        for number in range(count):
            offset = elst.payload_start + 8 + number * entry_size
            patches.append((offset, b"\x00" * segment_size))

    for offset, replacement in sorted(patches, reverse=True):
        trak = (trak[:offset] + replacement
                + trak[offset + len(replacement):])
    return trak


def make_styp(major: bytes = b"msdh",
              compatible: Tuple[bytes, ...] = (b"msdh", b"msix")) -> bytes:
    return ftyp_like(b"styp", major, list(compatible))


# ---------------------------------------------------------------------------
# Media fragments


def _tfhd_payload(data: bytes, node: Box) -> dict:
    payload = node.payload_start
    if payload + 8 > node.end:
        raise ValueError("truncated tfhd box")
    flags = struct.unpack_from(">I", data, payload)[0] & 0x00FFFFFF
    offset = payload + 4
    track_id = struct.unpack_from(">I", data, offset)[0]
    offset += 4
    result = {
        "track_id": track_id,
        "flags": flags,
        "base_data_offset": None,
        "description_index": None,
        "duration": None,
        "size": None,
        "sample_flags": None,
    }
    if flags & 0x000001:
        result["base_data_offset"] = struct.unpack_from(">Q", data, offset)[0]
        offset += 8
    if flags & 0x000002:
        result["description_index"] = struct.unpack_from(">I", data, offset)[0]
        offset += 4
    if flags & 0x000008:
        result["duration"] = struct.unpack_from(">I", data, offset)[0]
        offset += 4
    if flags & 0x000010:
        result["size"] = struct.unpack_from(">I", data, offset)[0]
        offset += 4
    if flags & 0x000020:
        result["sample_flags"] = struct.unpack_from(">I", data, offset)[0]
        offset += 4
    return result


def _tfdt_base(data: bytes, node: Box) -> Optional[int]:
    version = data[node.payload_start]
    offset = node.payload_start + 4
    if version == 1:
        if offset + 8 > node.end:
            raise ValueError("truncated tfdt box")
        return struct.unpack_from(">Q", data, offset)[0]
    if offset + 4 > node.end:
        raise ValueError("truncated tfdt box")
    return struct.unpack_from(">I", data, offset)[0]


def _trun_samples(data: bytes, node: Box, defaults: dict) -> Tuple[list, Optional[int]]:
    version = data[node.payload_start]
    flags = struct.unpack_from(">I", data, node.payload_start)[0] & 0x00FFFFFF
    offset = node.payload_start + 4
    if offset + 4 > node.end:
        raise ValueError("truncated trun box")
    sample_count = struct.unpack_from(">I", data, offset)[0]
    offset += 4
    data_offset = None
    first_sample_flags = None
    if flags & 0x000001:
        data_offset = struct.unpack_from(">i", data, offset)[0]
        offset += 4
    if flags & 0x000004:
        first_sample_flags = struct.unpack_from(">I", data, offset)[0]
        offset += 4
    samples = []
    for index in range(sample_count):
        duration = defaults.get("duration")
        size = defaults.get("size")
        sample_flags = defaults.get("sample_flags")
        if flags & 0x000100:
            duration = struct.unpack_from(">I", data, offset)[0]
            offset += 4
        if flags & 0x000200:
            size = struct.unpack_from(">I", data, offset)[0]
            offset += 4
        if flags & 0x000400:
            sample_flags = struct.unpack_from(">I", data, offset)[0]
            offset += 4
        if flags & 0x000800:
            if version == 1:
                cto = struct.unpack_from(">i", data, offset)[0]
            else:
                cto = struct.unpack_from(">I", data, offset)[0]
                # Some muxers store a negative offset in a version-0 trun.
                if cto >= 0x80000000:
                    cto -= 0x100000000
            offset += 4
        else:
            cto = 0
        if index == 0 and first_sample_flags is not None:
            sample_flags = first_sample_flags
        if duration is None or size is None:
            raise ValueError("fragment does not carry sample duration/size")
        samples.append({
            "duration": int(duration),
            "size": int(size),
            "flags": int(sample_flags or 0),
            "cto": int(cto),
        })
    if offset > node.end:
        raise ValueError("truncated trun sample table")
    return samples, data_offset


def sample_is_key(flags: int) -> bool:
    return ((flags >> 16) & 0x1) == 0


def _payload_at(data: bytes, absolute: int, size: int,
                mdat_start: int, mdat_end: int) -> bytes:
    if size < 0 or absolute < mdat_start or absolute + size > mdat_end:
        raise ValueError("fragment sample points outside its mdat payload")
    return data[absolute:absolute + size]


def parse_fragment(data: bytes, trex_defaults: Optional[Dict[int, dict]] = None) -> dict:
    """Parse one fragment file (one or more moof/mdat pairs).

    Returns ``{"tracks": {track_id: {"samples": [...]}}, "sequence": n}``.
    Each sample carries ``duration``, ``size``, ``flags``, ``cto`` and ``data``;
    ``dts`` is present when the fragment has a ``tfdt`` box.
    """
    boxes = parse_tree(data)
    trex_defaults = trex_defaults or {}
    tracks: Dict[int, List[dict]] = {}
    sequence = None
    index = 0
    while index < len(boxes):
        moof = boxes[index]
        if moof.kind != b"moof":
            index += 1
            continue
        mdat = None
        for candidate in boxes[index + 1:]:
            if candidate.kind == b"mdat":
                mdat = candidate
                break
            if candidate.kind == b"moof":
                break
        if mdat is None:
            raise ValueError("moof without a following mdat")
        mfhd = find_box(moof.children, b"mfhd")
        if mfhd is not None:
            sequence = struct.unpack_from(">I", data, mfhd.payload_start + 4)[0]
        for traf in [box for box in moof.children if box.kind == b"traf"]:
            tfhd = find_box(traf.children, b"tfhd")
            if tfhd is None:
                raise ValueError("traf has no tfhd box")
            tfhd = _tfhd_payload(data, tfhd)
            track_id = tfhd["track_id"]
            trex = trex_defaults.get(track_id, {})
            defaults = {
                "duration": tfhd["duration"] if tfhd["duration"] is not None
                else trex.get("duration"),
                "size": tfhd["size"] if tfhd["size"] is not None
                else trex.get("size"),
                "sample_flags": tfhd["sample_flags"]
                if tfhd["sample_flags"] is not None else trex.get("flags"),
            }
            tfdt = find_box(traf.children, b"tfdt")
            base = _tfdt_base(data, tfdt) if tfdt is not None else None
            if tfhd["base_data_offset"] is not None:
                base_offset = tfhd["base_data_offset"]
                position = tfhd["base_data_offset"]
            elif tfhd["flags"] & 0x020000:
                base_offset = moof.start
                position = mdat.payload_start
            else:
                base_offset = moof.end
                position = mdat.payload_start
            current_dts = base
            samples: List[dict] = []
            for trun in [box for box in traf.children if box.kind == b"trun"]:
                raw_samples, data_offset = _trun_samples(data, trun, defaults)
                first_in_trun = True
                for raw in raw_samples:
                    if first_in_trun and data_offset is not None:
                        absolute = base_offset + data_offset
                    else:
                        absolute = position
                    blob = _payload_at(data, absolute, raw["size"],
                                       mdat.payload_start, mdat.end)
                    sample = dict(raw)
                    sample["data"] = blob
                    if current_dts is not None:
                        sample["dts"] = current_dts
                        sample["pts"] = current_dts + raw["cto"]
                        current_dts += raw["duration"]
                    samples.append(sample)
                    position = absolute + raw["size"]
                    first_in_trun = False
            tracks.setdefault(track_id, []).extend(samples)
        index += 1
    if not tracks:
        raise ValueError("fragment contains no media track")
    return {"tracks": tracks, "sequence": sequence}


# ---------------------------------------------------------------------------
# Output segment writer


def _make_traf(track_id: int, samples: List[dict], data_offset: int) -> bytes:
    tfhd = full_box(b"tfhd", 0, 0x020000, struct.pack(">I", track_id))
    first_dts = samples[0]["dts"]
    if first_dts < 0:
        raise ValueError("negative base decode time")
    tfdt = full_box(b"tfdt", 1, 0, struct.pack(">Q", first_dts))
    trun_flags = 0x000001 | 0x000100 | 0x000200 | 0x000400 | 0x000800
    entries = []
    for sample in samples:
        entries.append(struct.pack(
            ">IIIi",
            sample["duration"], sample["size"], sample["flags"],
            sample["cto"],
        ))
    payload = (
        struct.pack(">I", len(samples))
        + struct.pack(">i", data_offset)
        + b"".join(entries)
    )
    trun = full_box(b"trun", 1, trun_flags, payload)
    return box(b"traf", tfhd + tfdt + trun)


def make_segment(sequence: int, track_samples: List[Tuple[int, List[dict]]]) -> bytes:
    """Write one two-track fragment.

    ``track_samples`` is an ordered list of ``(track_id, samples)``.  Payloads
    are copied byte-for-byte and sample DTS values are taken from
    ``samples[i]["dts"]``.
    """
    active = [(track_id, samples) for track_id, samples in track_samples if samples]
    first = b""
    for track_id, samples in active:
        first += _make_traf(track_id, samples, 0)
    mfhd = full_box(b"mfhd", 0, 0, struct.pack(">I", sequence))
    moof_size = 8 + len(mfhd) + len(first)
    offsets = {}
    payload_size = 0
    for track_id, samples in active:
        offsets[track_id] = moof_size + 8 + payload_size
        payload_size += sum(sample["size"] for sample in samples)
    trafs = b"".join(
        _make_traf(track_id, samples, offsets[track_id])
        for track_id, samples in active
    )
    moof = box(b"moof", mfhd + trafs)
    if len(moof) != moof_size:
        raise AssertionError("unstable moof size")
    payload = b"".join(
        sample["data"] for _, samples in active for sample in samples
    )
    mdat = box(b"mdat", payload)
    return make_styp() + moof + mdat


def iter_segment_samples(data: bytes) -> List[Tuple[int, dict]]:
    """Read back the samples of a generated segment as ``(track_id, sample)``."""
    parsed = parse_fragment(data)
    result = []
    for track_id, samples in parsed["tracks"].items():
        for sample in samples:
            result.append((track_id, sample))
    return result
