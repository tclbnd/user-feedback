"""Ogg pages and packets (RFC 3533).

A physical Ogg stream is a sequence of pages.  Each page belongs to one
logical bitstream (identified by its serial number) and carries a lacing
table that splits the page body into packet fragments.  A lacing value of
255 means the packet continues in the next segment (possibly on the next
page); a value below 255 terminates the packet.

Pages may be multiplexed (several serials interleaved) and complete
bitstreams may be chained (a new BOS page starts another one).  Checksums
cover the whole page with the checksum field itself zeroed.
"""
from collections import namedtuple

Page = namedtuple("Page", "offset flags granule serial seq crc segments body")
Stream = namedtuple("Stream", "serial pages")
ScanStats = namedtuple("ScanStats", "crc_errors sync_errors truncated")

CONTINUED = 0x01
BOS = 0x02
EOS = 0x04

#: Granule position used by Ogg to say "no packet finishes on this page".
NO_GRANULE = 0xFFFFFFFFFFFFFFFF


class OggError(ValueError):
    pass


def _crc_table():
    """Ogg's CRC-32 table (polynomial 0x04C11DB7, no reflection)."""
    poly = 0x04C11DB7
    table = []
    for value in range(256):
        reg = value << 24
        for _ in range(8):
            if reg & 0x80000000:
                reg = ((reg << 1) ^ poly) & 0xFFFFFFFF
            else:
                reg = (reg << 1) & 0xFFFFFFFF
        table.append(reg)
    return tuple(table)


_CRC_TABLE = _crc_table()


def crc32(data):
    """Ogg CRC-32 over *data* (initial value 0, no final xor)."""
    crc = 0
    for byte in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _CRC_TABLE[((crc >> 24) ^ byte) & 0xFF]
    return crc


# Alias used by callers that just want the page checksum primitive.
crc = crc32


def page_crc(page_bytes):
    """Checksum of a complete page, with its stored CRC field zeroed."""
    if len(page_bytes) < 27:
        raise ValueError("truncated Ogg page")
    return crc32(page_bytes[:22] + b"\x00\x00\x00\x00" + page_bytes[26:])


def granule_position(page):
    """The page granule, or None when the page marks it as unknown."""
    value = page.granule
    if value == NO_GRANULE:
        return None
    # Granule positions are signed 64-bit values; other negative values are
    # not meaningful for a normal stream.
    if value >= 1 << 63:
        return None
    return value


def scan(data):
    """Parse *data* into ``(pages, stats)``.

    Parsing is resilient: leading junk and a damaged page are skipped when a
    following ``OggS`` capture pattern can be found.  Pages whose checksum
    does not match are still returned (so duration can be recovered) but are
    counted in ``stats.crc_errors``.  A page that runs past the end of the
    buffer sets ``stats.truncated``; trailing bytes with no further page are
    ignored.
    """
    page_list = []
    crc_errors = 0
    sync_errors = 0
    truncated = False
    pos = 0
    size = len(data)

    def resync(from_pos):
        """Next plausible page start after *from_pos*, or -1."""
        nxt = data.find(b"OggS", from_pos)
        while nxt >= 0:
            if nxt + 5 > size or data[nxt + 4] == 0:
                return nxt
            nxt = data.find(b"OggS", nxt + 1)
        return -1

    while pos < size:
        if data[pos:pos + 4] != b"OggS" or (pos + 5 <= size and data[pos + 4] != 0):
            nxt = resync(pos + 1)
            if nxt < 0:
                break
            sync_errors += 1
            pos = nxt
            continue

        if pos + 27 > size:
            truncated = True
            break

        flags = data[pos + 5]
        granule = int.from_bytes(data[pos + 6:pos + 14], "little")
        serial = int.from_bytes(data[pos + 14:pos + 18], "little")
        seq = int.from_bytes(data[pos + 18:pos + 22], "little")
        stored_crc = int.from_bytes(data[pos + 22:pos + 26], "little")
        nseg = data[pos + 26]
        header_end = pos + 27 + nseg

        if header_end > size:
            truncated = True
            nxt = resync(pos + 1)
            if nxt < 0:
                break
            sync_errors += 1
            pos = nxt
            continue

        lacing = list(data[pos + 27:header_end])
        body_end = header_end + sum(lacing)
        if body_end > size:
            truncated = True
            nxt = resync(pos + 1)
            if nxt < 0:
                break
            sync_errors += 1
            pos = nxt
            continue

        if page_crc(data[pos:body_end]) != stored_crc:
            crc_errors += 1

        page_list.append(
            Page(pos, flags, granule, serial, seq, stored_crc,
                 lacing, data[header_end:body_end])
        )
        pos = body_end

    return page_list, ScanStats(crc_errors, sync_errors, truncated)


def pages(data):
    """Yield every structurally valid page in *data*."""
    page_list, _stats = scan(data)
    for page in page_list:
        yield page


def logical_streams(page_list):
    """Group pages by logical bitstream, in order of appearance."""
    streams = []
    for group in chains(page_list):
        streams.extend(group)
    return streams


def chains(page_list):
    """Group logical streams into chained groups.

    RFC 3533 has every logical stream start with a BOS page, and all BOS
    pages of a multiplexed group precede any data page of that group.  A BOS
    page therefore starts a new chain whenever pages of the current group
    have already appeared.  Each group is a list of :class:`Stream` objects
    in order of appearance.
    """
    groups = []
    current = {}
    order = []
    seen_data = False

    def flush():
        nonlocal current, order, seen_data
        if order:
            groups.append(order)
        current = {}
        order = []
        seen_data = False

    for page in page_list:
        if page.flags & BOS:
            if seen_data:
                flush()
            stream = current.get(page.serial)
            if stream is None:
                stream = Stream(page.serial, [])
                current[page.serial] = stream
                order.append(stream)
        else:
            seen_data = True
            stream = current.get(page.serial)
            if stream is None:
                # A page without a preceding BOS page (damaged or truncated
                # file): treat it as an implicitly started stream.
                stream = Stream(page.serial, [])
                current[page.serial] = stream
                order.append(stream)
        stream.pages.append(page)

    if order:
        groups.append(order)
    return groups


def packets(page_list, stats=None):
    """Yield complete packets as ``(bytes, first_page)`` pairs.

    Lacing values of 255 are continuations, not packet boundaries, and a
    packet may span any number of pages.  ``stats`` (a dict) is updated
    with ``missing_continuation`` and ``unterminated`` counters when the
    stream is damaged.
    """
    partial = {}

    def bump(name):
        if stats is not None:
            stats[name] = stats.get(name, 0) + 1

    for page in page_list:
        continued = bool(page.flags & CONTINUED)
        entry = partial.pop(page.serial, None)

        if continued:
            if entry is None:
                # Continuation with no packet start: discard until the next
                # packet boundary instead of emitting a truncated packet.
                bump("missing_continuation")
                buf = None
                first = page
            else:
                buf, first = entry
        else:
            # A new packet starts; anything still open was never finished.
            if entry is not None:
                bump("unterminated")
            buf = bytearray()
            first = page

        if not page.segments:
            if continued and buf is not None:
                partial[page.serial] = (buf, first)
            continue

        at = 0
        for size in page.segments:
            chunk = page.body[at:at + size]
            at += size
            if buf is not None:
                buf += chunk
            if size < 255:
                if buf is not None:
                    yield bytes(buf), first
                buf = bytearray()
                first = page

        if page.segments[-1] == 255 and buf is not None:
            partial[page.serial] = (buf, first)

    for buf, _first in partial.values():
        if buf:
            bump("unterminated")


def sequence_gaps(page_list):
    """Count pages of one logical stream that skip a sequence number.

    RFC 3533 numbers every page of a logical bitstream consecutively; a gap
    means pages (and therefore packets) are missing.  Call this per logical
    stream: a chained file legitimately restarts numbering at 0.
    """
    gaps = 0
    previous = None
    for page in page_list:
        if previous is not None and page.seq != (previous + 1) & 0xFFFFFFFF:
            gaps += 1
        previous = page.seq
    return gaps
