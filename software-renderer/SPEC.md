# Software OBJ Renderer

Implement a software rasterizer that renders a Wavefront OBJ mesh to a PNG
image. Python standard library only — no third-party packages.

## Deliverables

- `obj_loader.py`: parse `v` / `f` records, triangulate polygon faces,
  return triangles (each a tuple of three vertices).
- `rasterizer.py`: perspective camera on +Z looking at origin
  (configurable fov / distance), barycentric or edge-function triangle
  rasterization, z-buffer, Lambert diffuse shading with a directional
  light. Backface culling optional.
- `png_writer.py`: minimal PNG encoder built on `zlib` + `struct`
  (no Pillow), 8-bit RGB.
- `render.py`: CLI — `python3 render.py input.obj out.png [--size N]`
  (default 512). Must print stats: triangle count, foreground pixels,
  foreground ratio, depth range.
- `README.md`: usage and design notes.

## Acceptance

`python3 render.py assets/cube.obj render.png` produces a valid
512x512 PNG where the cube silhouette is visible: foreground ratio
between 5% and 90%. Report the stats after running.
