"""CD-Text (Red Book subcode text): 18-byte packs, CRC, text strings."""
import struct

FIELDS = {0x80: "title", 0x81: "performer", 0x82: "songwriter", 0x83: "composer",
          0x84: "arranger", 0x85: "message"}
DISC_ID = 0x86
GENRE = 0x87
UPC_ISRC = 0x8E
SIZE_INFO = 0x8F

CHARSETS = {0x00: "iso-8859-1", 0x01: "ascii", 0x80: "ms-jis"}

PACK_SIZE = 18
HEADER_SIZE = 4  # some drives prepend a 4-byte length header to the pack dump


class CdTextError(ValueError):
    pass


def crc16(b):
    c = 0
    for x in b:
        c ^= x << 8
        for _ in range(8):
            c = (c << 1) ^ 0x1021 if c & 0x8000 else c << 1
            c &= 0xFFFF
    return c


def crc_ok(pack):
    # The stored CRC is the one's complement of the CRC-16/CCITT over the
    # first 16 bytes of the pack, most significant byte first.
    return (~crc16(pack[:16])) & 0xFFFF == struct.unpack(">H", pack[16:18])[0]


class Strings:
    """Rebuild null-terminated text strings from the 12-byte text fields of
    consecutive packs of one language block.

    A string may span several packs, and one pack may hold the end of one
    string and the start of the next.  A pack's track number belongs to the
    string in progress at the start of the pack; a string that begins in the
    middle of a pack belongs to the following track.
    """

    def __init__(self):
        self.kind = None
        self.track = 0
        self.buf = bytearray()
        self.items = []  # (pack type, track, raw bytes)

    def feed(self, kind, track, text):
        if kind != self.kind:
            self.buf = bytearray()
            self.kind = kind
        if not self.buf:
            self.track = track
        for byte in text:
            if byte:
                self.buf.append(byte)
            elif self.buf:
                self.items.append((self.kind, self.track, bytes(self.buf)))
                self.buf = bytearray()
                self.track += 1

    def flush(self):
        # Tolerate a final string that fills the packs with no null left over.
        if self.buf:
            self.items.append((self.kind, self.track, bytes(self.buf)))
            self.buf = bytearray()


class Block:
    def __init__(self, number):
        self.number = number
        self.double_byte = False
        self.text = Strings()
        self.genre = None
        self.genre_text = Strings()
        self.size_packs = []

    def charset_code(self):
        # First size-information pack, byte 4: character code.
        if self.size_packs:
            return self.size_packs[0][4]
        return None

    def codec(self):
        code = self.charset_code()
        if code == 0x80 or self.double_byte:
            return "cp932"  # MS-JIS / double-byte text
        if code == 0x01:
            return "ascii"
        return "latin-1"


def strip_header(data):
    if len(data) % PACK_SIZE == 0:
        return data
    if len(data) > HEADER_SIZE and (len(data) - HEADER_SIZE) % PACK_SIZE == 0:
        return data[HEADER_SIZE:]
    raise CdTextError("not a whole number of CD-Text packs")


def parse(data):
    if not data:
        raise CdTextError("not a whole number of CD-Text packs")
    data = strip_header(data)
    packs = [data[i:i + PACK_SIZE] for i in range(0, len(data), PACK_SIZE)]
    crc_errors = sum(1 for p in packs if not crc_ok(p))

    blocks = {}
    for p in packs:
        kind = p[0]
        track = p[1] & 0x7F
        number = (p[3] >> 4) & 7
        block = blocks.get(number)
        if block is None:
            block = blocks[number] = Block(number)
        if p[3] & 0x80:
            block.double_byte = True
        if kind in FIELDS or kind in (DISC_ID, UPC_ISRC):
            block.text.feed(kind, track, p[4:16])
        elif kind == GENRE:
            if block.genre is None:
                block.genre = struct.unpack(">H", p[4:6])[0]
            block.genre_text.feed(kind, track, p[6:16])
        elif kind == SIZE_INFO:
            block.size_packs.append(p)

    for block in blocks.values():
        block.text.flush()
        block.genre_text.flush()

    # Language codes for blocks 0-7: third size-information pack, bytes 8-15.
    languages = None
    for number in sorted(blocks):
        size = blocks[number].size_packs
        if len(size) >= 3:
            languages = size[2][8:16]
            break

    out = []
    for number in sorted(blocks):
        block = blocks[number]
        code = block.charset_code()
        charset = None if code is None else CHARSETS.get(code, "type_%d" % code)
        language = None if languages is None else languages[number]
        codec = block.codec()

        def value(raw):
            return raw.decode(codec, "replace") or None

        album = {}
        tracks = {}
        for kind, track, raw in block.text.items:
            if kind == UPC_ISRC:
                if track:
                    tracks.setdefault(track, {})["isrc"] = value(raw)
                else:
                    album["upc"] = value(raw)
            elif kind == DISC_ID:
                album["disc_id"] = value(raw)
            elif track:
                tracks.setdefault(track, {})[FIELDS[kind]] = value(raw)
            else:
                album[FIELDS[kind]] = value(raw)
        if block.genre is not None:
            album["genre"] = block.genre
        for _kind, _track, raw in block.genre_text.items:
            album["genre_text"] = value(raw)
            break

        out.append({"block": number, "charset": charset, "language": language,
                    "double_byte": block.double_byte, "album": album,
                    "tracks": [dict(number=k, **v) for k, v in sorted(tracks.items())]})
    return {"packs": len(packs), "crc_errors": crc_errors, "blocks": out}
