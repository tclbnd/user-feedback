"""Large randomized cross-validation against the independent reference."""
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import convert  # noqa: E402
import reference  # noqa: E402

rng = random.Random(987654321)
CASES = 200


def random_frame():
    w = rng.randint(1, 17)
    h = rng.randint(1, 13)
    siting = rng.choice(("center", "left"))
    range_kind = rng.choice(("limited", "full"))
    cw, ch = (w + 1) // 2, (h + 1) // 2
    frame = {
        "width": w,
        "height": h,
        "metadata": {
            "primaries": "bt2020", "transfer": "pq", "matrix": "bt2020nc",
            "range": range_kind, "siting": siting,
        },
        "y": [[rng.randint(0, 1023) for _ in range(w)] for _ in range(h)],
        "cb": [[rng.randint(0, 1023) for _ in range(cw)] for _ in range(ch)],
        "cr": [[rng.randint(0, 1023) for _ in range(cw)] for _ in range(ch)],
    }
    return frame


def main():
    max_undithered = 0
    max_dithered = 0
    for case in range(CASES):
        frame = random_frame()
        target = rng.choice(("center", "left"))
        white = rng.choice((203.0, 250.0, 1234.5, 5000.0))
        ry, rcb, rcr, rdbg, rlum = reference.reference_pipeline(
            frame, white, target)

        # Drive the production primitives directly with the same white.
        rgb2020, rgb709, luma709 = convert.decode_linear709(frame)
        py, pcb, pcr, pmapped = convert.encode_sdr(
            rgb709, luma709, white, frame["width"], frame["height"],
            target, None)
        for got, ref in ((py, ry), (pcb, rcb), (pcr, rcr)):
            for ga, ra in zip(got, ref):
                for gv, rv in zip(ga, ra):
                    max_undithered = max(max_undithered, abs(gv - rv))
        # Debug grids match the reference floats (relative tolerance, since
        # nit values span several orders of magnitude).
        for ggrid, egrid in (
                (rgb2020, rdbg["rgb2020_nits"]),
                (rgb709, rdbg["rgb709_nits"]),
                (pmapped, rdbg["mapped_linear709"])):
            for grow, erow in zip(ggrid, egrid):
                for gp, ep in zip(grow, erow):
                    for gv, ev in zip(gp, ep):
                        scale = max(1.0, abs(ev))
                        assert abs(gv - ev) <= 1e-10 * scale, (
                            case, gv, ev)
        for grow, erow in zip(luma709, rlum):
            for gv, ev in zip(grow, erow):
                scale = max(1.0, abs(ev))
                assert abs(gv - ev) <= 1e-10 * scale, (case, gv, ev)

        # Dithered outputs must remain within one code of the reference.
        seed = rng.randint(-10 ** 9, 10 ** 9)
        dy, dcb, dcr, _ = convert.encode_sdr(
            rgb709, luma709, white, frame["width"], frame["height"],
            target, seed)
        for got, ref in ((dy, ry), (dcb, rcb), (dcr, rcr)):
            for ga, ra in zip(got, ref):
                for gv, rv in zip(ga, ra):
                    max_dithered = max(max_dithered, abs(gv - rv))
                    assert abs(gv - rv) <= 1

        # Dither determinism: same seed reproduces, seed changes only <=1.
        dy2, dcb2, dcr2, _ = convert.encode_sdr(
            rgb709, luma709, white, frame["width"], frame["height"],
            target, seed)
        assert dy == dy2 and dcb == dcb2 and dcr == dcr2

    print("cases:", CASES)
    print("max undithered code difference vs reference:", max_undithered)
    print("max dithered code difference vs reference:", max_dithered)
    assert max_undithered == 0
    assert max_dithered <= 1


if __name__ == "__main__":
    main()
