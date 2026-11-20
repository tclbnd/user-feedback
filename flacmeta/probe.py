"""Catalogue summary of one FLAC file."""
from fractions import Fraction

from . import parse
from .blocks import (APPLICATION, CUESHEET, PADDING, PICTURE, SEEKTABLE,
                     STREAMINFO, VORBIS_COMMENT, FlacError, blocks)


def probe(data):
    found, audio = blocks(data)
    if not found or found[0].type != STREAMINFO:
        raise FlacError("STREAMINFO must be the first metadata block")

    seen = set()
    for block in found:
        if block.type in seen and block.type in (STREAMINFO, VORBIS_COMMENT):
            raise FlacError("duplicate %s block" % block.type)
        seen.add(block.type)

    info = parse.streaminfo(found[0].body)
    vendor, tags, points, pictures, padding, applications, cuesheets = (
        None, {}, 0, [], 0, 0, 0)
    for block in found[1:]:
        if block.type == VORBIS_COMMENT:
            vendor, tags = parse.vorbis_comment(block.body)
        elif block.type == SEEKTABLE:
            points += len(parse.seektable(block.body))
        elif block.type == PICTURE:
            pictures.append(parse.picture(block.body))
        elif block.type == PADDING:
            padding += block.length
        elif block.type == APPLICATION:
            applications += 1
        elif block.type == CUESHEET:
            cuesheets += 1

    # A zero total sample count means "unknown": report no duration rather
    # than inventing one for the release sheet.
    if info["total_samples"] is None:
        duration = None
    else:
        duration = str(Fraction(info["total_samples"], info["sample_rate"]))

    info.update(duration=duration, vendor=vendor, tags=tags, seekpoints=points,
                pictures=pictures, padding=padding, application_blocks=applications,
                cuesheet_blocks=cuesheets, audio_offset=audio)
    return info
