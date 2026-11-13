"""Per-chart canonical mip chains.

The chain lives in each chart's own [0,1] canonical orientation, so
rotation, mirroring, origin and write order can never change it.  Every
mip texel is the mean of the 2x2 block texels that belong to the same
chart; texels of other charts (impossible here) or still-empty texels
are simply skipped.  Any texel still empty after the block average is
filled by level-local nearest-owner propagation inside the same chart,
never by a cross-chart filter.
"""
from .dilate import fill_all


def downsample(lights, owners, width, chart_id):
    """Halve resolution once; never mix owners inside a block."""
    h = width // 2
    out_l = [None] * (h * h)
    out_o = [None] * (h * h)
    if h == 0:
        return out_l, out_o, 0
    for oy in range(h):
        for ox in range(h):
            acc = 0.0
            cnt = 0
            for dy in (0, 1):
                for dx in (0, 1):
                    i = (2 * oy + dy) * width + (2 * ox + dx)
                    if owners[i] == chart_id and lights[i] is not None:
                        acc += lights[i]
                        cnt += 1
            if cnt:
                k = oy * h + ox
                out_l[k] = acc / cnt
                out_o[k] = chart_id
    return out_l, out_o, h


def build_chain(lights, owners, sources, width, chart_id, levels, padding):
    """Return the canonical mip chain for one chart.

    Each entry: dict(size, light, owner, mask, sources) where mask is
    cover (original coverage or a same-chart 2x2 average), fill
    (nearest-owner propagation) or null.
    """
    chain = []
    cur_l, cur_o, cur_s, w = list(lights), list(owners), sorted(sources), width
    first_sources = set(cur_s)
    for level in range(levels):
        if level > 0 and w > 1:
            cur_l, cur_o, w = downsample(cur_l, cur_o, w, chart_id)
            valid = {i for i, o in enumerate(cur_o) if o == chart_id}
            cur_s = sorted(valid)
            if len(cur_s) < w * w:
                # Full in-chart fill keeps the private chain complete;
                # no foreign texel can participate.
                fill_all(cur_l, cur_o, cur_s, w, chart_id)
            filled = set(cur_s) - valid
        else:
            filled = (set(cur_s) - first_sources) if level == 0 else set()
        mask = []
        for i in range(w * w):
            if cur_o[i] != chart_id:
                mask.append(None)
            elif level == 0 and i in first_sources:
                mask.append("cover")
            elif i in filled:
                mask.append("fill")
            else:
                # Level-0 originals and higher-level 2x2 block averages.
                mask.append("cover")
        chain.append({
            "size": w,
            "light": cur_l,
            "owner": cur_o,
            "mask": mask,
            "sources": sorted(cur_s),
        })
        if w <= 1:
            break
    return chain
