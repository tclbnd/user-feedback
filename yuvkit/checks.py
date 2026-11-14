"""Mixed standard / mixed range detection.

The rule the delivery bug violated: a BT.601 encode followed by a BT.709
decode (or the reverse) is not a round trip.  The matrices differ by
several percent in the chroma reconstruction, which shows up as a hue
shift on saturated colours and a green/magenta cast on neutrals, while
luma stays plausible - so it is easy to miss without an explicit check.

The detector deliberately reports *where* the disagreement comes from:

``mixed-standard``  a chain (or its declared metadata) mixes standards
``mixed-range``     a chain mixes full and limited without a conversion
``range-mismatch``  code values contradict the declared range
"""

from .standards import UnknownStandard, canonical_name

__all__ = [
    "check_meta",
    "check_chain",
    "compare_constants",
    "explain",
]


def check_meta(container, standard):
    """Return the meta standard if it contradicts *standard*, else None."""
    meta = container.get("meta") if isinstance(container, dict) else None
    if not isinstance(meta, dict):
        return None
    meta_std = meta.get("standard")
    if meta_std is None:
        return None
    try:
        meta_std = canonical_name(meta_std)
    except UnknownStandard:
        return str(meta_std)
    current = canonical_name(standard)
    return None if meta_std == current else meta_std


def check_chain(req):
    """True when a declared chain mixes standards or ranges."""
    chain = req.get("chain")
    if not isinstance(chain, list) or not chain:
        return False
    standards = set()
    ranges = set()
    for link in chain:
        if not isinstance(link, dict):
            continue
        std = link.get("standard")
        if std is not None:
            try:
                standards.add(canonical_name(std))
            except UnknownStandard:
                standards.add(str(std))
        yuv = link.get("yuv_range")
        if yuv is not None:
            ranges.add(str(yuv).lower())
        rgb = link.get("rgb_range")
        if rgb is not None:
            ranges.add(str(rgb).lower())
    return len(standards) > 1 or _range_conflict(ranges)


def _range_conflict(ranges):
    has_full = any(r in ("full", "pc", "jpeg") for r in ranges)
    has_limited = any(r in ("limited", "tv", "video", "mpeg") for r in ranges)
    return has_full and has_limited


def compare_constants(names):
    """Tabulate the constants of several standards and their differences.

    Used by the CLI diagnostic: reporting the actual numeric delta makes
    "601/709 mixed" actionable instead of abstract.
    """
    from .matrix import get_matrix
    table = {}
    matrices = {canonical_name(n): get_matrix(n) for n in names}
    for name, m in matrices.items():
        table[name] = m.describe()
    deltas = {}
    base = None
    for name in matrices:
        if base is None:
            base = name
            continue
        a, b = matrices[base], matrices[name]
        deltas["%s-vs-%s" % (name, base)] = {
            "delta_kr": b.krn / 10000.0 - a.krn / 10000.0,
            "delta_kb": b.kbn / 10000.0 - a.kbn / 10000.0,
            "delta_cb_gain": (b.cb_num / b.cb_den) - (a.cb_num / a.cb_den),
            "delta_cr_gain": (b.cr_num / b.cr_den) - (a.cr_num / a.cr_den),
        }
    return {"constants": table, "deltas": deltas}


def explain(tokens):
    """Human readable expansions of the warning tokens in *tokens*."""
    from .request import WARNING_TEXT
    return [WARNING_TEXT.get(token, token) for token in tokens]

