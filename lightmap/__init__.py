"""Chart-isolated lightmap baking toolkit (pure Python 3.9 stdlib).

Modules
-------
vecmath : tiny 2D/3D vector helpers and barycentric coordinates
mesh    : sloped mesh builder with shared edges, seams and mirrored UVs
raster  : per-chart rasterisation (position / normal / coverage)
dilate  : nearest-owned-texel dilation (stable tie break)
mips    : per-chart canonical mip chains (no cross-chart filtering)
packer  : shelf and deterministic random packing with rotation
atlas   : atlas assembly with explicit collision policy
bake    : end-to-end pipeline and traceable outputs
sample  : bilinear sampling with chart inverse mapping
"""
from .bake import bake_charts, build_atlas, bake
from .sample import sample_chart, sample_atlas, inverse_pixel
from .packer import pack_shelf, pack_random

__all__ = [
    "bake_charts", "build_atlas", "bake",
    "sample_chart", "sample_atlas", "inverse_pixel",
    "pack_shelf", "pack_random",
]
