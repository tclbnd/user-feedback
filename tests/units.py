"""Small TrueHD / MLP / AC-3 streams for the tests."""
import struct

SYNC_THD, SYNC_MLP = 0xF8726FBA, 0xF8726FBB


def sync(info=0x0001E000, word=SYNC_THD):
    return struct.pack(">II", word, info) + struct.pack(">HHHH", 0xB752, 0, 0, 1000) + bytes([0x20]) + bytes(11)


def unit(words, with_sync=False, **sync_kw):
    body = sync(**sync_kw) if with_sync else b""
    body += bytes(words * 2 - 4 - len(body))
    return struct.pack(">HH", 0xA000 | words, 0) + body


def ac3(words=384, sizecod=20, fscod=0):
    """An AC-3 core frame; defaults to 192 kbit/s at 48 kHz (768 bytes)."""
    return struct.pack(">HHB", 0x0B77, 0, (fscod << 6) | sizecod) + bytes(words * 2 - 5)
