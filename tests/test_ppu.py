import struct
import unittest

from snesbg.layer import LayerError, parse, _rgb


def put_word(vram, addr, w):
    vram[addr & 0xFFFF] = w & 0xFF
    vram[(addr + 1) & 0xFFFF] = w >> 8


def get_word(vram, addr):
    return vram[addr & 0xFFFF] | (vram[(addr + 1) & 0xFFFF] << 8)


def encode_tile(pixels, bpp):
    # SNES layout: bpp planes of 8 contiguous bytes each, interleaved in pairs
    out = bytearray(8 * bpp)
    for plane in range(bpp):
        for y in range(8):
            byte = 0
            for x in range(8):
                byte |= ((pixels[y][x] >> plane) & 1) << (7 - x)
            out[plane * 8 + y] = byte
    return out

def put_tile(vram, addr, pixels, bpp):
    chunk = encode_tile(pixels, bpp)
    a = addr & 0xFFFF
    vram[a:a + len(chunk)] = chunk


def make(mode=1, big=False, sc=0, nba=0, hofs=0, vofs=0, cgram=None, vram=None, vram_size=0x10000):
    bgmode = mode | (0x10 if big else 0)
    head = b"SNESBG\0\0" + bytes([bgmode, sc, nba, 0]) + struct.pack("<HH", hofs, vofs)
    cg = cgram if cgram is not None else [0] * 256
    cg_bytes = bytearray()
    for w in cg:
        cg_bytes += bytes((w & 0xFF, w >> 8))
    vr = vram if vram is not None else bytearray(0x10000)
    return head + bytes(cg_bytes) + bytes(vr[:vram_size])


def solid(v):
    return [[v] * 8 for _ in range(8)]


def runs(line):
    out = []
    for c in line:
        if out and out[-1][0] == c:
            out[-1][1] += 1
        else:
            out.append([c, 1])
    return out


class PPUTests(unittest.TestCase):
    def test_bgr_color_order(self):
        # CGRAM words hold RRRRR low, GGGGG middle, BBBBB high
        cg = [0] * 256
        cg[1], cg[2], cg[3] = 0x001F, 0x03E0, 0x7C00  # pure red, green, blue
        vr = bytearray(0x10000)
        px = [[0] * 8 for _ in range(8)]
        px[0][0], px[0][1], px[0][2] = 1, 2, 3
        put_tile(vr, 0x200 * 32, px, 4)  # tile 0x200, tile 0 stays blank
        put_word(vr, 0, 0x200)
        out = parse(make(cgram=cg, vram=vr))
        self.assertEqual(out["first_row"][:3],
                         [["#ff0000", 1], ["#00ff00", 1], ["#0000ff", 1]])

    def test_widen_5bit(self):
        self.assertEqual(_rgb(0x000F), "#7b0000")  # round(15*255/31)=123
        self.assertEqual(_rgb(0x7FFF), "#ffffff")

    def test_bitplane_decode_4bpp(self):
        # value 5 = planes 0,2 ; value 10 = planes 1,3
        cg = [0] * 256
        cg[5] = 0x001F
        cg[10] = 0x7C00
        vr = bytearray(0x10000)
        px = [[0] * 8 for _ in range(8)]
        px[0][0], px[0][1] = 5, 10  # first row, no scrolling needed
        put_tile(vr, 0x200 * 32, px, 4)  # tile 0x200
        put_word(vr, 0, 0x200)
        out = parse(make(cgram=cg, vram=vr))
        self.assertEqual(out["first_row"][:2], [["#ff0000", 1], ["#0000ff", 1]])

    def test_map_base_2k_units(self):
        # BG1SC = 0x20 -> map base $4000 (old code read $0800)
        cg = [0] * 256
        cg[1] = 0x001F
        vr = bytearray(0x10000)
        put_tile(vr, 1 * 32, solid(1), 4)  # tile 1 at char base 0
        put_word(vr, 0x4000, 1)  # only cell (0,0) of the map at $4000
        out = parse(make(sc=0x20, cgram=cg, vram=vr))
        self.assertEqual(out["first_row"], [["#ff0000", 8], ["#000000", 248]])

    def test_char_base_8k_units(self):
        # NBA = 1 -> character base $2000 (old code used $1000)
        cg = [0] * 256
        cg[2] = 0x03E0
        vr = bytearray(0x10000)
        put_tile(vr, 0x2000 + 2 * 32, solid(2), 4)  # tile 2 at base $2000
        put_word(vr, 0, 2)
        out = parse(make(nba=1, cgram=cg, vram=vr))
        self.assertEqual(out["first_row"][0], ["#00ff00", 8])

    def test_wide_map_second_screen(self):
        vr = bytearray(0x10000)
        cg = [0] * 256
        cg[1], cg[2] = 0x001F, 0x7C00
        put_tile(vr, 1 * 32, solid(1), 4)       # tile 1 red
        put_tile(vr, 2 * 32, solid(2), 4)       # tile 2 blue
        put_word(vr, 0x800, 1)  # cell (32,0): second screen start
        put_word(vr, 0x802, 2)  # cell (33,0)
        out = parse(make(sc=1, hofs=256, cgram=cg, vram=vr))
        self.assertEqual(out["first_row"][0], ["#ff0000", 8])
        out = parse(make(sc=1, hofs=264, cgram=cg, vram=vr))
        self.assertEqual(out["first_row"][0], ["#0000ff", 8])

    def test_tall_map_second_screen(self):
        vr = bytearray(0x10000)
        cg = [0] * 256
        cg[1] = 0x001F
        put_tile(vr, 1 * 32, solid(1), 4)  # tile 1
        put_word(vr, 0x800, 1)  # cell (0,32): stacked second screen
        out = parse(make(sc=2, vofs=256, cgram=cg, vram=vr))
        self.assertEqual(out["first_row"][0], ["#ff0000", 8])

    def test_hflip_vflip_8x8(self):
        cg = [0] * 256
        cg[1] = 0x001F
        vr = bytearray(0x10000)
        px = [[0] * 8 for _ in range(8)]
        px[0][0] = 1  # marker at tile top-left
        put_tile(vr, 0x200 * 32, px, 4)  # tile 0x200
        put_word(vr, 0, 0x4000 | 0x200)  # hflip: marker moves to top-right
        out = parse(make(cgram=cg, vram=vr))
        self.assertEqual(out["first_row"],
                         [["#000000", 7], ["#ff0000", 1], ["#000000", 248]])
        # marker at tile bottom-left, vflip moves it to screen top-left
        py = [[0] * 8 for _ in range(8)]
        py[7][0] = 1
        put_tile(vr, 0x200 * 32, py, 4)
        put_word(vr, 0, 0x8000 | 0x200)
        out = parse(make(cgram=cg, vram=vr))
        self.assertEqual(out["first_row"][0], ["#ff0000", 1])

    def test_16x16_groups_and_flip(self):
        cg = [0] * 256
        for i, w in enumerate((0x001F, 0x03E0, 0x7C00, 0x03FF), start=1):
            cg[i] = w  # cg[1..4] = red, green, blue, yellow
        vr = bytearray(0x10000)
        # hardware clears the low tile-number bit of the group entry, so the
        # even base 0x200 expands to subtiles 0x200..0x203 (each 32 bytes)
        for t in range(4):
            put_tile(vr, (0x200 + t) * 32, solid(t + 1), 4)  # tiles 0x200..
        # junk in the three slave cells must be ignored
        put_word(vr, 2, 0xFFFF)
        put_word(vr, 64, 0xFFFF)
        put_word(vr, 66, 0xFFFF)
        put_word(vr, 0, 0x0200)  # group top-left, no flip
        out = parse(make(big=True, cgram=cg, vram=vr))
        self.assertEqual(out["first_row"][:2], [["#ff0000", 8], ["#00ff00", 8]])
        # bottom subtiles of the same group, reached one cell-row down with a
        # fresh group row whose slave entries are left blank
        put_word(vr, 128, 0x0200)
        out = parse(make(big=True, vofs=8, cgram=cg, vram=vr))
        self.assertEqual(out["first_row"][:2], [["#0000ff", 8], ["#ffff00", 8]])
        # both flips: the subtile order returns to the no-flip layout (the two
        # quadrant swaps cancel); solid fills hide the intra-tile pixel flip
        put_word(vr, 0, 0x0200 | 0xC000)
        out = parse(make(big=True, cgram=cg, vram=vr))
        self.assertEqual(out["first_row"][:2], [["#ff0000", 8], ["#00ff00", 8]])

    def test_mode0_palette_stride(self):
        cg = [0] * 256
        cg[9] = 0x001F   # palette 1, pixel 1 -> index 1*8+1 (old code used 17)
        cg[10] = 0x7C00
        vr = bytearray(0x10000)
        px = [[0] * 8 for _ in range(8)]
        px[0][0], px[0][1] = 1, 2
        put_tile(vr, 0x100 * 16, px, 2)  # 2bpp tile 0x100 (10-bit tile field)
        put_word(vr, 0x4000, 0x0100 | (1 << 10))  # tile 0x100, palette number 1
        out = parse(make(mode=0, sc=0x20, cgram=cg, vram=vr))
        self.assertEqual(out["first_row"][:2], [["#ff0000", 1], ["#0000ff", 1]])
        self.assertEqual(out["palette_pixels"][1], 2)

    def test_mode3_direct_cgram(self):
        cg = [0] * 256
        cg[255] = 0x03E0
        vr = bytearray(0x10000)
        px = [[0] * 8 for _ in range(8)]
        px[0][0] = 255  # single opaque pixel, value 255 sets every bitplane once
        put_tile(vr, 0x200 * 64, px, 8)  # 8bpp tile 0x200 at byte $8000
        put_word(vr, 0x4000, 0x0200 | (7 << 10))  # map at $4000, palette bits ignored
        out = parse(make(mode=3, sc=0x20, cgram=cg, vram=vr))
        self.assertEqual(out["first_row"][0], ["#00ff00", 1])
        # the one opaque pixel is still bucketed by the entry palette field
        self.assertEqual(sum(out["palette_pixels"]), 1)
        self.assertEqual(out["transparent_pixels"], 57343)
    def test_incomplete_snapshot(self):
        out = parse(make(vram_size=100))
        self.assertFalse(out["complete"])
        self.assertIsNone(out["colors_used"])
        self.assertIsNone(out["first_row"])
        self.assertEqual(out["bpp"], 4)

    def test_unsupported_mode(self):
        with self.assertRaises(LayerError):
            parse(make(mode=2))

    def test_scroll_wrap(self):
        cg = [0] * 256
        cg[1] = 0x001F
        vr = bytearray(0x10000)
        px = [[0] * 8 for _ in range(8)]
        px[0][7] = 1
        put_tile(vr, 0x200 * 32, px, 4)  # tile 0x200
        put_word(vr, 2 * 31, 0x200)  # cell 31: last column of the 32-wide map
        # BG x 255 appears at screen column 0, so the map wraps without a seam
        out = parse(make(hofs=255, cgram=cg, vram=vr))
        self.assertEqual(out["first_row"][0], ["#ff0000", 1])


def reference_render(bgmode, sc, nba, hofs, vofs, cg, vram):
    """Independent renderer: blit the whole BG plane first, then sample it."""
    mode = bgmode & 7
    bpp = {0: 2, 1: 4, 3: 8}[mode]
    big = bool(bgmode & 0x10)
    mapbase = (sc >> 2) * 0x800
    wide, tall = sc & 1, (sc >> 1) & 1
    charbase = (nba & 15) * 0x2000
    mw, mh = 256 * (1 + wide), 256 * (1 + tall)
    ncx, ncy = mw // 8, mh // 8

    def cell_entry(cx, cy):
        screen = (cy >> 5) * (1 + wide) + (cx >> 5)
        addr = mapbase + screen * 0x800 + ((cy & 31) * 32 + (cx & 31)) * 2
        return get_word(vram, addr)

    def tile_pixel(tile, x, y):
        base = charbase + tile * 8 * bpp
        v = 0
        for p in range(bpp):
            a = base + p * 8 + y
            v |= ((vram[a & 0xFFFF] >> (7 - x)) & 1) << p
        return v

    plane = {}
    for cy in range(ncy):
        for cx in range(ncx):
            if big:
                if cx & 1 or cy & 1:
                    continue
                e = cell_entry(cx, cy)
                t0, pal = e & 0x3FF, (e >> 10) & 7
                for oy in range(2):
                    for ox in range(2):
                        t = (t0 & 0x3FE) | (ox | (oy << 1))
                        for y in range(8):
                            for x in range(8):
                                tx = 7 - x if e & 0x4000 else x
                                ty = 7 - y if e & 0x8000 else y
                                plane[(cx * 8 + ox * 8 + x,
                                       cy * 8 + oy * 8 + y)] = (tile_pixel(t, tx, ty), pal)
            else:
                e = cell_entry(cx, cy)
                t, pal = e & 0x3FF, (e >> 10) & 7
                for y in range(8):
                    for x in range(8):
                        tx = 7 - x if e & 0x4000 else x
                        ty = 7 - y if e & 0x8000 else y
                        plane[(cx * 8 + x, cy * 8 + y)] = (tile_pixel(t, tx, ty), pal)

    def color_of(c, pal):
        if c == 0:
            return _rgb(cg[0]), True
        if mode == 3:
            idx = c
        elif mode == 0:
            idx = pal * 8 + c
        else:
            idx = pal * 16 + c
        return _rgb(cg[idx]), False

    seen, ppx, clear = set(), [0] * 8, 0
    rows = []
    for sy in range(224):
        row = []
        for sx in range(256):
            x, y = (sx + hofs) % mw, (sy + vofs) % mh
            c, pal = plane[(x, y)]
            col, is_back = color_of(c, pal)
            seen.add(col)
            if is_back:
                clear += 1
            else:
                ppx[pal] += 1
            row.append(col)
        rows.append(row)
    return len(seen), clear, ppx, runs(rows[0]), runs(rows[-1])


class DifferentialTests(unittest.TestCase):
    def test_random_snapshots(self):
        import random
        rng = random.Random(108)
        for trial in range(24):
            mode = rng.choice((0, 1, 3))
            big = rng.choice((False, True))
            wide, tall = rng.choice(((0, 0), (1, 0), (0, 1), (1, 1)))
            sc = rng.choice((0x00, 0x04, 0x10, 0x20, 0x38, 0x7C)) | wide | (tall << 1)
            nba = rng.choice((0, 1, 4, 7, 14))
            hofs, vofs = rng.randrange(600), rng.randrange(600)
            cg = [rng.randrange(0x8000) for _ in range(256)]
            vr = bytearray(rng.randrange(256) for _ in range(0x10000))
            ncx, ncy = 32 * (1 + wide), 32 * (1 + tall)
            step = 2 if big else 1
            for cy in range(0, ncy, step):
                for cx in range(0, ncx, step):
                    screen = (cy >> 5) * (1 + wide) + (cx >> 5)
                    addr = ((sc >> 2) * 0x800 + screen * 0x800
                            + ((cy & 31) * 32 + (cx & 31)) * 2) & 0xFFFF
                    e = rng.randrange(0x400) | (rng.randrange(8) << 10)
                    if rng.random() < 0.5:
                        e |= rng.choice((0x4000, 0x8000, 0xC000))
                    put_word(vr, addr, e)
            snap = make(mode=mode, big=big, sc=sc, nba=nba,
                        hofs=hofs, vofs=vofs, cgram=cg, vram=vr)
            out = parse(snap)
            exp = reference_render(mode | (0x10 if big else 0),
                                   sc, nba, hofs, vofs, cg, vr)
            self.assertEqual(
                (out["colors_used"], out["transparent_pixels"],
                 out["palette_pixels"], out["first_row"], out["last_row"]),
                exp, "trial %d" % trial)


if __name__ == "__main__":
    unittest.main()
