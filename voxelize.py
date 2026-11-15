import hashlib
from itertools import product
import json
import math
from pathlib import Path
import sys


def voxelize(data):
    resolution = data["resolution"]
    depth = resolution.bit_length() - 1
    occupied = set()
    for triangle in data["triangles"]:
        for vertex in triangle:
            cell = tuple(math.floor(axis) for axis in vertex)
            if all(0 <= axis < resolution for axis in cell):
                occupied.add(cell)
    levels = []
    for d in range(depth + 1):
        step = 1 << (depth - d)
        nodes = []
        for x, y, z in product(range(1 << d), repeat=3):
            children = [p for p in occupied if tuple(axis // step for axis in p) == (x, y, z)]
            if not children:
                continue
            mask = 0
            if step > 1:
                for a, b, c in children:
                    mask |= 1 << ((a // (step // 2) % 2) + 2 * (b // (step // 2) % 2) + 4 * (c // (step // 2) % 2))
            nodes.append(dict(x=x, y=y, z=z, child_mask=mask, occupied_leaves=len(children)))
        levels.append(dict(depth=d, count=len(nodes), nodes=nodes))
    result = dict(leaves=sorted(occupied), levels=levels)
    result["digest"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result


if __name__ == "__main__":
    data = json.loads(Path(sys.argv[1]).read_text())
    Path(sys.argv[2]).write_text(json.dumps(voxelize(data)))
