"""Ogg pages and packets."""
from collections import namedtuple

Page = namedtuple("Page", "offset flags granule serial seq crc segments body")


class OggError(ValueError):
    pass


def pages(data):
    pos = 0
    while pos + 27 <= len(data):
        if data[pos:pos + 4] != b"OggS":
            raise OggError(f"lost page sync at {pos}")
        flags = data[pos + 5]
        granule = int.from_bytes(data[pos + 6:pos + 14], "little")
        serial = int.from_bytes(data[pos + 14:pos + 18], "little")
        seq = int.from_bytes(data[pos + 18:pos + 22], "little")
        crc = int.from_bytes(data[pos + 22:pos + 26], "little")
        nseg = data[pos + 26]
        lacing = list(data[pos + 27:pos + 27 + nseg])
        start = pos + 27 + nseg
        body = data[start:start + sum(lacing)]
        yield Page(pos, flags, granule, serial, seq, crc, lacing, body)
        pos = start + sum(lacing)


def packets(page_list):
    """Packets in order, as (bytes, page) pairs."""
    for page in page_list:
        at = 0
        for size in page.segments:
            yield page.body[at:at + size], page
            at += size
