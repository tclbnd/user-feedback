"""A tiny little-endian microsecond capture with one RTP packet on Ethernet/IPv4/UDP."""
import struct


def one_packet(seq=1, payload=160):
    rtp = struct.pack(">BBHII", 0x80, 0, seq, 0, 0xCAFE0001) + bytes(payload)
    udp = struct.pack(">HHHH", 5004, 5004, 8 + len(rtp), 0) + rtp
    ip = struct.pack(">BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), 0, 0, 64, 17, 0, bytes(4), bytes(4)) + udp
    frame = bytes(12) + b"\x08\x00" + ip
    head = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)
    return head + struct.pack("<IIII", 1, 0, len(frame), len(frame)) + frame
