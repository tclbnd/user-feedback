"""Nearest-owned-texel dilation with a fully deterministic tie break.

Unowned texels inherit the light/owner of the closest covered texel
(plain Euclidean / 4-neighbour BFS distance on the pixel grid).  Equal
distances resolve by smallest y, then smallest x.  Dilation never
crosses a chart boundary because it works inside each chart's own
canonical grid.

Both routines below are multi-source BFS, i.e. O(width*width):
  dilate   : bounded propagation used for the padding contract
  fill_all : propagation over the whole private chart grid
"""
from collections import deque


def _neighbours(idx, n):
    y, x = divmod(idx, n)
    if x > 0:
        yield idx - 1
    if x + 1 < n:
        yield idx + 1
    if y > 0:
        yield idx - n
    if y + 1 < n:
        yield idx + n


def _bfs(lights, owners, sources, width, max_dist=None):
    """Multi-source nearest-owner fill; stable tie break y then x.

    Sources are enqueued in ascending flat index (row-major = y then x)
    and a cell is only claimed once, so on equal distance the first
    eligible source wins deterministically.
    """
    n = width
    dist = {s: 0 for s in sources}
    q = deque(sorted(sources))
    filled = set()
    while q:
        cur = q.popleft()
        d = dist[cur]
        if max_dist is not None and d >= max_dist:
            continue
        for nb in _neighbours(cur, n):
            if owners[nb] is not None or nb in dist:
                continue
            dist[nb] = d + 1
            lights[nb] = lights[cur]
            owners[nb] = owners[cur]
            filled.add(nb)
            q.append(nb)
    sources.extend(sorted(filled))
    return filled


def dilate(lights, owners, sources, width, padding):
    """Fill unowned entries up to padding BFS steps from an owner."""
    return _bfs(lights, owners, sources, width, max_dist=padding)


def fill_all(lights, owners, sources, width, chart_id):
    """Fill every remaining unowned texel with the nearest owned one.

    Used so a chart's private mip chain stays fully valid: a bilinear
    tap can then never hit a null or a foreign chart.  Tie break is the
    same smallest y then x rule as bounded dilation.
    """
    return _bfs(lights, owners, sources, width, max_dist=None)
