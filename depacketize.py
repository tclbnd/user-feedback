"""RTP/H.264 depacketizer for offline packet captures.

Reads a JSON capture {window, first_sequence, packets[]} and rebuilds
Annex B video:

* 16-bit RTP sequence numbers and 32-bit RTP timestamps are extended
  past their wraparounds; packets are reordered inside a bounded
  window.  The window is sealed (missing positions settled as lost)
  when a packet arrives more than ``window`` positions ahead of the
  next expected sequence number, and at EOF.  Duplicates, late, lost
  and malformed packets are accounted separately and the pending
  queue never exceeds the window.
* Single-NAL, STAP-A and FU-A payloads are reassembled and grouped
  into access units by timestamp, marker and protocol order.
  Aggregation lengths, fragment start/end and parameter sets are
  validated; any access unit that cannot be proven complete is
  dropped with a recorded reason.
* After any loss or corruption the decoder reference state is
  considered untrusted; output resumes only at a complete IDR access
  unit that carries matching SPS/PPS before the IDR VCL (the local
  recovery convention).  The first output unit follows the same rule.

Usage: python3 depacketize.py packets.json recovered.h264 report.json
"""
import argparse
import hashlib
import json
from collections import deque
from pathlib import Path

START = b"\x00\x00\x00\x01"
SEQ_MOD = 1 << 16
TS_MOD = 1 << 32
STAP_A = 24
FU_A = 28


class Stats:
    def __init__(self):
        self.max_pending = 0
        self.lost = 0
        self.duplicates = 0
        self.late = 0
        self.malformed = 0

    def as_dict(self):
        return {
            "max_pending": self.max_pending,
            "lost": self.lost,
            "duplicates": self.duplicates,
            "late": self.late,
            "malformed": self.malformed,
        }


class SeqUnwrapper:
    """Extend 16-bit sequence numbers; extended(first_sequence) is
    first_sequence itself and later numbers stay within half a cycle
    of the highest value seen so far."""

    def __init__(self, first):
        self.first = first
        self.highest = None

    def unwrap(self, seq):
        if self.highest is None:
            ext = self.first + (seq - self.first) % SEQ_MOD
        else:
            delta = (seq - self.highest % SEQ_MOD) % SEQ_MOD
            if delta >= SEQ_MOD // 2:
                delta -= SEQ_MOD
            ext = self.highest + delta
        if self.highest is None or ext > self.highest:
            self.highest = ext
        return ext


class TsUnwrapper:
    """Extend 32-bit RTP timestamps in consumption order.  The first
    extended timestamp is the raw 32-bit value; later ones pick the
    cycle that keeps the step below half a period."""

    def __init__(self):
        self.prev = None

    def unwrap(self, ts):
        if self.prev is None:
            self.prev = ts
            return ts
        delta = (ts - self.prev % TS_MOD) % TS_MOD
        if delta >= TS_MOD // 2:
            delta -= TS_MOD
        self.prev += delta
        return self.prev


class ReorderBuffer:
    """Bounded reorder window over extended sequence numbers.

    Pending packets always lie in [next_expected, next_expected +
    window), so the queue never exceeds ``window`` entries.  Positions
    behind the window are settled: present packets are emitted in
    order, missing ones are counted lost and reported as gaps.
    """

    def __init__(self, first, window, stats, on_packet, on_gap):
        self.next_expected = first
        self.window = window
        self.stats = stats
        self.on_packet = on_packet
        self.on_gap = on_gap
        self.pending = {}
        self.retired = deque()
        self.retired_set = set()
        self.retired_max = max(4 * window, 32)

    def _remember(self, ext):
        self.retired.append(ext)
        self.retired_set.add(ext)
        while len(self.retired) > self.retired_max:
            self.retired_set.discard(self.retired.popleft())

    def _emit(self, ext):
        packet = self.pending.pop(ext)
        self._remember(ext)
        self.on_packet(packet)

    def _settle_below(self, limit):
        while self.next_expected < limit:
            if self.next_expected in self.pending:
                self._emit(self.next_expected)
            else:
                self.stats.lost += 1
                self.on_gap(self.next_expected)
            self.next_expected += 1

    def _drain(self):
        while self.next_expected in self.pending:
            self._emit(self.next_expected)
            self.next_expected += 1

    def push(self, ext, packet):
        if ext in self.pending or ext in self.retired_set:
            self.stats.duplicates += 1
            return
        if ext < self.next_expected:
            self.stats.late += 1
            return
        if ext - self.next_expected >= self.window:
            self._settle_below(ext - self.window + 1)
        self.pending[ext] = packet
        self.stats.max_pending = max(self.stats.max_pending, len(self.pending))
        self._drain()

    def finish(self):
        if self.pending:
            self._settle_below(max(self.pending) + 1)


def _rbsp(nal):
    """Strip emulation prevention bytes, skipping the NAL header."""
    out = bytearray()
    zeros = 0
    for byte in nal[1:]:
        if zeros >= 2 and byte == 3:
            zeros = 0
            continue
        out.append(byte)
        zeros = zeros + 1 if byte == 0 else 0
    return bytes(out)


class _Bits:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def u(self, n):
        value = 0
        for _ in range(n):
            byte = self.data[self.pos // 8]
            value = value << 1 | (byte >> (7 - self.pos % 8)) & 1
            self.pos += 1
        return value

    def ue(self):
        zeros = 0
        while self.u(1) == 0:
            zeros += 1
            if zeros > 31:
                raise ValueError("exp-golomb code too long")
        return (1 << zeros) - 1 + self.u(zeros)


def _sps_id(nal):
    bits = _Bits(_rbsp(nal))
    bits.u(8)  # profile_idc
    bits.u(8)  # constraint flags + reserved
    bits.u(8)  # level_idc
    return bits.ue()  # seq_parameter_set_id


def _pps_ids(nal):
    bits = _Bits(_rbsp(nal))
    return bits.ue(), bits.ue()  # pic_parameter_set_id, seq_parameter_set_id


def _is_recovery_idr(nals):
    """True when the unit carries SPS and a matching PPS before its
    first IDR VCL NAL (the local recovery convention)."""
    sps = None
    pps_match = False
    for nal in nals:
        kind = nal[0] & 0x1F
        if kind == 5:
            return sps is not None and pps_match
        try:
            if kind == 7:
                sps = _sps_id(nal)
            elif kind == 8 and sps is not None:
                pps_match = _pps_ids(nal)[1] == sps
        except (IndexError, ValueError):
            return False
    return False


class AccessUnit:
    def __init__(self, ts_raw, ts_ext):
        self.ts_raw = ts_raw
        self.ts_ext = ts_ext
        self.nals = []
        self.fu = None
        self.broken = False
        self.reasons = []


class Depacketizer:
    """Consumes packets and gaps in extended-sequence order, builds
    access units and applies the recovery gate."""

    def __init__(self, stats, out):
        self.stats = stats
        self.out = out
        self.ts = TsUnwrapper()
        self.cur = None
        self.last_closed_ts = None
        self.trusted = False
        self.units = []
        self.dropped = []

    # -- stream events -------------------------------------------------
    def on_packet(self, packet):
        try:
            ts_raw = int(packet["timestamp"]) % TS_MOD
            marker = bool(packet.get("marker", False))
            payload = bytes.fromhex(packet["hex"])
        except (KeyError, TypeError, ValueError):
            self.stats.malformed += 1
            self.trusted = False
            if self.cur is not None:
                self._break("unreadable packet record")
            return
        ts_ext = self.ts.unwrap(ts_raw)
        if self.cur is not None and ts_raw != self.cur.ts_raw:
            self._close(marked=False)
        if self.cur is None:
            self.cur = AccessUnit(ts_raw, ts_ext)
            if ts_raw == self.last_closed_ts:
                self._break("timestamp reused across access-unit boundary")
        self._payload(payload)
        if marker:
            self._close(marked=True)

    def on_gap(self, ext):
        if self.cur is not None:
            self.cur.fu = None
            self._break("packet lost inside access unit")
        self.trusted = False

    def finish(self):
        if self.cur is not None:
            self._close(marked=False)

    # -- access-unit lifecycle -----------------------------------------
    def _break_on(self, unit, reason):
        unit.broken = True
        if reason not in unit.reasons:
            unit.reasons.append(reason)
        self.trusted = False

    def _break(self, reason):
        self._break_on(self.cur, reason)

    def _malformed(self, reason):
        self.stats.malformed += 1
        self._break("malformed: " + reason)

    def _close(self, marked):
        unit, self.cur = self.cur, None
        self.last_closed_ts = unit.ts_raw
        if unit.fu is not None:
            unit.fu = None
            self._break_on(unit, "FU-A fragment never completed")
        if not marked:
            self._break_on(unit, "marker missing: access unit truncated")
        if not unit.nals and not unit.broken:
            self._break_on(unit, "empty access unit")
        if unit.broken:
            self.dropped.append({
                "timestamp": unit.ts_ext,
                "reason": "; ".join(unit.reasons),
            })
        elif self.trusted:
            self._emit(unit)
        elif _is_recovery_idr(unit.nals):
            self._emit(unit)
            self.trusted = True
        else:
            self.dropped.append({
                "timestamp": unit.ts_ext,
                "reason": "reference state untrusted: waiting for complete "
                          "IDR with matching SPS/PPS",
            })

    def _emit(self, unit):
        for nal in unit.nals:
            self.out.write(START + nal)
        self.units.append({
            "timestamp": unit.ts_ext,
            "nals": [hashlib.sha256(nal).hexdigest() for nal in unit.nals],
        })

    # -- payload reassembly --------------------------------------------
    def _payload(self, data):
        if not data:
            self._malformed("empty payload")
            return
        kind = data[0] & 0x1F
        if 1 <= kind <= 23:
            self.cur.nals.append(data)
        elif kind == STAP_A:
            self._stap_a(data)
        elif kind == FU_A:
            self._fu_a(data)
        else:
            self._malformed("unsupported nal unit type %d" % kind)

    def _stap_a(self, data):
        body = data[1:]
        if not body:
            self._malformed("STAP-A: empty aggregation")
            return
        nals = []
        while body:
            if len(body) < 2:
                self._malformed("STAP-A: truncated length field")
                return
            size = int.from_bytes(body[:2], "big")
            body = body[2:]
            if size == 0 or size > len(body):
                self._malformed("STAP-A: nal length %d exceeds aggregation" % size)
                return
            nal, body = body[:size], body[size:]
            if not 1 <= (nal[0] & 0x1F) <= 23:
                self._malformed("STAP-A: invalid inner nal type")
                return
            nals.append(nal)
        self.cur.nals.extend(nals)

    def _fu_a(self, data):
        if len(data) < 2:
            self._malformed("FU-A: truncated header")
            return
        indicator, header = data[0], data[1]
        start, end = header >> 7, (header >> 6) & 1
        ftype = header & 0x1F
        if (header >> 5) & 1:
            self._malformed("FU-A: reserved bit set")
            return
        if start and end:
            self._malformed("FU-A: start and end both set")
            return
        if not 1 <= ftype <= 23:
            self._malformed("FU-A: invalid nal type %d" % ftype)
            return
        fu = self.cur.fu
        if start:
            if fu is not None:
                self._break("FU-A: restarted before previous fragment ended")
            self.cur.fu = {"nri": indicator & 0xE0, "type": ftype,
                           "parts": [data[2:]]}
            return
        if fu is None:
            self._malformed("FU-A: fragment without start")
            return
        if (indicator & 0xE0) != fu["nri"] or ftype != fu["type"]:
            self._malformed("FU-A: indicator or type changed mid-fragment")
            return
        fu["parts"].append(data[2:])
        if end:
            nal = bytes([fu["nri"] | fu["type"]]) + b"".join(fu["parts"])
            self.cur.nals.append(nal)
            self.cur.fu = None


def depacketize(data, out):
    window = int(data["window"])
    if not 1 <= window <= 64:
        raise ValueError("window must be in 1..64")
    first = int(data["first_sequence"]) % SEQ_MOD
    stats = Stats()
    dep = Depacketizer(stats, out)
    seqs = SeqUnwrapper(first)
    buf = ReorderBuffer(first, window, stats, dep.on_packet, dep.on_gap)
    for packet in data["packets"]:
        try:
            seq = int(packet["sequence"]) % SEQ_MOD
        except (KeyError, TypeError, ValueError):
            stats.malformed += 1
            continue
        buf.push(seqs.unwrap(seq), packet)
    buf.finish()
    dep.finish()
    return {"units": dep.units, "dropped": dep.dropped, "stats": stats.as_dict()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packets", type=Path)
    parser.add_argument("video", type=Path)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    data = json.loads(args.packets.read_text())
    with args.video.open("wb") as out:
        report = depacketize(data, out)
    args.report.write_text(json.dumps(report) + "\n")


if __name__ == "__main__":
    main()
