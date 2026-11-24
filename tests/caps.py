"""Builders for small synthetic captures: any link layer, byte order and time resolution."""
import struct


def rtp(seq=1, ts=0, ssrc=0xCAFE0001, pt=0, marker=0, payload=160, csrc=(), ext=None, pad=0):
    """An RTP packet.  ext is (profile, bytes) for a header extension; pad is the padding length."""
    b0 = 0x80 | (0x20 if pad else 0) | (0x10 if ext else 0) | len(csrc)
    body = struct.pack(">BBHII", b0, (marker << 7) | pt, seq, ts, ssrc) + b"".join(csrc)
    if ext:
        profile, data = ext
        body += struct.pack(">HH", profile, len(data) // 4) + data
    body += bytes(payload)
    if pad:
        body += bytes(pad - 1) + bytes([pad])
    return body


def udp(payload, sport=5004, dport=5004):
    return struct.pack(">HHHH", sport, dport, 8 + len(payload), 0) + payload


def ipv4(payload, proto=17, frag=0, ihl=20):
    head = struct.pack(">BBHHHBBH4s4s", 0x40 | ihl // 4, 0, ihl + len(payload), 0, frag, 64, proto, 0,
                       bytes(4), bytes(4))
    return head + bytes(ihl - 20) + payload


def ipv6(payload, nxt=17, exts=()):
    """Extension headers between the IPv6 header and payload: each entry is full header bytes,
    starting with its own next-header field."""
    chain = b"".join(data for _, data in exts)
    plen = len(chain) + len(payload)
    head = struct.pack(">IHBB", 6 << 28, plen, exts[0][0] if exts else nxt, 64)
    return head + bytes(32) + chain + payload


def hbh(next_header, options=b""):
    """Hop-by-hop options header (header number 0)."""
    body = bytes([next_header, 0]) + options
    return 0, body.ljust(8, b"\x00")


def fragment(next_header, offset=0, more=0, ident=0):
    """Fragment header (header number 44); offset is in 8-octet units."""
    return 44, bytes([next_header, 0]) + struct.pack(">HI", (offset << 3) | more, ident)


def ethernet(payload, ethertype=0x0800, vlans=()):
    """Ethernet II; vlans is the stack of tag TPIDs outermost first (e.g. (0x88A8, 0x8100))."""
    chain = struct.pack(">H", ethertype)
    for tag in reversed(vlans):
        chain = struct.pack(">HH", tag, 0x0001) + chain
    return bytes(12) + chain + payload


def sll(payload, ethertype=0x0800):
    """Linux cooked capture v1."""
    return struct.pack(">HHH8sH", 0, 1, 6, bytes(8), ethertype) + payload


def sll2(payload, ethertype=0x0800):
    """Linux cooked capture v2: protocol type comes first."""
    return struct.pack(">HHIHBB8s", ethertype, 0, 1, 1, 0, 6, bytes(8)) + payload


def loopback(payload, family=2):
    """BSD loopback: a host-byte-order address family, then the IP packet."""
    return struct.pack("<I", family) + payload


def pcap(frames, linktype=1, big=False, nano=False, truncate=()):
    """frames is a list of (time, bytes) with time in seconds as a Fraction or float."""
    from fractions import Fraction
    e = ">" if big else "<"
    unit = 1000000000 if nano else 1000000
    magic = 0xA1B23C4D if nano else 0xA1B2C3D4
    out = [struct.pack(e + "IHHiIII", magic, 2, 4, 0, 0, 65535, linktype)]
    for i, (when, frame) in enumerate(frames):
        when = Fraction(when)
        sec = int(when)
        frac = int((when - sec) * unit)
        orig = len(frame)
        incl = 32 if i in truncate else orig
        out.append(struct.pack(e + "IIII", sec, frac, incl, orig) + frame[:incl])
    return b"".join(out)


def one_packet(seq=1, payload=160):
    return pcap([(1, ethernet(ipv4(udp(rtp(seq=seq, payload=payload)))))])

