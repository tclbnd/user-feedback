"""Scenario builders: near crossings, thin occluders, fast motion, mass occlusion."""
import random


def inst(ident, material, box):
    return dict(id=ident, material=material, box=[float(v) for v in box])


def occ(rect, z, opaque=True):
    return dict(rect=[int(v) for v in rect], z=float(z), opaque=opaque)


def grid_instance(ident, material, cx, cy, z, w=0.18, h=0.14, d=0.05):
    """A small box in camera space that projects to a handful of pixels."""
    return inst(ident, material, [cx - w, cy - h, z, cx + w, cy + h, z + d])


def mass_occlusion_scene(width=64, height=48, near=0.25):
    """A large opaque wall: >=25% of instances culled, some visibly surviving.

    Three identical frames give the culler a chance to reach steady state, then
    a fourth frame removes the wall so that previously hidden instances must
    come back.  Instances in front of the wall (z=0.6) stay visible throughout.
    """
    instances = []
    for idx in range(120):
        cx = -1.2 + 2.4 * ((idx % 12) + 0.5) / 12.0
        cy = -0.9 + 1.8 * ((idx // 12) + 0.5) / 10.0
        instances.append(grid_instance(idx + 1, "m%d" % (idx % 3), cx, cy, 4.0))
    front = []
    for idx in range(30):
        cx = -0.9 + 1.8 * ((idx % 6) + 0.5) / 6.0
        cy = -0.6 + 1.2 * ((idx // 6) + 0.5) / 5.0
        front.append(grid_instance(200 + idx, "f", cx, cy, 0.6, d=0.02))
    occluders = []
    for y in range(6, height - 6, 4):
        for x in range(6, width - 6, 4):
            occluders.append(occ([x, y, x + 4, y + 4], 0.9))
    all_instances = instances + front
    frames = [
        dict(occluders=occluders, instances=all_instances) for _ in range(3)
    ]
    frames.append(dict(occluders=[], instances=all_instances))
    return dict(size=[width, height], near=near, frames=frames)


def thin_occluder_scene(width=48, height=36, near=0.25):
    """One-pixel-wide opaque slivers must never prove a tile fully occluded."""
    instances = [
        grid_instance(1, "a", 0.0, 0.0, 2.0),
        grid_instance(2, "a", 0.3, 0.0, 2.0),
        grid_instance(3, "b", -0.3, 0.2, 2.0),
    ]
    frames = [
        dict(occluders=[occ([24, 0, 25, height], 1.0)], instances=instances),
        dict(occluders=[occ([25, 0, 26, height], 1.0)], instances=instances),
    ]
    return dict(size=[width, height], near=near, frames=frames)


def near_crossing_scene(width=40, height=30, near=0.25):
    """Boxes straddling and passing through the near plane."""
    frames = []
    crossing = [
        inst(1, "a", [-0.5, -0.4, 0.1, 0.5, 0.4, 0.6]),
        inst(2, "b", [-0.2, -0.2, -0.4, 0.2, 0.2, 0.5]),
        inst(3, "c", [0.1, 0.1, 0.24, 0.6, 0.6, 0.9]),
        inst(4, "c", [-0.8, -0.6, 0.25, -0.2, -0.2, 0.3]),
    ]
    occluders = [occ([0, 0, width, height], 1.2)]
    frames.append(dict(occluders=occluders, instances=crossing))
    for t in range(3):
        moving = [
            inst(1, "a", [-0.5, -0.4, 0.1 + t * 0.6, 0.5, 0.4, 0.6 + t * 0.6]),
            inst(2, "b", [-0.2, -0.2, 0.2 + t * 0.9, 0.2, 0.2, 0.5 + t * 0.9]),
            inst(3, "c", [0.1, 0.1, 0.5 + t * 0.2, 0.6, 0.6, 0.9 + t * 0.2]),
        ]
        frames.append(dict(occluders=occluders, instances=moving))
    return dict(size=[width, height], near=near, frames=frames)


def fast_motion_scene(width=32, height=24, near=0.25):
    """Instances move so far per frame that a previous visible set is useless."""
    frames = []
    for t in range(4):
        instances = []
        for idx in range(16):
            phase = (t * 3 + idx) % 16
            cx = -1.0 + 2.0 * (phase + 0.5) / 16.0
            instances.append(grid_instance(idx + 1, "m%d" % (idx % 2), cx, 0.1, 3.0))
        occluders = [occ([0, 0, width // 2, height], 1.0 + 0.05 * (t % 2))]
        frames.append(dict(occluders=occluders, instances=instances))
    return dict(size=[width, height], near=near, frames=frames)


def offscreen_scene(width=32, height=24, near=0.25):
    """Instances projecting wholly outside the viewport."""
    instances = [
        inst(1, "a", [-4.0, 0.2, 1.0, -3.0, 0.4, 1.2]),
        inst(2, "a", [3.0, 0.2, 1.0, 4.0, 0.4, 1.2]),
        inst(3, "b", [0.1, 0.1, 1.0, 0.2, 0.2, 1.2]),
    ]
    return dict(size=[width, height], near=near,
                frames=[dict(occluders=[], instances=instances)])


def transparent_occluder_scene(width=40, height=30, near=0.25):
    """A transparent quad must not cull; the same quad opaque must cull."""
    instances = [grid_instance(i + 1, "a", -0.4 + 0.2 * i, 0.0, 2.0) for i in range(5)]
    frames = [
        dict(occluders=[occ([0, 0, width, height], 1.0, opaque=True)],
             instances=instances),
        dict(occluders=[occ([0, 0, width, height], 1.0, opaque=False)],
             instances=instances),
        dict(occluders=[occ([0, 0, width, height], 1.0, opaque=True),
                        occ([0, 0, width, height], 1.0, opaque=False)],
             instances=instances),
    ]
    return dict(size=[width, height], near=near, frames=frames)


def randomized_scene(seed, width=48, height=32, near=0.25, frames=3, count=40):
    rng = random.Random(seed)
    all_frames = []
    for _ in range(frames):
        instances = []
        for idx in range(count):
            z0 = rng.uniform(0.1, 3.5)
            z1 = z0 + rng.uniform(0.02, 0.6)
            x0 = rng.uniform(-1.6, 1.4)
            y0 = rng.uniform(-1.2, 1.0)
            w = rng.uniform(0.01, 0.4)
            h = rng.uniform(0.01, 0.35)
            instances.append(inst(idx + 1, "m%d" % rng.randrange(3),
                                  [x0, y0, z0, x0 + w, y0 + h, z1]))
        occluders = []
        for _ in range(rng.randrange(0, 7)):
            x0 = rng.randrange(-4, width + 4)
            y0 = rng.randrange(-4, height + 4)
            x1 = min(width + 4, x0 + rng.randrange(1, width))
            y1 = min(height + 4, y0 + rng.randrange(1, height))
            occluders.append(occ([x0, y0, max(x0 + 1, x1), max(y0 + 1, y1)],
                                 rng.uniform(0.3, 4.0),
                                 opaque=(rng.random() < 0.8)))
        all_frames.append(dict(occluders=occluders, instances=instances))
    return dict(size=[width, height], near=near, frames=all_frames)


def shuffled(scene, seed):
    """Copy of a scene with instance order permuted (results must not change)."""
    rng = random.Random(seed)
    copy = dict(size=list(scene["size"]), near=scene["near"], frames=[])
    for frame in scene["frames"]:
        instances = list(frame["instances"])
        rng.shuffle(instances)
        copy["frames"].append(dict(occluders=list(frame["occluders"]),
                                   instances=instances))
    return copy
