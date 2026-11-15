"""Chunked (streaming) driver that is identical to the batch path.

The analyser defines an absolute frame grid from ``fs`` and the signal length
alone, and frame ``k`` always covers samples ``[k*hop, k*hop + frame_len)``.
This module feeds that same grid from arbitrarily sized chunks:

* a persistent ring buffer per track holds only the samples that later frames
  still need (one frame plus a little slack), so memory does not grow with
  the file length;
* a frame is computed as soon as both buffers hold its full absolute range
  (or as soon as the sources are exhausted, in which case its zero padding is
  final -- exactly what the batch path would see);
* frames are computed strictly in order and handed to the same
  :func:`analysis.analyze_pair` post-processing.

Because each frame's inputs are byte-identical regardless of how they were
gathered, the reconstructed tracks are identical to the whole-file run.
:func:`verify_chunk_invariance` asserts that.
"""
from __future__ import annotations

import analysis
import dsp


class TrackBuffer:
    """Holds the tail of a track that later frames still reference."""

    def __init__(self, keep):
        self.keep = max(1, keep)
        self.buf = []
        self.base = 0        # absolute index of buf[0]
        self.total = 0       # total samples seen

    def push(self, samples):
        self.buf.extend(samples)
        self.total += len(samples)
        self._trim()

    def _trim(self):
        drop = self.total - self.keep - self.base
        if drop > 0:
            del self.buf[:drop]
            self.base += drop

    def ready(self, start, length):
        return start + length <= self.total


def chunked_frames(ref, tgt, cfg, chunk):
    """Compute every analysis frame from chunked input.

    Returns ``(specs, plan, mask, smw, win)`` exactly as
    :func:`analysis.analyze_pair` would produce its ``specs``.
    """
    fs = cfg["fs"]
    plan = analysis.frame_plan(fs, max(len(ref), len(tgt)), cfg)
    mask = analysis.analysis_mask(plan, cfg)
    smw = analysis.msc_smooth_bins(plan, cfg)
    win = dsp.hann(plan["frame_len"])
    keep = plan["frame_len"] + plan["hop"] * 2
    rb = TrackBuffer(keep)
    tb = TrackBuffer(keep)
    specs = []
    pos_r = 0
    pos_t = 0
    for start in plan["starts"]:
        while not (rb.ready(start, plan["frame_len"]) and
                   tb.ready(start, plan["frame_len"])):
            if pos_r < len(ref):
                end = min(len(ref), pos_r + chunk)
                rb.push(ref[pos_r:end])
                pos_r = end
            if pos_t < len(tgt):
                end = min(len(tgt), pos_t + chunk)
                tb.push(tgt[pos_t:end])
                pos_t = end
            if pos_r >= len(ref) and pos_t >= len(tgt):
                break
        specs.append(analysis.frame_xspec(ref, tgt, start, plan, cfg,
                                          mask, smw, win))
    return specs, plan, mask, smw, win


def verify_chunk_invariance(ref, tgt, cfg, chunks):
    """Check that every chunk size reproduces the whole-file frames exactly.

    Returns ``True`` when all requested chunk sizes agree with the batch
    analysis frame for frame.
    """
    base = analysis.analyze_pair(ref, tgt, cfg)["specs"]
    for chunk in chunks:
        got, _plan, _m, _s, _w = chunked_frames(ref, tgt, cfg, chunk)
        if len(got) != len(base):
            return False
        for a, b in zip(base, got):
            if abs(a["overlap"] - b["overlap"]) > 1e-15:
                return False
            if abs(a["coh"] - b["coh"]) > 1e-12:
                return False
            if abs(a["ref_db"] - b["ref_db"]) > 1e-12:
                return False
            if a["na"] != b["na"] or a["nb"] != b["nb"]:
                return False
            for i in range(len(a["spec"])):
                if a["spec"][i] != b["spec"][i]:
                    return False
            for i in range(len(a["cospec"])):
                if a["cospec"][i] != b["cospec"][i]:
                    return False
    return True

