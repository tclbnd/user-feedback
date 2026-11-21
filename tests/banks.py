"""Builders for small synthetic SoundFont 2 banks used by the tests."""
import struct


def _chunk(cid, body):
    return cid + struct.pack("<I", len(body)) + body + (b"\0" if len(body) & 1 else b"")


def _list(kind, *chunks_):
    body = kind + b"".join(chunks_)
    return b"LIST" + struct.pack("<I", len(body)) + body


def _gen(oper, amount):
    if isinstance(amount, tuple):
        return struct.pack("<HBB", oper, amount[0], amount[1])
    return struct.pack("<Hh", oper, amount) if amount < 0 else struct.pack("<HH", oper, amount)


def _zones(items, terminal_kind):
    """items: list of zone lists; each zone is a list of (oper, amount)."""
    bags, gens = [], []
    heads = []
    for zones in items:
        heads.append(len(bags))
        for z in zones:
            bags.append((len(gens), 0))
            gens.extend(z)
    heads.append(len(bags))
    bags.append((len(gens), 0))
    gens.append((0, 0))
    return heads, b"".join(struct.pack("<HH", *b) for b in bags), b"".join(_gen(o, a) for o, a in gens)


def sf2(name="Bank", version=(2, 1), samples=(), instruments=(), presets=(), sm24=False, frames=None):
    """samples: (name, start, end, loop_start, loop_end, rate, root, corr, type) or with a link before type
    instruments: (name, zones); presets: (name, preset, bank, zones); zones are lists of (oper, amount)."""
    info = _list(b"INFO", _chunk(b"ifil", struct.pack("<HH", *version)), _chunk(b"INAM", name.encode() + b"\0"))
    total = frames if frames is not None else max([s[2] for s in samples] + [0]) + 46
    sdta = [_chunk(b"smpl", b"\0\0" * total)] + ([_chunk(b"sm24", b"\0" * total)] if sm24 else [])
    iheads, ibag, igen = _zones([z for _, z in instruments], "EOI")
    pheads, pbag, pgen = _zones([z for *_, z in presets], "EOP")
    inst = b"".join(struct.pack("<20sH", n.encode(), h) for (n, _), h in zip(instruments, iheads)) + struct.pack("<20sH", b"EOI", iheads[-1])
    phdr = b"".join(struct.pack("<20sHHHIII", n.encode(), p, b, h, 0, 0, 0) for (n, p, b, _), h in zip(presets, pheads))
    phdr += struct.pack("<20sHHHIII", b"EOP", 0, 0, pheads[-1], 0, 0, 0)
    shdr = b"".join(struct.pack("<20sIIIIIBbHH", s[0].encode(), *(s[1:] if len(s) == 10 else s[1:8] + (0, s[8]))) for s in samples) + struct.pack("<20sIIIIIBbHH", b"EOS", 0, 0, 0, 0, 0, 0, 0, 0, 0)
    pdta = _list(b"pdta", _chunk(b"phdr", phdr), _chunk(b"pbag", pbag), _chunk(b"pmod", bytes(10)), _chunk(b"pgen", pgen),
                 _chunk(b"inst", inst), _chunk(b"ibag", ibag), _chunk(b"imod", bytes(10)), _chunk(b"igen", igen), _chunk(b"shdr", shdr))
    body = b"sfbk" + info + _list(b"sdta", *sdta) + pdta
    return b"RIFF" + struct.pack("<I", len(body)) + body
