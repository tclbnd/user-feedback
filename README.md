# Software OBJ Renderer

A small, dependency-free software rasterizer that renders Wavefront OBJ
meshes to 8-bit RGB PNG images using only the Python standard library.
No Pillow, NumPy, or OpenGL is involved.

## Usage

```sh
python3 render.py assets/cube.obj render.png
python3 render.py assets/cube.obj render.png --size 256 --fov 50 --distance 6
python3 render.py assets/cube.obj render.png --no-cull --light 0.2,0.9,1.0
```

```
usage: render.py [-h] [--size SIZE] [--fov FOV] [--distance DISTANCE]
                 [--light LIGHT] [--no-cull]
                 input output
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--size N` | `512` | Square output resolution in pixels |
| `--fov DEG` | `45` | Vertical field of view |
| `--distance D` | `5` | Camera distance along the +Z axis |
| `--light X,Y,Z` | `0.45,0.8,1` | Direction *towards* the directional light |
| `--no-cull` | off | Disable backface culling |

Default camera: eye at `(0, 0, 5)`, looking at the origin down `-Z`, vertical
FOV 45°.  The unit cube spans `z = -1..1`, so the front face is 4 units from
the camera.

## Acceptance result

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

Independent decoding of the written file confirms:

* valid PNG signature and CRCs, chunks `IHDR`/`IDAT`/`IEND`;
* `512x512`, bit depth 8, colour type 2 (truecolour RGB);
* decompressed scanline size `512 * (1 + 3 * 512) = 786944` bytes;
* 96,100 covered pixels (36.66%), inside the required 5–90% range;
* silhouette bounding box 310 × 310 pixels, centred in the image.

The cube is viewed exactly face-on, so the visible front face is a flat
square at depth 4 and its two triangles are rendered twice each in the
summary: the asset's two +Y triangles are wound inward despite the file's
comment, so the face-normal culling test keeps them; the z-buffer then
correctly hides them behind the front face.  Use `--no-cull` to rasterize
all 12 triangles; the image is identical.

## Files

### `obj_loader.py`

Parses `v` and `f` records.  Vertex positions are stored as
`(x, y, z)` tuples.  Face references accept every common form
(`v`, `v/vt`, `v//vn`, `v/vt/vn`), 1-based and negative indices, and
polygons are fan-triangulated as `(0, i, i+1)`.  Other OBJ records
(`o`, `g`, `s`, `vt`, `vn`, materials) are ignored.  Logical lines
continued with a trailing backslash are joined.  `load_obj(path)` returns
a list of triangles, each a tuple of three vertex tuples.

### `rasterizer.py`

* **Camera / projection** — a pinhole camera at `(0, 0, distance)` looks
  along `-Z`.  For a view-space point `(x_v, y_v, z_v)`:

  ```
  ndc_x = x_v / (z_v * tan(fov/2) * aspect)
  ndc_y = y_v / (z_v * tan(fov/2))
  screen_x = (ndc_x + 1) * 0.5 * width
  screen_y = (1 - ndc_y) * 0.5 * height
  ```

  `aspect` is `width / height` (1 for the square CLI output).  Points with
  `z_v <= 1e-6` are rejected; triangles crossing the near plane are skipped.
* **Rasterization** — screen-space barycentric coordinates via edge
  functions at pixel centres `(px + 0.5, py + 0.5)`; a per-triangle
  bounding box limits the scan.  Degenerate and edge-on triangles are
  skipped.
* **Depth** — `1/z` is interpolated linearly with the screen barycentrics,
  which is perspective-correct for a planar triangle.  The z-buffer stores
  reciprocal depth; larger values are nearer, and the buffer is initialized
  to `-inf` ("empty").
* **Shading** — flat face normals come from the triangle winding
  (`cross(b-a, c-a)`).  Lambert diffuse plus ambient:

  ```
  intensity = ambient + (1 - ambient) * max(0, dot(normal, light))
  ```

  with `ambient = 0.18` and a warm base colour `(238, 140, 68)`.
* **Culling** — optional (on by default).  A triangle is kept when its
  normal points towards the eye:
  `dot(normal, eye - centroid) > 0`.

`render(...)` returns a `RenderResult` containing the RGB pixel rows and
the triangle count, drawn-triangle count, foreground pixel count and
fraction, and the min/max view-space depth of visible fragments.

### `png_writer.py`

Writes PNG from scratch:

* signature `\x89PNG\r\n\x1a\n`;
* `IHDR` with width, height, bit depth 8, colour type 2 (RGB), no
  interlace;
* one `IDAT` containing zlib-compressed scanlines, each prefixed with
  filter type 0 (None);
* `IEND`;
* per-chunk CRC-32 via `zlib.crc32` and big-endian lengths via `struct`.

`encode_png(width, height, rows)` returns the bytes; `write_png(...)` writes
them to disk.  Pixel values are clamped to 0–255.

### `render.py`

The CLI: loads the OBJ, renders, writes the PNG, and prints triangle count,
foreground pixels, foreground ratio, depth range, resolution, and output
path.  Invalid arguments, unreadable inputs, meshes with no faces, and
unwritable outputs produce a non-zero exit status with a message on stderr.

## Design notes and limitations

* Pure standard library: `math`, `struct`, `zlib`, `argparse`, `dataclasses`.
* No anti-aliasing: coverage is binary per pixel centre.
* No textures, vertex normals, materials, or transformations: shading is
  flat per triangle and the mesh is rendered in object space.
* No near-plane clipping: a triangle with any vertex behind the camera is
  skipped (irrelevant for the default framing).
* Output is always square (`--size`), matching the CLI contract.
* OBJ faces are assumed to be planar and simple; non-convex polygons are
  fan-triangulated, which is exact for convex faces such as the cube's.
