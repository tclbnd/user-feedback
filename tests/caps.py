"""Tiny synthetic captures used by the tests.

The helpers build frames the same way capture tools write them, so the parser
can be exercised against VLAN tags, CSRC lists, header extension, padding,
both byte orders, both time stamp resolutions and every supported link layer.
"""
import struct


def rtp_packet(seq=1, ts=0, ssrc=0xCAFE0001, pt=0, marker=0, payload=b"", csrc=(), ext=b"", pad=0):
    """One RTP packet, RFC 3550 header plus the given payload decorations."""
    cc = len(csrc)
    flags = 0x80 | cc | (0x10 if ext else 0) | (0x20 if pad else 0) | (0x80 if marker else 0)
    body = struct.pack(">BBHII", flags, pt, seq, ts, ssrc) + b"".join(struct.pack(">I", c) for c in csrc)
    if ext:
        body += struct.pack(">HH", 0xBEDE, len(ext) // 4) + ext
    body += payload
    if pad:
        body += bytes(pad - 1) + bytes([pad])
    return body


def udp_packet(rtp, sport=5004, dport=5004):
    return struct.pack(">HHHH", sport, dport, 8 + len(rtp), 0) + rtp


def ipv4_packet(udp, proto=17):
    return struct.pack(">BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), 0, 0, 64, proto, 0, bytes(4), bytes(4)) + udp


def ipv6_packet(udp):
    return struct.pack(">IHBB16s16s", 6 << 28, len(udp), 17, 64, bytes(16), bytes(16)) + udp


def ethernet(frame, ethertype=0x0800, vlans=()):
    head = bytes(12)
    for tci in vlans:
        head += struct.pack(">HH", 0x8100, tci)
    return head + struct.pack(">H", ethertype) + frame


def pcap(frames, order="<", nanosecond=False, linktype=1, starts=1700000000, step=1000):
    """A libpcap file holding the given frames; step is in the chosen units."""
    magic = 0xA1B23C4D if nanosecond else 0xA1B2C3D4
    out = struct.pack(order + "IHHiIII", magic, 2, 4, 0, 0, 65535, linktype)
    for i, frame in enumerate(frames):
        out += struct.pack(order + "IIII", starts, i * step, len(frame), len(frame)) + frame
    return out


def one_packet(seq=1, payload=160):
    rtp = struct.pack(">BBHII", 0x80, 0, seq, 0, 0xCAFE0001) + bytes(payload)
    udp = struct.pack(">HHHH", 5004, 5004, 8 + len(rtp), 0) + rtp
    ip = struct.pack(">BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), 0, 0, 64, 17, 0, bytes(4), bytes(4)) + udp
    frame = bytes(12) + b"\x08\x00" + ip
    head = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)
    return head + struct.pack("<IIII", 1, 0, len(frame), len(frame)) + frame
