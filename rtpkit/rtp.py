"""RTP streams in pcap captures: link layers, IPv4/IPv6, UDP, RTP headers, loss, reordering, jitter (RFC 3550)."""
import struct
from fractions import Fraction

# RFC 3551 static payload types: clock rate in Hz.  G.722 (PT 9) uses the
# 8000 Hz RTP timestamp clock, even though its audio sampling rate is 16 kHz.
CLOCKS = {0: 8000, 3: 8000, 4: 8000, 5: 8000, 6: 16000, 7: 8000, 8: 8000, 9: 8000, 10: 44100, 11: 44100, 12: 8000,
          13: 8000, 14: 90000, 15: 8000, 16: 11025, 17: 22050, 18: 8000, 25: 90000, 26: 90000, 28: 90000, 31: 90000,
          32: 90000, 33: 90000, 34: 90000}

# pcap magic -> (struct byte order, timestamp denominator)
MAGICS = {
    b"\xd4\xc3\xb2\xa1": ("<", 1000000),
    b"\xa1\xb2\xc3\xd4": (">", 1000000),
    b"\x4d\x3c\xb2\xa1": ("<", 1000000000),
    b"\xa1\xb2\x3c\x4d": (">", 1000000000),
}

ETH_P_IPV4 = 0x0800
ETH_P_IPV6 = 0x86DD
VLAN_TYPES = {0x8100, 0x88A8, 0x9100, 0x9200}

# IPv6 extension headers whose length uses the RFC 2460 Hdr Ext Len field.
IPV6_EXT_LENGTHS = {0, 43, 44, 60, 135}
SEQ_MOD = 1 << 16
TS_MOD = 1 << 32


class RtpError(ValueError):
    pass


def ip_payload(pkt, p):
    """UDP payload of an IPv4 / IPv6 packet starting at p, or None."""
    if p >= len(pkt):
        return None
    ver = pkt[p] >> 4
    if ver == 4:
        if p + 20 > len(pkt):
            return None
        ihl = (pkt[p] & 0x0F) * 4
        if ihl < 20 or p + ihl + 8 > len(pkt):
            return None
        proto = pkt[p + 9]
        frag = struct.unpack_from(">H", pkt, p + 6)[0]
        if proto != 17 or frag & 0x1FFF:                   # not UDP, or a later fragment
            return None
        p += ihl
    elif ver == 6:
        if p + 40 > len(pkt):
            return None
        next_header = pkt[p + 6]
        p += 40
        while next_header != 17:
            if p + 2 > len(pkt):
                return None
            if next_header == 44:                          # IPv6 fragmentation header
                if p + 8 > len(pkt):
                    return None
                fragment = struct.unpack_from(">H", pkt, p + 2)[0]
                if fragment & 0xFFF8:                      # later fragment has no UDP header
                    return None
            if next_header in IPV6_EXT_LENGTHS:
                length = (pkt[p + 1] + 1) * 8
            elif next_header == 51:                       # Authentication Header
                length = (struct.unpack_from(">B", pkt, p + 1)[0] + 2) * 4
            else:
                return None
            next_header = pkt[p]
            p += length
            if p > len(pkt):
                return None
        if p + 8 > len(pkt):
            return None
    else:
        return None

    if p + 8 > len(pkt):
        return None
    length = struct.unpack_from(">H", pkt, p + 4)[0]
    if length < 8:
        return None
    return pkt[p + 8:p + length]


def _ethertype_payload(pkt, ethertype_offset):
    """Skip zero or more VLAN tags and return the IP/UDP payload."""
    if ethertype_offset + 2 > len(pkt):
        return None
    ethertype = struct.unpack_from(">H", pkt, ethertype_offset)[0]
    while ethertype in VLAN_TYPES:
        ethertype_offset += 4
        if ethertype_offset + 2 > len(pkt):
            return None
        ethertype = struct.unpack_from(">H", pkt, ethertype_offset)[0]
    if ethertype not in (ETH_P_IPV4, ETH_P_IPV6):
        return None
    return ip_payload(pkt, ethertype_offset + 2)


def link_payload(pkt, linktype, e):
    if linktype == 1:                                      # Ethernet, with any number of VLAN tags
        return _ethertype_payload(pkt, 12)
    if linktype == 113:                                    # Linux cooked capture v1
        return _ethertype_payload(pkt, 14)
    if linktype == 276:                                    # Linux cooked capture v2
        if len(pkt) < 20:
            return None
        ethertype = struct.unpack_from(">H", pkt, 0)[0]
        if ethertype not in (ETH_P_IPV4, ETH_P_IPV6):
            return None
        return ip_payload(pkt, 20)
    if linktype in (101, 12, 228, 229):                   # raw IPv4/IPv6
        return ip_payload(pkt, 0)
    if linktype in (0, 108):                               # BSD/OpenBSD loopback, 4-byte AF header
        return ip_payload(pkt, 4)
    raise RtpError("unsupported link type %d" % linktype)


def is_rtcp(udp):
    """Return True for well-known RTCP packet types (RFC 3550 and extensions)."""
    return len(udp) >= 2 and udp[0] >> 6 == 2 and 200 <= udp[1] <= 207


def extended_value(value, previous, modulus):
    """Extend a wrapping integer to the cycle closest to previous."""
    cycles = (previous + modulus // 2 - value) // modulus
    return value + cycles * modulus


def rtp_payload_length(udp):
    """RTP payload length after CSRC list, header extension and padding."""
    cc = udp[0] & 0x0F
    padding = bool(udp[0] & 0x20)
    extension = bool(udp[0] & 0x10)
    header_length = 12 + 4 * cc
    if extension:
        if header_length + 4 > len(udp):
            raise RtpError("truncated RTP header extension")
        extension_words = struct.unpack_from(">H", udp, header_length + 2)[0]
        header_length += 4 + 4 * extension_words
    padding_length = udp[-1] if padding and udp else 0
    if not 0 <= header_length <= len(udp):
        raise RtpError("truncated RTP header")
    if padding_length and not 1 <= padding_length <= len(udp) - header_length:
        raise RtpError("invalid RTP padding length")
    return max(0, len(udp) - header_length - padding_length)


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
        if not udp or is_rtcp(udp):
            continue
        if len(udp) < 12 or udp[0] >> 6 != 2:
            continue                                       # not RTP
        pt, marker = udp[1] & 0x7F, udp[1] >> 7
        seq, ts, ssrc = struct.unpack_from(">HII", udp, 2)
        payload_length = rtp_payload_length(udp)
        s = streams.get(ssrc)
        if s is None:
            s = streams[ssrc] = {"ssrc": "%08x" % ssrc, "payload_type": pt, "packets": 0,
                                 "payload_bytes": 0, "markers": 0,
                                 "_max": seq, "_ts_max": ts, "_seen": set(),
                                 "duplicates": 0, "out_of_order": 0,
                                 "_first": arrival, "_last": arrival,
                                 "_j": Fraction(0), "_prev": None,
                                 "_clock": CLOCKS.get(pt)}
            order.append(ssrc)
        # Extended sequence number: the 32-bit value closest to the highest one seen so far.
        ext = extended_value(seq, s["_max"], SEQ_MOD)
        if ext < s["_max"]:
            s["out_of_order"] += 1
        if ext in s["_seen"]:
            s["duplicates"] += 1
            continue
        s["_seen"].add(ext)
        s["packets"] += 1
        s["payload_bytes"] += payload_length
        s["markers"] += marker

        clock = s["_clock"]
        ext_ts = extended_value(ts, s["_ts_max"], TS_MOD)
        if clock is not None and s["_prev"] is not None:
            prev_arrival, prev_ts = s["_prev"]
            difference = abs((arrival - prev_arrival) * clock - (ext_ts - prev_ts))
            s["_j"] += (difference - s["_j"]) / 16
        if clock is not None:
            s["_prev"] = (arrival, ext_ts)
        s["_ts_max"] = max(s["_ts_max"], ext_ts)

        s["_last"] = arrival
        s["_max"] = max(s["_max"], ext)
    out = []
    for ssrc in order:
        s = streams[ssrc]
        exts = sorted(s["_seen"]) or [0]
        expected = exts[-1] - exts[0] + 1
        jitter = round(float(s["_j"]), 2) if s["_clock"] is not None else None
        out.append({"ssrc": s["ssrc"], "payload_type": s["payload_type"], "packets": s["packets"],
                    "lost": expected - s["packets"], "duplicates": s["duplicates"],
                    "out_of_order": s["out_of_order"],
                    "payload_bytes": s["payload_bytes"], "markers": s["markers"],
                    "duration": str(s["_last"] - s["_first"]),
                    "jitter": jitter})
    return {"link_type": linktype, "nanosecond": unit == 1000000000,
            "truncated_packets": truncated, "streams": out}
