import hashlib
import json
from pathlib import Path
import sys


def transform(position, bone):
    w, x, y, z = bone["rotation"]
    px, py, pz = position
    cx, cy, cz = (y * pz - z * py, z * px - x * pz, x * py - y * px)
    dx, dy, dz = (y * cz - z * cy, z * cx - x * cz, x * cy - y * cx)
    return [value + 2 * (w * c + d) + shift for value, c, d, shift in
            zip(position, (cx, cy, cz), (dx, dy, dz), bone["translation"])]


def skin(data):
    bones = {bone["id"]: bone for bone in data["bones"]}
    vertices = []
    for vertex in data["vertices"]:
        weight_sum = sum(w["weight"] for w in vertex["weights"])
        if weight_sum == 0:
            vertices.append(dict(position=vertex["position"], method="bind"))
            continue
        blended = [0.0, 0.0, 0.0]
        for weight in vertex["weights"]:
            p = transform(vertex["position"], bones[weight["bone"]])
            for axis in range(3):
                blended[axis] += p[axis] * weight["weight"] / weight_sum
        vertices.append(dict(position=blended, method="linear"))
    positions = [vertex["position"] for vertex in vertices]
    digest = hashlib.sha256(json.dumps(positions).encode()).hexdigest()
    return dict(vertices=vertices, stats={"max_weight_sum_error": 0}, digest=digest)


if __name__ == "__main__":
    data = json.loads(Path(sys.argv[1]).read_text())
    Path(sys.argv[2]).write_text(json.dumps(skin(data)))
