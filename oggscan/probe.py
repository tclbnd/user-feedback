"""Duration of an Ogg Opus voice note."""
from fractions import Fraction

from . import ogg, opus


def probe(data):
    page_list = list(ogg.pages(data))
    pk = list(ogg.packets(page_list))
    if not pk or not pk[0][0].startswith(b"OpusHead"):
        raise ogg.OggError("not an Ogg Opus file")
    h = opus.head(pk[0][0])
    audio = pk[2:]
    total = sum(opus.samples(p) for p, _ in audio)
    last = page_list[-1].granule
    return {
        "channels": h.channels,
        "pre_skip": h.pre_skip,
        "input_rate": h.input_rate,
        "output_gain_db": h.gain_db,
        "pages": len(page_list),
        "packets": len(audio),
        "total_samples": total,
        "granule_end": last,
        "playable_samples": last,
        "duration": str(Fraction(last, h.input_rate)),
        "crc_errors": 0,
    }
