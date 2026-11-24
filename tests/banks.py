"""Minimal DLS banks: helpers to assemble instruments, regions and a wave pool."""
import struct


def ck(cid, body):
    # RIFF pads every chunk to an even length; the pad byte is not counted.
    pad = b"\0" if len(body) & 1 else b""
    return cid + struct.pack("<I", len(body)) + body + pad


def wsmp(unity=60, gain=0, loop=None):
    body = struct.pack("<IHhiII", 20, unity, 0, gain, 0, 1 if loop else 0)
    if loop is not None:
        body += struct.pack("<IIII", 16, 0, loop[0], loop[1])
    return body


def region(keys=(0, 127), cue=0, wsmp_body=None, kind=b"rgn "):
    body = ck(b"rgnh", struct.pack("<6H", keys[0], keys[1], 0, 127, 0, 0))
    if wsmp_body is not None:
        body += ck(b"wsmp", wsmp_body)
    body += ck(b"wlnk", struct.pack("<HHII", 0, 0, 1, cue))
    return ck(b"LIST", kind + body)


def wave(frames=100, wsmp_body=None, name=None, data_size=None, extra_info=b""):
    fmt = struct.pack("<HHIIHH", 1, 1, 22050, 44100, 2, 16)
    body = ck(b"fmt ", fmt)
    if wsmp_body is not None:
        body += ck(b"wsmp", wsmp_body)
    body += ck(b"data", bytes(data_size if data_size is not None else frames * 2))
    if name is not None or extra_info:
        info = b"INFO"
        if name is not None:
            info += ck(b"INAM", name)
        body += ck(b"LIST", info + extra_info)
    return ck(b"LIST", b"wave" + body)


def instrument(bank=0, program=0, name=None, regions=(), drum=False, extra_info=b""):
    """`bank` is the 14-bit MIDI value (MSB << 7 | LSB)."""
    # ulBank keeps the LSB in the first byte and the 7-bit MSB in the second;
    # the drum-kit flag is the top bit of the dword.
    insh = (bank & 0x7F) | (((bank >> 7) & 0x7F) << 8)
    body = ck(b"insh", struct.pack("<III", len(regions),
                                   insh | (0x80000000 if drum else 0), program))
    body += ck(b"LIST", b"lrgn" + b"".join(regions))
    if name is not None or extra_info:
        info = b"INFO"
        if name is not None:
            info += ck(b"INAM", name)
        body += ck(b"LIST", info + extra_info)
    return ck(b"LIST", b"ins " + body)


def make_bank(instruments, waves, cues=None):
    """Assemble a bank; `cues` lists the wave index each cue points at."""
    pool = b"".join(waves)
    offsets, at = [], 0
    for w in waves:
        offsets.append(at)
        at += len(w)
    if cues is None:
        cues = list(range(len(waves)))
    cue_table = [offsets[i] for i in cues]
    ptbl = ck(b"ptbl", struct.pack("<II", 8, len(cue_table))
              + struct.pack("<%dI" % len(cue_table), *cue_table))
    body = (b"DLS " + ck(b"colh", struct.pack("<I", len(instruments)))
            + ck(b"LIST", b"lins" + b"".join(instruments)) + ptbl
            + ck(b"LIST", b"wvpl" + pool))
    return b"RIFF" + struct.pack("<I", len(body)) + body


def bank(frames=100):
    return make_bank([instrument(program=5, regions=[region()])],
                     [wave(frames)])
