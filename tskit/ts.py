"""MPEG-2 transport stream (ISO/IEC 13818-1): PSI, PCR, PES timing."""
from fractions import Fraction

STREAM_TYPES = {0x01: "mpeg1_video", 0x02: "mpeg2_video", 0x03: "mpeg1_audio", 0x04: "mpeg2_audio", 0x06: "private",
                0x0F: "aac_adts", 0x11: "aac_latm", 0x1B: "h264", 0x24: "hevc", 0x81: "ac3", 0x87: "eac3"}


class TsError(ValueError):
    pass




def pts(b):
    return ((b[0] >> 1) & 7) << 30 | b[1] << 22 | (b[2] >> 1) << 15 | b[3] << 7 | b[4] >> 1


class Section:
    def feed(self, payload, pusi):
        """Yield the section that starts in this packet."""
        if pusi:
            pointer = payload[0]
            yield bytes(payload[1 + pointer:])


def _stream(s):
    out = {k: v for k, v in s.items() if k != "span"}
    out["duration"] = str(Fraction(s["span"], 90000))
    return out


def scan(data):
    size, off = 188, 0
    pmt_pids, streams, pcr_pids = {}, {}, {}
    sections = {0: Section()}
    last_cc = {}
    stats = {"packets": 0, "tei": 0, "null": 0, "bad_sections": 0, "cc_errors": 0, "pcr": 0}
    programs = {}
    for start in range(0, len(data) - size + 1, size):
        p = data[start + off:start + off + 188]
        if p[0] != 0x47:
            raise TsError("lost sync at byte %d" % start)
        stats["packets"] += 1
        pusi = bool(p[1] & 0x40)
        pid = ((p[1] & 0x1F) << 8) | p[2]
        afc, cc = (p[3] >> 4) & 3, p[3] & 15
        if pid == 0x1FFF:
            stats["null"] += 1
            continue
        pos = 4
        if afc & 2:
            alen = p[4]
            if alen:
                flags = p[5]
                if flags & 0x10 and alen >= 7:
                    stats["pcr"] += 1
                    pcr_pids[pid] = pcr_pids.get(pid, 0) + 1
            pos = 5 + alen
        prev = last_cc.get(pid)
        if prev is not None and cc != (prev + 1) & 15:
            stats["cc_errors"] += 1
            if pid in streams:
                streams[pid]["cc_errors"] += 1
        last_cc[pid] = cc
        if not afc & 1:
            continue
        payload = p[pos:]
        if pid in sections:
            for sec in sections[pid].feed(payload, pusi):
                if sec[0] == 0x00 and pid == 0:
                    end = 3 + (((sec[1] & 0x0F) << 8) | sec[2]) - 4
                    for i in range(8, end, 4):
                        prog = (sec[i] << 8) | sec[i + 1]
                        ppid = ((sec[i + 2] & 0x1F) << 8) | sec[i + 3]
                        if ppid not in pmt_pids:
                            pmt_pids[ppid] = prog
                            sections[ppid] = Section()
                elif sec[0] == 0x02 and pid in pmt_pids:
                    prog = (sec[3] << 8) | sec[4]
                    pcr_pid = ((sec[8] & 0x1F) << 8) | sec[9]
                    info_len = ((sec[10] & 0x0F) << 8) | sec[11]
                    i = 12 + info_len
                    end = 3 + (((sec[1] & 0x0F) << 8) | sec[2]) - 4
                    es = []
                    while i + 5 <= min(end, len(sec)):
                        st = sec[i]
                        epid = ((sec[i + 1] & 0x1F) << 8) | sec[i + 2]
                        es_len = ((sec[i + 3] & 0x0F) << 8) | sec[i + 4]
                        es.append(epid)
                        if epid not in streams:
                            streams[epid] = {"pid": epid, "stream_type": STREAM_TYPES.get(st, "0x%02x" % st), "packets": 0,
                                             "pes": 0, "first_pts": None, "last_pts": None, "span": 0, "cc_errors": 0}
                        i += 5 + es_len
                    programs[prog] = {"program": prog, "pmt_pid": pid, "pcr_pid": pcr_pid, "streams": es}
            continue
        s = streams.get(pid)
        if s is None:
            continue
        s["packets"] += 1
        if pusi and payload[:3] == b"\0\0\1":
            s["pes"] += 1
            if payload[7] == 0x80:
                v = pts(payload[9:14])
                if s["first_pts"] is None:
                    s["first_pts"] = v
                s["last_pts"] = v
                s["span"] = v - s["first_pts"]
    out_programs = []
    for prog in sorted(programs):
        pr = programs[prog]
        out_programs.append({"program": prog, "pmt_pid": pr["pmt_pid"], "pcr_pid": pr["pcr_pid"],
                             "pcr_count": pcr_pids.get(pr["pcr_pid"], 0),
                             "streams": [_stream(streams[e]) for e in pr["streams"]]})
    return {"packet_size": size, **stats, "programs": out_programs}
