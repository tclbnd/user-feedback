"""Independent brute-force reference: no culling, no hierarchy, no shared code.

Deliberately written from the specification rather than by importing the
implementation's helpers, so it can catch a shared mistake in ``render``.
Every instance is rasterised at full precision over all pixels of its bounding
projection; the per-pixel instance id is the final drawn primitive.
"""

INF = float("inf")


def reference_image(scene, frame_index):
    width, height = scene["size"]
    near = scene["near"]
    frame = scene["frames"][frame_index]

    depth = [[INF for _ in range(width)] for _ in range(height)]
    image = [[None for _ in range(width)] for _ in range(height)]
    for occ in frame["occluders"]:
        if not occ["opaque"]:
            continue
        rx0, ry0, rx1, ry1 = occ["rect"]
        for py in range(height):
            for px in range(width):
                if rx0 <= px < rx1 and ry0 <= py < ry1 and occ["z"] < depth[py][px]:
                    depth[py][px] = occ["z"]

    for w in sorted(frame["instances"], key=lambda item: item["id"]):
        x0, y0, z0, x1, y1, z1 = w["box"]
        if z1 < near:
            continue
        planes = (max(near, z0), z1)
        us = [width / 2.0 + width / 2.0 * x / z for x in (x0, x1) for z in planes]
        vs = [height / 2.0 + height / 2.0 * y / z for y in (y0, y1) for z in planes]
        ua, ub, va, vb = min(us), max(us), min(vs), max(vs)
        z = max(near, z0)
        for py in range(height):
            for px in range(width):
                if not (ua <= px + 0.5 < ub and va <= py + 0.5 < vb):
                    continue
                if z < depth[py][px]:
                    depth[py][px] = z
                    image[py][px] = w["id"]
    return image


def visible_ids(image):
    return set(v for row in image for v in row if v is not None)
