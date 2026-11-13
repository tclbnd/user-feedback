"""Single-ray replay of the software volume compositing pass."""
import argparse
import json
import math
from pathlib import Path


def shade(ray, step, skip_empty):
    cutoff = min(ray["far"], ray.get("surface_t",ray["far"]))
    steps=0
    transmit, scatter=1.0,0.0
    t=ray["near"]
    while t < cutoff:
        midpoint=t+step/2
        density=0
        light=0
        for span in ray["medium"]:
            if span["a"] <= midpoint < span["b"]:
                density=span["rho"][0]
                light=span["light"][0]
                break
        attenuation=math.exp(-density*step)
        scatter += transmit*(1-attenuation)*light
        transmit *= attenuation
        steps += 1
        t += step
    return {"transmit":transmit,"scatter":scatter,"color":scatter+transmit*ray["surface_color"],
            "steps":steps,"skipped":0}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("scene",type=Path)
    parser.add_argument("output",type=Path)
    parser.add_argument("--no-skip",action="store_true")
    args=parser.parse_args()
    scene=json.loads(args.scene.read_text())
    result=[shade(ray,scene["step"],not args.no_skip) for ray in scene["rays"]]
    args.output.write_text(json.dumps({"rays":result},allow_nan=False)+"\n")


if __name__=="__main__":
    main()
