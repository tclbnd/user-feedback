"""End-to-end chart baking pipeline.

Stages (each independently testable):
  1. raster_chart            : position / normal / coverage
  2. irradiance per texel    : ambient + max(0, dot(n, light)) * strength
  3. dilate                  : isolated padding inside the chart grid
  4. build_chain             : canonical per-chart mip chain
  5. blit_level per level    : atlas with explicit conflict policy

The pipeline is order-independent: charts and triangles may arrive in
any order, atlas writes may be reordered, and every chart's local
arrays stay bit-for-bit identical.  Trace records keep the owning
chart, source triangle, barycentric position, normal and mask for
every texel, so every output light value is attributable.
"""
from .raster import raster_chart
from .dilate import dilate, fill_all
from .mips import build_chain
from .atlas import blit_level, atlas_size_for
from .packer import pack_shelf


def _irradiance(normal, light, strength, ambient):
    d = normal[0] * light[0] + normal[1] * light[1] + normal[2] * light[2]
    return ambient + (d if d > 0.0 else 0.0) * strength


def bake_charts(charts, light, strength, padding, levels,
                placements=None, trace=True):
    """Bake every chart in its own canonical space.

    Returns (chains, traces, chart_sizes).  placements only affects the
    returned per-chart placement metadata; canonical data never depends
    on it.
    """
    chains = {}
    traces = {}
    chart_sizes = {}
    for chart in charts:
        cid = chart["id"]
        n = int(chart["size"])
        chart_sizes[cid] = n
        ambient = float(chart.get("ambient", 0.0))
        covered = raster_chart(chart)

        lights = [None] * (n * n)
        owners = [None] * (n * n)
        sources = []
        per_pixel = [None] * (n * n)
        for idx, info in enumerate(covered):
            if info is None:
                continue
            val = _irradiance(info["normal"], light, strength, ambient)
            lights[idx] = val
            owners[idx] = cid
            sources.append(idx)
            per_pixel[idx] = {
                "chart": cid,
                "triangle": info["tri"],
                "bary": list(info["bary"]),
                "normal": list(info["normal"]),
                "mirrored_uv": info["mirrored"],
                "light": val,
                "mask": "cover",
            }

        # Isolated dilation before mip generation: padding marks the
        # contract distance; a final in-chart fill guarantees the
        # private chain is fully valid without ever crossing a chart.
        before = set(sources)
        dilate(lights, owners, sources, n, padding)
        padding_filled = set(sources) - before
        fill_all(lights, owners, sources, n, cid)
        for idx in sources:
            if idx not in before and per_pixel[idx] is None:
                per_pixel[idx] = {
                    "chart": cid,
                    "triangle": None,
                    "bary": None,
                    "normal": None,
                    "mirrored_uv": None,
                    "light": lights[idx],
                    "mask": "dilate" if idx in padding_filled else "fill",
                }

        chain = build_chain(lights, owners, sources, n, cid, levels, padding)
        chains[cid] = chain
        if trace:
            traces[cid] = {
                "size": n,
                "pixels": per_pixel,
                "placement": placements.get(cid) if placements else chart.get("origin"),
                "rotate": chart.get("rotate", 0),
            }
    return chains, traces, chart_sizes


def build_atlas(chains, chart_sizes, placements, levels):
    """Assemble all mip levels with the documented no-mix conflict policy."""
    max_side = atlas_size_for(placements, chart_sizes, 0)
    out = []
    for level in range(levels):
        entry = blit_level(placements, chart_sizes, chains, level, max_side)
        out.append({
            "level": level,
            "size": entry["side"],
            "light": entry["light"],
            "owners": entry["owner"],
            "conflicts": entry["conflicts"],
            "policy": entry["policy"],
        })
    return out


def _default_placements(charts):
    sizes = {c["id"]: int(c["size"]) for c in charts}
    _, pl = pack_shelf(sizes)
    # Honour explicit origin/rotate when provided (level 0 contract).
    for c in charts:
        cid = c["id"]
        if "origin" in c:
            pl[cid]["x"], pl[cid]["y"] = c["origin"]
        if "rotate" in c:
            pl[cid]["rotate"] = int(c["rotate"])
    return pl


def bake(data, placements=None, trace=True):
    """Full pipeline matching the demo.json schema."""
    charts = data["charts"]
    light = tuple(data.get("light", (0.0, 0.0, 1.0)))
    strength = float(data.get("strength", 1.0))
    padding = int(data.get("padding", 0))
    levels = int(data.get("levels", 1))

    if placements is None:
        placements = _default_placements(charts)
    # Never request more levels than the largest chart can represent.
    max_levels = max(1, min(levels, max(
        int(chart["size"]).bit_length() for chart in charts)))

    chains, traces, chart_sizes = bake_charts(
        charts, light, strength, padding, max_levels, placements, trace)
    atlas = build_atlas(chains, chart_sizes, placements, max_levels)

    out_charts = {}
    for cid, chain in chains.items():
        out_charts[cid] = {
            "levels": [
                {
                    "size": e["size"],
                    "light": e["light"],
                    "owners": e["owner"],
                    "mask": e["mask"],
                }
                for e in chain
            ],
        }
    result = {
        "charts": out_charts,
        "atlas": atlas,
        "placements": {cid: dict(p) for cid, p in placements.items()},
        "params": {
            "light": list(light), "strength": strength,
            "padding": padding, "levels": max_levels,
            "mip_rule": "2x2 mean of same-chart valid texels",
            "dilation_rule": "nearest owned texel; ties by y then x",
            "atlas_conflict_policy": atlas[0]["policy"] if atlas else "",
        },
    }
    if trace:
        result["trace"] = traces
    return result
