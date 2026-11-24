"""MIDI Sample Dump Standard transfers saved as .syx: dump header, data packets, checksums, handshakes, samples."""
from fractions import Fraction

LOOP_OFF = 0x7F
LOOPS = {0x00: "forward", 0x01: "alternating"}
HANDSHAKES = {0x7F: "ack", 0x7E: "nak", 0x7D: "cancel", 0x7C: "wait", 0x7B: "eof"}

UNIVERSAL_ID = 0x7E
DUMP_HEADER = 0x01
DATA_PACKET = 0x02
DATA_BYTES = 120
PACKET_SIZE = 7 + DATA_BYTES  # F0 7E dev 02 number ...data... checksum F7
HEADER_SIZE = 21
HANDSHAKE_SIZE = 6


class SdsError(ValueError):
    pass


def num(b):
    """SDS multi-byte number: 7-bit groups, least significant group first."""
    return sum(v << (7 * i) for i, v in enumerate(b))


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


def sign_extend(value, bits):
    """Interpret the low ``bits`` of ``value`` as a two's complement signed integer."""
    if value & (1 << (bits - 1)):
        value -= 1 << bits
    return value


def decode_words(bodies, bits, count):
    """Decode sample words from SDS data packet bodies.

    Each sample word is carried in the minimum number of 7-bit data bytes
    needed to hold ``bits`` bits, with the least significant 7-bit group
    sent first. Unused high bits hold the sign, so masking to ``bits``
    bits and sign extending reconstructs the two's complement word.
    Packing runs on across packet boundaries.
    """
    groups_per_word = -(-bits // 7)
    mask = (1 << bits) - 1
    total = b"".join(bodies)
    words = []
    offset = 0
    for _ in range(count):
        if offset + groups_per_word > len(total):
            break
        value = 0
        for j in range(groups_per_word):
            value |= (total[offset + j] & 0x7F) << (7 * j)
        offset += groups_per_word
        words.append(sign_extend(value & mask, bits))
    return words


def parse(data):
    head = None
    packets = bad = order = 0
    expect = None
    bodies = []
    shakes = {}
    for m in messages(data):
        if len(m) < 5 or m[1] != UNIVERSAL_ID:
            continue
        dev, sub = m[2], m[3]
        if sub == DUMP_HEADER and len(m) == HEADER_SIZE:
            head = {"device": dev, "sample_number": num(m[4:6]), "bits": m[6],
                    "period": num(m[7:10]), "length": num(m[10:13]),
                    "loop_start": num(m[13:16]), "loop_end": num(m[16:19]),
                    "loop_type": m[19]}
        elif sub == DATA_PACKET and len(m) == PACKET_SIZE:
            packets += 1
            number, body, checksum = m[4], m[5:125], m[125]
            # SDS checksum: XOR of every byte from the universal ID through
            # the final data byte (the leading F0 and checksum itself excluded).
            running = 0
            for b in m[1:125]:
                running ^= b
            if running & 0x7F != checksum:
                bad += 1
            # Packet numbers run 0..127 and wrap; a transfer need not start at 0.
            if expect is not None and number != expect:
                order += 1
            expect = (number + 1) & 0x7F
            bodies.append(body)
        elif sub in HANDSHAKES and len(m) == HANDSHAKE_SIZE:
            name = HANDSHAKES[sub]
            shakes[name] = shakes.get(name, 0) + 1
    if head is None:
        raise SdsError("no sample dump header")

    length = head["length"]
    bits = head["bits"]
    full_scale = 1 << (bits - 1)
    groups_per_word = -(-bits // 7)
    available = (len(bodies) * DATA_BYTES) // groups_per_word
    words = decode_words(bodies, bits, min(length, available))
    rate = Fraction(10 ** 9, head["period"]) if head["period"] else None

    if head["loop_type"] == LOOP_OFF:
        loop = None
    else:
        loop = {"start": head["loop_start"], "end": head["loop_end"],
                "type": LOOPS.get(head["loop_type"], "type_%d" % head["loop_type"])}

    return {"device_id": head["device"], "sample_number": head["sample_number"], "bits": bits,
            "sample_rate": str(rate) if rate else None, "length": length,
            "duration": str(Fraction(length, rate)) if rate else None,
            "loop": loop,
            "packets": packets, "bad_checksums": bad, "out_of_order": order,
            "samples": len(words), "complete": len(words) == length,
            "first_sample": words[0] if words else None,
            "peak": "%.6f" % (max(abs(w) for w in words) / full_scale) if words else None,
            "handshakes": dict(sorted(shakes.items()))}

