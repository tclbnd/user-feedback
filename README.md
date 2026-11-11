# Deterministic Software OBJ Renderer

A dependency-free software rasterizer (Python standard library only) that
renders Wavefront OBJ meshes to 8-bit RGB PNG images and per-pixel
depth / coverage / UV channels, built for **deterministic offline dataset
generation**: identical input always produces bit-identical output,
regardless of tile execution order or worker process count.

## Usage

```sh
python3 render.py assets/cube.obj render.png
python3 render.py assets/cube.obj render.png --samples 4 --tile-size 32 --workers 4
python3 render.py mesh.obj render.png --texture --depth-png depth.png
python3 acceptance.py          # one-command local acceptance
```

```
usage: render.py [-h] [--size SIZE] [--fov FOV] [--distance DISTANCE]
                 [--near NEAR] [--far FAR] [--light LIGHT]
                 [--samples {1,4}] [--tile-size TILE_SIZE]
                 [--workers WORKERS] [--texture] [--depth-png PATH]
                 [--no-cull]
                 input output
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--size N` | `512` | Square output resolution in pixels |
| `--fov DEG` | `45` | Vertical field of view |
| `--distance D` | `5` | Camera distance along the +Z axis |
| `--near` / `--far` | `0.25` / `500` | Clip distances (view-space depth) |
| `--light X,Y,Z` | `0.45,0.8,1` | Direction *towards* the directional light |
| `--samples {1,4}` | `1` | Sub-samples per pixel (1x or 2x2 grid) |
| `--tile-size N` | `64` | Rasterization tile size in pixels |
| `--workers N` | `1` | Worker processes for tile rasterization |
| `--texture` | off | Procedural checker texture from `vt` UVs |
| `--depth-png PATH` | off | Also write a grayscale depth visualization |
| `--no-cull` | off | Disable backface culling |

## Pipeline and conventions

1. **Clip space** (`clipspace.py`) — world points become view points
   `(x, y, z - distance)` and are mapped by an OpenGL-style perspective
   matrix; `w` equals view-space depth.  Triangles are clipped against
   **all six frustum planes** (`-w <= x,y,z <= w`) with
   Sutherland-Hodgman; UVs are interpolated linearly in clip space along
   clipped edges.  A 1e-9 relative tolerance keeps vertices that are
   mathematically on a plane (e.g. shared edges exactly on the frustum
   boundary) bit-identical across adjacent triangles.
2. **Perspective divide** (`rasterizer._project_prim`) — one divide per
   vertex; screen-space interpolation uses a single convention
   everywhere: `1/w`, `u/w`, `v/w` are linear in screen space, view depth
   is `1 / (1/w)`, attributes are `(a/w) / (1/w)`.  Zero-area and
   `w <= 0` primitives are dropped, so depth is always finite.
3. **Tiled rasterization** (`rasterizer._rasterize_tile`) — primitives
   are binned into screen tiles and each tile is rasterized
   independently at 1x (pixel centre) or 4x (2x2 sub-samples).

### Determinism rules

* **Fill rule** — triangles are normalized to positive signed area; a
  sample is inside when every edge function is positive, or exactly zero
  on a **top-left** edge (`dy < 0`, or `dy == 0 and dx < 0` in y-down
  screen space).  A shared edge belongs to exactly one triangle.
* **Occlusion** — resolved per sample with the total order
  `(1/w, -primitive_id)`: nearer wins, exact depth ties go to the
  earliest input primitive.  The order is total, so the result is
  independent of evaluation order, tile order and worker count.
* **Degenerate input** — zero-area and collinear triangles are skipped
  before rasterization; uncovered pixels read as the far-plane depth, so
  every output depth is finite and valid.

## Acceptance (`acceptance.py`)

`python3 acceptance.py` runs six sections and exits non-zero on failure:

1. **Cube regression** — `assets/cube.obj` at default framing must match
   golden statistics (12 triangles, 96,100 foreground pixels, 36.66%,
   depth 4.0) and a golden SHA-256 over color+coverage+depth+UV.
2. **Clip-space unit checks** — near/far rejection, near-plane
   straddling, UV interpolation along a clipped edge.
3. **Pixel-level scene regressions** — coplanar quad in both
   triangulations (identical coverage+color, depth/UV to 1e-9), shared
   edges owned by exactly one triangle (separate vs. combined renders,
   1x and 4x), front/back occlusion, mixed winding, degenerate/sub-pixel
   triangles, camera-through-surface with finite depths.
4. **Independent reference** — `reference.py` re-implements visibility
   with per-sample ray casting (Moeller-Trumbore), sharing no clip or
   coverage code with the main pipeline; full-image coverage masks must
   match exactly and depth/UV at selected probe points to 1e-6.
5. **Determinism** — a 7,200-triangle mesh rendered with tile orders
   forward/reverse/shuffle and 1/2/4 workers must give one identical
   digest.
6. **Performance** — a 20,000-triangle heightfield at tile sizes
   16/32/64/128 with wall time and peak RSS per run.

## Files

* `obj_loader.py` — OBJ parser: `v`, `vt`, `f` (all reference forms,
  negative indices, fan triangulation).  `load_mesh` returns positions,
  UVs and indexed faces; `load_obj` keeps the legacy triangle-list view.
* `clipspace.py` — projection and homogeneous six-plane clipping.
* `rasterizer.py` — primitive preparation, tile binning, 1x/4x sampled
  tile rasterization, deterministic merge, `RenderResult` with
  color/depth/coverage/UV channels and a stable `digest()`.
* `texture.py` — procedural checker texture (picklable spec tuples).
* `reference.py` — independent ray-cast reference path.
* `scenes.py` — deterministic scene generators (coplanar quad, textured
  near-plane crossing, front/back overlap, mixed winding, camera through
  surface, frustum-boundary shared edges, large heightfield).
* `png_writer.py` — from-scratch PNG encoder (zlib + struct).
* `render.py` — CLI entry point.
* `acceptance.py` — one-command local acceptance.

## Limitations

* Pure standard library; no NumPy/Pillow/OpenGL.
* Flat per-face Lambert shading plus optional procedural texture; no
  materials, vertex normals or transformations (meshes render in object
  space).
* Square output only (`--size`), matching the CLI contract.
* 4x sampling uses an ordered 2x2 sub-sample grid; coverage masks are
  4-bit per pixel.
