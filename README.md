# Software 3D Pipeline — Skinning + Shadows + Texturing

A from-scratch software 3D rendering pipeline using **only the Python
standard library** (`zlib`, `struct`, `json`, `math`, `os`, `time`).
It animates a linear-blend-skinned bar, casts a moving shadow from a
directional light onto a checkerboard floor, and shades everything with
a perspective-correct textured Phong pass.

## Run

```bash
python3 render.py
```

Produces 12 `256x256` RGB PNGs in `frames/frame_00.png … frame_11.png`
and prints per-frame statistics (triangles drawn, fragment count,
shadowed-fragment percentage, wall time). On the reference machine the
whole run takes about **2 seconds** (limit: 120 s).

## Modules

| File | Responsibility |
|---|---|
| `math3d.py` | Row-major 4×4 matrices, Rodrigues rotation, perspective / orthographic projection, `look_at`, vector helpers |
| `png_texture.py` | PNG decoder built on `zlib`/`struct` (8-bit RGB plus the other common color types / bit depths, Adam7, filters 0–4), bilinear repeat-wrapping sampler, 8-bit RGB PNG encoder |
| `obj_loader.py` | OBJ `v` / `vt` / `f` parsing with fan triangulation and negative indices; MTL `Kd` / `map_Kd` (material texture + diffuse color) |
| `skinning.py` | Loads `rig.json` (joint hierarchy, per-vertex weights, rotation keyframes); computes bind/inverse-pose and per-frame skinning matrices, then applies linear-blend skinning |
| `pipeline.py` | Near-plane clipping, MVP transforms, perspective-correct (`1/w`) interpolation of UVs and world positions, z-buffer rasterizer, orthographic directional-light shadow map with depth comparison and bias, Phong shading (ambient + diffuse + Blinn-style specular), gamma output |
| `render.py` | Per-frame driver: skin → shadow depth pass → shaded main pass → PNG + stats |

## Pipeline details

### Skinning (`skinning.py`)

Each joint gets a bind-pose world transform from the parent chain of
translations; the animated pose inserts the keyframed axis-angle
rotation at the animated joint (`elbow`). A vertex is deformed with

```
v' = Σ_j  w(v,j) · (pose_world_j · bind_world_j⁻¹) · v
```

which is standard linear-blend skinning. The elbow pivots about
`(0, 2, 0)` and swings from 0° → 90° → 15° over the 12 keyframes, so the
bar bends and straightens back.

### Shadow map (`pipeline.py`)

1. **Depth pass** — the scene is rendered from the directional light's
   point of view with an orthographic camera into a `256×256` depth
   buffer (no shading, z-buffer only).
2. **Main pass** — every shaded fragment is transformed into light
   clip space; if its depth is farther than the stored shadow-map depth
   (plus a constant bias to avoid self-shadow acne), the fragment is
   shadowed and only the ambient term contributes.

### Perspective-correct texturing

Rasterization interpolates `u/w`, `v/w`, `world/w` and `1/w` in screen
space and divides through by the interpolated `1/w` before sampling, so
the checkerboard on the sloped ground plane stays square instead of
affine-skewing. UVs are sampled bilinearly with repeat wrapping.

## Scene

- `assets/bar.obj` — 36-vertex skinned bar (8 stacked segments),
  checker texture, `68` triangles.
- `assets/ground.obj` — `16 × 16` ground quad tiled `4 × 4` with the
  same checker texture, `2` triangles.
- `assets/rig.json` — `root` + `elbow` joints, per-vertex weights,
  12-frame Z-axis rotation animation.
- `assets/scene.mtl`, `assets/checker.png` — material and `64×64` RGB
  checker texture.

## Output statistics example

```
frame 00: tris= 70 fragments= 38146 shadowed=  8.12% time= 0.167s
frame 06: tris= 70 fragments= 35998 shadowed=  6.15% time= 0.165s
...
done: 12 frames in 2.00s (avg 0.166s/frame)
```

`tris` counts triangles surviving near-plane clipping; `shadowed` is the
percentage of written fragments that failed the shadow-map comparison.
