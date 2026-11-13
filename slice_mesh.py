"""Triangle-mesh input, basic classification, and offline OBJ output."""
import argparse
import json
import math
from pathlib import Path


def signed_distance(vertex, normal, offset):
    return sum(a*b for a,b in zip(vertex,normal)) - offset


def slice_mesh(mesh, plane):
    normal = plane["normal"]
    offset = plane["offset"]
    if len(normal) != 3 or math.hypot(*normal) == 0:
        raise ValueError("invalid plane normal")
    vertices = mesh["vertices"]
    if any(len(v) != 3 or not all(map(math.isfinite,v)) for v in vertices):
        raise ValueError("invalid vertex")
    groups = {"positive":[], "negative":[]}
    # The existing viewer keeps whole triangles based on their centroids.
    # Crossing triangles remain unsplit and the exposed section is open.
    for face in mesh["faces"]:
        ids = face["indices"]
        if len(ids) != 3 or any(not 0 <= i < len(vertices) for i in ids):
            raise ValueError("invalid triangle index")
        d = sum(signed_distance(vertices[i],normal,offset) for i in ids)
        groups["positive" if d >= 0 else "negative"].append(face)
    return {side:{"vertices":vertices, "faces":faces} for side,faces in groups.items()}


def write_obj(mesh, path):
    lines = ["v "+" ".join(map(str,v)) for v in mesh["vertices"]]
    for face in mesh["faces"]:
        lines.append("usemtl "+face.get("material","surface"))
        lines.append("f "+" ".join(str(i+1) for i in face["indices"]))
    path.write_text("\n".join(lines)+"\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input",type=Path)
    parser.add_argument("output",type=Path)
    parser.add_argument("--obj-dir",type=Path)
    args = parser.parse_args()
    try:
        data = json.loads(args.input.read_text())
        result = slice_mesh(data["mesh"],data["plane"])
        if args.obj_dir:
            args.obj_dir.mkdir(parents=True,exist_ok=True)
            for side,mesh in result.items():
                write_obj(mesh,args.obj_dir/(side+".obj"))
        args.output.write_text(json.dumps(result,allow_nan=False)+"\n")
    except (ValueError, KeyError, TypeError) as error:
        parser.exit(2,str(error)+"\n")


if __name__ == "__main__":
    main()
