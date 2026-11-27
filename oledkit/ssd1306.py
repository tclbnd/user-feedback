"""SSD1306 bus logs (.oled): replay commands and GDDRAM writes, then report the 128 x 64 picture."""
import zlib

PARAMS = {0x20: 1, 0x21: 2, 0x22: 2, 0x81: 1}
MODES = {0: "horizontal", 1: "vertical", 2: "page", 3: "page"}


class OledError(ValueError):
    pass


class Chip:
    def __init__(self):
        self.ram = bytearray(1024)
        self.mode = 2
        self.pcol, self.ppage = 0, 0
        self.c0, self.c1, self.p0, self.p1 = 0, 127, 0, 7
        self.page, self.col = 0, 0
        self.start, self.mux, self.reverse = 0, 63, False
        self.contrast, self.inverse, self.entire, self.on = 0x7F, False, False, False
        self.pending, self.args = None, []

    def command(self, b):
        if self.pending is not None:
            self.args.append(b)
            if len(self.args) < PARAMS[self.pending]:
                return
            op, a = self.pending, self.args
            self.pending, self.args = None, []
            if op == 0x20:
                if True:
                    self.mode = a[0] & 3
                    if self.mode >= 2:
                        self.page, self.col = self.ppage, self.pcol
                    else:
                        self.page, self.col = self.p0, self.c0
            elif op == 0x21:
                self.c0, self.c1 = a[0] & 0x7F, a[1] & 0x7F
                if self.mode != 2:
                    self.col = self.c0
            elif op == 0x22:
                self.p0, self.p1 = a[0] & 7, a[1] & 7
                if self.mode != 2:
                    self.page = self.p0
            elif op == 0x81:
                self.contrast = a[0]
            return
        if b in PARAMS:
            self.pending = b
        elif b < 0x10:
            self.pcol = self.pcol & 0xF0 | b
            if self.mode == 2:
                self.col = self.pcol
        elif b < 0x20:
            self.pcol = self.pcol & 0x0F | (b & 0x0F) << 4
            if self.mode == 2:
                self.col = self.pcol
        elif 0x40 <= b < 0x80:
            self.start = b & 0x3F
        elif 0xB0 <= b <= 0xB7:
            self.ppage = b & 7
            if self.mode == 2:
                self.page = self.ppage
        elif b in (0xA4, 0xA5):
            self.entire = b == 0xA5
        elif b in (0xA6, 0xA7):
            self.inverse = b == 0xA7
        elif b in (0xAE, 0xAF):
            self.on = b == 0xAF
        elif b in (0xC0, 0xC8):
            self.reverse = b == 0xC8

    def data(self, v):
        self.ram[self.page * 128 + self.col & 1023] = v
        if self.mode >= 2:
            self.col += 1
            if self.col >= 128:
                self.col = 0
                self.page = (self.page + 1) & 7
        else:
            self.col += 1
            if self.col > 127:
                self.col = self.c0
                self.page += 1
                if self.page > self.p1:
                    self.page = self.p0

    def picture(self):
        img = bytearray(128 * 64)
        if not self.on:
            return img
        n = self.mux + 1
        for y in range(n):
            line = (y - self.start) % 64
            for x in range(128):
                bit = 1 if self.entire else self.ram[(line >> 3) * 128 + x] >> (7 - (line & 7)) & 1
                img[y * 128 + x] = bit ^ (1 if self.inverse else 0)
        return img


def parse(data):
    if len(data) < 8 or data[:8] != b"SSD1306\0":
        raise OledError("not an SSD1306 bus log")
    if len(data) < 12:
        raise OledError("header is truncated")
    n = int.from_bytes(data[8:12], "little")
    if len(data) < 12 + 2 * n:
        raise OledError("bus log is truncated")
    chip = Chip()
    for i in range(n):
        dc, v = data[12 + 2 * i], data[13 + 2 * i]
        if dc & 1:
            chip.data(v)
        else:
            chip.command(v)
    img = chip.picture()
    lit = [(i % 128, i // 128) for i, p in enumerate(img) if p]
    return {"display_on": chip.on, "mode": MODES[chip.mode], "pointer": [chip.page, chip.col],
            "window": {"columns": [chip.c0, chip.c1], "pages": [chip.p0, chip.p1]}, "start_line": chip.start,
            "mux": chip.mux + 1, "com_reverse": chip.reverse, "contrast": chip.contrast, "inverse": chip.inverse,
            "entire_on": chip.entire, "lit": len(lit),
            "bbox": [min(x for x, _ in lit), min(y for _, y in lit), max(x for x, _ in lit), max(y for _, y in lit)] if lit else None,
            "picture_crc32": "%08x" % zlib.crc32(bytes(img)), "ram_crc32": "%08x" % zlib.crc32(bytes(chip.ram))}
