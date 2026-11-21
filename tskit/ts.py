"""MPEG-2 transport stream (ISO/IEC 13818-1): PSI, PCR, PES timing."""
from fractions import Fraction

STREAM_TYPES = {0x01: "mpeg1_video", 0x02: "mpeg2_video", 0x03: "mpeg1_audio", 0x04: "mpeg2_audio", 0x06: "private",
                0x0F: "aac_adts", 0x11: "aac_latm", 0x1B: "h264", 0x24: "hevc", 0x81: "ac3", 0x87: "eac3"}

PACKET_SIZES = (188, 192, 204)   # plain, Blu-ray (4-byte prefix), DVB (16-byte parity)
PTS_MOD = 1 << 33


class TsError(ValueError):
    pass


def _crc_table():
    out = []
    for i in range(256):
        c = i << 24
        for _ in range(8):
            c = ((c << 1) ^ 0x04C11DB7) if c & 0x80000000 else c << 1
        out.append(c & 0xFFFFFFFF)
    return out


_CRC_TABLE = _crc_table()


def crc32(data):
    """CRC-32/MPEG-2 as used by PSI sections (a valid section hashes to 0)."""
    c = 0xFFFFFFFF
    for b in data:
        c = ((c << 8) & 0xFFFFFFFF) ^ _CRC_TABLE[(c >> 24) ^ b]
    return c


def pts(b):
    return ((b[0] >> 1) & 7) << 30 | b[1] << 22 | (b[2] >> 1) << 15 | b[3] << 7 | b[4] >> 1


def _resync(data, start, size):
    """Find the next position after `start` that looks like a packet start.

    A candidate is accepted when its sync byte is followed by a sync byte one
    packet later (or when it is the last packet of the file).  Searching every
    byte recovers captures where a chunk was dropped or inserted.
    """
    for pos in range(start + 1, len(data) - 187):
        if data[pos] != 0x47:
            continue
        nxt = pos + size
        if nxt + 188 > len(data) or data[nxt] == 0x47:
            return pos
    return None


def _packets(data, size, off):
    """Yield the offset of every TS packet in `data`, resyncing after damage."""
    pos = off
    while pos + 188 <= len(data):
        if data[pos] != 0x47 or (data[pos + 3] >> 4) & 3 == 0:
            # Lost sync, or adaptation_field_control == '00' (reserved).
            nxt = _resync(data, pos, size)
            if nxt is None:
                return
            pos = nxt
            continue
        yield pos
        pos += size


def _find_layout(data):
    """Return (packet_size, sync_offset) for the 188/192/204 packet variants.

    192-byte packets (Blu-ray) start with a 4-byte timestamp prefix; 204-byte
    packets (DVB) end with 16 bytes of Reed-Solomon parity.  Candidates are
    first ranked by the length of an uninterrupted run of packets; the few
    close contenders are then counted over the whole file (with resync).
    """
    if len(data) < 188:
        raise TsError("lost sync at byte 0")
    probe = data[:min(len(data), 65536)]
    scored = []
    for size in PACKET_SIZES:
        canonical = 4 if size == 192 else 0
        for off in range(min(size, len(probe) - 187)):
            if probe[off] != 0x47:
                continue
            n, pos = 0, off
            while pos + 188 <= len(probe) and probe[pos] == 0x47:
                if (probe[pos + 3] >> 4) & 3 == 0:
                    break                   # adaptation_field_control == '00'
                n += 1
                pos += size
                if n == 512:
                    break
            if n:
                scored.append((n, 1 if off == canonical else 0, size, off))
    if not scored:
        raise TsError("lost sync at byte 0")
    scored.sort(reverse=True)
    # Only layouts that explain a comparable part of the probe deserve a full
    # pass; a short run from a chance sync byte never wins over a real stream.
    contenders = [c for c in scored[:2] if c[0] * 2 >= scored[0][0]]
    best = None
    for _, _, size, off in contenders:
        n = sum(1 for _ in _packets(data, size, off))
        if best is None or n > best[0]:
            best = (n, size, off)
    return best[1], best[2]


class Section:
    """Reassembles PSI sections (which may span several TS packets)."""

    def __init__(self):
        self.buf = b""

    def _drain(self):
        out = []
        while True:
            buf = self.buf
            if len(buf) < 3:
                return out
            if buf[0] == 0xFF:          # stuffing after the last section
                self.buf = b""
                return out
            total = 3 + (((buf[1] & 0x0F) << 8) | buf[2])
            minimum = 12 if buf[1] & 0x80 else 3    # continues with 4-byte CRC
            if total < minimum or total > 4096:
                self.buf = b""
                return out
            if len(buf) < total:
                return out
            out.append(buf[:total])
            self.buf = buf[total:]

    def feed(self, payload, pusi):
        """Return the complete sections carried by this packet."""
        out = []
        if pusi:
            if len(payload) < 1:
                return out
            pointer = payload[0]
            if pointer > len(payload) - 1:
                self.buf = b""
                return out
            tail = payload[1:1 + pointer]
            if self.buf and tail:
                # Finish the section that started in an earlier packet.
                self.buf += tail
                out.extend(self._drain())
            # Whatever is still buffered is an unfinished (broken) section;
            # a new one starts right after the pointer field.
            self.buf = bytes(payload[1 + pointer:])
            out.extend(self._drain())
        elif self.buf:
            self.buf += payload
            out.extend(self._drain())
        return out


def _mpeg1_pes_pts(buf):
    """PTS from an MPEG-1 style PES header (no PES_header_data_length)."""
    i = 6
    while i < len(buf) and buf[i] == 0xFF:        # stuffing bytes
        i += 1
    if i + 2 > len(buf) or buf[i] & 0xC0 != 0x40:  # '01' STD buffer field
        return None
    i += 2
    if i + 5 <= len(buf) and buf[i] >> 4 in (2, 3):
        return pts(buf[i:i + 5])
    return None


def _pes_pts(buf):
    """Return (complete, pts-or-None) for a PES header buffer."""
    if len(buf) < 7:
        return False, None
    if buf[:3] != b"\0\0\1":
        return True, None
    stream_id = buf[3]
    if stream_id in (0xBC, 0xBE, 0xBF, 0xF0, 0xF1, 0xF2, 0xF8, 0xFF):
        return True, None                     # no optional PES header
    if buf[6] & 0xC0 != 0x80:                 # MPEG-1 style PES header
        return True, _mpeg1_pes_pts(buf)
    if len(buf) < 9:
        return False, None
    header_len = buf[8]
    if len(buf) < 9 + header_len:
        return False, None
    # PTS_DTS_flags: '10' (PTS only) or '11' (PTS and DTS); both carry the PTS
    # first, so B-frame streams with a DTS must not be skipped.
    if not buf[7] & 0x80 or header_len < 5:
        return True, None                     # PTS_DTS_flags == '00'
    return True, pts(buf[9:14])


class Pes:
    """Collects the (possibly split) header at the start of a PES packet."""

    def __init__(self):
        self.buf = None

    def feed(self, payload, pusi):
        """Return (started, pts-or-None)."""
        if pusi:
            if len(payload) < 3 or payload[:3] != b"\0\0\1":
                self.buf = None
                return False, None
            self.buf = bytes(payload)
            started = True
        elif self.buf is not None:
            self.buf += payload
            started = False
        else:
            return False, None
        complete, value = _pes_pts(self.buf)
        if complete or len(self.buf) > 4096:      # malformed header
            self.buf = None
        return started, value


def _stream(s):
    out = {k: v for k, v in s.items() if k != "span"}
    out["duration"] = str(Fraction(s["span"], 90000))
    return out


def _update_pts(s, value):
    if s["first_pts"] is None:
        s["first_pts"] = value
    s["last_pts"] = value
    # PTS is a 33-bit counter that wraps at 2**33.  The elapsed time is the
    # forward distance from the first to the last PTS taken modulo 2**33, so a
    # recording crossing the wrap gives a short positive span rather than a
    # large negative one.  (first_pts/last_pts stay raw values.)
    s["span"] = (s["last_pts"] - s["first_pts"]) % PTS_MOD


def scan(data):
    size, off = _find_layout(data)
    pmt_pids, programs, streams = {}, {}, {}
    sections = {0: Section()}
    pes_state = {}
    last_cc, last_packet = {}, {}
    pcr_pids = {}
    stats = {"packets": 0, "tei": 0, "null": 0, "bad_sections": 0, "cc_errors": 0, "pcr": 0}

    for pos in _packets(data, size, off):
        p = data[pos:pos + 188]
        stats["packets"] += 1
        pusi = bool(p[1] & 0x40)
        pid = ((p[1] & 0x1F) << 8) | p[2]
        afc, cc = (p[3] >> 4) & 3, p[3] & 15
        if p[1] & 0x80:                       # transport_error_indicator
            # The packet is unreliable: count it but ignore its contents.  Its
            # continuity counter cannot be trusted, so it does not take part
            # in the CC chain either.
            stats["tei"] += 1
            last_cc.pop(pid, None)
            last_packet.pop(pid, None)
            continue
        if pid == 0x1FFF:
            stats["null"] += 1
            continue

        payload_at = 4
        disc = False
        if afc & 2:
            alen = p[4]
            if alen:
                flags = p[5]
                disc = bool(flags & 0x80)     # discontinuity_indicator
                if flags & 0x10 and alen >= 7:
                    stats["pcr"] += 1
                    pcr_pids[pid] = pcr_pids.get(pid, 0) + 1
            payload_at = 5 + alen

        # Only packets that actually carry payload advance the continuity
        # counter: adaptation-field-only packets (e.g. PCR packets) keep the
        # counter of the previous payload packet.
        has_payload = bool(afc & 1) and payload_at < 188
        duplicate = False
        if has_payload:
            prev = last_cc.get(pid)
            if disc:
                # The discontinuity_indicator announces a CC jump.
                last_cc.pop(pid, None)
                last_packet.pop(pid, None)
            elif prev is not None:
                if cc == prev:
                    # Two packets may repeat a CC only if they repeat the
                    # packet exactly (duplicate packet).
                    if bytes(p) == last_packet.get(pid):
                        duplicate = True
                    else:
                        stats["cc_errors"] += 1
                        if pid in streams:
                            streams[pid]["cc_errors"] += 1
                elif cc != (prev + 1) & 15:
                    stats["cc_errors"] += 1
                    if pid in streams:
                        streams[pid]["cc_errors"] += 1
            last_cc[pid] = cc
            last_packet[pid] = bytes(p)

        if duplicate:
            # A duplicate packet still carries payload, but it continues the
            # PES (or repeats a whole one) and must not be parsed again.
            s = streams.get(pid)
            if s is not None:
                s["packets"] += 1
            continue

        if has_payload and pid in sections:
            for sec in sections[pid].feed(p[payload_at:], pusi):
                if sec[1] & 0x80 and crc32(sec) != 0:
                    stats["bad_sections"] += 1
                    continue
                if sec[0] == 0x00 and pid == 0:
                    _pat(sec, pmt_pids, programs, sections)
                elif sec[0] == 0x02 and pid in pmt_pids:
                    _pmt(sec, programs, streams, pes_state)
        elif has_payload:
            s = streams.get(pid)
            if s is not None:
                s["packets"] += 1
                started, value = pes_state[pid].feed(p[payload_at:], pusi)
                if started:
                    s["pes"] += 1
                if value is not None:
                    _update_pts(s, value)

    out_programs = []
    for prog in sorted(programs):
        pr = programs[prog]
        out_programs.append({"program": prog, "pmt_pid": pr["pmt_pid"], "pcr_pid": pr["pcr_pid"],
                             "pcr_count": pcr_pids.get(pr["pcr_pid"], 0),
                             "streams": [_stream(streams[e]) for e in pr["streams"]]})
    return {"packet_size": size, **stats, "programs": out_programs}


def _pat(sec, pmt_pids, programs, sections):
    end = len(sec) - 4                 # strip CRC_32
    i = 8
    while i + 4 <= end:
        prog = (sec[i] << 8) | sec[i + 1]
        ppid = ((sec[i + 2] & 0x1F) << 8) | sec[i + 3]
        if prog != 0 and ppid != 0x1FFF:      # skip network PID / stuffing
            pmt_pids[ppid] = prog
            if ppid not in sections:
                sections[ppid] = Section()
            pr = programs.setdefault(prog, {"program": prog, "pmt_pid": ppid,
                                            "pcr_pid": None, "streams": []})
            pr["pmt_pid"] = ppid
        i += 4


def _pmt(sec, programs, streams, pes_state):
    if len(sec) < 16:
        return
    prog = (sec[3] << 8) | sec[4]
    pr = programs.get(prog)
    if pr is None:
        return
    pr["pcr_pid"] = ((sec[8] & 0x1F) << 8) | sec[9]
    info_len = ((sec[10] & 0x0F) << 8) | sec[11]
    end = len(sec) - 4
    i = 12 + info_len
    es = []
    while i + 5 <= end:
        st = sec[i]
        epid = ((sec[i + 1] & 0x1F) << 8) | sec[i + 2]
        es_len = ((sec[i + 3] & 0x0F) << 8) | sec[i + 4]
        es.append(epid)
        if epid not in streams:
            streams[epid] = {"pid": epid, "stream_type": STREAM_TYPES.get(st, "0x%02x" % st), "packets": 0,
                             "pes": 0, "first_pts": None, "last_pts": None, "span": 0, "cc_errors": 0}
            pes_state[epid] = Pes()
        i += 5 + es_len
    pr["streams"] = es


