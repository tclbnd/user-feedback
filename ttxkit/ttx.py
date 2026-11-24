"""Teletext (ETSI EN 300 706) captured as T42 packets: addresses, page headers, control bits, parity."""

PACKET = 42

# Status values returned by the Hamming 8/4 decoder.
_OK = 0          # the received byte was a valid codeword
_CORRECTED = 1   # a single-bit error was corrected
_BAD = 2         # uncorrectable error (two or more bits wrong)


class TtxError(ValueError):
    pass


def _hamming_codewords():
    """Hamming 8/4 (minimum distance 4) conventions seen on tape captures.

    ``etsi`` is the codebook of ETSI EN 300 706 clause 7.2.4: the four data
    bits d1..d8 occupy bit positions 0, 2, 4, 6, the protection bits P1..P4
    positions 1, 3, 5, 7, and every codeword has even overall parity.

    Some capture cards store the same 4-bit nibbles with the check bits
    arranged differently, producing the odd-overall-parity codebook in
    ``alternate`` (recovered from real off-air T42 files).  Both are valid
    single-error-correcting codes with minimum distance 4, so the convention
    used by a file is detected by counting which codebook produces
    error-free bytes.
    """
    etsi = {
        0x00: 0x0, 0x2B: 0x1, 0x8E: 0x2, 0xA5: 0x3,
        0xB2: 0x4, 0x99: 0x5, 0x3C: 0x6, 0x17: 0x7,
        0xE8: 0x8, 0xC3: 0x9, 0x66: 0xA, 0x4D: 0xB,
        0x5A: 0xC, 0x71: 0xD, 0xD4: 0xE, 0xFF: 0xF,
    }
    alternate = {
        0x15: 0x0, 0x02: 0x1, 0x49: 0x2, 0x5E: 0x3,
        0x64: 0x4, 0x73: 0x5, 0x38: 0x6, 0x2F: 0x7,
        0xD0: 0x8, 0xC7: 0x9, 0x8C: 0xA, 0x9B: 0xB,
        0xB6: 0xC, 0xA1: 0xD, 0xEA: 0xE, 0xFD: 0xF,
    }
    return etsi, alternate


def _build_decoder(codewords):
    """Return a 256-entry nearest-codeword decode table for one codebook.

    Each entry is ``(nibble, status)``: an exact match, a unique one-bit
    neighbour (a corrected error), or ``(None, _BAD)`` when no codeword is
    within one bit, i.e. the byte cannot be decoded.
    """
    table = {}
    for byte in range(256):
        if byte in codewords:
            table[byte] = (codewords[byte], _OK)
            continue
        best_distance = 9
        candidates = []
        for codeword, nibble in codewords.items():
            distance = bin(byte ^ codeword).count("1")
            if distance < best_distance:
                best_distance = distance
                candidates = [nibble]
            elif distance == best_distance:
                candidates.append(nibble)
        if best_distance == 1 and len(candidates) == 1:
            table[byte] = (candidates[0], _CORRECTED)
        else:
            table[byte] = (None, _BAD)
    return table


_ETSI_WORDS, _ALTERNATE_WORDS = _hamming_codewords()
_ETSI_DECODE = _build_decoder(_ETSI_WORDS)
_ALTERNATE_DECODE = _build_decoder(_ALTERNATE_WORDS)
_DECODERS = (_ETSI_DECODE, _ALTERNATE_DECODE)


def _select_decoder(data):
    """Pick the Hamming convention producing the most error-free bytes.

    The two conventions are equally valid distance-4 codes; in a clean
    capture every Hamming byte matches exactly one of them exactly, so the
    score separates the conventions decisively.  A tie (e.g. an empty file)
    keeps the standard ETSI EN 300 706 convention.
    """
    best = _ETSI_DECODE
    best_score = -1
    for decoder in _DECODERS:
        score = 0
        for k in range(0, len(data) - PACKET + 1, PACKET):
            packet = data[k:k + PACKET]
            for byte in packet[0:10]:
                if decoder[byte][1] == _OK:
                    score += 1
        if score > best_score:
            best_score = score
            best = decoder
    return best


def _text_char(byte):
    """Decode one text byte, which carries odd parity in bit 7.

    A byte failing the odd-parity check is unreadable and shown as a space,
    per the output convention.  Otherwise the parity bit is stripped and the
    remaining 7-bit character is used, with control codes also rendered as
    spaces.
    """
    if bin(byte).count("1") % 2 == 0:
        return " ", True
    value = byte & 0x7F
    if value < 0x20:
        return " ", False
    return chr(value), False


def parse(data):
    if len(data) % PACKET:
        raise TtxError("file size is not a whole number of 42-byte packets")
    stats = {"corrected": 0, "hamming_errors": 0, "parity_errors": 0}
    out = {"packets": len(data) // PACKET, "pages": [], "display_rows": 0, "extension_packets": 0,
           "header_text": None, "magazines": []}
    decoder = _select_decoder(data)
    magazines = set()
    for k in range(len(data) // PACKET):
        packet = data[k * PACKET:(k + 1) * PACKET]
        address_hi, status_hi = decoder[packet[0]]
        address_lo, status_lo = decoder[packet[1]]
        if address_hi is None or address_lo is None:
            stats["hamming_errors"] += (address_hi is None) + (address_lo is None)
            continue
        stats["corrected"] += (status_hi == _CORRECTED) + (status_lo == _CORRECTED)
        magazine = address_hi & 7
        if magazine == 0:
            magazine = 8
        row = ((address_hi >> 3) << 4) | address_lo
        magazines.add(magazine)
        if row == 0:
            header = []
            uncorrectable = False
            for byte in packet[2:10]:
                nibble, status = decoder[byte]
                if nibble is None:
                    stats["hamming_errors"] += 1
                    uncorrectable = True
                else:
                    stats["corrected"] += status == _CORRECTED
                    header.append(nibble)
            if uncorrectable:
                continue
            units, tens, s1, s2, s3, s4, c7_10, c11_14 = header
            chars = []
            for byte in packet[10:42]:
                char, parity_error = _text_char(byte)
                stats["parity_errors"] += parity_error
                chars.append(char)
            out["pages"].append({
                "page": "%d%X%X" % (magazine, tens, units),
                "subcode": "%X%X%X%X" % (s4 & 3, s3, s2 & 7, s1),
                "erase": bool(s2 & 8),
                # ETSI EN 300 706: C5 is the newsflash indicator, C4 the subtitle indicator.
                "newsflash": bool(s4 & 4),
                "subtitle": bool(s4 & 8),
                "suppress_header": bool(c7_10 & 1),
                "magazine_serial": bool(c11_14 & 1)})
            if out["header_text"] is None and not (tens == 0xF and units == 0xF):
                out["header_text"] = "".join(chars).rstrip()
        else:
            if row <= 24:
                out["display_rows"] += 1
            else:
                out["extension_packets"] += 1
            for byte in packet[2:42]:
                _, parity_error = _text_char(byte)
                stats["parity_errors"] += parity_error
    out.update(stats)
    out["magazines"] = sorted(magazines)
    return out
