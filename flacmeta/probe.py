"""Catalogue summary of one FLAC file."""
from fractions import Fraction

from . import parse
from .blocks import FlacError, blocks, PADDING, PICTURE, SEEKTABLE, STREAMINFO, VORBIS_COMMENT

# RFC 9639 allows at most one block of each of these types.
_SINGLETON = {STREAMINFO, SEEKTABLE, VORBIS_COMMENT}


def probe(data):
    found, audio_offset = blocks(data)
    if not found or found[0].type != STREAMINFO:
        raise FlacError("STREAMINFO must be the first metadata block")

    seen = set()
    info = None
    vendor = None
    tags = {}
    seekpoints = 0
    pictures = []
    padding = 0

    for block in found:
        if block.type in seen and block.type in _SINGLETON:
            raise FlacError("duplicate %s metadata block" % block.type)
        seen.add(block.type)

        if block.type == STREAMINFO:
            info = parse.streaminfo(block.body)
        elif block.type == VORBIS_COMMENT:
            vendor, tags = parse.vorbis_comment(block.body)
        elif block.type == SEEKTABLE:
            seekpoints = len(parse.seektable(block.body))
        elif block.type == PICTURE:
            pictures.append(parse.picture(block.body))
        elif block.type == PADDING:
            padding += parse.padding(block.body)
        # APPLICATION, CUESHEET and reserved blocks are legal and simply do
        # not contribute to the catalogue summary.

    duration = None
    if info["total_samples"]:
        duration = str(Fraction(info["total_samples"], info["sample_rate"]))

    info.update(
        duration=duration,
        vendor=vendor,
        tags=tags,
        seekpoints=seekpoints,
        pictures=pictures,
        padding=padding,
        audio_offset=audio_offset,
    )
    return info
