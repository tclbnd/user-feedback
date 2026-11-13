"""Performance sanity: 40 glyphs, 40 renders at 96 px/em."""
import random
import time
import glyph
import fuzz


def main():
    rng = random.Random(7)
    glyphs = [fuzz.rand_glyph(rng, "p%d" % i) for i in range(40)]
    renders = []
    for i in range(40):
        renders.append({"id": "r%d" % i, "glyph": "p%d" % i, "width": 110,
                        "height": 110, "scale": 96,
                        "origin": [5.0, 5.0], "stroke": 0.0})
    data = {"glyphs": glyphs, "renders": renders,
            "texels_per_em": 64, "padding": 5}
    t0 = time.time()
    out = glyph.render(data)
    t1 = time.time()
    print("40 glyphs / 40 renders @96px: %.2fs, atlas %dx%d"
          % (t1 - t0, out["atlas"]["width"], out["atlas"]["height"]))


if __name__ == "__main__":
    main()
