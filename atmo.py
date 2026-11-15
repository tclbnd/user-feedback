#!/usr/bin/env python3
"""Precomputed atmosphere LUTs (flat-path baseline)."""
import json, sys, math, hashlib

R_E, H, BETA, TOP = 6360.0, 8.0, 5.5e-3, 60.0

def transmittance(cz, alt, samples):
    # BUG: straight-line path, no spherical geometry/refraction
    c = max(cz, 1e-3)
    length = (TOP - alt) / c
    ds = length / samples
    tau = sum(math.exp(-min(700, max(0, alt + ds * i * c)) / H) * ds * BETA
              for i in range(samples))
    return math.exp(-min(700, tau))

def inscatter(mu, alt, cz_sun, samples):
    # BUG: no phase function, no earth-shadow, flat assumption
    return transmittance(cz_sun, alt, samples) * math.exp(-alt / H) * 0.1

def build(cfg):
    n, s = cfg["lut_size"], cfg["samples"]
    T = [[round(transmittance(2*i/(n-1)-1, 60*j/(n-1), s), 9)
          for j in range(n)] for i in range(n)]
    S = [[round(inscatter(2*i/(n-1)-1, 60*j/(n-1), 0.7, s), 9)
          for j in range(n)] for i in range(n)]
    h = hashlib.sha256(repr([T, S]).encode()).hexdigest()[:16]
    return {"T": T, "S": S, "meta": {"lut_size": n, "samples": s, "checksum": h}}

def query(lut, name, cz, alt):
    # BUG: no clamping/interp boundary handling
    tab = lut[name]
    n = lut["meta"]["lut_size"]
    i = int((cz + 1) / 2 * (n - 1)); j = int(alt / 60 * (n - 1))
    return tab[i][j]

if __name__ == "__main__":
    if sys.argv[1] == "--query":
        lut = json.load(open(sys.argv[2]))
        print(query(lut, sys.argv[3], float(sys.argv[4]), float(sys.argv[5])))
    else:
        cfg = json.load(open(sys.argv[1]))
        json.dump(build(cfg), open(sys.argv[2], "w"))
