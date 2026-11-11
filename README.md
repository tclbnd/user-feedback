# Software 3D Pipeline — Skinning + Cascaded Shadows + Texturing

A from-scratch software 3D rendering pipeline using **only the Python
standard library** (`zlib`, `struct`, `json`, `math`, `os`, `time`,
`bisect`, `hashlib`).  It renders two scenes:

* **bar** (legacy) — a linear-blend-skinned bar bending on a checker
  floor, driven by the original `rig.json` axis-angle animation.
* **blend** — a 13-joint, 4-level humanoid that cross-fades from a
  walk cycle into a 180° turn (with non-uniform bind/animated scale)
  while the camera sweeps laterally, passes the character's head
  through the near plane, and ground fragments cross cascade
  boundaries.  Ground shadows follow the deformed, turning character.

## Run

```bash
python3 render.py          # legacy bar scene  -> frames/frame_XX.png
python3 render_blend.py    # blend scene       -> frames_blend/blend_XX{,_depth,_shadow}.png
python3 validate.py        # independent verification, exit 0 = PASS
python3 regression.py      # one-click determinism regression, exit 0 = PASS
```

`render.py` produces 12 `256x256` RGB PNGs; `render_blend.py` produces
16 frames of color + view-depth + shadow-mask PNGs for a fixed time
sequence covering the action switch, the close camera pass and cascade
transitions.

## Modules

| File | Responsibility |
|---|---|
| `math3d.py` | Row-major 4×4 matrices, quaternions (slerp with hemisphere alignment), TRS composition, general affine inverse, projections, `look_at` |
| `png_texture.py` | PNG decoder (8-bit RGB plus common color types / bit depths, Adam7, filters 0–4), bilinear repeat-wrapping sampler, 8-bit RGB PNG encoder |
| `obj_loader.py` | OBJ `v` / `vt` / `f` parsing with fan triangulation and negative indices; MTL `Kd` / `map_Kd` |
| `skinning.py` | `rig.json` loading (legacy axis-angle clips **and** named TRS-track actions), multi-level hierarchies, non-uniform scale, per-joint local-pose cross-fades, linear-blend skinning |
| `pipeline.py` | Near-plane clipping, MVP transforms, perspective-correct (`1/w`) interpolation, z-buffer rasterizer, **cascaded shadow maps** with texel snapping and cross-cascade blending, slope-scaled bias + 2×2 PCF, Phong shading, depth/shadow-mask export |
| `blend_scene.py` | Shared blend-scene definition: timeline, walk→turn blend spec, camera path, mesh construction (pure functions of frame index) |
| `render.py` | Legacy bar-scene driver (unchanged entry point) |
| `render_blend.py` | Blend-scene driver: color + depth + shadow mask per frame |
| `validate.py` | Independent verification (see below) |
| `regression.py` | One-click determinism regression (see below) |
| `tools/make_character.py` | Generates `assets/character.obj` + `assets/character_rig.json` |

## Skinning (`skinning.py`)

Each joint has a bind local TRS; animated clips provide per-joint
translation / rotation-quaternion / scale keyframe tracks.  A frame
request is a *blend spec* — a list of `(action, local_time, weight)`
triples.  Per joint, local poses are blended first (lerp translations
and scales; hemisphere-aligned normalized quaternion blending, so
key quaternions stored as `q` or `-q` are interchangeable), then
composed down the hierarchy:

```
skin_j = pose_world_j * inverse(bind_world_j)     (general affine inverse)
v'     = sum_j  w(v,j) * skin_j * v
```

Pose evaluation is a **pure function** of the blend spec: any time can
be rendered directly, in sequence, or out of order with identical
results.  The optional memoization cache only stores immutable results
keyed by the exact request and can be disabled via
`rig.cache_enabled = False`.  The legacy `rig.json` format keeps its
original API (`frame_count`, `skin_vertices(vertices, frame)`).

## Cascaded shadow maps (`pipeline.py`)

1. The view frustum `[near, shadow_far]` is split into 3 depth slices
   (practical log/uniform split).  Each slice gets an orthographic
   depth-only pass fitted to the slice's bounding sphere.
2. **Texel snapping** — the slice radius is quantized and the ortho
   window origin is snapped to the shadow-texel grid (depth range too),
   so sub-texel camera motion cannot re-align the cascade grid: static
   ground shadows do not swim.
3. **Continuous cascade transitions** — fragments inside a blend band
   at a cascade's far edge sample both neighbouring cascades and
   interpolate the lit fraction.
4. Shadow test uses a slope-scaled bias and 2×2 PCF; color, depth and
   shadow-mask buffers all come from the same rasterized geometry.

All passes clip against the near plane in clip space, so characters or
shadow casters crossing the near plane are cut correctly.

## Validation (`validate.py`)

* **A. Joint endpoints** — a tiny analytic rig (non-uniform root scale,
  sign-flipped key quaternions, T/R/S tracks) checked against
  closed-form trigonometry; character joint positions checked against
  an independent forward-kinematics implementation reading the JSON
  directly; dense cross-fade sweeps checked for continuity.
* **B. Skinned vertices** — selected character vertices compared
  against an independent LBS implementation (own track sampling, own
  adjugate affine inverse).
* **C. Occlusion** — ground sample points away from shadow edges
  classified by Möller–Trumbore ray/triangle intersection against the
  deformed character, compared with the rendered shadow mask at the
  projected pixel (reports the consistency rate).
* **D. Stability** — frozen pose, camera nudged in 0.3-texel steps;
  fixed world-space ground points must keep their shadow-mask values.

## Regression (`regression.py`)

One click, ~20 s, exit code 0 on success:

1. **bar scene** — 12 frames rendered sequentially vs a fixed shuffled
   order: byte-identical.
2. **blend scene** — 8 frames (walk, mid-fade, near-plane crossing,
   post-turn) rendered sequentially vs shuffled vs direct random access
   with a fresh renderer/rig: byte-identical.
3. **pose cache** — re-rendering a frame after churning the cache with
   all other frames reproduces the original bytes; cache-on equals
   cache-off.

## Assets

* `assets/bar.obj`, `assets/ground.obj`, `assets/rig.json`,
  `assets/scene.mtl`, `assets/checker.png` — legacy scene.
* `assets/character.obj`, `assets/character_rig.json` — generated by
  `tools/make_character.py` (12 boxes, 288 verts, 13 joints, `walk`
  and `turn` clips; some key quaternions deliberately sign-flipped).
