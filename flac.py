#!/usr/bin/env python3
"""FLAC fixed-predictor subframe reconstruction.

Supports the JSON fixture contract described in README.md:

  constant / verbatim / fixed(order 0..4) subframes,
  Rice residual partitions (metadata or bit-packed FLAC-style),
  escape residuals (legacy and partition escape codes),
  independent / left_side / right_side / mid_side stereo restore,
  mono frames, truncation localisation and bit-cursor bookkeeping.

Run: python3 flac.py input.json output.json
"""
import base64
import binascii
import json
import sys

# Fixed-predictor coefficients: s[i] = r[i] + sum(COEF[o][k] * s[i-k-1])
COEF = {0: [], 1: [1], 2: [2, -1], 3: [3, -3, 1], 4: [4, -6, 4, -1]}

MAX_PARTITION_ORDER = 15
DEFAULT_BPS = 16


class BitTruncated(Exception):
    """Raised when a bitstream ends before the declared data is read."""


class FrameError(Exception):
    """A corrupt / truncated frame. Carries the failing partition and bit."""

    def __init__(self, error, partition=None, bit=None, detail=None):
        super().__init__(detail or error)
        self.error = error
        self.partition = partition
        self.bit = bit
        self.detail = detail


class BitReader:
    """MSB-first bit cursor over a byte buffer, exactly like the FLAC stream."""

    def __init__(self, data, length=None, start=0):
        self.data = data
        self.pos = start                      # bit cursor
        self.end = len(data) * 8 if length is None else length
        if self.end > len(data) * 8:
            raise BitTruncated("declared bit length exceeds buffer")

    def read_bit(self):
        if self.pos >= self.end:
            raise BitTruncated("unexpected end of bitstream")
        bit = (self.data[self.pos >> 3] >> (7 - (self.pos & 7))) & 1
        self.pos += 1
        return bit

    def read_bits(self, n):
        if n < 0:
            raise ValueError("negative read width")
        v = 0
        for _ in range(n):
            v = (v << 1) | self.read_bit()
        return v

    def read_unary(self):
        """Unary code: n zero bits terminated by a one bit."""
        n = 0
        while self.read_bit() == 0:
            n += 1
        return n

    def read_signed(self, n):
        """Read n bits as a two's-complement signed integer."""
        if n == 0:
            return 0
        v = self.read_bits(n)
        if v & (1 << (n - 1)):
            v -= 1 << n
        return v


class BitWriter:
    """MSB-first bit sink used by the test fixtures to build Rice streams."""

    def __init__(self):
        self.buf = bytearray()
        self.pos = 0

    def write_bit(self, b):
        if (self.pos & 7) == 0:
            self.buf.append(0)
        if b & 1:
            self.buf[self.pos >> 3] |= 1 << (7 - (self.pos & 7))
        self.pos += 1

    def write_bits(self, v, n):
        for k in range(n - 1, -1, -1):
            self.write_bit((v >> k) & 1)

    def write_unary(self, v):
        for _ in range(v):
            self.write_bit(0)
        self.write_bit(1)

    def write_signed(self, v, n):
        if n:
            # Two's complement, including the negative boundary -2^(n-1).
            self.write_bits(v & ((1 << n) - 1), n)

    def bytes(self):
        return bytes(self.buf)


def to_unsigned(r):
    """FLAC residual zig-zag map: signed residual -> unsigned Rice symbol."""
    return (r << 1) ^ (-1 if r < 0 else 0)


def to_signed(u):
    """Inverse zig-zag map; keeps negative residuals negative (no overflow)."""
    return (u >> 1) ^ -(u & 1)


def _open_bits(spec):
    """Accept hex / base64 / binary-string bit payloads, with optional length."""
    if isinstance(spec, dict):
        payload = spec.get("data", b"")
        length = spec.get("length")
    else:
        payload, length = spec, None
    if isinstance(payload, str):
        s = payload.strip()
        if s and set(s) <= {"0", "1"}:
            data = bytearray()
            for i in range(0, len(s), 8):
                chunk = s[i:i + 8]
                data.append(int(chunk.ljust(8, "0"), 2))
            data = bytes(data)
            if length is None:
                length = len(s)
        else:
            try:
                data = bytes.fromhex(s)
            except ValueError:
                try:
                    data = base64.b64decode(s, validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise FrameError("malformed",
                                     detail="undecodable bits: %s" % exc)
    elif isinstance(payload, (bytes, bytearray)):
        data = bytes(payload)
    else:
        raise FrameError("malformed", detail="bits must be hex/base64/binary")
    try:
        return BitReader(data, length)
    except BitTruncated:
        raise FrameError("truncated", detail="bit length exceeds buffer")


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _check_length(samples, block, where):
    """A declared sample range that does not match decodable length is corrupt."""
    if block is not None and len(samples) != block:
        err = "truncated" if len(samples) < block else "length"
        raise FrameError(err,
                         detail="%s: have %d samples, block declares %d"
                         % (where, len(samples), block))


def _read_rice_stream(sf, block, order):
    """Decode a FLAC-style bit-packed Rice partition residual stream."""
    br = _open_bits(sf["bits"])
    partition_order = sf.get("partition_order", 0)
    if not _is_int(partition_order) or \
            not (0 <= partition_order <= MAX_PARTITION_ORDER):
        raise FrameError("malformed", detail="partition_order out of range")
    nparts = 1 << partition_order
    if block % nparts:
        raise FrameError(
            "malformed",
            detail="block %d not divisible into %d partitions" % (block, nparts))
    per = block // nparts
    if per < order:
        raise FrameError(
            "malformed",
            detail="partition size %d smaller than predictor order %d"
            % (per, order))
    bps = sf.get("bps", DEFAULT_BPS)
    if not _is_int(bps) or not (1 <= bps <= 33):
        raise FrameError("malformed", detail="bps must be an integer 1..33")
    param_bits = 4 if sf.get("rice_parameter_bits", 4) == 4 else 5
    escape_code = (1 << param_bits) - 1
    residuals = []
    p = 0
    try:
        for p in range(nparts):
            # FLAC partition layout: each partition covers block/2^p sample
            # slots, and the first partition's `order` slots are the warm-up
            # samples, so it carries that many fewer residuals. With a
            # single partition it is simply the whole residual body.
            count = per - order if p == 0 else per
            rice_param = br.read_bits(param_bits)
            if rice_param == escape_code:              # escape partition
                raw = [br.read_signed(bps) for _ in range(count)]
            else:
                raw = []
                for _ in range(count):
                    q = br.read_unary()
                    low = br.read_bits(rice_param)
                    raw.append(to_signed((q << rice_param) | low))
            residuals.extend(raw)
    except BitTruncated:
        raise FrameError("truncated", partition=p, bit=br.pos,
                         detail="ran out of bits in partition %d" % p)
    return residuals, br.pos, nparts


def _read_metadata_partitions(sf, block, order):
    """Decode partition tables supplied as JSON metadata (no bitstream)."""
    parts = sf["partitions"]
    if block % len(parts):
        raise FrameError("malformed", detail="partition sizes do not tile block")
    per = block // len(parts)
    if per < order:
        raise FrameError(
            "malformed",
            detail="partition size %d smaller than predictor order %d"
            % (per, order))
    residuals = []
    for p, part in enumerate(parts):
        count = per - order if p == 0 else per
        items = part.get("residuals", [])
        raw = []
        if part.get("escape"):
            raw = [it["escape"] if isinstance(it, dict) else it for it in items]
        else:
            for it in items:
                if isinstance(it, dict) and "escape" in it:
                    raw.append(it["escape"])
                elif isinstance(it, dict) and "unsigned" in it:
                    raw.append(to_signed(it["unsigned"]))
                else:
                    raw.append(it)
        if len(raw) != count:
            raise FrameError(
                "truncated" if len(raw) < count else "length", partition=p,
                detail="partition %d expects %d residuals, got %d"
                % (p, count, len(raw)))
        residuals.extend(raw)
    return residuals


def _legacy_residuals(sf, block, order):
    res = [r["escape"] if isinstance(r, dict) else r for r in sf["residuals"]]
    expected = block - order
    if len(res) != expected:
        raise FrameError(
            "truncated" if len(res) < expected else "length",
            detail="expected %d residuals, got %d" % (expected, len(res)))
    return res


def reconstruct_fixed(warmup, residuals, order):
    """Apply the order-0..4 fixed predictor with strictly signed arithmetic."""
    if order not in COEF:
        raise FrameError("malformed", detail="fixed order must be 0..4")
    if len(warmup) != order:
        raise FrameError(
            "truncated" if len(warmup) < order else "length",
            detail="need %d warm-up samples, got %d" % (order, len(warmup)))
    s = list(warmup)
    coef = COEF[order]
    for r in residuals:
        if not _is_int(r):
            raise FrameError("malformed", detail="non-integer residual")
        pred = sum(c * s[-k - 1] for k, c in enumerate(coef))
        s.append(r + pred)          # Python ints stay signed; no wrap to positive
    return s


def decode_subframe(sf):
    """Return (samples, info) where info carries bit/partition diagnostics."""
    if not isinstance(sf, dict):
        raise FrameError("malformed", detail="subframe is not an object")
    typ = sf.get("type")

    if typ == "constant":
        block = sf.get("block")
        if not _is_int(block) or block < 0:
            raise FrameError("malformed", detail="constant needs a block size")
        value = sf.get("value", 0)
        if not _is_int(value):
            raise FrameError("malformed", detail="constant value must be integer")
        return [value] * block, {"partitions": 0, "bit": None}

    if typ == "verbatim":
        samples = sf.get("samples")
        if not isinstance(samples, list) or \
                not all(_is_int(x) for x in samples):
            raise FrameError("malformed", detail="verbatim needs integer samples")
        block = sf.get("block", len(samples))
        _check_length(samples, block, "verbatim")
        return list(samples), {"partitions": 0, "bit": None}

    if typ != "fixed":
        raise FrameError("malformed", detail="unknown subframe type %r" % (typ,))

    order = sf.get("order")
    if not _is_int(order) or order not in COEF:
        raise FrameError("malformed", detail="fixed order must be 0..4")
    warmup = list(sf.get("warmup", []))
    if len(warmup) != order or not all(_is_int(w) for w in warmup):
        raise FrameError(
            "truncated" if len(warmup) < order else "length",
            detail="need %d integer warm-up samples" % order)

    block = sf.get("block")
    if block is None:
        if "bits" in sf:
            raise FrameError("malformed",
                             detail="bit-packed subframe must declare block")
        if "partitions" in sf:
            block = order + sum(len(p.get("residuals", []))
                                for p in sf["partitions"])
        else:
            block = order + len(sf.get("residuals", []))
    if not _is_int(block) or block < order:
        raise FrameError("malformed", detail="invalid declared block size")

    info = {"partitions": 1, "bit": None}
    if "bits" in sf:
        residuals, bit, nparts = _read_rice_stream(sf, block, order)
        info = {"partitions": nparts, "bit": bit}
    elif "partitions" in sf:
        if not isinstance(sf["partitions"], list) or not sf["partitions"]:
            raise FrameError("malformed",
                             detail="partitions must be a non-empty list")
        residuals = _read_metadata_partitions(sf, block, order)
        info = {"partitions": len(sf["partitions"]), "bit": None}
    else:
        residuals = _legacy_residuals(sf, block, order)

    samples = reconstruct_fixed(warmup, residuals, order)
    _check_length(samples, block, "fixed")
    return samples, info


def restore_stereo(a, b, mode):
    """Invert the inter-channel decorrelation with signed arithmetic shifts."""
    if len(a) != len(b):
        raise FrameError("length", detail="channel blocks differ in length")
    if mode == "independent":
        return [a, b]
    if mode == "left_side":                       # side = L - R
        return [a, [x - y for x, y in zip(a, b)]]
    if mode == "right_side":                      # side = R - L
        return [[x + y for x, y in zip(a, b)], b]
    if mode == "mid_side":                        # mid=a, side=b
        left = [m + ((s + 1) >> 1) for m, s in zip(a, b)]
        right = [m - (s >> 1) for m, s in zip(a, b)]
        return [left, right]
    raise FrameError("malformed", detail="unknown channel_mode %r" % (mode,))


def decode_frame(fr):
    subframes = fr.get("subframes")
    if not isinstance(subframes, list) or not subframes:
        raise FrameError("malformed", detail="frame needs subframes")
    decoded, infos = [], []
    for sf in subframes:
        samples, info = decode_subframe(sf)
        decoded.append(samples)
        infos.append(info)
    mode = fr.get("channel_mode", "independent")
    if len(decoded) == 1:
        channels = decoded
    elif len(decoded) == 2:
        channels = restore_stereo(decoded[0], decoded[1], mode)
    else:
        raise FrameError("malformed", detail="only mono/stereo frames supported")
    return channels, infos


def _error_payload(fr, exc):
    out = {"index": fr.get("index"), "error": exc.error}
    if exc.partition is not None:
        out["partition"] = exc.partition
    if exc.bit is not None:
        out["bit"] = exc.bit
    if exc.detail is not None:
        out["detail"] = exc.detail
    return out


def decode_document(doc):
    if not isinstance(doc, dict) or not isinstance(doc.get("frames"), list):
        raise ValueError("input must be an object with a frames array")
    out = []
    for fr in doc["frames"]:
        try:
            channels, infos = decode_frame(fr)
        except FrameError as exc:
            # Bad frame reports its location only; the next complete frame
            # is still decoded (no swallowed data across the frame boundary).
            out.append(_error_payload(fr, exc))
            continue
        entry = {"index": fr.get("index"), "channels": channels}
        if any(i["bit"] is not None for i in infos):
            entry["bits_consumed"] = [i["bit"] for i in infos]
        if any(i["partitions"] > 1 for i in infos):
            entry["partitions"] = [i["partitions"] for i in infos]
        out.append(entry)
    return {"frames": out}


def main(argv):
    if len(argv) != 3:
        sys.stderr.write("usage: %s input.json output.json\n" % argv[0])
        return 2
    with open(argv[1]) as fh:
        doc = json.load(fh)
    result = decode_document(doc)
    with open(argv[2], "w") as fh:
        json.dump(result, fh, indent=1)
        fh.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
