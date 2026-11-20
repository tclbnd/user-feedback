"""Catalogue summary of one FLAC file."""
from fractions import Fraction

from . import parse
from .blocks import FlacError, blocks


def probe(data):
    found, audio = blocks(data)
    if not found or found[0].type != 0:
        raise FlacError("STREAMINFO must come first")
    info = parse.streaminfo(found[0].body)
    vendor, tags, points, pictures, padding = None, {}, 0, [], 0
    for b in found[1:]:
        if b.type == 4:
            vendor, tags = parse.vorbis_comment(b.body)
        elif b.type == 3:
            points = len(parse.seektable(b.body))
        elif b.type == 6:
            pictures.append(parse.picture(b.body))
        elif b.type == 1:
            padding += b.length
    info.update(duration=str(Fraction(info["total_samples"], info["sample_rate"])), vendor=vendor, tags=tags,
                seekpoints=points, pictures=pictures, padding=padding, audio_offset=audio)
    return info
