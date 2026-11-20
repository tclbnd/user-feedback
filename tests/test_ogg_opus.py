"""Conformance tests for Ogg (RFC 3533) and Ogg Opus (RFC 7845)."""
import unittest
from fractions import Fraction

from oggscan import ogg, opus
from oggscan.probe import probe

FISHEAD = b"fishead\x00"


def head_packet(channels=1, pre_skip=0, rate=48000, gain=0, family=0,
                streams=1, coupled=0, mapping=()):
    body = bytearray(b"OpusHead")
    body += bytes([1, channels])
    body += pre_skip.to_bytes(2, "little")
    body += rate.to_bytes(4, "little")
    body += int(round(gain * 256)).to_bytes(2, "little", signed=True)
    body += bytes([family])
    if family:
        body += bytes([streams, coupled])
        body += bytes(mapping)
    return bytes(body)


def tags_packet(vendor=b"test", comments=()):
    body = bytearray(b"OpusTags")
    body += len(vendor).to_bytes(4, "little") + vendor
    body += len(comments).to_bytes(4, "little")
    for comment in comments:
        body += len(comment).to_bytes(4, "little") + comment
    return bytes(body)


def audio_packet(config=19, code=0, frames=None, length=None):
    """Packet whose TOC describes *config*; padded to *length* bytes."""
    toc = (config << 3) | code
    body = bytearray([toc])
    if code == 3:
        body.append(1 if frames is None else frames)
    if length is not None:
        if len(body) > length:
            raise ValueError("length too small for TOC")
        body += bytes(length - len(body))
    return bytes(body)


def lacing(length):
    return [255] * (length // 255) + [length % 255]


def make_page(serial, seq, granule, segments, body, flags=0, crc=None):
    raw = bytearray(b"OggS")
    raw += bytes([0, flags])
    raw += granule.to_bytes(8, "little")
    raw += serial.to_bytes(4, "little")
    raw += seq.to_bytes(4, "little")
    raw += b"\x00\x00\x00\x00"
    raw += bytes([len(segments)])
    raw += bytes(segments)
    raw += bytes(body)
    checksum = ogg.crc32(bytes(raw)) if crc is None else crc
    raw[22:26] = checksum.to_bytes(4, "little")
    return bytes(raw)


def page_size(page):
    return 27 + len(page.segments) + len(page.body)


def stream(packets, serial=1, samples=None):
    """One packet per page; granule marks the cumulative sample count."""
    if samples is None:
        samples = [0] * len(packets)
    out = []
    granule = 0
    for index, (packet, count) in enumerate(zip(packets, samples)):
        granule += count
        flags = ogg.BOS if index == 0 else 0
        if index == len(packets) - 1:
            flags |= ogg.EOS
        out.append(make_page(serial, index, granule, lacing(len(packet)),
                             packet, flags))
    return b"".join(out)


def note(packets, pre_skip=0, rate=48000, serial=1, granule=True, samples=None):
    if samples is None:
        samples = []
        for packet in packets:
            try:
                samples.append(opus.packet_samples(packet))
            except opus.OpusError:
                samples.append(0)
    samples = [0, 0] + list(samples)
    pages = []
    total = 0
    for index, (packet, count) in enumerate(
            zip([head_packet(pre_skip=pre_skip, rate=rate), tags_packet()] + packets,
                samples)):
        total += count
        flags = ogg.BOS if index == 0 else 0
        if index == len(samples) - 1:
            flags |= ogg.EOS
        pages.append(make_page(serial, index, total if granule else ogg.NO_GRANULE,
                               lacing(len(packet)), packet, flags))
    return b"".join(pages)


def replace_page(data, page, replacement):
    start = page.offset
    return data[:start] + replacement + data[start + page_size(page):]


def ogg_data(page):
    """Re-serialise a parsed page, keeping its stored CRC."""
    raw = bytearray(b"OggS")
    raw += bytes([0, page.flags])
    raw += page.granule.to_bytes(8, "little")
    raw += page.serial.to_bytes(4, "little")
    raw += page.seq.to_bytes(4, "little")
    raw += page.crc.to_bytes(4, "little")
    raw += bytes([len(page.segments)])
    raw += bytes(page.segments)
    raw += bytes(page.body)
    return bytes(raw)


class LacingTests(unittest.TestCase):
    def test_packet_exactly_255_bytes_is_a_single_packet(self):
        data = stream([head_packet(), tags_packet(), audio_packet(length=255)])
        pk = list(ogg.packets(list(ogg.pages(data))))
        self.assertEqual([len(p) for p, _ in pk], [19, 20, 255])
        self.assertEqual(len(pk), 3)

    def test_large_packet_is_not_split_at_255(self):
        big = audio_packet(length=421)
        data = note([big])
        pk = list(ogg.packets(list(ogg.pages(data))))
        self.assertEqual(len(pk), 3)
        self.assertEqual(pk[-1][0], big)

    def test_packet_spanning_pages(self):
        big = audio_packet(length=700)
        parts = lacing(len(big))
        data = b"".join([
            make_page(1, 0, 0, lacing(19), head_packet(), ogg.BOS),
            make_page(1, 1, 0, lacing(20), tags_packet()),
            make_page(1, 2, 0, parts[:2], big[:510]),
            make_page(1, 3, 960, parts[2:], big[510:], ogg.CONTINUED | ogg.EOS),
        ])
        pk = list(ogg.packets(list(ogg.pages(data))))
        self.assertEqual(len(pk), 3)
        self.assertEqual(pk[-1][0], big)
        self.assertEqual(probe(data)["duration"], "1/50")

    def test_continued_page_without_packet_start_is_dropped(self):
        data = b"".join([
            make_page(1, 0, 0, lacing(19), head_packet(), ogg.BOS),
            make_page(1, 1, 0, lacing(20), tags_packet()),
            make_page(1, 2, 0, [100], b"\x98" + bytes(99), ogg.CONTINUED),
            make_page(1, 3, 960, lacing(len(audio_packet())),
                      audio_packet(), ogg.EOS),
        ])
        stats = {}
        pk = list(ogg.packets(list(ogg.pages(data)), stats))
        self.assertEqual(len(pk), 3)
        self.assertEqual(stats.get("missing_continuation"), 1)

    def test_packet_left_open_at_eof_is_reported(self):
        data = b"".join([
            make_page(1, 0, 0, lacing(19), head_packet(), ogg.BOS),
            make_page(1, 1, 0, lacing(20), tags_packet()),
            make_page(1, 2, 0, [255], b"\x98" + bytes(254), ogg.EOS),
        ])
        stats = {}
        list(ogg.packets(list(ogg.pages(data)), stats))
        self.assertEqual(stats.get("unterminated"), 1)

    def test_multiple_packets_on_one_page(self):
        packets = [audio_packet()] * 3
        body = b"".join(packets)
        segments = [s for p in packets for s in lacing(len(p))]
        data = b"".join([
            make_page(1, 0, 0, lacing(19), head_packet(), ogg.BOS),
            make_page(1, 1, 0, lacing(20), tags_packet()),
            make_page(1, 2, 3 * 960, segments, body, ogg.EOS),
        ])
        pk = list(ogg.packets(list(ogg.pages(data))))
        self.assertEqual([p for p, _ in pk], [head_packet(), tags_packet()] + packets)
        self.assertEqual(probe(data)["duration"], "3/50")


class GranuleAndDurationTests(unittest.TestCase):
    def test_thirty_second_16k_note_is_not_ninety_seconds(self):
        # 30 s of audio at 48 kHz = 1,440,000 granule; dividing that by the
        # 16 kHz *input* rate (the old bug) reports 90 s instead of 30.
        packets = [audio_packet()] * 1500
        data = note(packets, pre_skip=3840, rate=16000)
        result = probe(data)
        self.assertEqual(result["granule_end"], 1_440_000)
        self.assertAlmostEqual(result["duration_seconds"], 29.92, places=6)
        self.assertNotAlmostEqual(result["duration_seconds"], 90.0, places=3)

    def test_16k_input_rate_still_counts_48k_granules(self):
        data = note([audio_packet()] * 90, pre_skip=3840, rate=16000)
        result = probe(data)
        self.assertEqual(result["input_rate"], 16000)
        self.assertEqual(result["sample_rate"], 48000)
        self.assertEqual(result["total_samples"], 90 * 960)
        self.assertEqual(result["granule_end"], 90 * 960)
        self.assertEqual(result["playable_samples"], 90 * 960 - 3840)
        self.assertEqual(result["duration"], "43/25")  # 1.72 s
        self.assertEqual(result["duration_seconds"], 1.72)

    def test_input_rate_zero_falls_back_to_48k(self):
        result = probe(note([audio_packet()] * 5, rate=0))
        self.assertEqual(result["duration"], "1/10")

    def test_pre_skip_is_subtracted(self):
        result = probe(note([audio_packet()] * 10, pre_skip=312))
        self.assertEqual(result["playable_samples"], 9600 - 312)
        self.assertEqual(result["duration"], "387/2000")

    def test_end_trimming_comes_from_the_eos_granule(self):
        data = note([audio_packet()] * 10, pre_skip=312)
        last = list(ogg.pages(data))[-1]
        trimmed = make_page(last.serial, last.seq, 9000, last.segments,
                            last.body, last.flags)
        result = probe(replace_page(data, last, trimmed))
        self.assertEqual(result["granule_end"], 9000)
        self.assertEqual(result["playable_samples"], 9000 - 312)

    def test_unknown_granule_is_skipped(self):
        data = note([audio_packet()] * 4)
        pages = list(ogg.pages(data))
        page = pages[3]
        unknown = make_page(page.serial, page.seq, ogg.NO_GRANULE,
                            page.segments, page.body, page.flags)
        result = probe(replace_page(data, page, unknown))
        self.assertEqual(result["granule_end"], 4 * 960)

    def test_packets_only_granule_falls_back_to_decoded_total(self):
        data = note([audio_packet()] * 4, granule=False)
        result = probe(data)
        self.assertEqual(result["granule_end"], 4 * 960)
        self.assertEqual(result["playable_samples"], 4 * 960)

    def test_end_trim_does_not_override_a_larger_last_granule(self):
        data = note([audio_packet()] * 10)
        pages = list(ogg.pages(data))
        # A non-final page with a bigger granule than the EOS page: the last
        # granule in stream order wins, matching liboggz/ffmpeg behaviour.
        page = pages[5]
        bumped = make_page(page.serial, page.seq, 20000, page.segments,
                           page.body, page.flags)
        result = probe(replace_page(data, page, bumped))
        self.assertEqual(result["granule_end"], 9600)


class MultiplexAndChainingTests(unittest.TestCase):
    def skeleton_bos(self, serial=9):
        body = FISHEAD + bytes(12)
        return make_page(serial, 0, 0, lacing(len(body)), body, ogg.BOS)

    def test_skeleton_granule_is_not_mistaken_for_the_end(self):
        opus_part = note([audio_packet()] * 5, serial=7)
        # Skeleton granules are milliseconds; a huge tail value must not be
        # read as the audio length.
        tail = make_page(9, 1, 5_000_000, lacing(10), bytes(10), ogg.EOS)
        result = probe(self.skeleton_bos() + opus_part + tail)
        self.assertEqual(result["skeleton_streams"], 1)
        self.assertEqual(result["streams"], 1)
        self.assertEqual(result["granule_end"], 5 * 960)
        self.assertEqual(result["duration"], "1/10")

    def test_interleaved_skeleton_pages(self):
        head = head_packet(pre_skip=312)
        pages = [
            self.skeleton_bos(),
            make_page(7, 0, 0, lacing(len(head)), head, ogg.BOS),
            make_page(7, 1, 0, lacing(20), tags_packet()),
            make_page(9, 1, 0, lacing(8), bytes(8)),
            make_page(7, 2, 960, lacing(len(audio_packet())), audio_packet()),
            make_page(9, 2, 4000, lacing(8), bytes(8), ogg.EOS),
            make_page(7, 3, 1920, lacing(len(audio_packet())), audio_packet(),
                      ogg.EOS),
        ]
        result = probe(b"".join(pages))
        self.assertEqual(result["granule_end"], 1920)
        self.assertEqual(result["playable_samples"], 1920 - 312)
        self.assertEqual(result["skeleton_streams"], 1)
        self.assertEqual(result["pages"], 7)

    def test_chained_streams_add_up(self):
        first = note([audio_packet()] * 5, serial=1, pre_skip=312)
        second = note([audio_packet()] * 7, serial=2, pre_skip=120)
        result = probe(first + second)
        self.assertTrue(result["chained"])
        self.assertEqual(result["streams"], 2)
        self.assertEqual(result["packets"], 12)
        expected = (5 * 960 - 312) + (7 * 960 - 120)
        self.assertEqual(result["playable_samples"], expected)
        self.assertEqual(result["duration"], str(Fraction(expected, 48000)))
        self.assertEqual([s["serial"] for s in result["stream_info"]], [1, 2])

    def test_chained_stream_with_different_rates(self):
        first = note([audio_packet()] * 5, serial=1, rate=16000, pre_skip=3840)
        second = note([audio_packet()] * 5, serial=2, rate=48000, pre_skip=312)
        result = probe(first + second)
        self.assertEqual(result["stream_info"][0]["input_rate"], 16000)
        self.assertEqual(result["stream_info"][1]["input_rate"], 48000)
        expected = (5 * 960 - 3840) + (5 * 960 - 312)
        self.assertEqual(result["playable_samples"], expected)
        self.assertEqual(result["sequence_errors"], 0)

    def test_chained_streams_sharing_a_serial(self):
        # Chained bitstreams may reuse a serial number; numbering restarts
        # at zero and that is not a sequence error.
        first = note([audio_packet()] * 4, serial=5)
        second = note([audio_packet()] * 6, serial=5)
        result = probe(first + second)
        self.assertTrue(result["chained"])
        self.assertEqual(result["streams"], 2)
        self.assertEqual(result["sequence_errors"], 0)
        self.assertEqual(result["granule_end"], 10 * 960)

    def test_skeleton_is_reported_per_chain(self):
        first = self.skeleton_bos() + note([audio_packet()] * 3, serial=7)
        second = self.skeleton_bos() + note([audio_packet()] * 2, serial=8)
        result = probe(first + second)
        self.assertEqual(result["skeleton_streams"], 2)
        self.assertEqual(result["streams"], 2)
        self.assertEqual(result["granule_end"], 5 * 960)

    def test_two_serial_streams_are_not_summed_as_audio(self):
        # A multiplexed group may only carry one Opus stream (RFC 7845 s4);
        # the first is the audio and any other is reported but not counted.
        first = b"".join([
            make_page(1, 0, 0, lacing(19), head_packet(), ogg.BOS),
            make_page(1, 1, 0, lacing(20), tags_packet()),
            make_page(1, 2, 3 * 960, lacing(len(audio_packet())), audio_packet(),
                      ogg.EOS),
        ])
        second = note([audio_packet()] * 9, serial=2)
        pages = list(ogg.pages(first))
        other = list(ogg.pages(second))
        # All BOS pages first, then interleave every following page pair.
        data = (ogg_data(pages[0]) + ogg_data(other[0])
                + ogg_data(pages[1]) + ogg_data(other[1])
                + b"".join(ogg_data(p) + ogg_data(o)
                           for p, o in zip(pages[2:], other[2:])))
        result = probe(data)
        self.assertEqual(result["streams"], 1)
        self.assertEqual(result["granule_end"], 3 * 960)


class IntegrityTests(unittest.TestCase):
    def test_crc_errors_are_counted_but_duration_survives(self):
        data = bytearray(note([audio_packet()] * 4))
        page = list(ogg.pages(bytes(data)))[3]
        data[page.offset + 27 + len(page.segments)] ^= 0xFF
        result = probe(bytes(data))
        self.assertEqual(result["crc_errors"], 1)
        self.assertEqual(result["granule_end"], 4 * 960)

    def test_every_bad_page_is_counted(self):
        data = bytearray(note([audio_packet()] * 6))
        for page in list(ogg.pages(bytes(data)))[2:5]:
            data[page.offset + 27 + len(page.segments)] ^= 0x01
        self.assertEqual(probe(bytes(data))["crc_errors"], 3)

    def test_corrupt_header_page_is_flagged(self):
        data = bytearray(note([audio_packet()] * 3))
        page = list(ogg.pages(bytes(data)))[0]
        # Damage the output-gain bytes inside OpusHead: still parseable, so
        # the file is reported with a CRC error rather than rejected.
        data[page.offset + 27 + len(page.segments) + 16] ^= 0x20
        result = probe(bytes(data))
        self.assertEqual(result["crc_errors"], 1)

    def test_junk_prefix_is_resynced(self):
        clean = note([audio_packet()] * 3)
        result = probe(b"garbage" + clean)
        self.assertEqual(result["sync_errors"], 1)
        self.assertEqual(result["crc_errors"], 0)
        self.assertEqual(result["granule_end"], 3 * 960)

    def test_trailing_junk_is_ignored(self):
        result = probe(note([audio_packet()] * 3) + b"\x00" * 40)
        self.assertEqual(result["granule_end"], 3 * 960)
        self.assertFalse(result["truncated"])

    def test_truncated_last_page_keeps_whole_pages(self):
        clean = note([audio_packet()] * 4)
        pages = list(ogg.pages(clean))
        last = pages[-1]
        # Keep the final page's header but drop its body.
        cut = last.offset + 27 + len(last.segments)
        result = probe(clean[:cut])
        self.assertTrue(result["truncated"])
        self.assertEqual(result["granule_end"], 3 * 960)

    def test_missing_continuation_is_counted(self):
        data = b"".join([
            make_page(1, 0, 0, lacing(19), head_packet(), ogg.BOS),
            make_page(1, 1, 0, lacing(20), tags_packet()),
            make_page(1, 3, 960, lacing(len(audio_packet())), audio_packet(),
                      ogg.EOS),
        ])
        result = probe(data)
        self.assertEqual(result["packets"], 1)
        # The gap itself is a sequence discontinuity, not a packet problem.
        self.assertEqual(result["sequence_errors"], 1)
        self.assertEqual(result["continuity_errors"], 0)

    def test_sequence_gaps_are_counted_per_stream(self):
        data = note([audio_packet()] * 5)
        pages = list(ogg.pages(data))
        # Drop the third packet's page: sequence numbers now jump there.
        dropped = pages[3]
        data = data[:dropped.offset] + data[dropped.offset + page_size(dropped):]
        self.assertEqual(probe(data)["sequence_errors"], 1)

    def test_non_opus_file_is_rejected(self):
        data = stream([b"\x01vorbis" + bytes(20)])
        with self.assertRaises(ogg.OggError):
            probe(data)

    def test_invalid_opus_head_is_reported_clearly(self):
        data = stream([head_packet(channels=3), tags_packet(), audio_packet()])
        with self.assertRaises(ogg.OggError) as caught:
            probe(data)
        self.assertIn("mapping family", str(caught.exception))

    def test_empty_input_is_rejected(self):
        with self.assertRaises(ogg.OggError):
            probe(b"")


class HeaderTests(unittest.TestCase):
    def test_family_zero(self):
        parsed = opus.head(head_packet(channels=2, pre_skip=312, rate=16000))
        self.assertEqual((parsed.channels, parsed.pre_skip, parsed.input_rate,
                          parsed.mapping), (2, 312, 16000, 0))

    def test_family_one_mapping(self):
        parsed = opus.head(head_packet(channels=4, family=1, streams=2,
                                       coupled=1, mapping=(0, 2, 1, 255)))
        self.assertEqual(parsed.channels, 4)
        self.assertEqual(parsed.stream_count, 2)
        self.assertEqual(parsed.coupled_count, 1)
        self.assertEqual(parsed.channel_map, (0, 2, 1, 255))

    def test_invalid_heads_are_rejected(self):
        for packet in (
            b"OpusHead\x01\x02\x00",                       # truncated
            head_packet(channels=0),                       # no channels
            head_packet(channels=3),                       # family 0, 3ch
            head_packet(channels=2, family=1, streams=0, coupled=0,
                        mapping=(0, 1)),                   # zero streams
            head_packet(channels=2, family=1, streams=1, coupled=2,
                        mapping=(0, 1)),                   # coupled > streams
            head_packet(channels=2, family=1, streams=1, coupled=0,
                        mapping=(0, 5)),                   # map out of range
            b"OpusHead\x20\x01" + bytes(17),               # version 32
        ):
            with self.assertRaises(opus.OpusError, msg=repr(packet)):
                opus.head(packet)

    def test_signed_gain(self):
        self.assertEqual(opus.head(head_packet(gain=-3.5)).gain_db, -3.5)
        self.assertEqual(opus.head(head_packet(gain=7)).gain_db, 7)

    def test_tags_parse(self):
        parsed = opus.tags(tags_packet(b"libopus", (b"TITLE=hi", b"X=1")))
        self.assertEqual(parsed.vendor, "libopus")
        self.assertEqual(parsed.comments, ("TITLE=hi", "X=1"))

    def test_tags_without_opustags_is_rejected(self):
        with self.assertRaises(opus.OpusError):
            opus.tags(b"\x01vorbis" + bytes(20))

    def test_new_fields_do_not_break_old_names(self):
        parsed = opus.head(head_packet(channels=1, pre_skip=3840, rate=16000))
        self.assertEqual(parsed[:4], (1, 3840, 16000, 0))  # _replace-compatible

    def test_code3_frame_count_is_honoured(self):
        six = audio_packet(config=19, code=3, frames=6)
        three = audio_packet(config=10, code=3, frames=3)  # 40 ms frames
        self.assertEqual(opus.packet_samples(six), 6 * 960)
        self.assertEqual(opus.packet_samples(three), 3 * 1920)
        result = probe(note([six, three]))
        self.assertEqual(result["total_samples"], 6 * 960 + 3 * 1920)
        self.assertEqual(result["packets"], 2)
        self.assertEqual(result["granule_end"], 6 * 960 + 3 * 1920)

    def test_code3_without_frame_count_byte_is_rejected(self):
        with self.assertRaises(opus.OpusError):
            opus.packet_samples(bytes([(19 << 3) | 3]))
        with self.assertRaises(opus.OpusError):
            opus.packet_samples(bytes([(19 << 3) | 3, 0]))
        with self.assertRaises(opus.OpusError):
            opus.packet_samples(bytes([(19 << 3) | 3, 49]))

    def test_codes_0_1_2(self):
        self.assertEqual(opus.frame_count(bytes([(19 << 3) | 0])), 1)
        self.assertEqual(opus.frame_count(bytes([(19 << 3) | 1])), 2)
        self.assertEqual(opus.frame_count(bytes([(19 << 3) | 2])), 2)
        self.assertEqual(opus.packet_samples(audio_packet(config=19, code=1)),
                         2 * 960)
        self.assertEqual(opus.packet_samples(audio_packet(config=19, code=2)),
                         2 * 960)

    def test_packet_over_120ms_is_rejected(self):
        too_long = audio_packet(config=3, code=3, frames=3)  # 3 x 60 ms
        with self.assertRaises(opus.OpusError):
            opus.packet_samples(too_long)

    def test_2_5ms_frames_are_exact_at_other_rates(self):
        packet = audio_packet(config=16)
        self.assertEqual(opus.samples(packet), 120)
        self.assertEqual(opus.samples(packet, 44100), Fraction(441, 4))
        self.assertEqual(opus.samples(packet, 11025), Fraction(441, 16))

    def test_bad_packet_does_not_abort_the_count(self):
        good = [audio_packet()] * 4
        bad = bytes([(19 << 3) | 3])  # code 3, no frame byte
        result = probe(note(good[:2] + [bad] + good[2:]))
        self.assertEqual(result["packet_errors"], 1)
        self.assertEqual(result["packets"], 4)
        self.assertEqual(result["total_samples"], 4 * 960)
        self.assertEqual(result["granule_end"], 4 * 960)
