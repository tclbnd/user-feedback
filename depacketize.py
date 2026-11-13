"""RTP/H.264 depacketizer over offline packet records.

Rebuilds an Annex B stream from a single non-interleaved H.264 RTP flow
(single NAL units, STAP-A aggregation, FU-A fragmentation):

* bounded reorder window over extended 16-bit sequence numbers, with
  deduplication and late-packet accounting;
* 32-bit RTP timestamps extended in protocol (sequence) order, assuming
  adjacent deltas below half the clock period;
* payload validation: STAP-A lengths, FU-A start/end/reserved-bit and
  timestamp/type consistency, empty payloads, illegal NAL types;
* access units assembled by timestamp, marker and contiguous sequence
  numbers -- a unit is emitted only when it is provably complete;
* after any loss or error the reference state is untrusted: output is
  gated until a complete IDR access unit carrying matching in-band
  SPS/PPS (per this capture's convention) restores decoding.

Usage: python3 depacketize.py packets.json recovered.h264 report.json
"""
import argparse
import hashlib
import json
from collections import deque
from pathlib import Path

START_CODE = b"\x00\x00\x00\x01"
SEQ_SPACE = 1 << 16
SEQ_HALF = 1 << 15
TS_SPACE = 1 << 32
TS_HALF = 1 << 31

STAP_A = 24
FU_A = 28
NAL_SPS = 7
NAL_PPS = 8
NAL_IDR = 5


class Depacketizer:
    def __init__(self, window, first_sequence):
        if not 1 <= window <= 64:
            raise ValueError("window must be within 1..64")
        self.window = window
        self.next_expected = first_sequence      # extended seq of next slot to settle
        self.highest = first_sequence - 1        # highest extended seq accepted so far
        self.pending = {}                        # extended seq -> (raw ts, marker, payload)
        self.recent = deque(maxlen=2 * window)   # recently settled seqs (dup filter)
        self.stats = {"max_pending": 0, "lost": 0, "duplicates": 0,
                      "late": 0, "malformed": 0}
        # timestamp extension (protocol order)
        self.last_ts = None
        # access unit assembly
        self.cur_ts = None          # extended timestamp of the open access unit
        self.cur_nals = []
        self.cur_broken = None      # first reason the open unit cannot be trusted
        self.prev_ts = None         # extended timestamp of the last closed unit
        self.loss_between = False   # a loss was settled while no unit was open
        # FU-A reassembly state
        self.fu_active = False
        self.fu_ts = None
        self.fu_type = None
        self.fu_head = None
        self.fu_buf = None
        # decoder gate: cleared by any loss/error, restored by a complete
        # IDR access unit with matching in-band parameter sets
        self.decoding = False
        self.sps = None
        self.pps = None
        # output
        self.units = []
        self.drops = []
        self.annexb = bytearray()

    # ---- sequence numbers and the bounded reorder window ---------------

    def extend_seq(self, seq16):
        """Map a 16-bit sequence number near self.highest onto the full line."""
        ext = (self.highest & ~(SEQ_SPACE - 1)) | seq16
        if ext - self.highest > SEQ_HALF:
            ext -= SEQ_SPACE
        elif self.highest - ext >= SEQ_HALF:
            ext += SEQ_SPACE
        return ext

    def feed(self, packet):
        try:
            seq16 = int(packet["sequence"])
            ts = int(packet["timestamp"])
            marker = bool(packet["marker"])
            payload = bytes.fromhex(packet["hex"])
        except (KeyError, TypeError, ValueError):
            self.stats["malformed"] += 1
            return
        if not 0 <= seq16 < SEQ_SPACE or not 0 <= ts < TS_SPACE:
            self.stats["malformed"] += 1
            return
        ext = self.extend_seq(seq16)
        if ext < self.next_expected:
            if ext in self.recent:
                self.stats["duplicates"] += 1  # copy of an already settled packet
            else:
                self.stats["late"] += 1        # slot already settled: too late
            return
        if ext in self.pending:
            self.stats["duplicates"] += 1
            return
        # Window closure: the new packet must fit inside the window, so
        # settle (as lost) every missing slot that fell behind it.
        while ext - self.next_expected >= self.window:
            self.stats["lost"] += 1
            self.note_loss()
            self.next_expected += 1
            self.drain()
        self.pending[ext] = (ts, marker, payload)
        if ext > self.highest:
            self.highest = ext
        if len(self.pending) > self.stats["max_pending"]:
            self.stats["max_pending"] = len(self.pending)
        self.drain()

    def drain(self):
        """Settle every contiguous slot starting at next_expected."""
        while self.next_expected in self.pending:
            ts, marker, payload = self.pending.pop(self.next_expected)
            self.recent.append(self.next_expected)
            self.next_expected += 1
            self.process(ts, marker, payload)

    def finish(self):
        """EOF: settle everything still pending, then close the last unit."""
        while self.pending:
            if self.next_expected in self.pending:
                self.drain()
            else:
                self.stats["lost"] += 1
                self.note_loss()
                self.next_expected += 1
        self.close_au(marker_seen=False)

    # ---- loss / error propagation ---------------------------------------

    def note_loss(self):
        self.decoding = False                  # reference state no longer trusted
        if self.cur_ts is not None:
            if self.cur_broken is None:
                self.cur_broken = "packet lost inside access unit"
        else:
            self.loss_between = True

    def note_error(self, reason):
        self.decoding = False
        if self.cur_ts is not None:
            if self.cur_broken is None:
                self.cur_broken = reason
        else:
            self.loss_between = True

    def malformed(self, reason):
        self.stats["malformed"] += 1
        self.note_error("malformed packet: " + reason)

    # ---- timestamp extension ---------------------------------------------

    def extend_ts(self, raw):
        if self.last_ts is None:
            self.last_ts = raw                 # first unit keeps its raw value
            return raw
        cand = (self.last_ts & ~(TS_SPACE - 1)) | raw
        if cand - self.last_ts >= TS_HALF:
            cand -= TS_SPACE
        elif self.last_ts - cand >= TS_HALF:
            cand += TS_SPACE
        self.last_ts = cand
        return cand

    # ---- per-packet payload handling (settled, in sequence order) --------

    def process(self, ts_raw, marker, payload):
        ts = self.extend_ts(ts_raw)
        if self.cur_ts is not None and ts != self.cur_ts:
            self.close_au(marker_seen=False)   # timestamp moved on: previous unit ends
        if self.cur_ts is None:
            self.open_au(ts)
        if not payload:
            self.malformed("empty payload")
        else:
            kind = payload[0] & 0x1F
            if 1 <= kind <= 23:
                self.cur_nals.append(payload)                # single NAL unit
            elif kind == STAP_A:
                self.handle_stap_a(payload)
            elif kind == FU_A:
                self.handle_fu_a(ts, payload)
            else:
                self.malformed("unsupported NAL unit type %d" % kind)
        if marker:
            self.close_au(marker_seen=True)

    def handle_stap_a(self, payload):
        pos = 1
        if len(payload) < pos + 2:
            self.malformed("STAP-A without length field")
            return
        while pos < len(payload):
            if pos + 2 > len(payload):
                self.malformed("truncated STAP-A length field")
                return
            size = int.from_bytes(payload[pos:pos + 2], "big")
            pos += 2
            if size == 0 or pos + size > len(payload):
                self.malformed("STAP-A length %d exceeds packet" % size)
                return
            nal = payload[pos:pos + size]
            pos += size
            inner = nal[0] & 0x1F
            if not 1 <= inner <= 23:
                self.malformed("illegal NAL type %d inside STAP-A" % inner)
                return
            self.cur_nals.append(nal)

    def handle_fu_a(self, ts, payload):
        if len(payload) < 3:
            self.malformed("FU-A without fragmented payload")
            return
        indicator, header = payload[0], payload[1]
        start = bool(header & 0x80)
        end = bool(header & 0x40)
        if header & 0x20:
            self.malformed("FU-A reserved bit set")
            return
        if start and end:
            self.malformed("FU-A start and end both set")
            return
        ftype = header & 0x1F
        if not 1 <= ftype <= 23:
            self.malformed("illegal FU-A fragment type %d" % ftype)
            return
        if start:
            if self.fu_active:
                self.note_error("FU-A restart before previous end")
            self.fu_active = True
            self.fu_ts = ts
            self.fu_type = ftype
            self.fu_head = bytes([(indicator & 0xE0) | ftype])
            self.fu_buf = bytearray(payload[2:])
            return
        if not self.fu_active:
            self.malformed("FU-A continuation without start")
            return
        if ts != self.fu_ts or ftype != self.fu_type:
            self.malformed("FU-A timestamp/type mismatch")
            self.fu_reset()
            return
        self.fu_buf += payload[2:]
        if end:
            self.cur_nals.append(self.fu_head + bytes(self.fu_buf))
            self.fu_reset()

    def fu_reset(self):
        self.fu_active = False
        self.fu_ts = None
        self.fu_type = None
        self.fu_head = None
        self.fu_buf = None

    # ---- access unit assembly ---------------------------------------------

    def open_au(self, ts):
        self.cur_ts = ts
        self.cur_nals = []
        self.cur_broken = None
        self.fu_reset()
        if self.loss_between:
            self.cur_broken = "packet lost before access unit"
            self.loss_between = False
        elif self.prev_ts is not None and ts == self.prev_ts:
            self.cur_broken = "timestamp repeated after marker"

    def close_au(self, marker_seen):
        if self.cur_ts is None:
            return
        reason = self.cur_broken
        if self.fu_active:
            reason = reason or "truncated FU-A fragment"
        if not marker_seen:
            reason = reason or "missing marker"
        if reason is None and not self.cur_nals:
            reason = "empty access unit"
        if reason is None:
            self.gate(self.cur_ts, self.cur_nals)
        else:
            self.drops.append({"timestamp": self.cur_ts, "reason": reason,
                               "nals": len(self.cur_nals)})
        self.prev_ts = self.cur_ts
        self.cur_ts = None
        self.cur_nals = []
        self.cur_broken = None
        self.fu_reset()

    # ---- decoder gate ------------------------------------------------------

    def gate(self, ts, nals):
        has_idr = any((n[0] & 0x1F) == NAL_IDR for n in nals)
        sps = pps = None
        for nal in nals:                       # parameter sets count only
            kind = nal[0] & 0x1F               # ahead of the first VCL NAL
            if 1 <= kind <= 5:
                break
            if kind == NAL_SPS and sps is None:
                sps = nal
            elif kind == NAL_PPS and pps is None:
                pps = nal
        if self.decoding:
            if sps is not None and pps is not None:
                self.sps, self.pps = sps, pps  # in-band refresh while decoding
            self.emit(ts, nals)
            return
        if not has_idr:
            self.drop(ts, nals, "non-IDR access unit while reference state is untrusted")
        elif sps is None or pps is None:
            self.drop(ts, nals, "IDR access unit without in-band SPS/PPS")
        elif self.sps is not None and (sps != self.sps or pps != self.pps):
            self.drop(ts, nals, "IDR parameter sets do not match adopted SPS/PPS")
        else:
            self.sps, self.pps = sps, pps
            self.decoding = True
            self.emit(ts, nals)

    # ---- output --------------------------------------------------------------

    def emit(self, ts, nals):
        self.units.append({"timestamp": ts,
                           "nals": [hashlib.sha256(n).hexdigest() for n in nals]})
        for nal in nals:
            self.annexb += START_CODE + nal

    def drop(self, ts, nals, reason):
        self.drops.append({"timestamp": ts, "reason": reason, "nals": len(nals)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packets", type=Path)
    parser.add_argument("video", type=Path)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    data = json.loads(args.packets.read_text())
    dep = Depacketizer(int(data["window"]), int(data["first_sequence"]))
    for packet in data["packets"]:
        dep.feed(packet)
    dep.finish()
    args.video.write_bytes(bytes(dep.annexb))
    report = {"units": dep.units, "stats": dep.stats, "drops": dep.drops}
    args.report.write_text(json.dumps(report) + "\n")


if __name__ == "__main__":
    main()
