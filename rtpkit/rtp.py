"""RTP streams in pcap captures: link layers, IPv4/IPv6, UDP, RTP headers, loss, reordering, jitter (RFC 3550)."""
import struct
from fractions import Fraction

# RFC 3551 static payload types: clock rate in Hz.  G.722 (PT 9) runs its
# RTP clock at 8 kHz even though it samples audio at 16 kHz (RFC 3551 4.5.2).
CLOCKS = {0: 8000, 3: 8000, 4: 8000, 5: 8000, 6: 16000, 7: 8000, 8: 8000, 9: 8000, 10: 44100, 11: 44100, 12: 8000,
          13: 8000, 14: 90000, 15: 8000, 16: 11025, 17: 22050, 18: 8000, 25: 90000, 26: 90000, 28: 90000, 31: 90000,
          32: 90000, 33: 90000, 34: 90000}
# pcap magic number -> (struct byte order, time stamp units per second); both
# byte orders and both microsecond and nanosecond resolutions are valid.
MAGICS = {b"\xd4\xc3\xb2\xa1": ("<", 1000000), b"\xa1\xb2\xc3\xd4": (">", 1000000),
          b"\x4d\x3c\xb2\xa1": ("<", 1000000000), b"\xa1\xb2\x3c\x4d": (">", 1000000000)}
IP_TYPES = (0x0800, 0x86DD)                            # EtherType IPv4 / IPv6
VLAN_TYPES = (0x8100, 0x88A8, 0x9100)                  # 802.1Q, 802.1ad and QinQ tags
IPV6_EXT = (0, 43, 44, 51, 60)                         # hop-by-hop, routing, fragment, AH, destination options


class RtpError(ValueError):
    pass


def ip_payload(pkt, p):
    """UDP payload of an IPv4 / IPv6 packet starting at p, or None."""
    if p + 1 > len(pkt):
        return None
    ver = pkt[p] >> 4
    if ver == 4:
        if p + 20 > len(pkt):
            return None
        ihl = (pkt[p] & 0x0F) * 4
        proto = pkt[p + 9]
        frag = struct.unpack_from(">H", pkt, p + 6)[0]
        if ihl < 20 or proto != 17 or frag & 0x3FFF:       # not UDP, or any fragment (DF bit is 0x4000)
            return None
        p += ihl
    elif ver == 6:
        if p + 40 > len(pkt):
            return None
        nxt = pkt[p + 6]
        p += 40
        while nxt in IPV6_EXT:                             # walk extension headers to the upper-layer protocol
            if nxt == 44:                                  # fragment header: skip any fragment
                if p + 8 > len(pkt) or struct.unpack_from(">H", pkt, p + 2)[0] & 0xFFF9:
                    return None
                nxt, p = pkt[p], p + 8
            elif nxt == 51:                                # authentication header: length in 4-byte units + 2
                if p + 2 > len(pkt):
                    return None
                nxt, p = pkt[p], p + (pkt[p + 1] + 2) * 4
            else:
                if p + 2 > len(pkt):
                    return None
                nxt, p = pkt[p], p + (pkt[p + 1] + 1) * 8
        if nxt != 17:                                      # not UDP
            return None
    else:
        return None
    if p + 8 > len(pkt):
        return None
    length = struct.unpack_from(">H", pkt, p + 4)[0]
    if length == 0:                                        # IPv6 jumbogram: no UDP length
        return pkt[p + 8:]
    if length < 8:
        return None
    return pkt[p + 8:p + length]


def ethertype(pkt, off):
    """EtherType at off, or None if the frame is too short."""
    if off + 2 > len(pkt):
        return None
    return struct.unpack_from(">H", pkt, off)[0]


def link_payload(pkt, linktype, e):
    if linktype == 1:                                      # Ethernet, with any number of VLAN tags
        off = 12
        et = ethertype(pkt, off)
        while et in VLAN_TYPES:                            # each tag: 2-byte TCI then the next EtherType
            off += 4
            et = ethertype(pkt, off)
        return ip_payload(pkt, off + 2) if et in IP_TYPES else None
    if linktype == 113:                                    # Linux cooked capture
        return ip_payload(pkt, 16) if ethertype(pkt, 14) in IP_TYPES else None
    if linktype in (101, 12, 14):                          # raw IP
        return ip_payload(pkt, 0)
    if linktype == 0:                                      # BSD loopback: address family in capture byte order
        if len(pkt) < 4:
            return None
        fam, = struct.unpack_from(e + "I", pkt, 0)
        return ip_payload(pkt, 4) if fam in (2, 24, 28, 30) else None
    raise RtpError("unsupported link type %d" % linktype)


def sniff_rtp(udp):
    """Return (pt, marker, seq, ts, ssrc, payload) for a UDP payload that is RTP, else None.

    The RTP payload size excludes the CSRC list, the header extension and any
    padding: only the actual payload goes into payload_bytes."""
    if len(udp) < 12 or udp[0] >> 6 != 2:
        return None
    pt, marker = udp[1] & 0x7F, udp[1] >> 7
    if 72 <= pt <= 76:                                     # RTCP packet types 200-204 look like PT 72-76
        return None
    seq, ts, ssrc = struct.unpack_from(">HII", udp, 2)
    cc, ext = udp[0] & 0x0F, udp[0] & 0x10
    pad = (udp[0] >> 5) & 1
    end = len(udp)
    if pad:                                                # padding count includes the count byte itself
        count = udp[end - 1]
        if count == 0 or end - count < 12:
            return None
        end -= count
    off = 12 + 4 * cc                                     # skip the CSRC list
    if ext:                                                # header extension: 16-bit profile, 16-bit word count
        if off + 4 > end:
            return None
        words = struct.unpack_from(">H", udp, off + 2)[0]
        off += 4 + 4 * words
        if off > end:
            return None
    if off > end:
        return None
    return pt, marker, seq, ts, ssrc, bytes(udp[off:end])


def parse(data):
    if data[:4] not in MAGICS:
        raise RtpError("not a pcap capture")
    if len(data) < 24:
        raise RtpError("truncated pcap header")
    e, unit = MAGICS[data[:4]]
    linktype, = struct.unpack_from(e + "I", data, 20)
    streams, order = {}, []
    p, truncated = 24, 0
    while p + 16 <= len(data):
        sec, frac, incl, orig = struct.unpack_from(e + "IIII", data, p)
        if p + 16 + incl > len(data):
            truncated += 1                                 # record runs past end of file
            break
        pkt = data[p + 16:p + 16 + incl]
        p += 16 + incl
        if incl < orig:                                    # captured shorter than it was on the wire
            truncated += 1
            continue
        arrival = Fraction(sec) + Fraction(frac, unit)
        udp = link_payload(pkt, linktype, e)
        if udp is None:
            continue
        rtp = sniff_rtp(udp)
        if rtp is None:
            continue
        pt, marker, seq, ts, ssrc, payload = rtp
        s = streams.get(ssrc)
        if s is None:
            s = streams[ssrc] = {"ssrc": "%08x" % ssrc, "payload_type": pt, "packets": 0, "payload_bytes": 0, "markers": 0,
                                 "_max_ext": None, "_seen": set(), "duplicates": 0, "out_of_order": 0,
                                 "_first": arrival, "_last": arrival, "_j": 0.0, "_prev": None,
                                 "_clock": CLOCKS.get(pt)}
            order.append(ssrc)
        # Extended sequence number: unwrap the 16-bit sequence number around the
        # highest one seen so far, treating the delta as a signed 16-bit value
        # (RFC 3550 Appendix A.1).  This keeps 65535 -> 0 a +1 step rather than
        # a giant backwards jump, while still recognising duplicates/reordering.
        if s["_max_ext"] is None:
            ext = s["_max_ext"] = seq
        else:
            hi = s["_max_ext"] & 0xFFFF
            delta = (seq - hi + 0x8000) % 0x10000 - 0x8000
            ext = s["_max_ext"] + delta
        if ext in s["_seen"]:
            s["duplicates"] += 1
            continue
        if ext < s["_max_ext"]:
            s["out_of_order"] += 1
        elif ext > s["_max_ext"]:
            s["_max_ext"] = ext
        s["_seen"].add(ext)
        s["packets"] += 1
        s["payload_bytes"] += len(payload)
        s["markers"] += marker
        s["_last"] = arrival
        clock = s["_clock"]
        if clock:
            r = arrival * clock
            if s["_prev"] is not None:
                d = (r - s["_prev"][0]) - ((ts - s["_prev"][1] + 2 ** 31) % 2 ** 32 - 2 ** 31)
                s["_j"] += (abs(d) - s["_j"]) / 16
            s["_prev"] = (r, ts)
    out = []
    for ssrc in order:
        s = streams[ssrc]
        exts = sorted(s["_seen"])
        expected = exts[-1] - exts[0] + 1
        out.append({"ssrc": s["ssrc"], "payload_type": s["payload_type"], "packets": s["packets"],
                    "lost": expected - s["packets"], "duplicates": s["duplicates"], "out_of_order": s["out_of_order"],
                    "payload_bytes": s["payload_bytes"], "markers": s["markers"],
                    "duration": str(s["_last"] - s["_first"]),
                    "jitter": "%.2f" % s["_j"] if CLOCKS.get(s["payload_type"]) else None})
    return {"link_type": linktype, "nanosecond": unit == 1000000000, "truncated_packets": truncated, "streams": out}
