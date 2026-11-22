"""Builders for small synthetic BRSTM files used by the tests."""
import struct


def brstm(codec=2, channels=2, rate=32000, total=100000, loop=None, tracks=None, track_type=1, stale_loop=0, endian=">"):
    e, bsize, cut, no_adpc, ps, version = endian, 0x2000, 0, None, 0x23, 0x0100
    spb = bsize // 8 * 14 if codec == 2 else bsize // (1 if codec == 0 else 2)
    blocks = -(-total // spb)
    last_samples = total - (blocks - 1) * spb
    if codec == 2:
        last_size = last_samples // 14 * 8 + (last_samples % 14 and (last_samples % 14 + 1) // 2 + 1)
    else:
        last_size = last_samples * (1 if codec == 0 else 2)
    last_padded = -(-last_size // 32) * 32
    tracks = tracks if tracks is not None else [list(range(i, min(i + 2, channels))) for i in range(0, channels, 2)]
    # HEAD body: 3 refs, then stream info, track info, channel info
    body = bytearray(24)
    def put_ref(pos, off):
        body[pos:pos + 8] = struct.pack(e + "BBHI", 1, 0, 0, off)
    stream_off = len(body)
    body += struct.pack(e + "BBBBHH9III", codec, 1 if loop is not None else 0, channels, 0, rate, 0,
                        loop if loop is not None else stale_loop, total, 0, blocks, bsize, spb, last_size, last_samples, last_padded, bsize // 8 * 2, 0)
    track_off = len(body)
    body += struct.pack(e + "BBH", len(tracks), track_type, 0)
    refs_at = len(body)
    body += bytes(8 * len(tracks))
    for i, chs in enumerate(tracks):
        o = len(body)
        body[refs_at + i * 8:refs_at + i * 8 + 8] = struct.pack(e + "BBHI", 1, track_type, 0, o)
        if track_type == 1:
            body += bytes([100 + i, 64, 0, 0, 0, 0, 0, 0])
        body += bytes([len(chs)] + chs)
        body += bytes(-len(body) % 4)
    chan_off = len(body)
    body += struct.pack(e + "BBH", channels, 0, 0)
    crefs = len(body)
    body += bytes(8 * channels)
    for c in range(channels):
        o = len(body)
        body[crefs + c * 8:crefs + c * 8 + 8] = struct.pack(e + "BBHI", 1, 0, 0, o)
        inner = o + 8
        body += struct.pack(e + "BBHI", 1, 0, 0, inner)
        body += struct.pack(e + "16h", *[(c * 37 + k * 211) % 4000 - 2000 for k in range(16)])
        body += struct.pack(e + "HHhhHhh", 0, ps + c, 0, 0, ps + c, 0, 0) + bytes(2)
    put_ref(0, stream_off)
    put_ref(8, track_off)
    put_ref(16, chan_off)
    body += bytes(-len(body) % 32)
    head = b"HEAD" + struct.pack(e + "I", len(body) + 8) + bytes(body)
    adpc_wanted = codec == 2 if no_adpc is None else not no_adpc
    adpc = b"ADPC" + struct.pack(e + "I", 8 + 4 * blocks * channels) + bytes(4 * blocks * channels) if adpc_wanted else b""
    adpc += bytes(-len(adpc) % 32)
    audio_len = ((blocks - 1) * bsize + last_padded) * channels
    head_at = 0x40
    adpc_at = head_at + len(head)
    data_at = adpc_at + len(adpc)
    data_off_abs = data_at + 0x20
    head = head[:8] + head[8:8 + stream_off + 16] + struct.pack(e + "I", data_off_abs) + head[8 + stream_off + 20:]
    datachunk = b"DATA" + struct.pack(e + "II", 0x20 + audio_len, 0x18) + bytes(0x14) + bytes(audio_len)
    chunks = [(head_at, len(head))] + ([(adpc_at, len(adpc))] if adpc else []) + [(data_at, len(datachunk))]
    total_size = data_at + len(datachunk)
    hdr = b"RSTM" + (b"\xfe\xff" if e == ">" else b"\xff\xfe") + struct.pack(e + "HIHH", version, total_size, 0x40, len(chunks))
    for o, s in chunks:
        hdr += struct.pack(e + "II", o, s)
    hdr += bytes(0x40 - len(hdr))
    out = hdr + head + adpc + datachunk
    return out[:len(out) - cut] if cut else out
