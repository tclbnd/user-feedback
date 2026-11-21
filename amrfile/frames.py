"""AMR and AMR-WB storage format frames.

Frame types, speech payload sizes and bit rates follow 3GPP TS 26.101
(AMR-NB), TS 26.201 (AMR-WB) and RFC 4867: the RTP payload formats and the
file storage format of section 6.
"""

NB_MAGIC = b"#!AMR\n"
WB_MAGIC = b"#!AMR-WB\n"

# Speech bits per frame, ToC header octet excluded (RFC 4867 table 1).
# Frame types 9-13 are reserved, except that AMR-WB uses 9 for its SID frame.
# Type 15 is "no data" in both codecs and type 14 is AMR-WB's "speech lost"
# (AMR-NB leaves 14 reserved, but some encoders still use it for a lost
# frame).  Neither carries speech, so both are header-only one-octet blocks.
NB_BITS = [95, 103, 118, 134, 148, 159, 204, 244, 39,
           None, None, None, None, None, 0, 0]
WB_BITS = [132, 177, 253, 285, 317, 365, 397, 461, 477, 40,
           None, None, None, None, 0, 0]

# Speech modes, keyed by bit rate in kbit/s.
NB_KBPS = ["4.75", "5.15", "5.9", "6.7", "7.4", "7.95", "10.2", "12.2"]
WB_KBPS = ["6.6", "8.85", "12.65", "14.25", "15.85", "18.25", "19.85",
           "23.05", "23.85"]

SPEECH = "speech"
SID = "sid"
NO_DATA = "no_data"
BAD = "bad"

FRAME_TYPE_SPEECH_LOST = 14
FRAME_TYPE_NO_DATA = 15

CODECS = {
    "amr-nb": {"magic": NB_MAGIC, "sample_rate": 8000, "bits": NB_BITS,
               "kbps": NB_KBPS, "sid_ft": 8},
    "amr-wb": {"magic": WB_MAGIC, "sample_rate": 16000, "bits": WB_BITS,
               "kbps": WB_KBPS, "sid_ft": 9},
}


class AmrError(ValueError):
    pass


def frame_bits(ft, codec="amr-nb"):
    """Number of speech bits carried by frame type `ft`."""
    if codec not in CODECS:
        raise AmrError(f"unknown codec {codec}")
    bits = CODECS[codec]["bits"][ft]
    if bits is None:
        raise AmrError(f"unexpected frame type {ft}")
    return bits


def frame_size(ft, codec="amr-nb"):
    """Storage format frame size in bytes, header octet included."""
    return 1 + (frame_bits(ft, codec) + 7) // 8


NB_SIZE = [frame_size(ft) for ft in range(9)]
WB_SIZE = [frame_size(ft, "amr-wb") for ft in range(10)]


def classify(ft, quality, codec="amr-nb"):
    """Sort one frame block into speech, SID, no data or damaged.

    RFC 4867 uses a Q bit of 0 to mark a badly damaged block, whatever its
    frame type, and TS 26.201's speech-lost type carries no speech at all;
    both count as damaged rather than as speech or comfort noise.  Otherwise
    the frame type says whether the block is speech, comfort noise or no data.
    """
    if ft == FRAME_TYPE_SPEECH_LOST:
        return BAD
    if not quality:
        return BAD
    if ft == FRAME_TYPE_NO_DATA:
        return NO_DATA
    if ft == CODECS[codec]["sid_ft"]:
        return SID
    if ft < len(CODECS[codec]["kbps"]):
        return SPEECH
    return BAD


def read(data):
    """Parse a storage format file into (stream info, frame blocks)."""
    codec = None
    for name in ("amr-wb", "amr-nb"):
        if data.startswith(CODECS[name]["magic"]):
            codec = name
            break
    if codec is None:
        raise AmrError("not an AMR file")

    info = CODECS[codec]
    pos = len(info["magic"])
    frames = []
    while pos < len(data):
        header = data[pos]
        ft = (header >> 3) & 0x0F
        size = frame_size(ft, codec)
        if pos + size > len(data):
            break  # an incomplete frame at the end of the file is not counted
        quality = bool(header & 4)
        frames.append({"ft": ft, "quality": quality, "size": size,
                       "kind": classify(ft, quality, codec)})
        pos += size
    return {"codec": codec, "sample_rate": info["sample_rate"],
            "channels": 1}, frames
