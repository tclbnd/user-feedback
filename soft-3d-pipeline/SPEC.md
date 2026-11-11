# Software 3D Pipeline: Skinning + Shadows + Texturing

Implement a software 3D rendering pipeline that animates an LBS-skinned
mesh with dynamic shadows and textures. Python standard library only.

## Deliverables

- `obj_loader.py`: OBJ `v`/`vt`/`f` + MTL (`Kd`, `map_Kd`) parsing;
  triangulate faces.
- `png_texture.py`: PNG decoder via `zlib`/`struct` (8-bit RGB, filter
  types 0-4) + bilinear `u,v` sampler.
- `skinning.py`: load `rig.json` (joint hierarchy, per-vertex weights,
  animation keyframes); linear-blend-skin vertices for a given frame.
- `pipeline.py`: MVP transforms; perspective-correct attribute
  interpolation; z-buffer rasterizer; directional-light shadow map
  (depth pass from light POV + comparison in main pass); Phong
  shading (ambient + diffuse + specular).
- `render.py`: renders `frames/frame_XX.png` for every animation frame,
  printing per-frame stats (tris drawn, shadowed-pixel %, wall time).
- `README.md`

## Acceptance

`python3 render.py` must produce 12 valid 256x256 PNG frames in which:
the bar visibly **bends** over the sequence (skeletal animation working),
a **shadow** moves beneath it on the checkerboard floor (shadow map
working), the checkerboard texture is perspective-correct (not affine
skew), and total wall time is under 120 seconds. Report stats.
