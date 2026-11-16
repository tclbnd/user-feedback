#!/usr/bin/env python3
"""Generate sample_fixture.json exercising every supported subframe path."""
import json
import os

import flac
from test_flac import (ref_predict, encode_rice_partitions,
                       encode_escape_partition, signal_ramp, signal_impulse)

HERE = os.path.dirname(os.path.abspath(__file__))


def build():
    doc = {"frames": []}

    # 1. mono constant silence
    doc["frames"].append({
        "index": 0, "channel_mode": "independent",
        "subframes": [{"type": "constant", "value": 0, "block": 8}]})

    # 2. mono verbatim
    doc["frames"].append({
        "index": 1, "channel_mode": "independent",
        "subframes": [{"type": "verbatim",
                       "samples": [5, -5, 100, -100]}]})

    # 3. mono fixed order-2, bit-packed Rice, 2 partitions
    samples = signal_ramp(16)
    warm, res = ref_predict(samples, 2)
    hb, nb = encode_rice_partitions(res, 2, 16, 1)
    doc["frames"].append({
        "index": 2, "channel_mode": "independent",
        "subframes": [{"type": "fixed", "order": 2, "block": 16,
                       "warmup": warm,
                       "bits": {"data": hb, "length": nb},
                       "partition_order": 1, "bps": 16}]})

    # 4. stereo left_side, legacy residuals with an escape item
    block = 6
    left = [10, 20, 30, 40, 50, 60]
    right = [1, 2, 3, 4, 32000, 6]
    side = [l - r for l, r in zip(left, right)]
    wl, rl = ref_predict(left, 1)
    ws, rs = ref_predict(side, 1)
    rs[3] = {"escape": rs[3]}          # force an escaped raw residual
    doc["frames"].append({
        "index": 3, "channel_mode": "left_side",
        "subframes": [
            {"type": "fixed", "order": 1, "block": block,
             "warmup": wl, "residuals": rl},
            {"type": "fixed", "order": 1, "block": block,
             "warmup": ws, "residuals": rs}]})

    # 5. stereo mid_side incl. negative side; bit-packed escape partition
    block = 32
    left = signal_impulse(block, 9000)
    right = [i * 300 - 4500 for i in range(block)]
    side = [l - r for l, r in zip(left, right)]
    mid = [(l + r) // 2 for l, r in zip(left, right)]
    om, rm = ref_predict(mid, 1)
    os_, rs_ = ref_predict(side, 1)
    hbm, nbm = encode_rice_partitions(rm, 1, block, 2)
    hbs, nbs = encode_escape_partition(rs_, 1, block, 2)
    doc["frames"].append({
        "index": 4, "channel_mode": "mid_side",
        "subframes": [
            {"type": "fixed", "order": 1, "block": block, "warmup": om,
             "bits": {"data": hbm, "length": nbm},
             "partition_order": 2, "bps": 16},
            {"type": "fixed", "order": 1, "block": block, "warmup": os_,
             "bits": {"data": hbs, "length": nbs},
             "partition_order": 2, "bps": 16}]})

    # 6. truncated frame: declared block larger than the residual list
    doc["frames"].append({
        "index": 5, "channel_mode": "independent",
        "subframes": [{"type": "fixed", "order": 1, "block": 10,
                       "warmup": [1], "residuals": [2, 3]}]})

    # 7. the next complete frame must still decode (right_side verbatim)
    doc["frames"].append({
        "index": 6, "channel_mode": "right_side",
        "subframes": [
            {"type": "verbatim", "samples": [7, 8, 9]},
            {"type": "verbatim", "samples": [10, 20, 30]}]})

    return doc


def main():
    doc = build()
    out_path = os.path.join(HERE, "sample_fixture.json")
    with open(out_path, "w") as fh:
        json.dump(doc, fh, indent=1)
    # Self-check: the same document must decode without error except frame 5.
    result = flac.decode_document(doc)
    status = []
    for fr in result["frames"]:
        status.append((fr["index"],
                       fr.get("error", "ok") if "error" in fr else "ok"))
    print("wrote", out_path)
    print("decode status:", status)


if __name__ == "__main__":
    main()
