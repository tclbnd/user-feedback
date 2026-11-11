#!/usr/bin/env python3
"""One-command local acceptance for the deterministic software renderer.

    python3 acceptance.py

Sections:
    1. cube regression (golden statistics + output digest)
    2. clip-space unit checks (six planes, near-plane straddling, UVs)
    3. pixel-level scene regressions (quad splits, shared edges, overlap,
       mixed winding, near-plane crossing, camera through surface)
    4. independent ray-cast reference comparison (coverage masks and
       depth/UV at selected points)
    5. determinism across tile execution orders and worker counts
    6. large-scene performance and peak memory per tile size

Exits 0 when every check passes, 1 otherwise.
"""

from __future__ import annotations

import math
import resource
import sys
import time

import clipspace
import obj_loader
import rasterizer
import reference
import scenes
import texture as texture_mod

CHECKER = texture_mod.DEFAULT_CHECKER
FOV_REF = 45.0

# Golden values recorded for the reference machine.  Re-record by running
# the corresponding section and pasting the printed "actual" values.
GOLDEN_CUBE = {
    "triangle_count": 12,
    "foreground_pixels": 96100,
    "depth_min": 4.0,
    "depth_max": 4.0,
    "digest": "8476101b178a2ea40c42eaad4149815d727d26806e311e43d910314d0ab942f3",
}
GOLDEN_FRONT_BACK_DIGEST = "af9db3519626b77a7a0cd1997ba302d94abc2a77d1f2b880b8fa9e8fb644d0c3"
GOLDEN_MIXED_WINDING_DIGEST = "cd7cdcbbce4be8875509a3037b3705741b5b43f9c1a4f9a980088af8405ccaf1"

FAILURES = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    line = "  [%s] %s" % (status, name)
    if detail:
        line += " -- " + detail
    print(line)
    if not ok:
        FAILURES.append(name)


def peak_rss_mb(which=resource.RUSAGE_SELF) -> float:
    rss = resource.getrusage(which).ru_maxrss
    if sys.platform == "darwin":
        return rss / (1024.0 * 1024.0)  # bytes on macOS
    return rss / 1024.0                 # kilobytes on Linux


# ---------------------------------------------------------------------------
# 1. Cube regression
# ---------------------------------------------------------------------------

def section_cube() -> None:
    print("1. cube regression (assets/cube.obj, default framing)")
    mesh = obj_loader.load_mesh("assets/cube.obj")
    result = rasterizer.render(
        mesh.positions, mesh.uvs, mesh.faces,
        size=512, samples=1, tile_size=64,
    )
    digest = result.digest()
    print("  actual: foreground=%d ratio=%.4f depth=%.4f..%.4f digest=%s"
          % (result.foreground_pixels, result.foreground_ratio,
             result.depth_min or 0.0, result.depth_max or 0.0, digest))
    check("triangle count", result.triangle_count == GOLDEN_CUBE["triangle_count"],
          "%d" % result.triangle_count)
    check("foreground pixels",
          result.foreground_pixels == GOLDEN_CUBE["foreground_pixels"],
          "%d vs golden %d" % (result.foreground_pixels,
                               GOLDEN_CUBE["foreground_pixels"]))
    check("foreground ratio sane", 0.05 <= result.foreground_ratio <= 0.90,
          "%.4f" % result.foreground_ratio)
    check("depth range",
          abs(result.depth_min - GOLDEN_CUBE["depth_min"]) < 1e-9
          and abs(result.depth_max - GOLDEN_CUBE["depth_max"]) < 1e-9,
          "%.4f..%.4f" % (result.depth_min, result.depth_max))
    check("output digest", digest == GOLDEN_CUBE["digest"], digest)


# ---------------------------------------------------------------------------
# 2. Clip-space unit checks
# ---------------------------------------------------------------------------

def section_clip() -> None:
    print("2. clip-space unit checks")
    proj = clipspace.make_projection(256, 256, fov_deg=90.0, distance=5.0,
                                     near=0.5, far=50.0)

    def cv(x, y, z, u=0.0, v=0.0):
        return clipspace.world_to_clip((x, y, z), u, v, proj)

    # Fully behind the near plane -> empty.
    behind = clipspace.clip_triangle([
        cv(-1.0, 0.0, 5.4), cv(1.0, 0.0, 5.4), cv(0.0, 1.0, 5.6)])
    check("triangle behind near plane clipped away", behind == [])

    # Fully beyond the far plane -> empty.
    beyond = clipspace.clip_triangle([
        cv(-100.0, 0.0, -100.0), cv(100.0, 0.0, -100.0), cv(0.0, 100.0, -120.0)])
    check("triangle beyond far plane clipped away", beyond == [])

    # Straddling the near plane: one vertex in front, two behind.
    tri = [cv(0.0, 1.0, 4.0, u=0.0, v=0.0),
           cv(-1.0, -1.0, 6.0, u=1.0, v=0.0),
           cv(1.0, -1.0, 6.0, u=0.0, v=1.0)]
    clipped = clipspace.clip_triangle(tri)
    ok = len(clipped) > 0
    finite = True
    inside = True
    for out_tri in clipped:
        for vert in out_tri:
            x, y, z, w, u, v = vert
            if not all(math.isfinite(c) for c in vert):
                finite = False
            if w <= 0.0:
                inside = False
            dists = (x + w, w - x, y + w, w - y, z + w, w - z)
            if min(dists) < -1e-6:
                inside = False
    check("near-straddling triangle produces output", ok,
          "%d clipped triangles" % len(clipped))
    check("clipped vertices finite and inside frustum", finite and inside)

    # UV interpolation along a clipped edge is linear in clip space.
    a = cv(0.0, 0.0, 4.0, u=0.0, v=0.0)   # view depth 1.0 (in front)
    b = cv(0.0, 0.0, 6.0, u=2.0, v=0.0)   # view depth -1.0 (behind)
    c = cv(0.0, 1.0, 4.0, u=0.0, v=2.0)
    clipped = clipspace.clip_triangle([a, b, c])
    # The a-b edge is cut where view depth == near == 0.5, i.e. t = 0.25.
    expected_u = 2.0 * 0.25
    found = False
    for out_tri in clipped:
        for vert in out_tri:
            if abs(vert[4] - expected_u) < 1e-9 and abs(vert[5]) < 1e-9:
                found = True
    check("clip edge interpolates UV linearly in clip space", found,
          "expected u=%.6f" % expected_u)


# ---------------------------------------------------------------------------
# 3. Pixel-level scene regressions
# ---------------------------------------------------------------------------

def _render(scene, **kwargs):
    positions, uvs, faces = scene
    return rasterizer.render(positions, uvs, faces, **kwargs)


def section_regressions() -> None:
    print("3. pixel-level scene regressions")

    # 3a. Coplanar quad: both triangulations must give identical coverage
    # and color; depth/UV agree to within interpolation rounding (the two
    # diagonals evaluate the same plane with different barycentrics).
    for samples in (1, 4):
        ra = _render(scenes.coplanar_quad("a"), size=128, samples=samples,
                     texture=CHECKER)
        rb = _render(scenes.coplanar_quad("b"), size=128, samples=samples,
                     texture=CHECKER)
        exact = True
        depth_err = 0.0
        uv_err = 0.0
        for y in range(128):
            for x in range(128):
                if (ra.coverage[y][x] != rb.coverage[y][x]
                        or ra.pixels[y][x] != rb.pixels[y][x]):
                    exact = False
                if ra.coverage[y][x]:
                    depth_err = max(
                        depth_err,
                        abs(ra.depth[y][x] - rb.depth[y][x]) / ra.depth[y][x])
                    uv_err = max(uv_err,
                                 abs(ra.attr_u[y][x] - rb.attr_u[y][x]),
                                 abs(ra.attr_v[y][x] - rb.attr_v[y][x]))
        check("coplanar quad split A == split B: coverage+color (%dx)" % samples,
              exact, "covered=%d" % ra.foreground_pixels)
        # With 4x sampling the per-pixel UV comes from the nearest covered
        # sub-sample; a 1-ulp depth difference between the two diagonals
        # can legitimately flip which sub-sample that is, shifting UV by
        # one pixel's UV gradient (~1/128 of the UV span here).
        uv_tol = 1e-9 if samples == 1 else 2e-2
        check("coplanar quad split A ~= split B: depth/UV (%dx)" % samples,
              depth_err < 1e-9 and uv_err < uv_tol,
              "depth err %.2e, uv err %.2e" % (depth_err, uv_err))

    # 3b. Shared edges belong to exactly one triangle (no gap, no overlap).
    scene = scenes.frustum_boundary_shared_edge()
    positions, uvs, faces = scene
    pairs = [("frustum-boundary edge", faces[0], faces[1]),
             ("pixel-centre diagonal", faces[2], faces[3])]
    for samples in (1, 4):
        for name, fa, fb in pairs:
            only_a = _render(scene[:2] + ([fa],), size=256, fov_deg=90.0,
                             samples=samples, cull_backfaces=False,
                             texture=CHECKER)
            only_b = _render(scene[:2] + ([fb],), size=256, fov_deg=90.0,
                             samples=samples, cull_backfaces=False,
                             texture=CHECKER)
            both = _render(scene[:2] + ([fa, fb],), size=256, fov_deg=90.0,
                           samples=samples, cull_backfaces=False,
                           texture=CHECKER)
            ok = True
            for y in range(256):
                for x in range(256):
                    ma = only_a.coverage[y][x]
                    mb = only_b.coverage[y][x]
                    if ma & mb or (ma | mb) != both.coverage[y][x]:
                        ok = False
            check("shared edge owned by exactly one triangle: %s (%dx)"
                  % (name, samples), ok)

    # 3c. Front/back overlap: nearer quad wins; digest pinned.
    front_back = _render(scenes.front_back_overlap(), size=128,
                         texture=CHECKER)
    digest = front_back.digest()
    print("  actual: front/back digest=%s" % digest)
    check("front/back overlap digest", digest == GOLDEN_FRONT_BACK_DIGEST,
          digest)
    # The front quad is centred right; its centre pixel must be nearer
    # than a back-only pixel on the left.
    front_depth = front_back.depth[64][80]
    back_depth = front_back.depth[64][20]
    check("front quad occludes back quad",
          front_depth < back_depth,
          "front=%.4f back=%.4f" % (front_depth, back_depth))

    # 3d. Mixed winding with culling disabled: pinned digest.
    mixed = _render(scenes.mixed_winding(), size=128, cull_backfaces=False,
                    texture=CHECKER)
    digest = mixed.digest()
    print("  actual: mixed winding digest=%s" % digest)
    check("mixed winding digest", digest == GOLDEN_MIXED_WINDING_DIGEST, digest)

    # 3e. Degenerate and tiny triangles never produce invalid depth.
    deg_positions = [
        (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0),      # zero area
        (1.0, 1.0, 0.0), (2.0, 2.0, 0.0), (3.0, 3.0, 0.0),      # collinear
        (-0.5, -0.5, 0.5), (1.0, 0.0, 0.5), (0.0, 1.0, 0.5),    # normal
        (0.1, 0.1, 0.2), (0.1001, 0.1, 0.2), (0.1, 0.1001, 0.2),  # sub-pixel
    ]
    deg_faces = [((0, 1, 2), (-1, -1, -1)), ((3, 4, 5), (-1, -1, -1)),
                 ((6, 7, 8), (-1, -1, -1)), ((9, 10, 11), (-1, -1, -1))]
    deg = _render((deg_positions, [], deg_faces), size=128,
                  cull_backfaces=False)
    finite = all(
        math.isfinite(deg.depth[y][x])
        for y in range(deg.height) for x in range(deg.width)
    )
    check("degenerate/tiny triangles keep depth finite everywhere", finite,
          "drawn=%d of %d" % (deg.triangles_drawn, deg.triangle_count))

    # 3f. Camera through surface: every covered depth is finite and valid.
    through = _render(scenes.camera_through_surface(), size=128,
                      cull_backfaces=False, texture=CHECKER)
    finite = True
    for y in range(through.height):
        for x in range(through.width):
            if through.coverage[y][x]:
                d = through.depth[y][x]
                if not (math.isfinite(d) and 0.25 - 1e-9 <= d <= 500.0):
                    finite = False
    check("camera-through-surface depths finite and in [near, far]", finite,
          "covered=%d" % through.foreground_pixels)
    check("camera-through-surface covers part of the image",
          0 < through.foreground_pixels < through.width * through.height)


# ---------------------------------------------------------------------------
# 4. Independent reference comparison
# ---------------------------------------------------------------------------

def _compare_with_reference(name, scene, size, samples, cull, texture,
                            max_probes=8, fov_deg=FOV_REF):
    positions, uvs, faces = scene
    proj = clipspace.make_projection(size, size, fov_deg=fov_deg,
                                     distance=5.0, near=0.25, far=500.0)
    result = _render(scene, size=size, fov_deg=fov_deg, samples=samples,
                     cull_backfaces=cull, texture=texture)
    offsets = rasterizer.SAMPLE_OFFSETS[samples]
    ref_masks = reference.coverage_mask(positions, faces, uvs, proj, offsets,
                                        cull_backfaces=cull)
    mismatches = 0
    for y in range(size):
        for x in range(size):
            if ref_masks[y][x] != result.coverage[y][x]:
                mismatches += 1
    check("%s: coverage mask matches reference (%dx)" % (name, samples),
          mismatches == 0, "%d mismatched pixels" % mismatches)

    # Selected probe points: a deterministic, evenly spaced subset of the
    # covered pixels.  For each, find the nearest covered sub-sample via
    # the reference and compare the pixel's depth/UV against it.
    covered = [(x, y) for y in range(size) for x in range(size)
               if result.coverage[y][x]]
    if not covered:
        check("%s: depth/UV probe points (%dx)" % (name, samples), False,
              "no covered pixels")
        return
    step = max(1, len(covered) // max_probes)
    probes = covered[::step][:max_probes]

    worst_depth = 0.0
    worst_uv = 0.0
    compared = 0
    for (px, py) in probes:
        best = None
        for si, (ox, oy) in enumerate(offsets):
            if not (result.coverage[py][px] >> si) & 1:
                continue
            hit = reference.sample_ray(positions, faces, uvs, proj,
                                       px + ox, py + oy, cull_backfaces=cull)
            if not hit[0]:
                continue
            if best is None or hit[1] < best[1]:
                best = hit
        if best is None:
            continue
        compared += 1
        _c, ref_depth, ref_u, ref_v, _f = best
        worst_depth = max(
            worst_depth,
            abs(result.depth[py][px] - ref_depth) / max(ref_depth, 1e-12))
        worst_uv = max(worst_uv, abs(result.attr_u[py][px] - ref_u),
                       abs(result.attr_v[py][px] - ref_v))
    check("%s: depth/UV at %d probe points (%dx)" % (name, compared, samples),
          compared > 0 and worst_depth < 1e-6 and worst_uv < 1e-6,
          "rel depth err %.2e, uv err %.2e" % (worst_depth, worst_uv))



def section_reference() -> None:
    print("4. independent ray-cast reference comparison")
    _compare_with_reference(
        "coplanar quad", scenes.coplanar_quad("a"), 128, 1, True, CHECKER)
    _compare_with_reference(
        "textured near-plane crossing", scenes.textured_near_crossing(),
        128, 1, True, CHECKER)
    _compare_with_reference(
        "textured near-plane crossing", scenes.textured_near_crossing(),
        128, 4, True, CHECKER)
    _compare_with_reference(
        "front/back overlap", scenes.front_back_overlap(), 128, 1, True,
        CHECKER)
    _compare_with_reference(
        "mixed winding (no cull)", scenes.mixed_winding(), 128, 1, False,
        CHECKER)
    _compare_with_reference(
        "camera through surface (no cull)", scenes.camera_through_surface(),
        128, 1, False, CHECKER)
    _compare_with_reference(
        "frustum-boundary shared edges (no cull)",
        scenes.frustum_boundary_shared_edge(), 256, 1, False, CHECKER,
        fov_deg=90.0)


# ---------------------------------------------------------------------------
# 5. Determinism across tile order and worker count
# ---------------------------------------------------------------------------

def section_determinism() -> None:
    print("5. determinism across tile order and worker count (large mesh)")
    scene = scenes.large_mesh(grid=60)
    digests = {}
    for order in ("forward", "reverse", "shuffle"):
        for workers in (1, 2, 4):
            result = _render(scene, size=512, samples=1, tile_size=32,
                             workers=workers, tile_order=order,
                             texture=CHECKER)
            digests[(order, workers)] = result.digest()
            print("  order=%-8s workers=%d digest=%s"
                  % (order, workers, result.digest()))
    unique = set(digests.values())
    check("color/depth/coverage identical for all orders and worker counts",
          len(unique) == 1, "%d distinct digests" % len(unique))


# ---------------------------------------------------------------------------
# 6. Performance and memory
# ---------------------------------------------------------------------------

def section_performance() -> None:
    print("6. large-scene performance and peak memory")
    scene = scenes.large_mesh(grid=100)
    print("  scene: %d triangles, 512x512, 1x sampling" % len(scene[2]))
    for tile_size in (16, 32, 64, 128):
        start = time.perf_counter()
        result = _render(scene, size=512, samples=1, tile_size=tile_size,
                         texture=CHECKER)
        elapsed = time.perf_counter() - start
        rss = peak_rss_mb()
        print("  tile=%4d  time=%6.2fs  peak-rss=%7.1f MB  covered=%d"
              % (tile_size, elapsed, rss, result.foreground_pixels))
    start = time.perf_counter()
    _render(scene, size=512, samples=1, tile_size=64, workers=4,
            texture=CHECKER)
    elapsed = time.perf_counter() - start
    child = peak_rss_mb(resource.RUSAGE_CHILDREN)
    print("  tile=%4d  time=%6.2fs  peak-child-rss=%7.1f MB  (workers=4)"
          % (64, elapsed, child))
    check("performance section completed", True)


# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 72)
    print("deterministic software renderer -- local acceptance")
    print("=" * 72)
    section_cube()
    section_clip()
    section_regressions()
    section_reference()
    section_determinism()
    section_performance()
    print("=" * 72)
    if FAILURES:
        print("RESULT: FAIL (%d checks failed)" % len(FAILURES))
        for name in FAILURES:
            print("  - %s" % name)
        return 1
    print("RESULT: PASS (all checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
