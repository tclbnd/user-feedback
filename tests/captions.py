"""Small SCC files for the tests."""


def par(b):
    return b | (0x80 if bin(b).count("1") % 2 == 0 else 0)


def word(b1, b2):
    return "%02x%02x" % (par(b1), par(b2))


def control(code):
    return [word(0x14, code)] * 2


def text(s):
    data = [ord(c) for c in s] + ([0] if len(s) % 2 else [])
    return [word(data[i], data[i + 1]) for i in range(0, len(data), 2)]


def caption(s, row=15):
    rowcode = {14: 0x40, 15: 0x60}[row]
    return control(0x20) + control(0x2E) + [word(0x14, rowcode)] * 2 + text(s) + control(0x2F)


def scc(cues):
    out = ["Scenarist_SCC V1.0", ""]
    for label, words in cues:
        out += [label + "\t" + " ".join(words), ""]
    return "\n".join(out) + "\n"
