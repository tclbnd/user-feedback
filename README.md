# Software 3D Pipeline — Skinning, Cascaded Shadows and Texturing

A from-scratch software 3D rendering pipeline using **only the Python
standard library** (`zlib`, `struct`, `json`, `math`, `os`, `time`).
It supports two scenes:

1. the original skinned **bar** (single rigid rig, one shadow map);
2. a new multi-level **humanoid character** that cross-fades from a walk
   to a turn, lit by a directional light with **cascaded shadow maps**,
   while a camera slides parallel to its near plane and then grazes
   through it.

## Run

```bash
# original bar scene (unchanged entry point, byte-identical output)
python3 render.py

# new character time series: color_XX / depth_XX / mask_XX PNG triples
python3 render_character.py

# one-click local regression (all verification suites)
python3 run_regression.py
```

`render.py` writes 12 `256x256` frames to `frames/`. `render_character.py`
writes ten fixed-time triples (color, linear depth, shadow mask) plus a
JSON report to `frames_character/`. The full regression runs in ~4 s.

## Modules

| File | Responsibility |
|---|---|
| `math3d.py` | Row-major 4×4 matrices, Rodrigues rotation, perspective/ortho, `look_at`, **general 4×4 inverse (non-uniform scale)**, **quaternions (slerp/nlerp, hemisphere canonicalization)**, **Moller–Trumbore ray/triangle** |
| `png_texture.py` | PNG decoder (zlib/struct) + bilinear repeat-wrapping sampler + RGB encoder |
| `obj_loader.py` | OBJ `v`/`vt`/`f` and MTL `Kd`/`map_Kd` (unchanged) |
| `skinning.py` | Legacy `Rig` (bar, untouched) **and** format-2 `Skeleton`/`AnimationClip`/`Pose`: multi-level hierarchy, TRS channels, quaternion clips, local-pose cross-fades, general-inverse bind matrices, LBS |
| `pipeline.py` | Legacy single-shadow `Renderer` (bar, untouched) plus shared clipping/projection helpers |
| `csm_pipeline.py` | `CascadeRenderer`: N-cascade directional shadow maps, texel-snapped fixed-axis light frusta, linear light depth, adaptive slope bias, 2×2 PCF, cascade blend bands, depth/mask export |
| `scene_character.py` | Format-2 scene as a pure function of time: blended pose → one shared deformed mesh → shadow + color; lateral camera slide and view-axis near-plane dolly |
| `render_character.py` | Fixed time-series driver emitting color/depth/mask images |
| `tools/make_character.py` | Deterministic generator for the character OBJ/MTL/PNG and `character_rig.json` |
| `run_regression.py` | One-click runner over every verification suite |

## Skeleton and animation (format 2)

`assets/character_rig.json` declares a 12-joint hierarchy (root → pelvis
→ spine → head/shoulders/elbows, and pelvis → knees → feet), per-vertex
skin weights, deliberately **non-uniform bind scales**, and two clips:

* `walk` — periodic limb swings, spine sway and animated scale;
* `turn` — a 140° root yaw with counter-sway, tucking arms and flexing
  knees.

Clips store unit quaternions; equivalent `q` and `-q` representations
are mixed deliberately. At evaluation each keyframe is canonicalized to
one hemisphere before interpolation, and the cross-fade blends **local
joint poses** — weighted translation/scale lerps plus a hemisphere-aware
weighted quaternion average — before the world/skin chain is rebuilt.
Bind inverses use a general Gauss–Jordan inverse because rigid-body
inversion cannot represent non-uniform scale.

```
v' = Σ_j w(v,j) · (pose_world_j · bind_world_j⁻¹) · v
```

Evaluation is a pure function of `(clip, time)`: there is no pose cache,
so rendering any time directly, or in any order, is bit-identical.

## Cascaded shadow maps

* The light keeps a fixed world-space basis; only the orthographic
  origin moves, and it is **snapped to whole shadow texels**. A static
  world point therefore keeps the same shadow texel while the camera
  makes sub-texel movements, so the static ground does not shimmer from
  grid re-alignment.
* Each cascade fits a camera sub-frustum and stores **linear light-space
  depth**; the bias is derived from the cascade depth quantum plus the
  receiver's light-incidence slope, making the comparison identical
  across cascades and free of self-acne.
* Lookups project through the full snapped light view-projection (the
  same transform the depth rasterizer uses) and take a 2×2 PCF average.
* Around every split a **blend band** lerps the two cascades' shadow
  factors, so crossing a cascade boundary is continuous rather than a
  hard switch.
* Near-plane clipping (Sutherland–Hodgman) runs in both the shadow and
  color passes, and both consume the **same deformed geometry**.

## Verification (`tests/`)

* `test_skeleton_math.py` — independent first-principles FK/LBS reference:
  joint endpoint error (0.0), selected-vertex error (0.0), quaternion
  hemisphere equivalence, single-layer blend exactness, cross-fade
  continuity.
* `test_shadows.py` — ray/triangle occlusion vs the raster shadow factor
  on confident ground samples (**100% agreement**), cascade-split
  continuity (0 delta), and static-ground inter-frame change under
  sub-texel camera motion (only terminator-edge pixels move).
* `test_determinism.py` — random-access vs sequential rendering produce
  identical color/depth/mask hashes, unordered render invariance,
  pose/buffer cache isolation, and byte-identical bar golden frames.
* `golden_bar_sha256.json` — captured PNG hashes of the original bar run.

## Regression output example

```
SUITE: skeleton math ...   RESULT: PASS
  joint endpoint checks    : 348 (max err 0.0e+00)
SUITE: shadows ...         RESULT: PASS
  ray/triangle occlusion agreement: 196/196 = 100.00%
  static-ground inter-frame change : 3/12963 pixels (0.0231%) ...
SUITE: determinism ...     RESULT: PASS
  random vs sequential buffer mismatches: []
  legacy bar golden frames                : 12/12 match
TOTAL: 4.31s -- ALL PASS
```
