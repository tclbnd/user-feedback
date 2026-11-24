"""RTP streams in pcap captures: link layers, IPv4/IPv6, UDP, RTP headers, loss, reordering, jitter (RFC 3550)."""
import struct
from fractions import Fraction

# RFC 3551 static payload types: clock rate in Hz.  G.722 (9) samples at 16 kHz but its RTP clock ticks at 8 kHz.
CLOCKS = {0: 8000, 3: 8000, 4: 8000, 5: 8000, 6: 16000, 7: 8000, 8: 8000, 9: 8000, 10: 44100, 11: 44100, 12: 8000,
          13: 8000, 14: 90000, 15: 8000, 16: 11025, 17: 22050, 18: 8000, 25: 90000, 26: 90000, 28: 90000, 31: 90000,
          32: 90000, 33: 90000, 34: 90000}
# pcap magic -> (byte order, timestamp units per second): both byte orders, both resolutions
MAGICS = {b"\xa1\xb2\xc3\xd4": (">", 1000000), b"\xd4\xc3\xb2\xa1": ("<", 1000000),
          b"\xa1\xb2\x3c\x4d": (">", 1000000000), b"\x4d\x3c\xb2\xa1": ("<", 1000000000)}

LOOPBACK = (0, 108)                                       # BSD loopback: 4-byte address family, then the IP packet
RAW_IP = (12, 101)                                        # raw IP: the IP header comes first
IPV4_ONLY, IPV6_ONLY = 228, 229
LINKTYPES = (0, 1, 12, 101, 108, 113, 228, 229, 276)
VLAN = (0x8100, 0x88A8, 0x9100, 0x9200)                   # 802.1Q, 802.1ad and older QinQ tags
# RTCP packet types 200-207 appear in RTP's 7-bit payload type field as 72-79 (RFC 3550, RFC 3551)
RTCP_PT = frozenset(range(72, 80))


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
        if ihl < 20 or p + ihl > len(pkt):
            return None
        total, = struct.unpack_from(">H", pkt, p + 2)
        frag, proto = struct.unpack_from(">H", pkt, p + 6)[0], pkt[p + 9]
        if proto != 17 or frag & 0x1FFF:                   # not UDP, or not the first fragment
            return None
        end = p + total
        p += ihl
    elif ver == 6:
        if p + 40 > len(pkt):
            return None
        nxt, = struct.unpack_from(">B", pkt, p + 6)
        end = p + 40 + struct.unpack_from(">H", pkt, p + 4)[0]
        p += 40
        while nxt != 17:                                   # walk the extension header chain
            if nxt == 44:                                  # fragment header
                if p + 8 > len(pkt):
                    return None
                if struct.unpack_from(">H", pkt, p + 2)[0] & 0xFFF8:   # not the first fragment
                    return None
                nxt, p = pkt[p], p + 8
            elif nxt in (0, 43, 60):                       # hop-by-hop, routing, destination options
                if p + 2 > len(pkt):
                    return None
                nxt, ln = pkt[p], pkt[p + 1]
                p += (ln + 1) * 8
            else:                                          # AH, ESP, TCP, ICMP, ...
                return None
            if p > len(pkt):
                return None
    else:
        return None
    if p + 8 > len(pkt) or end < p + 8:
        return None
    ulen = struct.unpack_from(">H", pkt, p + 4)[0]
    if ulen >= 8:                                          # a UDP length of 0 means a jumbogram (RFC 2675)
        end = min(end, p + ulen, len(pkt))
    return pkt[p + 8:min(end, len(pkt))]


def link_payload(pkt, linktype):
    """UDP payload of a captured frame, or None."""
    if linktype in LOOPBACK:
        return ip_payload(pkt, 4)
    if linktype in RAW_IP or linktype in (IPV4_ONLY, IPV6_ONLY):
        return ip_payload(pkt, 0)
    if linktype == 1:                                      # Ethernet II
        if len(pkt) < 14:
            return None
        p, et = 12, struct.unpack_from(">H", pkt, 12)[0]
    elif linktype == 113:                                  # Linux cooked capture v1 (LINKTYPE_LINUX_SLL)
        if len(pkt) < 16:
            return None
        p, et = 14, struct.unpack_from(">H", pkt, 14)[0]
    elif linktype == 276:                                  # Linux cooked capture v2 (LINKTYPE_LINUX_SLL2)
        if len(pkt) < 20:
            return None
        p, et = 18, struct.unpack_from(">H", pkt, 0)[0]    # protocol type first, payload after the 20-byte header
    else:
        raise RtpError("unsupported link type %d" % linktype)
    p += 2
    while et in VLAN:                                      # skip any number of stacked VLAN tags
        if p + 4 > len(pkt):
            return None
        et = struct.unpack_from(">H", pkt, p + 2)[0]
        p += 4
    return ip_payload(pkt, p) if et in (0x0800, 0x86DD) else None


def rtp_packet(udp):
    """(payload type, marker, sequence number, timestamp, ssrc, payload length), or None if this is not RTP."""
    if len(udp) < 12 or udp[0] >> 6 != 2:                  # too short, or not version 2
        return None
    pt, marker = udp[1] & 0x7F, udp[1] >> 7
    if pt in RTCP_PT:                                      # RTCP carried on the same port pair
        return None
    seq, ts, ssrc = struct.unpack_from(">HII", udp, 2)
    p = 12 + 4 * (udp[0] & 0x0F)                           # skip the CSRC list
    if udp[0] & 0x10:                                      # skip the header extension
        if p + 4 > len(udp):
            return None
        p += 4 + 4 * struct.unpack_from(">H", udp, p + 2)[0]
    if p > len(udp):
        return None
    pad = 0
    if udp[0] & 0x20:                                      # padding: the last octet counts itself too
        pad = udp[-1]
        if pad == 0 or pad > len(udp) - p:
            return None
    return pt, marker, seq, ts, ssrc, len(udp) - p - pad


def extend(s, seq):
    """Extended sequence number of seq; s["_max_seq"] tracks the highest sequence number seen (RFC 3550 A.1)."""
    if s["_max_seq"] is None:
        s["_max_seq"] = seq
        return seq
    delta = (seq - s["_max_seq"]) & 0xFFFF
    if delta < 0x8000:                                     # newer than anything seen so far
        if seq < s["_max_seq"]:                            # ... across a wrap of the 16-bit counter
            s["_cycles"] += 0x10000
        s["_max_seq"] = seq
        return s["_cycles"] + seq
    return s["_cycles"] + seq - (0x10000 if seq > s["_max_seq"] else 0)   # a straggler from before


def parse(data):
    if len(data) < 24 or data[:4] not in MAGICS:
        raise RtpError("not a pcap capture")
    e, unit = MAGICS[data[:4]]
    linktype, = struct.unpack_from(e + "I", data, 20)
    if linktype not in LINKTYPES:
        raise RtpError("unsupported link type %d" % linktype)
    streams, order = {}, []
    p, truncated = 24, 0
    while p + 16 <= len(data):
        sec, frac, incl, orig = struct.unpack_from(e + "IIII", data, p)
        pkt = data[p + 16:p + 16 + incl]
        p += 16 + incl
        if incl < orig:
            truncated += 1
            continue
        udp = link_payload(pkt, linktype)
        rtp = rtp_packet(udp) if udp else None
        if rtp is None:
            continue
        pt, marker, seq, ts, ssrc, size = rtp
        arrival = Fraction(sec) + Fraction(frac, unit)
        s = streams.get(ssrc)
        if s is None:
            s = streams[ssrc] = {"ssrc": "%08x" % ssrc, "payload_type": pt, "packets": 0, "payload_bytes": 0,
                                 "markers": 0, "duplicates": 0, "out_of_order": 0,
                                 "_clock": CLOCKS.get(pt), "_first": arrival, "_last": arrival, "_max_seq": None,
                                 "_cycles": 0, "_max_ext": None, "_min_ext": None, "_seen": set(),
                                 "_j": Fraction(0), "_prev_r": None, "_prev_s": None}
            order.append(ssrc)
        ext = extend(s, seq)
        if s["_max_ext"] is not None and ext < s["_max_ext"]:
            s["out_of_order"] += 1                         # below the highest extended number seen before it
        s["_max_ext"] = ext if s["_max_ext"] is None else max(s["_max_ext"], ext)
        s["_min_ext"] = ext if s["_min_ext"] is None else min(s["_min_ext"], ext)
        if ext in s["_seen"]:
            s["duplicates"] += 1
        else:
            s["_seen"].add(ext)
            s["packets"] += 1
            s["payload_bytes"] += size
            s["markers"] += marker
            s["_first"] = min(s["_first"], arrival)        # duration spans the distinct packets only
            s["_last"] = max(s["_last"], arrival)
        clock = s["_clock"]
        if clock:                                          # RFC 3550 6.4.1: D(i-1,i) / 16, per arrival in order
            r = arrival * clock
            if s["_prev_r"] is not None:
                d = (r - s["_prev_r"]) - (((ts - s["_prev_s"] + 0x80000000) & 0xFFFFFFFF) - 0x80000000)
                s["_j"] += (abs(d) - s["_j"]) / 16
            s["_prev_r"], s["_prev_s"] = r, ts
    out = []
    for ssrc in order:
        s = streams[ssrc]
        expected = s["_max_ext"] - s["_min_ext"] + 1
        out.append({"ssrc": s["ssrc"], "payload_type": s["payload_type"], "packets": s["packets"],
                    "lost": expected - s["packets"], "duplicates": s["duplicates"], "out_of_order": s["out_of_order"],
                    "payload_bytes": s["payload_bytes"], "markers": s["markers"],
                    "duration": str(s["_last"] - s["_first"]),
                    "jitter": float(round(s["_j"], 2)) if s["_clock"] else None})
    return {"link_type": linktype, "nanosecond": unit == 1000000000, "truncated_packets": truncated, "streams": out}
