"""Duration and stream figures for Ogg Opus voice notes (RFC 3533/7845)."""
from fractions import Fraction

from . import ogg, opus

#: Skeleton streams start with a fishead (and may carry fisbone packets).
SKELETON_MAGIC = (b"fishead\x00", b"fisbone\x00")


class _AudioStream:
    """Accumulated figures for one logical Opus bitstream."""

    def __init__(self, stream, head):
        self.serial = stream.serial
        self.head = head
        self.pages = stream.pages
        self.packets = 0
        self.total_samples = 0

    def add_packet(self, samples):
        self.packets += 1
        self.total_samples += samples

    @property
    def granule_end(self):
        """End of the stream on the 48 kHz granule clock.

        RFC 7845 stores the number of decoded samples (including pre-skip)
        in the granule position of the page where the last packet completes.
        The final (EOS) page may hold a smaller value to trim the end, so the
        last page's granule wins.  A page may also mark its granule as
        unknown.  If no granule ever advanced (a damaged or re-muxed file),
        the sum of the packet TOCs is used instead.
        """
        end = 0
        for page in self.pages:
            granule = ogg.granule_position(page)
            if granule is not None:
                end = granule
        return end if end else self.total_samples

    @property
    def playable(self):
        """Samples after pre-skip and end trimming, never negative."""
        return max(0, self.granule_end - self.head.pre_skip)

    @property
    def duration(self):
        return Fraction(self.playable, opus.RATE)

    def figure(self):
        return {
            "serial": self.serial,
            "channels": self.head.channels,
            "pre_skip": self.head.pre_skip,
            "input_rate": self.head.input_rate,
            "packets": self.packets,
            "total_samples": self.total_samples,
            "granule_end": self.granule_end,
            "playable_samples": self.playable,
            "duration": str(self.duration),
            "duration_seconds": float(self.duration),
        }


def _classify(stream, packet_stats, packet_errors):
    """Parse one logical stream once.

    Returns ``("skeleton", None)``, ``("opus", _AudioStream)``,
    ``("invalid", None)`` for a broken Opus bitstream, or ``("other", None)``
    for some other codec.  ``packet_errors`` collects human-readable notes
    about damaged streams; the file as a whole is still summarised.
    """
    pk = list(ogg.packets(stream.pages, packet_stats))
    if not pk:
        return "other", None
    first = pk[0][0]
    if first.startswith(SKELETON_MAGIC):
        return "skeleton", None
    if not opus.is_head(first):
        return "other", None

    try:
        head = opus.head(first)
    except opus.OpusError as exc:
        packet_errors.append(f"stream {stream.serial}: {exc}")
        return "invalid", None

    audio = pk[1:]
    # OpusTags is optional; when present it is exactly one packet.
    if audio and opus.is_tags(audio[0][0]):
        audio = audio[1:]

    fig = _AudioStream(stream, head)
    for packet, _page in audio:
        try:
            samples = opus.packet_samples(packet)
        except (opus.OpusError, IndexError):
            packet_errors.append(f"stream {stream.serial}: bad Opus packet")
            continue
        fig.add_packet(samples)
    return "opus", fig


def probe(data):
    """Summarise an Ogg Opus file.

    Duration is measured from granule positions on the 48 kHz Opus clock,
    with each logical stream's pre-skip subtracted.  Multiplexed
    (Skeleton + Opus) and chained files are both handled; every chained
    bitstream contributes its own playable length.
    """
    page_list, stats = ogg.scan(data)
    if not page_list:
        raise ogg.OggError("not an Ogg Opus file")

    chains = ogg.chains(page_list)
    packet_stats = {}
    packet_errors = []
    audio_streams = []
    skeleton_streams = 0
    other_streams = 0
    invalid_streams = 0
    sequence_errors = 0

    for group in chains:
        group_audio = []
        for stream in group:
            sequence_errors += ogg.sequence_gaps(stream.pages)
            kind, fig = _classify(stream, packet_stats, packet_errors)
            if kind == "skeleton":
                skeleton_streams += 1
            elif kind == "opus":
                group_audio.append(fig)
            elif kind == "invalid":
                invalid_streams += 1
            else:
                other_streams += 1

        if not group_audio:
            continue
        # RFC 7845 section 4.1: an Ogg Opus file carries exactly one Opus
        # stream.  If a pathological group has more, the first is the audio
        # and the rest are reported but not counted.
        audio_streams.append(group_audio[0])
        other_streams += len(group_audio) - 1

    if not audio_streams:
        if invalid_streams:
            raise ogg.OggError(
                f"invalid Opus stream: {packet_errors[0]}")
        raise ogg.OggError("not an Ogg Opus file")

    primary = audio_streams[0]
    total_samples = sum(s.total_samples for s in audio_streams)
    granule_end = sum(s.granule_end for s in audio_streams)
    playable = sum(s.playable for s in audio_streams)
    duration = Fraction(playable, opus.RATE)

    return {
        "channels": primary.head.channels,
        "pre_skip": primary.head.pre_skip,
        "input_rate": primary.head.input_rate,
        "output_gain_db": primary.head.gain_db,
        "sample_rate": opus.RATE,
        "pages": len(page_list),
        "packets": sum(s.packets for s in audio_streams),
        "total_samples": total_samples,
        "granule_end": granule_end,
        "playable_samples": playable,
        "duration": str(duration),
        "duration_seconds": float(duration),
        "crc_errors": stats.crc_errors,
        "sync_errors": stats.sync_errors,
        "sequence_errors": sequence_errors,
        "truncated": stats.truncated,
        "streams": len(audio_streams),
        "logical_streams": sum(len(group) for group in chains),
        "chained": len(audio_streams) > 1,
        "skeleton_streams": skeleton_streams,
        "other_streams": other_streams,
        "packet_errors": len(packet_errors),
        "continuity_errors": (packet_stats.get("missing_continuation", 0)
                              + packet_stats.get("unterminated", 0)),
        "stream_info": [s.figure() for s in audio_streams],
    }

