# CPU Path Tracer

Implement a Monte-Carlo path tracer that renders the Cornell-box-like
scene in `assets/cornell.txt` to a PNG. Python standard library only.

## Deliverables

- `scene.py`: parse the scene file — `quad` (triangulate into 2
  triangles), `sphere`, `light` (a quad with emission), `camera`.
- `bvh.py`: bounding volume hierarchy over all scene primitives with
  median or SAH split; a brute-force loop will not meet the time bound.
- `pathtracer.py`: perspective camera, per-pixel jittered sampling,
  up to max bounces, cosine-weighted hemisphere sampling, next-event
  estimation (direct light sampling), Russian roulette from bounce 3.
- `png_writer.py`: minimal 8-bit RGB PNG encoder via zlib + struct.
- `render.py`: `python3 render.py scene.txt out.png [--size N] [--spp S]`
  Prints stats: rays cast, average path depth, wall time.
- `README.md`

## Acceptance

`python3 render.py assets/cornell.txt render.png --size 256 --spp 64`
must finish in **under 90 seconds** and produce a valid PNG visibly
showing: red/green color bleeding on side walls, a soft shadow under
the spheres, and a lit ceiling. Report the run stats.
