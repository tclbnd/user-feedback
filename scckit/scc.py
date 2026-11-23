"""Scenarist SCC caption files: CEA-608 byte pairs, pop-on captions, timing."""
import re
from fractions import Fraction

HEADER = "Scenarist_SCC V1.0"
LINE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})([:;.,])(\d{2})\s+(.*)$")

PAC_ROWS = {0x11: (1, 2), 0x12: (3, 4), 0x15: (5, 6), 0x16: (7, 8), 0x17: (9, 10), 0x10: (11, 11),
            0x13: (12, 13), 0x14: (14, 15)}


class SccError(ValueError):
    pass


def frames_of(hh, mm, ss, ff):
    return ((hh * 60 + mm) * 60 + ss) * 30 + ff


def seconds(frames):
    return str(Fraction(frames * 1001, 30000))


class Channel:
    def __init__(self):
        self.mode = None
        self.pending = []          # non-displayed memory: [row, text]
        self.shown = []            # displayed memory
        self.shown_at = None
        self.captions = []

    def put(self, text):
        if not self.pending:
            self.pending.append([15, "", 0])
        self.pending[-1][1] += text

    def flush(self, now):
        if self.shown and self.shown_at is not None:
            self.captions.append({"start": seconds(self.shown_at), "end": seconds(now),
                                  "rows": [r for r, t, _ in self.shown if t], "columns": [c for _, t, c in self.shown if t],
                                  "text": "\n".join(t for _, t, _ in self.shown if t)})
        self.shown, self.shown_at = [], None

    def control(self, b2, now):
        if b2 == 0x20:
            self.mode = "pop-on"
        elif b2 == 0x2C:
            self.flush(now)
        elif b2 == 0x2F:
            self.flush(now)
            self.shown, self.pending = self.pending, []
            self.shown_at = now if self.shown else None


def parse(text):
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != HEADER:
        raise SccError("not a Scenarist SCC file")
    ch = Channel()
    words = redundant = 0
    prev = None
    end = 0
    for raw in lines[1:]:
        if not raw.strip():
            continue
        m = LINE.match(raw.strip())
        if not m:
            raise SccError("bad line: %r" % raw[:40])
        start = frames_of(int(m[1]), int(m[2]), int(m[3]), int(m[5]))
        for word in m[6].split():
            end = start + 1
            words += 1
            redundant += word.lower() == prev
            prev = word.lower()
            b1, b2 = int(word[:2], 16) & 0x7F, int(word[2:], 16)
            if 0x10 <= b1 <= 0x1F:
                if b2 >= 0x40 and b1 in PAC_ROWS:
                    rows = PAC_ROWS[b1]
                    ch.pending.append([rows[1] if b2 & 0x20 else rows[0], "", (b2 & 0x0F) * 2 if b2 & 0x10 else 0])
                elif b1 == 0x14:
                    ch.control(b2, start)
                continue
            if b1 == 0 and b2 == 0x80:
                continue
            for b in (b1, b2):
                if b >= 0x20:
                    ch.put(chr(b))
    if ch.shown and ch.shown_at is not None:
        ch.captions.append({"start": seconds(ch.shown_at), "end": None,
                            "rows": [r for r, t, _ in ch.shown if t], "columns": [c for _, t, c in ch.shown if t],
                            "text": "\n".join(t for _, t, _ in ch.shown if t)})
    return {"drop_frame": False, "words": words, "bad_parity_bytes": 0, "duration": seconds(end), "redundant_codes": redundant,
            "CC1": ch.captions, "CC2": []}
