#!/usr/bin/env python3
"""CEA-608 caption display model for the byte subset defined in README.md.

Usage: python3 cap.py input.json output.json
"""
import json
import sys
import unicodedata

ROWS = 4
BASE_ROW = ROWS - 1

# Only the pairs listed in README.md are commands: 0x14 0x25 is RU2 while
# 0x1C 0x25 is RU3.  Anything else with a 0x14/0x1C prefix is an
# out-of-subset command, never text.
CTRL = {
    (0x14, 0x20): "rcl", (0x1C, 0x20): "rcl",
    (0x14, 0x21): "bs",  (0x1C, 0x21): "bs",
    (0x14, 0x25): "ru2", (0x1C, 0x25): "ru3",
    (0x14, 0x29): "rdc", (0x1C, 0x29): "rdc",
    (0x14, 0x2C): "edm", (0x1C, 0x2C): "edm",
    (0x14, 0x2D): "cr",  (0x1C, 0x2D): "cr",
    (0x14, 0x2E): "enm", (0x1C, 0x2E): "enm",
    (0x14, 0x2F): "eoc", (0x1C, 0x2F): "eoc",
}
CONTROL_PREFIXES = (0x14, 0x1C)
# Padding carries no caption data and must stay transparent: it neither
# becomes text nor breaks the "identical control pair received twice in a
# row" adjacency used for repeat suppression.  This is what makes chunked
# reads of the same stream behave exactly like a single read.
PAD_PAIRS = frozenset({(0x80, 0x80), (0x00, 0x00)})


def blank():
    """Four empty, independently mutable rows."""
    return [bytearray() for _ in range(ROWS)]


def utf8_length(lead):
    if 0xC2 <= lead <= 0xDF:
        return 2
    if 0xE0 <= lead <= 0xEF:
        return 3
    if 0xF0 <= lead <= 0xF4:
        return 4
    return 0


def decode_row(buf):
    """Render one row's bytes as text.

    Accented characters may arrive as UTF-8 (two bytes per character, both
    halves in the same or consecutive pairs), so valid UTF-8 sequences are
    decoded as units.  Any byte that does not form UTF-8 is taken as a
    Latin-1 code point, which covers single-byte accents; C0/C1 controls
    such as the 0x80 padding are dropped.  Only trailing blanks are trimmed,
    keeping leading indentation so a caption's column is preserved.
    """
    out = []
    i = 0
    n = len(buf)
    while i < n:
        b = buf[i]
        if b < 0x80:
            if b >= 0x20:                 # 0x20-0x7F are printable
                out.append(chr(b))
            i += 1
            continue
        length = utf8_length(b)
        if length and i + length <= n:
            try:
                out.append(bytes(buf[i:i + length]).decode("utf-8"))
                i += length
                continue
            except UnicodeDecodeError:
                pass
        ch = chr(b)                       # Latin-1 fallback
        if unicodedata.category(ch) != "Cc":
            out.append(ch)
        i += 1
    return "".join(out).rstrip()


def backspace_row(buf):
    """Delete the last *character*; multi-byte characters go as a unit."""
    n = len(buf)
    if n == 0:
        return
    if buf[n - 1] < 0x80:
        del buf[n - 1:]
        return
    for length in (2, 3, 4):
        start = n - length
        if start < 0 or utf8_length(buf[start]) != length:
            continue
        try:
            bytes(buf[start:]).decode("utf-8")
        except UnicodeDecodeError:
            continue
        del buf[start:]
        return
    del buf[n - 1:]


class Decoder:
    """State machine for the README's 608 subset.

    Two 4-row memories share one write cursor, always the base (bottom)
    row.  Which memory receives text depends on the mode: pop-on loads the
    non-displayed memory, paint-on and roll-up write the display.  Text is
    appended to the current row until a line break moves it.

    CR is a roll-up scroll: the active memory shifts up one row inside the
    active window (the bottom 2 or 3 rows).  Rows above that window are
    cleared, so a line that scrolled out of a 2-row window cannot reappear
    when the window is later enlarged to 3 rows.  Outside roll-up, CR simply
    starts a new line by scrolling all four rows.

    Control pairs are deduplicated only against the immediately preceding
    control pair, so a repeated byte pair in a re-read chunk performs one
    action, never two.  Padding pairs are transparent to that adjacency.
    """

    def __init__(self):
        self.disp = blank()
        self.nondisp = blank()
        self.mode = "pop-on"
        self.roll_rows = 2             # size of the active roll-up window
        self.prev_ctrl = None          # last adjacent control pair (dedup)
        self.snapshots = []
        self.modes = []

    # -- helpers ---------------------------------------------------------
    def set_mode(self, mode, t):
        if mode != self.mode:
            self.mode = mode
            self.modes.append({"t": t, "mode": mode})

    def memory(self):
        """The memory text is written to in the current mode."""
        return self.nondisp if self.mode == "pop-on" else self.disp

    def window(self):
        """Number of rows CR scrolls in the current mode."""
        return self.roll_rows if self.mode == "roll-up" else ROWS

    # -- control words ---------------------------------------------------
    def control(self, op, t):
        if op in ("ru2", "ru3"):
            self.start_roll_up(2 if op == "ru2" else 3)
            self.set_mode("roll-up", t)
        elif op == "rcl":
            self.set_mode("pop-on", t)
        elif op == "rdc":
            self.set_mode("paint-on", t)
        elif op == "edm":
            self.disp = blank()
        elif op == "enm":
            self.nondisp = blank()
        elif op == "eoc":
            # Publish the loaded memory: display becomes what was hidden.
            self.disp, self.nondisp = self.nondisp, self.disp
        elif op == "cr":
            self.carriage_return()
        elif op == "bs":
            backspace_row(self.memory()[BASE_ROW])

    def start_roll_up(self, rows):
        """Enter or resize the roll-up window.

        The window is bottom-anchored: RU2 covers rows 2-3, RU3 rows 1-3.
        Rows above it are outside the roll-up display, so they are cleared:
        that is what stops a line scrolled out of a 2-row window from
        reappearing when the window grows to 3 rows, i.e. the previous
        sentence stacking onto the new one.  The base row itself is not
        cleared here -- the README reserves that for EDM/ENM -- so text
        continues to append to the current row, roll-up included.
        """
        self.roll_rows = rows
        for r in range(ROWS - rows):
            self.disp[r] = bytearray()

    def carriage_return(self):
        """Line break: scroll the active window and clear the base row."""
        mem = self.memory()
        top = ROWS - self.window()
        for r in range(top, BASE_ROW):
            mem[r] = bytearray(mem[r + 1])
        mem[BASE_ROW] = bytearray()

    # -- text ------------------------------------------------------------
    def text(self, lo, hi):
        """Append the usable bytes of a pair to the current row.

        ``0x20-0x7F`` are printable and ``hi`` == 0 means only ``lo``
        carries text.  Bytes >= 0x80 are kept as well so UTF-8 accented
        characters (both halves occupy one pair) survive to be decoded.
        """
        row = self.memory()[BASE_ROW]
        for b in (lo, hi):
            if b >= 0x20:          # 0x20-0x7F printable, 0x80+ kept for UTF-8
                row.append(b)

    # -- input -----------------------------------------------------------
    def feed(self, event):
        t = event.get("t")
        for pair in event.get("data", []):
            lo = pair[0] if len(pair) > 0 else 0
            hi = pair[1] if len(pair) > 1 else 0
            if lo in CONTROL_PREFIXES:
                op = CTRL.get((lo, hi))
                if op is None:
                    self.prev_ctrl = None   # unknown command, never text
                    continue
                if (lo, hi) == self.prev_ctrl:
                    continue                # adjacent repeat counts once
                self.prev_ctrl = (lo, hi)
                self.control(op, t)
                continue
            if (lo, hi) in PAD_PAIRS:
                continue                    # padding: not a control, not text
            self.prev_ctrl = None
            self.text(lo, hi)
        self.snapshots.append(
            {"t": t, "screen": [decode_row(r) for r in self.disp]}
        )


def main():
    with open(sys.argv[1]) as f:
        doc = json.load(f)
    decoder = Decoder()
    for event in doc.get("events", []):
        decoder.feed(event)
    with open(sys.argv[2], "w") as f:
        json.dump({"snapshots": decoder.snapshots, "modes": decoder.modes},
                  f, indent=1)


if __name__ == "__main__":
    main()
