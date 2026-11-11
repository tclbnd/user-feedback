# Deterministic Software OBJ Renderer

A dependency-free software rasterizer for offline dataset generation.  It
reads Wavefront OBJ meshes and writes 8-bit RGB PNG images using only the
Python standard library.  No Pillow, NumPy, or OpenGL is involved.

The renderer is deterministic by construction: the same triangles, camera
and sampling settings produce byte-identical colour, depth, coverage, UV and
primitive-id buffers regardless of tile size, tile execution order or worker
count.

## Usage

```sh
python3 render.py assets/cube.obj render.png
python3 render.py assets/cube.obj render.png --size 256 --fov 50 --distance 6
python3 render.py assets/cube.obj render.png --no-cull --light 0.2,0.9,1.0

# Four samples per pixel, 16-pixel tiles, four workers, reverse tile order.
python3 render.py scene.obj render.png --samples 4 --tile-size 16 --workers 4 \
    --tile-order reverse
```

```
usage: render.py [-h] [--size SIZE] [--fov FOV] [--distance DISTANCE]
                 [--light LIGHT] [--no-cull] [--samples {1,4}]
                 [--tile-size TILE_SIZE] [--workers WORKERS]
                 [--tile-order {row,reverse,serpentine,column,shuffle}]
                 [--near NEAR] [--far FAR]
                 input output
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--size N` | `512` | Square output resolution in pixels |
| `--fov DEG` | `45` | Vertical field of view |
| `--distance D` | `5` | Camera distance along the +Z axis |
| `--light X,Y,Z` | `0.45,0.8,1` | Direction *towards* the directional light |
| `--no-cull` | off | Disable backface culling |
| `--samples 1\|4` | `1` | Pixel-centre sampling or four sub-pixel samples |
| `--tile-size N` | `32` | Tile edge length used for bucketing |
| `--workers N` | `1` | Worker processes (thread fallback if `fork` is unavailable) |
| `--tile-order ORDER` | `row` | `row`, `reverse`, `serpentine`, `column`, `shuffle` |
| `--near D` | `1e-6` | Near clip distance in view units |
| `--far D` | `1e9` | Far clip distance in view units |

Default camera: eye at `(0, 0, 5)`, looking at the origin down `-Z`, vertical
FOV 45°.  The unit cube spans `z = -1..1`, so the front face is 4 units from
the camera.

## One-click acceptance

```sh
python3 acceptance.py
```

This runs every check and exits non-zero on the first category of failure.
It performs:

1. **Cube regression** — renders `assets/cube.obj` through `render.py`,
   decodes the written PNG, and compares it byte-for-byte with
   `tests/golden/cube.png`.
2. **Pixel-level golden regression** — all new scenes at 128×128 in 1× and
   4× modes, including colour, depth, coverage, UV and primitive-id digests.
3. **OBJ attribute path** — a textured, near-clipped mesh is written to OBJ,
   loaded back, and must match the in-memory scene exactly.
4. **Independent ray reference** — a separate brute-force ray caster
   (Möller–Trumbore against the original unclipped triangles) at 32×32
   checks coverage masks, resolved depth and UV values.
5. **Shared-edge ownership** — per-sample masks of adjacent triangles are
   disjoint and their union equals the combined coverage, including at
   frustum-clipped boundaries.
6. **Tiny / degenerate / equal-depth primitives** — sub-pixel triangles
   still cover their sample, zero-area triangles produce nothing, and
   exactly equal depths are resolved by the lower stable primitive id.
7. **Determinism** — 24 combinations of tile size, tile order and worker
   count are compared through buffer digests for every scene and both
   sampling modes.
8. **Large-scene benchmark** — a 192×192 quad grid (73,728 triangles) is
   rendered at 256×256 with tile sizes 16, 32, 64 and 128; wall time and
   tracemalloc peak are reported.

`python3 acceptance.py --skip-benchmark` omits step 8.

### Committed golden files

`tests/golden/` contains the cube baseline, the 1×/4× PNGs for every
regression scene, and `digests.json` with SHA-256 digests of the colour,
depth, coverage, UV and primitive-id buffers.  Regenerate deliberately with:

```sh
python3 tools/generate_goldens.py
```

## Cube regression

Running `python3 render.py assets/cube.obj render.png` prints:

```
triangle count: 12
triangles rasterized: 4
foreground pixels: 96100
foreground ratio: 36.66%
depth range: 4.0000 .. 4.0000
resolution: 512x512
output: render.png
```

The rendered image is byte-identical to the original baseline
(`sha256 6a3ad8202e916207ce6ba43ad2e6181fc896a07e8596547e077b4bffa3b10b62`).
Independent decoding confirms a valid PNG signature/CRCs, `IHDR`/`IDAT`/
`IEND` chunks, 512×512, bit depth 8, colour type 2, and decompressed scanline
size `512 * (1 + 3 * 512) = 786944` bytes.

The cube is viewed exactly face-on, so the visible front face is a flat
square at depth 4 and its two triangles are rendered twice each in the
summary: the asset's two +Y triangles are wound inward despite the file's
comment, so the face-normal culling test keeps them; the z-buffer then
correctly hides them behind the front face.  Use `--no-cull` to rasterize
all 12 triangles; the image is identical.

## Rendering pipeline

### Homogeneous clip space and six-plane clipping

For a view-space point `(x_v, y_v, z_v)` the renderer builds homogeneous
clip coordinates

```
x_c = x_v / (tan(fov/2) * aspect)
y_c = y_v / tan(fov/2)
w_c = z_v                      # view-space depth
```

and clips triangles with Sutherland–Hodgman against all six frustum planes:

| Plane | Inside test |
| --- | --- |
| left | `x_c + w_c >= 0` |
| right | `w_c - x_c >= 0` |
| bottom | `y_c + w_c >= 0` |
| top | `w_c - y_c >= 0` |
| near | `w_c - near >= 0` |
| far | `far - w_c >= 0` |

Every edge/plane intersection interpolates the homogeneous coordinates **and**
the per-corner UV attributes.  A triangle straddling the near plane therefore
becomes a clipped polygon instead of being dropped; the polygon is fan
triangulated and all resulting pieces keep the original stable primitive id.

### One perspective divide, one depth convention

Perspective division happens once, after clipping:

```
x_ndc = x_c / w_c
y_ndc = y_c / w_c
screen_x = (x_ndc + 1) * 0.5 * width
screen_y = (1 - y_ndc) * 0.5 * height
depth = w_c                    # positive view-space distance
```

Attributes interpolate perspective-correctly with the linear pair
`(1/w_c, attr/w_c)`: barycentrics are computed in screen space, then
`attr = interp(attr/w) / interp(1/w)`.

After clipping every `w_c` is at least `near`, so every emitted fragment has a
finite, positive depth in `[near, far]`.  A final clamp guards against
last-ulp excursions.  Zero-area triangles are skipped, while arbitrarily
small but non-zero triangles remain rasterizable because no minimum screen
area is imposed.

### Tiles, samples and stable resolution

Clipped screen triangles are bucketed into square tiles.  Each tile is
rendered independently and the tiles are merged in a fixed row-major order,
which is why execution order and worker count cannot change the output.

* **1× sampling** — one sample at the pixel centre `(px+0.5, py+0.5)`.
* **4× sampling** — samples at `(0.25,0.25)`, `(0.75,0.25)`, `(0.25,0.75)`
  and `(0.75,0.75)` inside each pixel.

For each sample there is an independent depth/primitive buffer.  A fragment
replaces the current one only when it is strictly nearer; bucket entries are
sorted by `(original primitive id, sub-triangle index)`, so an exact depth
tie is won by the lower stable primitive id.  A small relative depth tolerance
prevents last-ulp noise between coplanar primitives from defeating that rule.

Shared edges use a top-left fill rule.  A sample on an edge is owned by
exactly one of the two adjacent triangles, so there are neither double-owned
samples nor cracks.

Resolution from samples to a pixel:

* **coverage mask** — one bit per sample (`0b0001` for 1×, four bits for 4×);
* **colour** — rounded average of the covered sample colours (deterministic
  integer arithmetic);
* **depth and UV** — taken from the nearest covered sample, with ties
  resolved by the lower primitive id and then the lower sample index;
* **primitive id** — the stable input index of the winning sample.

`RenderResult` exposes `pixels`, `coverage`, `depth_values`, `uv_values`,
`primitive_ids`, `depth_min`/`depth_max` and the usual counts.

### Workers

With `--workers N > 1` the renderer forks worker processes (inheriting the
prepared scene without serialising it).  If `fork` is unavailable it falls
back to a thread pool.  Tiles are still independent; results are merged by
tile index, so the worker implementation cannot affect the buffers.

## OBJ loader

`obj_loader.py` parses:

* `v x y z [w]` — vertex positions (`w` ignored);
* `vt u v [w]` — texture coordinates (`w` ignored);
* `f ...` — polygons using `v`, `v/vt`, `v//vn` or `v/vt/vn`, with 1-based,
  relative negative and mixed indices.

Face references are fan-triangulated as `(0, i, i+1)`.  Passing
`triangulation="alternate"` to the loader uses the other diagonal for quads
`(0,1,3), (1,2,3)`.  Texture coordinates are retained per corner; triangles
with a full set of UVs carry them through clipping and perspective-correct
interpolation.  Other records (`o`, `g`, `s`, `vn`, materials) are ignored.

Two APIs are provided:

* `load_obj(path)` — legacy position-only triangles (used by the cube
  regression);
* `load_obj_scene(path)` — `geometry.Triangle` records with per-corner UVs
  and optional flat colours (used by the CLI).

## Scenes and the independent reference

`scenes.py` deterministically generates the regression and benchmark set:

| Scene | Purpose |
| --- | --- |
| `coplanar_quad_02` / `_13` | the two diagonal splits of one coplanar square |
| `near_clip_textured` | a large UV-mapped tilted plane crossing the near plane |
| `camera_inside_box` | the camera lies inside a box; every face is near-clipped |
| `overlap` | a front quad occluding a larger back quad |
| `mixed_winding` | adjacent triangles with opposite winding, culling off |
| `frustum_edge_shared` | a sample-aligned shared diagonal clipped at left/bottom frustum planes |
| `large_grid(N)` | an `N × N` quad grid (2N² triangles) for benchmarks |

`reference.py` is intentionally independent of `rasterizer.py`: it does not
clip polygons and does not reuse the rasterizer's coverage test.  Instead it
casts one ray per sample through the camera and intersects the original
world-space triangles with Möller–Trumbore, keeping the nearest hit in
`(near, far]`.  This yields ground truth coverage masks, per-sample and
resolved depth, and perspective-correct UV values for small images.

## Benchmark

Representative result from `python3 acceptance.py` on the development
machine (192×192 grid, 73,728 triangles, 256×256 output, 1× sampling,
`collect_uv=False`):

```
tile   time(s)  peak(MB) foreground digest
16     0.996    60.7     65536      3c0a2310a501
32     1.039    60.1     65536      3c0a2310a501
64     0.992    59.7     65536      3c0a2310a501
128    0.978    59.6     65536      3c0a2310a501
```

Wall time is measured without `tracemalloc`; peak memory is the
`tracemalloc` high-water mark from a second render of the same scene (the
OBJ was loaded beforehand).  All four tile sizes produce identical
colour/depth/coverage digests.  Timings are machine-dependent.

## Files

| File | Role |
| --- | --- |
| `render.py` | CLI: load OBJ, render, write PNG, print summary |
| `rasterizer.py` | clipping, perspective projection, tiling, sampling, z-buffer resolution |
| `geometry.py` | shared `Triangle` type and input normalisation |
| `obj_loader.py` | Wavefront OBJ geometry + UV parser |
| `png_writer.py` | dependency-free truecolour PNG encoder |
| `png_reader.py` | small decoder used to verify golden images |
| `scenes.py` | deterministic regression/benchmark scenes and OBJ grid writer |
| `reference.py` | independent ray-cast validation path |
| `metrics.py` | buffer digests used by acceptance/golden tools |
| `acceptance.py` | one-click acceptance |
| `tools/generate_goldens.py` | regenerate committed golden files |
| `tests/golden/` | cube baseline, scene PNGs and buffer digests |

## Design notes and limitations

* Pure standard library: `math`, `struct`, `zlib`, `argparse`, `dataclasses`,
  `multiprocessing`, `concurrent.futures`, `tracemalloc`.
* Flat shading only: no materials, vertex normals or per-vertex colours in
  OBJ; the optional flat colour is used by generated scenes.
* Output is always square (`--size`).
* No anti-aliasing in 1× mode; 4× mode averages covered sample colours and
  exposes the per-sample coverage mask.
* OBJ faces are assumed planar and simple; non-convex polygons are
  fan-triangulated.
* The default far plane (`1e9`) is effectively unbounded for the scene
  scales used here; depth remains the finite view-space distance.
