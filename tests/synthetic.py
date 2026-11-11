"""Synthetic MP4 fragments used by the unit tests.

The real media fixtures are produced with FFmpeg (see ``fixtures.py``).  These
helpers make structurally valid, tiny fragmented MP4 files so that the
ingest/mux logic can be tested without a codec.  Payload bytes are arbitrary
but unique; the recorder must preserve them verbatim.
"""

import struct

from mp4 import box, full_box, ftyp_like, make_segment


def _tkhd(track_id: int, volume: int = 0) -> bytes:
    matrix = (
        b"\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        b"\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00"
        b"\x00\x00\x00\x00\x00\x00\x00\x00\x40\x00\x00\x00"
    )
    payload = (
        struct.pack(">II", 0, 0)
        + struct.pack(">I", track_id)
        + struct.pack(">I", 0)
        + struct.pack(">I", 0)
        + b"\x00" * 8
        + struct.pack(">HHHH", 0, 0, volume, 0)
        + matrix
        + struct.pack(">II", 0, 0)
    )
    return full_box(b"tkhd", 0, 3, payload)


def _mdhd(timescale: int) -> bytes:
    payload = (
        struct.pack(">II", 0, 0)
        + struct.pack(">I", timescale)
        + struct.pack(">I", 0)
        + struct.pack(">HH", 0x55C4, 0)
    )
    return full_box(b"mdhd", 0, 0, payload)


def _hdlr(handler: bytes) -> bytes:
    payload = (
        struct.pack(">I", 0) + handler + b"\x00" * 12
        + b"handler\x00"
    )
    return full_box(b"hdlr", 0, 0, payload)


def make_init(track_id: int, timescale: int, handler: bytes,
              flavour: int = 0) -> bytes:
    """A single-track init segment (ftyp + moov)."""
    # ``flavour`` lets a test create a different initialisation configuration
    # (in real captures such a difference lives in the codec configuration).
    extra = box(b"free", struct.pack(">I", flavour)) if flavour else b""
    trak = box(
        b"trak",
        _tkhd(track_id, 0x0100 if handler == b"soun" else 0)
        + box(b"mdia", _mdhd(timescale) + _hdlr(handler))
        + extra,
    )
    trex = full_box(b"trex", 0, 0,
                    struct.pack(">IIIII", track_id, 1, 0, 0, 0))
    ftyp = ftyp_like(b"ftyp", b"iso5", [b"iso5", b"iso6", b"mp41"])
    moov = box(b"moov", trak + box(b"mvex", trex))
    return ftyp + moov


def make_unframed_init(track_id: int, timescale: int, handler: bytes) -> bytes:
    return make_init(track_id, timescale, handler)


def sample(dts: int, duration: int, cto: int, payload: bytes,
           key: bool = False, flags: int = None) -> dict:
    if flags is None:
        flags = 0x02000000 if key else 0x01010000
    return {
        "dts": dts,
        "duration": duration,
        "cto": cto,
        "size": len(payload),
        "flags": flags,
        "data": payload,
    }


def make_fragment(track_id: int, sequence: int, samples) -> bytes:
    return make_segment(sequence, [(track_id, samples)])


def video_gop(origin: int, index: int, frames: int = 60,
              tick: int = 3003) -> list:
    """A closed GOP in decode order with B frames.

    Presentation indices are 0..frames-1 and the decode order is
    I, P, B, B, P, B, B, ... so the GOP ends exactly one frame after the last
    presentation timestamp.  Composition offsets are therefore positive for
    P frames and negative for B frames.
    """
    presentation = [0]
    group = 0
    while len(presentation) < frames:
        anchor = 3 * group + 3
        if anchor < frames:
            presentation.append(anchor)
        for candidate in (3 * group + 1, 3 * group + 2):
            if candidate < frames and len(presentation) < frames:
                presentation.append(candidate)
        group += 1

    base = origin + index * frames * tick
    payloads = [
        ("video-%d-%d" % (index, number)).encode() * 3
        for number in range(frames)
    ]
    samples = []
    for order, presentation_index in enumerate(presentation):
        samples.append(sample(
            base + order * tick, tick,
            (presentation_index - order) * tick,
            payloads[order],
            key=(order == 0),
        ))
    return samples


def audio_gop(origin: int, index: int, frames: int = 94,
              samples_per_frame: int = 1024) -> list:
    base = origin + index * frames * samples_per_frame
    return [
        sample(base + number * samples_per_frame, samples_per_frame, 0,
               ("audio-%d-%d" % (index, number)).encode())
        for number in range(frames)
    ]
