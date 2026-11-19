"""Gross requirements for one product.

    python3 mrp.py bom.json <product> <units> out.json
"""
import json
import sys

from rcte.bom import mrp_requirements
from rcte.graph import load


def main():
    graph = load(json.load(open(sys.argv[1], encoding="utf-8")))
    result = mrp_requirements(graph, sys.argv[2], int(sys.argv[3]))
    result["loops"] = ["/".join(p) for p in result["loops"]]
    with open(sys.argv[4], "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
