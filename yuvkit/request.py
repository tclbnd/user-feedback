"""The JSON request/response contract used by ``convert.py``.

Request shape (superset of the original)::

    {"standard": "bt601"|"bt709"|"bt2020",
     "rgb_range": "full"|"limited",
     "yuv_range": "full"|"limited",
     "path": "matrix"|"lut"|"both",          # optional, default "matrix"
     "chroma_layout": "4:4:4"|"4:2:2"|"4:2:0"|"4:2:0-left",  # optional
     "trace": true|false,                     # optional, per-pixel trace
     "trace_limit": 1000,                     # optional, cap on records
     "roundtrip": true|false,                 # optional, global default
     "meta": {"standard": ...},               # optional, standard check
     "chain": [{"standard": ..., "direction": ...}],  # optional
     "ops": [{"direction": "rgb-to-yuv"|"yuv-to-rgb",
              "standard": ...,                 # optional per-op override
              "rgb_range": ..., "yuv_range": ...,  # optional per-op
              "pixels": [[a,b,c], ...],
              "roundtrip": true,               # optional per-op
              "trace": true,                   # optional per-op
              "meta": {"standard": ...}}]}

Response::

    {"ops": [{"pixels": [[...]],
              "warnings": [...],               # per-op warnings
              "roundtrip": {"max_err": n, "tolerance": t, "ok": bool,
                            "clipped": [[...]], "over_tolerance": [...]},
              "trace": [...],                  # only when tracing
              "path_delta": [...],             # only when paths differ
              "range_tags": {...}}],
     "warnings": [...],                        # aggregated, de-duplicated
     "trace": {"records": [...], "summary": {...}},
     "consistency": {...},                     # only for path="lut"/"both"
     "conventions": {...}}                     # defaults actually used

Warnings are stable machine-readable tokens; the human explanation lives
in :data:`WARNING_TEXT`.  ``clamp`` semantics: code values that fall
outside the declared range are clipped and reported as
``range-mismatch`` rather than aborting a whole batch - the strict
behaviour is available at the :class:`~yuvkit.converter.Converter` level.
"""

import json

from .checks import check_chain, check_meta
from .chroma import get_layout
from .converter import Converter, EPSILON, compare_paths
from .ranges import RangeError, get_range
from .standards import UnknownStandard, canonical_name

__all__ = ["run_request", "run_file", "WARNING_TEXT", "RequestError"]

WARNING_TEXT = {
    "mixed-standard": (
        "mixed-standard: this request combines different colour standards; "
        "the BT.601 / BT.709 / BT.2020 matrices are not interchangeable, so "
        "luma stays plausible while saturated colours shift hue and neutrals "
        "pick up a cast"),
    "mixed-range": (
        "mixed-range: the chain switches between full and limited range "
        "without an explicit range conversion step"),
    "range-mismatch": (
        "range-mismatch: declared range does not match the code values "
        "actually present (full-range codes under a limited declaration, or "
        "vice versa)"),
    "clipping": (
        "clipping: at least one sample hit a code limit, which indicates "
        "out-of-gamut colour or a range misconfiguration"),
    "roundtrip-over-tolerance": (
        "roundtrip-over-tolerance: the round trip exceeded the 2/255 budget, "
        "which means range scaling or matrix constants are inconsistent"),
}


class RequestError(ValueError):
    """Raised for a structurally invalid request."""


def _pixels(op, key="pixels"):
    raw = op.get(key)
    if raw is None:
        raise RequestError("op is missing %r" % (key,))
    if not isinstance(raw, list):
        raise RequestError("%r must be a list" % (key,))
    for p in raw:
        if not isinstance(p, (list, tuple)) or len(p) != 3:
            raise RequestError("every pixel must be a 3-element list, got %r"
                               % (p,))
    return raw


def run_request(req):
    """Execute a parsed request dict and return the response dict."""
    if not isinstance(req, dict):
        raise RequestError("request must be a JSON object")
    if "ops" not in req:
        raise RequestError("request is missing 'ops'")
    if not isinstance(req["ops"], list) or not req["ops"]:
        raise RequestError("'ops' must be a non-empty list")

    warnings = []
    seen = set()

    def warn(token):
        if token not in seen:
            seen.add(token)
            warnings.append(token)

    # -- global configuration ------------------------------------------
    top_std = req.get("standard", "bt709")
    try:
        canonical_name(top_std)
    except UnknownStandard as exc:
        raise RequestError(str(exc))
    try:
        cmd = get_layout(req.get("chroma_layout", "4:4:4")).name
    except ValueError as exc:
        raise RequestError(str(exc))
    path = req.get("path", "matrix")
    if path not in ("matrix", "lut", "both"):
        raise RequestError("path must be 'matrix', 'lut' or 'both'")
    trace_global = bool(req.get("trace", False))
    trace_limit = req.get("trace_limit")

    meta_std = check_meta(req, top_std)
    if meta_std:
        warn("mixed-standard")

    if check_chain(req):
        warn("mixed-standard")

    # -- per-op loop ----------------------------------------------------
    out_ops = []
    per_op_info = []
    trace_records = []
    trace_summary = None
    consistency = None

    for op_index, op in enumerate(req["ops"]):
        if not isinstance(op, dict):
            raise RequestError("op %d is not an object" % op_index)
        direction = op.get("direction")
        if direction not in ("rgb-to-yuv", "yuv-to-rgb"):
            raise RequestError(
                "op %d has invalid direction %r" % (op_index, direction))
        op_std = op.get("standard", top_std)
        try:
            op_std = canonical_name(op_std)
        except UnknownStandard as exc:
            raise RequestError("op %d: %s" % (op_index, exc))

        op_meta = check_meta(op, op_std)
        if op_meta:
            warn("mixed-standard")
        op_warnings = []
        if op_meta:
            op_warnings.append("mixed-standard")
        if op_std != canonical_name(top_std):
            if "mixed-standard" not in op_warnings:
                op_warnings.append("mixed-standard")
            warn("mixed-standard")

        rgb_range = op.get("rgb_range", req.get("rgb_range",
                                                "full" if direction ==
                                                "rgb-to-yuv" else "full"))
        yuv_range = op.get("yuv_range", req.get("yuv_range", "limited"))
        try:
            get_range(rgb_range)
            get_range(yuv_range)
        except RangeError as exc:
            raise RequestError("op %d: %s" % (op_index, exc))

        pixels = _pixels(op)
        conv = Converter(op_std, rgb_range, yuv_range, cmd, "matrix")
        # Clamp rather than reject, but *say so*: code values outside the
        # declared range are the classic "it looked fine in the preview"
        # mistake, and silently clamping them is how a full-range master
        # ends up in a limited-range delivery.  This covers both the RGB
        # and the YUV side in one place.
        results = [conv.convert(p, direction, clamp=True) for p in pixels]
        if any("input-clipped" in res.notes for res in results):
            op_warnings.append("range-mismatch")
            warn("range-mismatch")
        codes = [list(res.codes) for res in results]

        op_out = {"pixels": codes}

        if any(any(res.clipped) for res in results):
            op_warnings.append("clipping")
            warn("clipping")

        if path in ("lut", "both"):
            alt = Converter(op_std, rgb_range, yuv_range, cmd, "lut")
            alt_codes = [list(alt.convert(p, direction).codes) for p in pixels]
            differing = [(i, a, b) for i, (a, b) in enumerate(zip(codes,
                                                                alt_codes))
                         if a != b]
            cmp_info = compare_paths(pixels, direction, op_std, rgb_range,
                                     yuv_range, cmd)
            cmp_info["op"] = op_index
            consistency = consistency or {"comparisons": []}
            consistency["comparisons"].append(cmp_info)
            if differing:
                op_out["path_delta"] = [
                    {"index": i, "matrix": a, "lut": b}
                    for i, a, b in differing[:64]]

        # -- trace ------------------------------------------------------
        want_trace = bool(op.get("trace", trace_global))
        if want_trace:
            limit = op.get("trace_limit", trace_limit)
            recs = []
            for i, res in enumerate(results):
                if limit is not None and len(recs) >= int(limit):
                    break
                rec = res.trace(index=i, source=pixels[i])
                rec["op"] = op_index
                recs.append(rec)
                trace_records.append(rec)
            op_out["trace"] = recs
            if trace_summary is None:
                trace_summary = {"pixels": 0, "ops": len(req["ops"])}
            trace_summary["pixels"] = len(trace_records)

        # -- round trip -------------------------------------------------
        want_rt = bool(op.get("roundtrip", req.get("roundtrip", False)))
        if want_rt:
            back_dir = ("yuv-to-rgb" if direction == "rgb-to-yuv"
                        else "rgb-to-yuv")
            back = [conv.convert(res.codes, back_dir) for res in results]
            errors = []
            clipped = []
            over = []
            for i, (src, dst, res) in enumerate(zip(pixels, back, results)):
                err = max(abs(a - b) for a, b in zip(src, dst.codes))
                errors.append(err)
                if any(res.clipped):
                    clipped.append(list(res.codes))
                if err > EPSILON * 255:
                    over.append({"index": i, "src": list(src),
                                 "dst": list(dst.codes), "err": err})
            max_err = max(errors) if errors else 0
            rt = {
                "max_err": max_err,
                "tolerance": float(EPSILON * 255),
                "ok": max_err <= EPSILON * 255,
                "clipped": clipped,
                "clipped_count": len(clipped),
                "over_tolerance": over,
            }
            op_out["roundtrip"] = rt
            if not rt["ok"]:
                op_warnings.append("roundtrip-over-tolerance")
                warn("roundtrip-over-tolerance")

        op_out["warnings"] = op_warnings
        # The tag names the engine that actually produced these codes.
        op_out["range_tags"] = conv.tags(
            direction, "matrix" if path in ("matrix", "both") else "lut"
        ).describe()
        out_ops.append(op_out)
        per_op_info.append({"index": op_index, "direction": direction,
                            "standard": op_std, "rgb_range": conv.rgb_range,
                            "yuv_range": conv.yuv_range})

    # -- cross-op standard consistency ---------------------------------
    declared = {info["standard"] for info in per_op_info}
    if len(declared) > 1:
        warn("mixed-standard")

    response = {"ops": out_ops, "warnings": warnings}
    if trace_records:
        response["trace"] = {"records": trace_records,
                             "summary": trace_summary}
    if consistency is not None:
        consistency["identical"] = all(c["identical"] for c
                                      in consistency["comparisons"])
        consistency["max_delta"] = max(
            (c["max_delta"] for c in consistency["comparisons"]), default=0)
        response["consistency"] = consistency
    response["conventions"] = {
        "standard": canonical_name(top_std),
        "rgb_range": get_range(req.get("rgb_range", "full")).name,
        "yuv_range": get_range(req.get("yuv_range", "limited")).name,
        "chroma_layout": cmd,
        "path": path,
        "rounding": "half-up",
    }
    return response


def run_file(src, dst):
    """Read a request from *src* and write the response to *dst*."""
    with open(src, "r") as fh:
        req = json.load(fh)
    res = run_request(req)
    with open(dst, "w") as fh:
        json.dump(res, fh, indent=None, separators=(",", ":"))
        fh.write("\n")
    return res

