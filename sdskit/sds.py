"""MIDI Sample Dump Standard transfers saved as .syx: dump header, data packets, checksums, handshakes, samples."""
from fractions import Fraction

LOOPS = {0x00: "forward", 0x01: "alternating"}
HANDSHAKES = {0x7F: "ack", 0x7E: "nak", 0x7D: "cancel", 0x7C: "wait", 0x7B: "eof"}


class SdsError(ValueError):
    pass


def num(b):
    """7-bit groups, least significant first."""
    return sum(v << (7 * i) for i, v in enumerate(reversed(b)))


def messages(data):
    p = 0
    while True:
        s = data.find(b"\xf0", p)
        if s < 0:
            return
        e = data.find(b"\xf7", s)
        if e < 0:
            return
        yield data[s:e + 1]
        p = e + 1


def parse(data):
    head = None
    packets = bad = order = 0
    expect = 0
    words = []
    shakes = {}
    for m in messages(data):
        if len(m) < 5 or m[1] != 0x7E:
            continue
        dev, sub = m[2], m[3]
        if sub == 0x01 and len(m) == 21:
            head = {"device": dev, "sample_number": num(m[4:6]), "bits": m[6], "period": num(m[7:10]),
                    "length": num(m[10:13]), "loop_start": num(m[13:16]), "loop_end": num(m[16:19]), "loop_type": m[19]}
        elif sub == 0x02 and len(m) == 127:
            packets += 1
            k, body, chk = m[4], m[5:125], m[125]
            x = 0xF0 ^ 0x7E ^ dev ^ 0x02 ^ k
            for b in body:
                x ^= b
            if x & 0x7F != chk:
                bad += 1
            if k != expect:
                order += 1
            expect = (k + 1) & 0x7F
            if head:
                per = head["bits"] // 7
                for i in range(0, 120 - per + 1, per):
                    v = 0
                    for b in body[i:i + per]:
                        v = v << 7 | b
                    v >>= max(per * 7 - head["bits"], 0)
                    words.append(v)
        elif sub in HANDSHAKES and len(m) == 6:
            name = HANDSHAKES[sub]
            shakes[name] = shakes.get(name, 0) + 1
    if head is None:
        raise SdsError("no sample dump header")
    words = words[:head["length"]]
    rate = Fraction(10 ** 9, head["period"]) if head["period"] else None
    full = 1 << (head["bits"] - 1)
    return {"device_id": head["device"], "sample_number": head["sample_number"], "bits": head["bits"],
            "sample_rate": str(rate) if rate else None, "length": head["length"],
            "duration": str(head["length"] / rate) if rate else None,
            "loop": {"start": head["loop_start"], "end": head["loop_end"], "type": LOOPS.get(head["loop_type"], "type_%d" % head["loop_type"])}
            ,
            "packets": packets, "bad_checksums": bad, "out_of_order": order, "samples": len(words),
            "complete": len(words) == head["length"], "first_sample": words[0] if words else None,
            "peak": "%.6f" % (max(abs(w) for w in words) / full) if words else None, "handshakes": dict(sorted(shakes.items()))}
