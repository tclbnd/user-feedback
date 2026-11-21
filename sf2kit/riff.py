"""RIFF sfbk walk."""


class Sf2Error(ValueError):
    pass


def chunks(data, pos, end):
    out = []
    while pos + 8 <= end:
        cid = data[pos:pos + 4]
        size = int.from_bytes(data[pos + 4:pos + 8], "little")
        body = (pos + 8, min(end, pos + 8 + size))
        if cid == b"LIST":
            out.append((b"LIST:" + data[pos + 8:pos + 12], (pos + 12, body[1])))
        else:
            out.append((cid, body))
        pos += 8 + size + (size & 1)
    return out


def load(data):
    if data[:4] != b"RIFF" or data[8:12] != b"sfbk":
        raise Sf2Error("not a SoundFont 2 file")
    found = {}
    for cid, (s, e) in chunks(data, 12, min(len(data), 8 + int.from_bytes(data[4:8], "little"))):
        if cid.startswith(b"LIST:"):
            for sub, (a, b) in chunks(data, s, e):
                found[sub] = data[a:b]
        else:
            found[cid] = data[s:e]
    return found
