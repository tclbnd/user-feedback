#!/usr/bin/env python3
"""End-to-end tests for flac.py.

Builds synthetic FLAC-style fixtures (impulses, ramps, extrema, silence)
over several block sizes / Rice partition layouts and checks:

  * per-sample reconstruction for every fixed order and both channels,
  * warm-up handling, order-0 blocks, escape residuals,
  * signed zig-zag residuals (negative values must stay negative),
  * partition-switch independence (no carried Rice parameter -> no spike),
  * mid/side signed arithmetic shifts,
  * frame boundaries: a truncated frame never swallows the next frame,
  * bit-cursor accounting and truncation localisation down to partition,
  * the documented CLI contract.
"""
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flac  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------------- #
# Reference helpers
# --------------------------------------------------------------------------- #
def ref_predict(samples, order):
    """Return (warmup, residuals) for samples under fixed predictor `order`."""
    warmup = samples[:order]
    coef = flac.COEF[order]
    residuals = []
    for i in range(order, len(samples)):
        pred = sum(c * samples[i - k - 1] for k, c in enumerate(coef))
        residuals.append(samples[i] - pred)
    return warmup, residuals


def encode_rice_partitions(residuals, order, block, partition_order,
                           bps=16, param_bits=4):
    """Bit-pack residuals exactly like a FLAC residual subframe.

    A partition whose symbols cannot be represented by any legal Rice
    parameter is emitted as an escape partition (raw signed residuals),
    mirroring a real FLAC encoder.
    """
    nparts = 1 << partition_order
    per = block // nparts
    w = flac.BitWriter()
    max_param = (1 << param_bits) - 2
    escape_code = (1 << param_bits) - 1
    start = 0
    for p in range(nparts):
        count = per - order if p == 0 else per
        part = residuals[start:start + count]
        start += count
        symbols = [flac.to_unsigned(r) for r in part]
        # Pick the legal Rice parameter with the shortest total code length;
        # if none fits the parameter field, use a FLAC escape partition.
        param = None
        best = None
        for cand in range(max_param + 1):
            total = sum((u >> cand) + 1 + cand for u in symbols)
            if best is None or total < best:
                best, param = total, cand
        if param is None:
            w.write_bits(escape_code, param_bits)
            for r in part:
                w.write_signed(r, bps)
            continue
        w.write_bits(param, param_bits)
        for u in symbols:
            q, low = divmod(u, 1 << param)
            w.write_unary(q)
            w.write_bits(low, param)
    return w.bytes().hex(), w.pos


def encode_escape_partition(residuals, order, block, partition_order,
                            bps=16, param_bits=4):
    """One partition uses the escape code with raw signed residuals."""
    nparts = 1 << partition_order
    per = block // nparts
    w = flac.BitWriter()
    escape_code = (1 << param_bits) - 1
    start = 0
    for p in range(nparts):
        count = per - order if p == 0 else per
        part = residuals[start:start + count]
        start += count
        if p == nparts - 1:                       # force an escape partition
            w.write_bits(escape_code, param_bits)
            for r in part:
                w.write_signed(r, bps)
        else:
            symbols = [flac.to_unsigned(r) for r in part]
            best, param = None, 0
            for cand in range((1 << param_bits) - 1):
                total = sum((u >> cand) + 1 + cand for u in symbols)
                if best is None or total < best:
                    best, param = total, cand
            w.write_bits(param, param_bits)
            for r in part:
                u = flac.to_unsigned(r)
                q, low = divmod(u, 1 << param)
                w.write_unary(q)
                w.write_bits(low, param)
    return w.bytes().hex(), w.pos


# --------------------------------------------------------------------------- #
# Synthetic signals
# --------------------------------------------------------------------------- #
def signal_impulse(n, amplitude=20000):
    s = [0] * n
    s[n // 2] = amplitude
    return s


def signal_ramp(n, lo=-30000, hi=30000):
    if n == 1:
        return [lo]
    step = (hi - lo) // (n - 1)
    return [lo + step * i for i in range(n)]


def signal_extrema(n, bps=16):
    lo, hi = -(1 << (bps - 1)), (1 << (bps - 1)) - 1
    return [lo if i % 2 == 0 else hi for i in range(n)]


def signal_silence(n):
    return [0] * n


SIGNALS = {
    "impulse": signal_impulse,
    "ramp": signal_ramp,
    "extrema": signal_extrema,
    "silence": signal_silence,
}
BLOCKS = [1, 2, 4, 7, 16, 31, 64, 100]
ORDERS = [0, 1, 2, 3, 4]


class BitIOTests(unittest.TestCase):

    def test_roundtrip_bit_cursor(self):
        w = flac.BitWriter()
        for v, n in [(0, 1), (1, 1), (5, 3), (0, 4), (12345, 14), (0, 7)]:
            w.write_bits(v, n)
        w.write_unary(0)
        w.write_unary(6)
        w.write_signed(-5, 8)
        r = flac.BitReader(w.bytes(), w.pos)
        self.assertEqual([r.read_bits(n) for n in (1, 1, 3, 4, 14, 7)],
                         [0, 1, 5, 0, 12345, 0])
        self.assertEqual(r.read_unary(), 0)
        self.assertEqual(r.read_unary(), 6)
        self.assertEqual(r.read_signed(8), -5)
        self.assertEqual(r.pos, w.pos)

    def test_non_byte_aligned_length(self):
        w = flac.BitWriter()
        w.write_bits(0b10110, 5)
        r = flac.BitReader(w.bytes(), 5)
        self.assertEqual(r.read_bits(5), 0b10110)
        self.assertEqual(r.pos, 5)
        with self.assertRaises(flac.BitTruncated):
            r.read_bit()

    def test_zigzag_signedness(self):
        for r in range(-500, 501):
            u = flac.to_unsigned(r)
            self.assertGreaterEqual(u, 0)
            self.assertEqual(flac.to_signed(u), r)
        # The failure mode from the bug report: negatives must not turn positive.
        self.assertEqual(flac.to_signed(flac.to_unsigned(-32768)), -32768)
        self.assertEqual(flac.to_signed(flac.to_unsigned(-1)), -1)


class SubframeTests(unittest.TestCase):

    def _fixed_subframe(self, samples, order, partition_order=0,
                        escape=False, bps=16):
        warmup, residuals = ref_predict(samples, order)
        block = len(samples)
        if escape:
            hexbits, nbits = encode_escape_partition(
                residuals, order, block, partition_order, bps)
        else:
            hexbits, nbits = encode_rice_partitions(
                residuals, order, block, partition_order, bps)
        sf = {
            "type": "fixed", "order": order, "block": block,
            "warmup": warmup,
            "bits": {"data": hexbits, "length": nbits},
            "partition_order": partition_order, "bps": bps,
        }
        return sf, nbits

    def test_constant_and_verbatim(self):
        sf_c = {"type": "constant", "value": -7, "block": 13}
        out, info = flac.decode_subframe(sf_c)
        self.assertEqual(out, [-7] * 13)
        sf_v = {"type": "verbatim", "samples": [1, -2, 3, -4]}
        out, _ = flac.decode_subframe(sf_v)
        self.assertEqual(out, [1, -2, 3, -4])

    def test_verbatim_declared_block_mismatch(self):
        with self.assertRaises(flac.FrameError) as cm:
            flac.decode_subframe(
                {"type": "verbatim", "samples": [1, 2, 3], "block": 5})
        self.assertEqual(cm.exception.error, "truncated")
        with self.assertRaises(flac.FrameError) as cm:
            flac.decode_subframe(
                {"type": "verbatim", "samples": [1, 2, 3], "block": 2})
        self.assertEqual(cm.exception.error, "length")

    def test_all_orders_signals_blocks_bitpacked(self):
        for name, factory in SIGNALS.items():
            for block in BLOCKS:
                samples = factory(block)
                for order in ORDERS:
                    if order > block:
                        continue
                    # FLAC constraint: the first partition needs at least
                    # `order` slots, so block/2^po >= order. Exercise every
                    # legal partition order for this (block, order) pair.
                    for po in range(4):
                        nparts = 1 << po
                        if block % nparts or block // nparts < order:
                            continue
                        sf, nbits = self._fixed_subframe(samples, order, po)
                        out, info = flac.decode_subframe(sf)
                        self.assertEqual(out, samples,
                                         msg=(name, block, order, po))
                        self.assertEqual(info["bit"], nbits)
                        self.assertEqual(info["partitions"], nparts)

    def test_order0_zero_predictor_blocks(self):
        # Order 0: every sample is its own residual, including block size 1.
        for block in (1, 8, 33):
            samples = signal_ramp(block)
            sf, _ = self._fixed_subframe(samples, 0, 0)
            out, _ = flac.decode_subframe(sf)
            self.assertEqual(out, samples)

    def test_warmup_drives_predictor(self):
        # Hand-built order-2 frame: warm-up must seed the recurrence.
        sf = {"type": "fixed", "order": 2, "block": 5,
              "warmup": [10, 12], "residuals": [1, -2, 0]}
        out, _ = flac.decode_subframe(sf)
        # s2 = 1 + 2*12 - 10 = 15; s3 = -2 + 2*15 - 12 = 16;
        # s4 = 0 + 2*16 - 15 = 17
        self.assertEqual(out, [10, 12, 15, 16, 17])

    def test_legacy_escape_residuals(self):
        sf = {"type": "fixed", "order": 1, "block": 4,
              "warmup": [100],
              "residuals": [5, {"escape": -32000}, 7]}
        out, _ = flac.decode_subframe(sf)
        self.assertEqual(out, [100, 105, -31895, -31888])

    def test_bitpacked_escape_partition(self):
        # Use a gentle signal whose predictor residuals still fit a signed
        # 16-bit escape field, while exercising every Rice/escape branch.
        samples = [i * 257 - 4000 for i in range(32)]
        order, po = 1, 2
        bps = 16
        sf, nbits = self._fixed_subframe(
            samples, order, po, escape=True, bps=bps)
        out, info = flac.decode_subframe(sf)
        self.assertEqual(out, samples)
        self.assertEqual(info["partitions"], 4)
        self.assertEqual(info["bit"], nbits)

    def test_escape_field_holds_negative_boundary(self):
        # The escape partition stores raw two's-complement signed residuals;
        # the negative boundary must not be reinterpreted as positive.
        block, po = 16, 1
        p0 = [1, -2, 3, -4, 5, -6, 7, -8]
        p1 = [-32768, 32767, -1, 0, 100, -100, 1234, -4321]
        w = flac.BitWriter()
        w.write_bits(0, 4)                       # Rice partition, parameter 0
        for r in p0:
            w.write_unary(flac.to_unsigned(r))
        w.write_bits(15, 4)                      # escape partition marker
        for r in p1:
            w.write_signed(r, 16)
        sf = {"type": "fixed", "order": 0, "block": block, "warmup": [],
              "bits": {"data": w.bytes().hex(), "length": w.pos},
              "partition_order": po, "bps": 16}
        out, info = flac.decode_subframe(sf)
        self.assertEqual(out, p0 + p1)
        self.assertEqual(out[8], -32768)

    def test_negative_residuals_never_overflow_positive(self):
        samples = [0, -1, -3, -6, -10, -15, -21, -28]  # order-2-ish decay
        for order in (0, 1, 2):
            if order > len(samples):
                continue
            sf, _ = self._fixed_subframe(samples, order, 0)
            out, _ = flac.decode_subframe(sf)
            self.assertEqual(out, samples)
            self.assertTrue(all(s <= 0 for s in out))

    def test_partition_switch_uses_local_rice_parameter(self):
        # First partition holds tiny residuals (param 0), second huge ones.
        # A decoder that carries the parameter across the boundary spikes.
        block, order, po = 32, 0, 2
        samples = [0] * 16 + [12000 + i for i in range(16)]
        sf, nbits = self._fixed_subframe(samples, order, po)
        out, info = flac.decode_subframe(sf)
        self.assertEqual(out, samples)
        self.assertEqual(info["partitions"], 4)
        self.assertEqual(info["bit"], nbits)

    def test_metadata_partitions(self):
        samples = signal_ramp(24)
        order = 1
        warmup, residuals = ref_predict(samples, order)
        per = len(samples) // 2                  # partitions tile full block
        sf = {
            "type": "fixed", "order": order, "block": len(samples),
            "warmup": warmup,
            "partitions": [
                # First partition loses the warm-up slot.
                {"rice_param": 0, "residuals": residuals[:per - order]},
                {"rice_param": 2, "residuals": residuals[per - order:]},
            ],
        }
        out, info = flac.decode_subframe(sf)
        self.assertEqual(out, samples)
        self.assertEqual(info["partitions"], 2)

    def test_truncated_metadata_partition_is_located(self):
        # block 16, 2 partitions of 8; order 0, so each holds 8 residuals.
        sf = {
            "type": "fixed", "order": 0, "block": 16, "warmup": [],
            "partitions": [
                {"rice_param": 0, "residuals": [1, 2, 3, 4, 5, 6, 7, 8]},
                {"rice_param": 0, "residuals": [9, 10]},   # missing 6 samples
            ],
        }
        with self.assertRaises(flac.FrameError) as cm:
            flac.decode_subframe(sf)
        self.assertEqual(cm.exception.error, "truncated")
        self.assertEqual(cm.exception.partition, 1)

    def test_truncated_first_partition_names_partition_zero(self):
        # First partition of an order-2 frame expects per-order residuals.
        sf = {
            "type": "fixed", "order": 2, "block": 16,
            "warmup": [0, 1],
            "partitions": [
                {"rice_param": 0, "residuals": [2]},   # expects 8-2=6
                {"rice_param": 0, "residuals": list(range(8))},
            ],
        }
        with self.assertRaises(flac.FrameError) as cm:
            flac.decode_subframe(sf)
        self.assertEqual(cm.exception.error, "truncated")
        self.assertEqual(cm.exception.partition, 0)

    def test_legacy_declared_block_truncation(self):
        sf = {"type": "fixed", "order": 1, "block": 6,
              "warmup": [1], "residuals": [2, 3]}
        with self.assertRaises(flac.FrameError) as cm:
            flac.decode_subframe(sf)
        self.assertEqual(cm.exception.error, "truncated")


class StereoTests(unittest.TestCase):

    def _pair_fixture(self, left, right, mode):
        if mode == "independent":
            a, b = left, right
        elif mode == "left_side":
            a, b = left, [l - r for l, r in zip(left, right)]
        elif mode == "right_side":
            a, b = [l - r for l, r in zip(left, right)], right
        else:  # mid_side: mid=floor-ish, side=L-R
            side = [l - r for l, r in zip(left, right)]
            mid = [(l + r) // 2 for l, r in zip(left, right)]
            a, b = mid, side
        return ({"type": "verbatim", "samples": a},
                {"type": "verbatim", "samples": b})

    def test_all_channel_modes_sample_exact(self):
        left = signal_ramp(40, -25000, 25000)
        right = [x ^ 0xFF for x in signal_extrema(40)]
        for mode in ("independent", "left_side", "right_side", "mid_side"):
            sfa, sfb = self._pair_fixture(left, right, mode)
            frame = {"index": 0, "channel_mode": mode,
                     "subframes": [sfa, sfb]}
            channels, _ = flac.decode_frame(frame)
            self.assertEqual(len(channels), 2)
            self.assertEqual(len(channels[0]), len(left))
            self.assertEqual(len(channels[1]), len(right))
            if mode == "mid_side":
                # mid rounding can differ by 1; check exact FLAC identities.
                for i, (l, r) in enumerate(zip(left, right)):
                    self.assertIn(channels[0][i], (l - 1, l, l + 1))
                    self.assertIn(channels[1][i], (r - 1, r, r + 1))
            else:
                self.assertEqual(channels[0], left)
                self.assertEqual(channels[1], right)

    def test_midside_negative_side_arithmetic_shift(self):
        # Directly pin the corrected formulas, including negative side values.
        cases = [(0, 0), (1000, -5), (-1000, 5), (123, -1), (-123, 1),
                 (32767, -32768), (-32768, 32767), (7, -4)]
        a = [m for m, _ in cases]
        b = [s for _, s in cases]
        chs = flac.restore_stereo(a, b, "mid_side")
        for (m, s), l, r in zip(cases, chs[0], chs[1]):
            self.assertEqual(l, m + ((s + 1) >> 1))
            self.assertEqual(r, m - (s >> 1))

    def test_mono_frame_passthrough(self):
        sf = {"type": "constant", "value": 42, "block": 9}
        channels, _ = flac.decode_frame(
            {"index": 3, "channel_mode": "independent", "subframes": [sf]})
        self.assertEqual(channels, [[42] * 9])

    def test_channel_length_mismatch_is_frame_error(self):
        with self.assertRaises(flac.FrameError) as cm:
            flac.restore_stereo([1, 2], [1], "left_side")
        self.assertEqual(cm.exception.error, "length")


class ExtendedFormatTests(unittest.TestCase):

    def test_five_bit_rice_parameter_field(self):
        block = 20
        samples = [i * 1000 - 9000 for i in range(block)]   # wide symbols
        order = 0
        _, residuals = ref_predict(samples, order)
        w = flac.BitWriter()
        param = 16                                   # only legal with 5-bit field
        w.write_bits(param, 5)
        for r in residuals:
            u = flac.to_unsigned(r)
            q, low = divmod(u, 1 << param)
            w.write_unary(q)
            w.write_bits(low, param)
        sf = {"type": "fixed", "order": 0, "block": block, "warmup": [],
              "bits": {"data": w.bytes().hex(), "length": w.pos},
              "partition_order": 0, "rice_parameter_bits": 5}
        out, info = flac.decode_subframe(sf)
        self.assertEqual(out, samples)
        self.assertEqual(info["bit"], w.pos)

    def test_binary_string_bit_payload(self):
        samples = [3, -5, 8, -13]
        order, block = 0, 4
        _, residuals = ref_predict(samples, order)
        w = flac.BitWriter()
        w.write_bits(1, 4)                          # Rice parameter 1
        for r in residuals:
            u = flac.to_unsigned(r)
            q, low = divmod(u, 2)
            w.write_unary(q)
            w.write_bits(low, 1)
        bits = "".join(str((w.buf[i >> 3] >> (7 - (i & 7))) & 1)
                       for i in range(w.pos))
        sf = {"type": "fixed", "order": 0, "block": block, "warmup": [],
              "bits": bits, "partition_order": 0}
        out, _ = flac.decode_subframe(sf)
        self.assertEqual(out, samples)

    def test_partition_smaller_than_order_is_malformed(self):
        sf = {"type": "fixed", "order": 4, "block": 8,
              "warmup": [0, 0, 0, 0],
              "bits": "00", "partition_order": 2}   # per-partition size 2 < 4
        with self.assertRaises(flac.FrameError) as cm:
            flac.decode_subframe(sf)
        self.assertEqual(cm.exception.error, "malformed")

    def test_non_divisible_block_is_malformed(self):
        sf = {"type": "fixed", "order": 0, "block": 7, "warmup": [],
              "bits": "00", "partition_order": 1}
        with self.assertRaises(flac.FrameError) as cm:
            flac.decode_subframe(sf)
        self.assertEqual(cm.exception.error, "malformed")

    def test_base64_payload_roundtrip(self):
        import base64
        samples = signal_ramp(12)
        warmup, residuals = ref_predict(samples, 1)
        hexbits, nbits = encode_rice_partitions(
            residuals, 1, 12, 0)
        payload = base64.b64encode(bytes.fromhex(hexbits)).decode()
        sf = {"type": "fixed", "order": 1, "block": 12, "warmup": warmup,
              "bits": {"data": payload, "length": nbits},
              "partition_order": 0}
        out, _ = flac.decode_subframe(sf)
        self.assertEqual(out, samples)


class TruncationAndBoundaryTests(unittest.TestCase):
    def _good_frame(self, index, block=16):
        samples = signal_ramp(block)
        warmup, residuals = ref_predict(samples, 1)
        return {"index": index, "channel_mode": "independent",
                "subframes": [{"type": "fixed", "order": 1, "block": block,
                               "warmup": warmup, "residuals": residuals}]}

    def test_truncated_bitstream_locates_frame_and_partition(self):
        block, order, po = 32, 0, 2
        samples = signal_impulse(block, 15000)
        warmup, residuals = ref_predict(samples, order)
        hexbits, nbits = encode_rice_partitions(
            residuals, order, block, po)
        full = bytes.fromhex(hexbits)
        # Chop well inside the stream; the failure must name a partition >= 1.
        cut_bytes = max(1, len(full) // 2)
        sf = {"type": "fixed", "order": order, "block": block,
              "warmup": warmup,
              "bits": {"data": full[:cut_bytes].hex()},
              "partition_order": po}
        doc = {"frames": [{"index": 7, "channel_mode": "independent",
                           "subframes": [sf]}]}
        out = flac.decode_document(doc)["frames"][0]
        self.assertEqual(out["index"], 7)
        self.assertEqual(out["error"], "truncated")
        self.assertIn("partition", out)
        self.assertGreaterEqual(out["partition"], 1)
        self.assertIn("bit", out)

    def test_declared_length_truncation(self):
        block, order, po = 16, 0, 1
        samples = signal_ramp(block)
        warmup, residuals = ref_predict(samples, order)
        hexbits, nbits = encode_rice_partitions(
            residuals, order, block, po)
        sf = {"type": "fixed", "order": order, "block": block,
              "warmup": warmup,
              "bits": {"data": hexbits, "length": nbits - 3},
              "partition_order": po}
        doc = {"frames": [{"index": 0, "channel_mode": "independent",
                           "subframes": [sf]}]}
        out = flac.decode_document(doc)["frames"][0]
        self.assertEqual(out["error"], "truncated")
        self.assertEqual(out["bit"], nbits - 3)

    def test_bad_frame_does_not_swallow_next_frame(self):
        good_a = self._good_frame(0)
        bad = {"index": 1, "channel_mode": "independent",
               "subframes": [{"type": "fixed", "order": 1, "block": 10,
                              "warmup": [0], "residuals": [1, 2]}]}
        good_b = self._good_frame(2)
        good_c = self._good_frame(3, block=7)
        doc = {"frames": [good_a, bad, good_b, good_c]}
        out = flac.decode_document(doc)["frames"]
        self.assertEqual([f["index"] for f in out], [0, 1, 2, 3])
        self.assertEqual(out[1], {**out[1], "index": 1, "error": "truncated"})
        self.assertNotIn("error", out[0])
        self.assertNotIn("error", out[2])
        self.assertNotIn("error", out[3])
        # Next complete frame decodes to exactly the expected samples.
        expect, _ = flac.decode_frame(good_b)
        self.assertEqual(out[2]["channels"], expect)
        expect_c, _ = flac.decode_frame(good_c)
        self.assertEqual(out[3]["channels"], expect_c)

    def test_malformed_frame_is_contained(self):
        doc = {"frames": [
            {"index": 0, "channel_mode": "independent",
             "subframes": [{"type": "mystery"}]},
            {"index": 1, "channel_mode": "independent",
             "subframes": [{"type": "constant", "value": 9, "block": 4}]},
        ]}
        out = flac.decode_document(doc)["frames"]
        self.assertEqual(out[0]["error"], "malformed")
        self.assertEqual(out[1]["channels"], [[9, 9, 9, 9]])


class CLITests(unittest.TestCase):

    def test_cli_end_to_end(self):
        block = 24
        left = signal_impulse(block, 17000)
        right = signal_ramp(block)
        warm_l, res_l = ref_predict(left, 2)
        warm_r, res_r = ref_predict(right, 2)
        doc = {"frames": [
            {"index": 0, "channel_mode": "left_side", "subframes": [
                {"type": "fixed", "order": 2, "block": block,
                 "warmup": warm_l, "residuals": res_l},
                {"type": "fixed", "order": 2, "block": block,
                 "warmup": [left[0] - right[0], left[1] - right[1]],
                 "residuals": [(left[i] - right[i])
                               - 2 * (left[i - 1] - right[i - 1])
                               + (left[i - 2] - right[i - 2])
                               for i in range(2, block)]},
            ]},
            {"index": 1, "channel_mode": "independent", "subframes": [
                {"type": "constant", "value": 0, "block": block}]},
        ]}
        in_path = os.path.join(HERE, "_cli_in.json")
        out_path = os.path.join(HERE, "_cli_out.json")
        with open(in_path, "w") as fh:
            json.dump(doc, fh)
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "flac.py"), in_path, out_path],
            cwd=HERE, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(out_path) as fh:
            result = json.load(fh)
        frames = result["frames"]
        self.assertEqual(frames[0]["channels"][0], left)
        self.assertEqual(frames[0]["channels"][1], right)
        self.assertEqual(frames[1]["channels"], [[0] * block])
        for p in (in_path, out_path):
            os.remove(p)


if __name__ == "__main__":
    unittest.main(verbosity=2)
