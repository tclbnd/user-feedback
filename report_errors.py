"""Print acceptance error margins (inspection helper, not part of the suite)."""
import cube
from test_cube import (
    AXES,
    PrefilterTests,
    deterministic_dirs,
    edge_and_corner_directions,
    rasterize,
    ref_locate,
    rotated_env,
    FACES,
)


def main():
    faces = rasterize(16)
    interior = 0.0
    edge = 0.0
    const_worst = 0.0
    const_faces = {f: [[0.4] * 16 for _ in range(16)] for f in FACES}
    for d in edge_and_corner_directions():
        const_worst = max(const_worst, abs(cube.sample(const_faces, d) - 0.4))
        edge = max(edge, abs(cube.sample(faces, d) - rotated_env(d)))
    for d in deterministic_dirs(400):
        _, u, v = ref_locate(d)
        if abs(u) <= 0.8 and abs(v) <= 0.8:
            interior = max(interior, abs(cube.sample(faces, d) - rotated_env(d)))
    print("constant env linear max error : %.2e (limit 1e-5)" % const_worst)
    print("interior direction max error   : %.5f" % interior)
    print("edge/corner cross-face error   : %.5f (must be <= interior)" % edge)

    helper = PrefilterTests()
    directions = list(AXES)
    directions += [
        cube._norm(1, 1, 0),
        cube._norm(1, -1, 0.3),
        cube._norm(0.2, 1, -1),
        cube._norm(-1, 1, 1),
        cube._norm(1, -1, -1),
    ]
    directions += deterministic_dirs(6)
    for r in (0.25, 0.5, 0.75, 1.0, 1.3):
        errs = []
        ref_mean = 0.0
        for d in directions:
            ref = helper.sphere_reference(d, r)
            payload = {
                "faces": rasterize(24),
                "directions": [list(d)],
                "roughness": [r],
                "sizes": [4],
            }
            got = cube.run(payload)["levels"][0]["samples"][0]
            errs.append(abs(got - ref))
            ref_mean += ref
        ref_mean /= len(directions)
        normalized = sum(errs) / len(errs) / ref_mean
        print("r=%.2f normalized mean error=%.4f (limit 0.03)" % (r, normalized))


if __name__ == "__main__":
    main()
