import json

from renderer import render
from spectra import wavelength_spacing_weights


def test_nonuniform_wavelength_pdf_still_unbiased():
    cfg = json.load(open("demo.json"))
    cfg["spp"] = 0
    ref = render(cfg)
    cfg_mc = json.load(open("demo.json"))
    # Bias proposals toward long wavelengths; must converge to the same
    # enumeration reference because the weight divides by the proposal.
    cfg_mc["wavelength_pdf"] = [0.05, 0.05, 0.1, 0.15, 0.25, 0.4]
    cfg_mc["spp"] = 60000
    out = render(cfg_mc)
    for i in range(2):
        for c in range(3):
            rel = abs(out["image"][i][c] - ref["image"][i][c]) / max(
                1e-12, abs(ref["image"][i][c]))
            assert rel < 0.02, (i, c, rel)
    print("non-uniform wavelength proposal unbiased OK")


def test_nonuniform_grid_spacing():
    # Doubled interval width gets doubled integration weight, mean-normalised.
    w = wavelength_spacing_weights([400.0, 500.0, 700.0])
    # intervals: 100 then 200 -> raw weights 50, 150, 100; mean 100
    assert w == [0.5, 1.5, 1.0]
    assert wavelength_spacing_weights([500.0]) == [1.0]
    print("grid spacing weights OK")


def test_bad_light_pdf_rejected():
    cfg = json.load(open("demo.json"))
    # demo.json carries lights per ray; corrupt one ray's pdf normalisation.
    cfg["rays"][0]["lights"] = [
        dict(spectrum=[1] * 6, weight=1.0, pdf=0.7)]
    cfg["spp"] = 10
    try:
        render(cfg)
    except ValueError:
        print("bad ray pdf rejected OK")
        return
    raise AssertionError("non-normalised pdf was accepted")


def test_stack_underflow_rejected():
    cfg = json.load(open("demo.json"))
    cfg["rays"][0]["events"] = [dict(type="exit", material="glass",
                                     normal=0.0)]
    cfg["spp"] = 0
    try:
        render(cfg)
    except ValueError:
        print("stack underflow rejected OK")
        return
    raise AssertionError("exit on empty stack was accepted")


def test_tile_order_permutation_validated():
    cfg = json.load(open("demo.json"))
    cfg["spp"] = 100
    try:
        render(cfg, tile=1, workers=1, tile_order=[0, 0])
    except ValueError:
        print("invalid tile permutation rejected OK")
        return
    raise AssertionError("invalid tile_order was accepted")


if __name__ == "__main__":
    test_nonuniform_wavelength_pdf_still_unbiased()
    test_nonuniform_grid_spacing()
    test_bad_light_pdf_rejected()
    test_stack_underflow_rejected()
    test_tile_order_permutation_validated()
    print("edge tests OK")
