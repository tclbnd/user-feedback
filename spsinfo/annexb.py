"""NAL units from an Annex B byte stream."""


def nal_units(data):
    starts = []
    i = 0
    while i + 3 <= len(data):
        if data[i:i + 3] == b"\x00\x00\x01":
            starts.append(i + 3)
            i += 3
        else:
            i += 1
    out = []
    for n, s in enumerate(starts):
        end = starts[n + 1] - 3 if n + 1 < len(starts) else len(data)
        out.append(data[s:end].rstrip(b"\x00"))
    return out
