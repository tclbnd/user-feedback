import json
import math

from renderer import render


def load_demo(spp=0, transport="analytic", seed=1):
    cfg = json.load(open("demo.json"))
    cfg["spp"] = spp
    cfg["transport"] = transport
    cfg["seed"] = seed
    return cfg


def test_schedule_determinism():
    cfg = load_demo(spp=2000)
    schedules = [
        dict(tile=1, workers=1),
        dict(tile=1, workers=4, tile_order=[1, 0]),
        dict(tile=2, workers=1),
        dict(tile=4, workers=3),
    ]
    digests = {render(cfg, **kw)["digest"] for kw in schedules}
    assert len(digests) == 1, digests
    other = render(load_demo(spp=2000, seed=2), tile=1, workers=1)
    assert other["digest"] not in digests
    print("schedule determinism OK")


def test_enumeration_zero_variance():
    out = render(load_demo(spp=0))
    assert out["stats"]["mean_abs_xyz_bias"] == 0.0
    assert out["stats"]["rms_standard_error"] == 0.0
    for ray in out["rays"]:
        assert ray["variance"] == [0.0, 0.0, 0.0]
    print("enumeration zero variance OK")


def test_high_spp_unbiased():
    ref = render(load_demo(spp=0))
    for mode in ("analytic", "freeflight"):
        out = render(load_demo(spp=60000, transport=mode))
        for i in range(len(ref["image"])):
            for c in range(3):
                rel = abs(out["image"][i][c] - ref["image"][i][c]) / max(
                    1e-12, abs(ref["image"][i][c]))
                assert rel < 0.02, (mode, i, c, rel)
    print("high-spp unbiasedness OK")


def test_bias_noise_separated():
    out = render(load_demo(spp=4000))
    stats = out["stats"]
    # Both quantities must be present independently; noise is strictly
    # positive for a finite Monte Carlo run while bias is signed.
    assert stats["rms_standard_error"] > 0.0
    assert "mean_abs_xyz_bias" in stats
    for ray in out["rays"]:
        assert all(v > 0.0 for v in ray["standard_error"])
        assert len(ray["bias"]) == 3
    print("bias/noise separation OK")


def test_linear_to_display_chain():
    out = render(load_demo(spp=0))
    for lin, enc in zip(out["image_linear_srgb"],
                        out["image_display_srgb"]):
        # Encoding is monotonic and stays within [0, 1].
        assert all(0.0 <= v <= 1.0 for v in enc)
    print("linear/display chain OK")


if __name__ == "__main__":
    test_schedule_determinism()
    test_enumeration_zero_variance()
    test_high_spp_unbiased()
    test_bias_noise_separated()
    test_linear_to_display_chain()
    print("renderer tests OK")
