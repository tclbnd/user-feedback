import unittest

from mpckit.mpc import MpcError, parse, varint

from streams import ap, ct, ei, mpc, packet, rg, se, sh, stream, varint as enc_varint


class VarintTests(unittest.TestCase):
    def test_single_byte(self):
        self.assertEqual(varint(b"\x0e", 0), (14, 1))

    def test_multibyte(self):
        # 2001 = 0b11111010001 -> groups 0001111 / 1010001 -> 0x8f 0x51
        self.assertEqual(enc_varint(2001), b"\x8f\x51")
        self.assertEqual(varint(b"\x8f\x51", 0), (2001, 2))

    def test_three_bytes(self):
        self.assertEqual(varint(enc_varint(70000), 0), (70000, 3))


class Id3v2Tests(unittest.TestCase):
    def test_id3_prefix_is_skipped(self):
        body = mpc(sh(100), se())
        # ID3v2 sizes are synchsafe (7 bits/byte). The tag is a prefix of
        # 10 header bytes plus N payload bytes, then the MPCK stream starts.
        n = 128
        tag = (b"ID3\x04\x00\x00" + bytes([
            (n >> 21) & 0x7F, (n >> 14) & 0x7F,
            (n >> 7) & 0x7F, n & 0x7F]))
        out = parse(tag + b"\x00" * n + body)
        self.assertEqual(out["id3v2_bytes"], 10 + n)
        self.assertEqual(out["samples"], 100)
        self.assertEqual(out["packets"], ["SH", "SE"])

    def test_no_id3(self):
        self.assertEqual(parse(mpc(sh(100), se()))["id3v2_bytes"], 0)


class PacketWalkTests(unittest.TestCase):
    def test_large_packets_keep_aligned(self):
        # An AP packet over 127 bytes needs a multi-byte size; every packet
        # after it must still be recognised (the old single-byte decoder
        # desynchronised the whole rest of the stream).
        big = packet(b"AP", bytes(range(251)))
        out = parse(mpc(sh(1152), big, big, ct(), se()))
        self.assertEqual(out["audio_packets"], 2)
        self.assertEqual(out["chapters"], 1)
        self.assertTrue(out["stream_end"])
        self.assertEqual(out["packets"], ["SH", "AP", "AP", "CT", "SE"])

    def test_bytes_after_se_are_not_packets(self):
        out = parse(mpc(sh(100), se()) + b"APETAGEX junk junk junk")
        self.assertEqual(out["packets"], ["SH", "SE"])
        self.assertTrue(out["stream_end"])

    def test_packet_running_past_eof_raises(self):
        with self.assertRaises(MpcError):
            parse(mpc(sh(100), packet(b"AP", b"x" * 10))[:-2])


class StreamHeaderTests(unittest.TestCase):
    def test_frames_per_packet_is_power_of_two(self):
        for block, frames in ((0, 1), (1, 2), (2, 4), (3, 8)):
            out = parse(mpc(sh(1152, block=block), se()))
            self.assertEqual(out["frames_per_packet"], frames)

    def test_duration_subtracts_beginning_silence(self):
        out = parse(mpc(sh(1000, silence=240, rate=48000), se()))
        self.assertEqual(out["samples"], 1000)
        self.assertEqual(out["beginning_silence"], 240)
        self.assertEqual(out["duration"], "19/1200")
        # Frames still cover the whole stream, not just the playable part.
        self.assertEqual(out["frames"], 1)

    def test_header_crc_checked(self):
        self.assertTrue(parse(mpc(sh(100), se()))["header_crc_ok"])
        raw = sh(100)
        bad = raw[:7] + bytes([raw[7] ^ 0x01]) + raw[8:]
        self.assertFalse(parse(mpc(bad, se()))["header_crc_ok"])


class ReplayGainTests(unittest.TestCase):
    def test_negative_gains_stay_negative(self):
        out = parse(mpc(sh(100), rg(title_gain=-1536, album_gain=-1100), se()))
        self.assertEqual(out["replay_gain"]["title_gain"], "-6")
        self.assertEqual(out["replay_gain"]["album_gain"], "-275/64")

    def test_positive_gains_and_peaks(self):
        out = parse(mpc(sh(100), rg(title_gain=512, title_peak=1234,
                                        album_gain=256, album_peak=4321), se()))
        self.assertEqual(out["replay_gain"]["title_gain"], "2")
        self.assertEqual(out["replay_gain"]["title_peak"], 1234)
        self.assertEqual(out["replay_gain"]["album_gain"], "1")
        self.assertEqual(out["replay_gain"]["album_peak"], 4321)


class FullStreamTests(unittest.TestCase):
    def test_built_stream_counts_packets(self):
        out = parse(stream(samples=1152 * 4 * 5, block=2))
        self.assertEqual(out["frames_per_packet"], 4)
        self.assertEqual(out["audio_packets"], 5)
        self.assertTrue(out["stream_end"])


if __name__ == "__main__":
    unittest.main()
