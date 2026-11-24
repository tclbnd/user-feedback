"""Commodore 64 SID tunes (PSID / RSID, versions 1-4): header, addresses, speed flags, chips."""
import struct

CLOCKS = ("unknown", "PAL", "NTSC", "PAL and NTSC")
MODELS = ("unknown", "6581", "8580", "6581 and 8580")


class SidError(ValueError):
    pass


def text(raw):
    return raw.split(b"\0")[0].decode("latin-1") or None


def extra_sid(byte):
    """Second / third SID address byte: $Dxx0 for even values 0x42-0x7F and 0xE0-0xFE, else no chip."""
    if byte % 2 == 0 and (0x42 <= byte <= 0x7F or 0xE0 <= byte <= 0xFE):
        return "$%04X" % (0xD000 | byte << 4)
    return None


def parse(data):
    if len(data) < 0x76 or data[:4] not in (b"PSID", b"RSID"):
        raise SidError("not a SID tune")
    kind = data[:4].decode()
    # All header fields are big-endian.
    version, offset, load, init, play, songs, start, speed = struct.unpack_from(">HHHHHHHI", data, 4)
    payload = data[offset:]
    # A zero load address means the first two data bytes hold the real
    # big-endian load address and are not part of the C64 image.
    if load == 0:
        if len(payload) < 2:
            raise SidError("missing load address in tune data")
        load = struct.unpack_from(">H", payload, 0)[0]
        payload = payload[2:]
    # Init address 0 means "same as the load address".
    if init == 0:
        init = load
    out = {"format": kind, "version": version, "name": text(data[0x16:0x36]), "author": text(data[0x36:0x56]),
           "released": text(data[0x56:0x76]), "load_address": "$%04X" % load,
           "end_address": "$%04X" % (load + len(payload) - 1) if payload else None, "data_bytes": len(payload),
           "init_address": "$%04X" % init, "play_address": "$%04X" % play if play else None,
           "songs": songs, "start_song": start,
           "speeds": ["cia" if speed >> min(i, 31) & 1 else "vbi" for i in range(min(songs, 256))],
           "clock": None, "sid_model": None, "chips": ["$D400"], "player": None, "free_pages": None}
    if version >= 2:
        flags, = struct.unpack_from(">H", data, 0x76)
        out["clock"] = CLOCKS[flags >> 4 & 3]
        out["sid_model"] = [MODELS[flags >> 2 & 3]]
        if flags & 1:
            out["player"] = "compute_mus"
        elif flags & 2:
            out["player"] = "c64_basic" if kind == "RSID" else "playsid"
        if version >= 3:
            second = extra_sid(data[0x7A])
            if second:
                out["chips"].append(second)
                out["sid_model"].append(MODELS[flags >> 10 & 3])
                if version >= 4:
                    third = extra_sid(data[0x7B])
                    if third:
                        out["chips"].append(third)
                        out["sid_model"].append(MODELS[flags >> 12 & 3])
        page, length = data[0x78], data[0x79]
        if page == 0xFF:
            out["free_pages"] = []
        elif page:
            out["free_pages"] = ["$%02X00" % page, "$%02XFF" % (page + length - 1)] if length else []
    return out
