import struct
import zlib

MAGIC = b"ZQFR"
HEADER_SIZE = 20

_HDR_FIELDS = struct.Struct("<4sIII")
_HDR_CRC = struct.Struct("<I")


def header_crc(header16):
    return zlib.crc32(header16) & 0xFFFFFFFF


def payload_crc(payload):
    return zlib.crc32(payload) & 0xFFFFFFFF


def encode_frame(seq, payload):
    head = _HDR_FIELDS.pack(MAGIC, seq & 0xFFFFFFFF, len(payload), payload_crc(payload))
    return head + _HDR_CRC.pack(header_crc(head)) + bytes(payload)


def unpack_header(buf):
    magic, seq, plen, pcrc = _HDR_FIELDS.unpack(buf[:16])
    (hcrc,) = _HDR_CRC.unpack(buf[16:20])
    return magic, seq, plen, pcrc, hcrc


def header_valid(buf):
    if len(buf) < HEADER_SIZE or buf[:4] != MAGIC:
        return False
    return header_crc(buf[:16]) == _HDR_CRC.unpack(buf[16:20])[0]
