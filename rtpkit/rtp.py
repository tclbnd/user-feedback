"""RTP streams in pcap captures: link layers, IPv4/IPv6, UDP, RTP headers, loss, reordering, jitter (RFC 3550)."""
import struct
from fractions import Fraction

# RFC 3551 static payload types: clock rate in Hz
CLOCKS = {0: 8000, 3: 8000, 4: 8000, 5: 8000, 6: 16000, 7: 8000, 8: 8000, 9: 16000, 10: 44100, 11: 44100, 12: 8000,
          13: 8000, 14: 90000, 15: 8000, 16: 11025, 17: 22050, 18: 8000, 25: 90000, 26: 90000, 28: 90000, 31: 90000,
          32: 90000, 33: 90000, 34: 90000}
MAGICS = {b"\xd4\xc3\xb2\xa1": ("<", 1000000)}


class RtpError(ValueError):
    pass


def ip_payload(pkt, p):
    """UDP payload of an IPv4 / IPv6 packet starting at p, or None."""
    ver = pkt[p] >> 4
    if ver == 4:
        ihl = (pkt[p] & 0x0F) * 4
        proto = pkt[p + 9]
        frag = struct.unpack_from(">H", pkt, p + 6)[0]
        if proto != 17 or frag & 0x1FFF:                   # not UDP, or a later fragment
            return None
        p += ihl
    else:
        return None
    if p + 8 > len(pkt):
        return None
    length = struct.unpack_from(">H", pkt, p + 4)[0]
    return pkt[p + 8:p + length]


def link_payload(pkt, linktype, e):
    if linktype == 1:                                      # Ethernet, with any number of VLAN tags
        p, et = 12, struct.unpack_from(">H", pkt, 12)[0]
        return ip_payload(pkt, p + 2) if et in (0x0800, 0x86DD) else None
    raise RtpError("unsupported link type %d" % linktype)


def parse(data):
    if data[:4] not in MAGICS:
        raise RtpError("not a pcap capture")
    e, unit = MAGICS[data[:4]]
    linktype, = struct.unpack_from(e + "I", data, 20)
    streams, order = {}, []
    p, truncated = 24, 0
    while p + 16 <= len(data):
        sec, frac, incl, orig = struct.unpack_from(e + "IIII", data, p)
        pkt = data[p + 16:p + 16 + incl]
        p += 16 + incl
        if incl < orig:
            truncated += 1
            continue
        arrival = Fraction(sec) + Fraction(frac, unit)
        udp = link_payload(pkt, linktype, e)
        if not udp or len(udp) < 12 or udp[0] >> 6 != 2:
            continue                                       # not RTP (RTCP packet types 200-204 look like PT 72-76)
        cc, x, pad = udp[0] & 0x0F, udp[0] & 0x10, udp[0] & 0x20
        pt, marker = udp[1] & 0x7F, udp[1] >> 7
        seq, ts, ssrc = struct.unpack_from(">HII", udp, 2)
        size = len(udp) - 12
        s = streams.get(ssrc)
        if s is None:
            s = streams[ssrc] = {"ssrc": "%08x" % ssrc, "payload_type": pt, "packets": 0, "payload_bytes": 0, "markers": 0,
                                 "_max": seq, "_seen": set(), "duplicates": 0, "out_of_order": 0,
                                 "_first": arrival, "_last": arrival, "_j": 0.0, "_prev": None}
            order.append(ssrc)
        # extended sequence number: the 32-bit value closest to the highest one seen so far (RFC 3550 A.1)
        ext = seq
        s["_max"] = max(s["_max"], ext)
        s["_seen"].add(ext)
        s["packets"] += 1
        s["payload_bytes"] += size
        s["markers"] += marker
        s["_last"] = arrival
    out = []
    for ssrc in order:
        s = streams[ssrc]
        exts = sorted(s["_seen"]) or [0]
        expected = exts[-1] - exts[0] + 1
        out.append({"ssrc": s["ssrc"], "payload_type": s["payload_type"], "packets": s["packets"],
                    "lost": expected - s["packets"], "duplicates": s["duplicates"], "out_of_order": s["out_of_order"],
                    "payload_bytes": s["payload_bytes"], "markers": s["markers"],
                    "duration": str(s["_last"] - s["_first"]),
                    "jitter": None})
    return {"link_type": linktype, "nanosecond": unit == 1000000000, "truncated_packets": truncated, "streams": out}
