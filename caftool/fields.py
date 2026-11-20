"""Decoding of desc, pakt, chan and info chunks."""
from .chunks import CafError


def u32(b, p):
    return int.from_bytes(b[p:p + 4], "big")


def desc(body):
    if len(body) < 32:
        raise CafError("short desc chunk")
    return {
        "sample_rate": int.from_bytes(body[0:8], "big"),
        "format_id": body[8:12].decode("latin-1"),
        "format_flags": u32(body, 12),
        "bytes_per_packet": u32(body, 16),
        "frames_per_packet": u32(body, 20),
        "channels": u32(body, 24),
        "bits": u32(body, 28),
    }


def pakt(body):
    packets = int.from_bytes(body[0:8], "big")
    valid = int.from_bytes(body[8:16], "big")
    priming = u32(body, 16)
    remainder = u32(body, 20)
    sizes = list(body[24:24 + packets])
    return {"packets": packets, "valid_frames": valid, "priming": priming,
            "remainder": remainder, "sizes": sizes}


def chan(body):
    return {"tag": u32(body, 0), "bitmap": u32(body, 4), "descriptions": u32(body, 8)}


def info(body):
    count = u32(body, 0)
    parts = body[4:].split(b"\x00")
    out = {}
    for i in range(count):
        out[parts[2 * i].decode("utf-8", "replace")] = parts[2 * i + 1].decode("utf-8", "replace")
    return out
