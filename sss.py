#!/usr/bin/env python3
"""Deterministic curvature/thickness approximation for subsurface scattering."""
import hashlib
import json
import math
import os
import random
import struct
import sys

LUMA = (0.2126, 0.7152, 0.0722)


def clamp(value, low, high):
    return max(low, min(high, value))


def f32(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


def load_map(spec, base_dir):
    if isinstance(spec, str):
        path = spec if os.path.isabs(spec) else os.path.join(base_dir, spec)
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    return spec

def map_dims(data):
    height = len(data)
    width = len(data[0]) if height else 0
    channels = len(data[0][0]) if height and isinstance(data[0][0], list) else 1
    return height, width, channels


def bilinear_sample(data, height, width, channels, y, x):
    if channels == 1:
        def getter(yy, xx):
            return [data[yy][xx]]
    else:
        def getter(yy, xx):
            return data[yy][xx]

    y0 = max(int(math.floor(y)), 0)
    x0 = max(int(math.floor(x)), 0)
    y1 = min(y0 + 1, height - 1)
    x1 = min(x0 + 1, width - 1)
    fy = clamp(y - y0, 0.0, 1.0)
    fx = clamp(x - x0, 0.0, 1.0)
    result = []
    for c in range(channels):
        top = getter(y0, x0)[c] * (1.0 - fx) + getter(y0, x1)[c] * fx
        bottom = getter(y1, x0)[c] * (1.0 - fx) + getter(y1, x1)[c] * fx
        result.append(top * (1.0 - fy) + bottom * fy)
    return result if channels > 1 else result[0]


def resample_map(data, out_height, out_width):
    height, width, channels = map_dims(data)
    if height == out_height and width == out_width:
        return data
    result = []
    for y in range(out_height):
        row = []
        sy = (y + 0.5) * height / out_height - 0.5
        for x in range(out_width):
            sx = (x + 0.5) * width / out_width - 0.5
            row.append(bilinear_sample(data, height, width, channels, sy, sx))
        result.append(row)
    return result


def d1(field, height, width, y, x, axis, channel):
    if axis == 0:
        if y <= 0:
            return field[1][x][channel] - field[0][x][channel]
        if y >= height - 1:
            return field[height - 1][x][channel] - field[height - 2][x][channel]
        return 0.5 * (field[y + 1][x][channel] - field[y - 1][x][channel])
    if x <= 0:
        return field[y][1][channel] - field[y][0][channel]
    if x >= width - 1:
        return field[y][width - 1][channel] - field[y][width - 2][channel]
    return 0.5 * (field[y][x + 1][channel] - field[y][x - 1][channel])


def d2(field, height, width, y, x, axis, channel):
    if axis == 0:
        if y == 0:
            return field[1][x][channel] - field[0][x][channel]
        if y == height - 1:
            return field[height - 1][x][channel] - field[height - 2][x][channel]
        return field[y + 1][x][channel] - 2.0 * field[y][x][channel] + field[y - 1][x][channel]
    if x == 0:
        return field[y][1][channel] - field[y][0][channel]
    if x == width - 1:
        return field[y][width - 1][channel] - field[y][width - 2][channel]
    return field[y][x + 1][channel] - 2.0 * field[y][x][channel] + field[y][x - 1][channel]


def d2_wide(field, height, width, y, x, axis, channel):
    """Fourth-order central second derivative where stencil is available."""
    if axis == 0 and 1 < y < height - 2:
        numerator = (
            -field[y + 2][x][channel] + 16.0 * field[y + 1][x][channel]
            - 30.0 * field[y][x][channel] + 16.0 * field[y - 1][x][channel]
            - field[y - 2][x][channel]
        )
        return numerator / 12.0
    if axis == 1 and 1 < x < width - 2:
        numerator = (
            -field[y][x + 2][channel] + 16.0 * field[y][x + 1][channel]
            - 30.0 * field[y][x][channel] + 16.0 * field[y][x - 1][channel]
            - field[y][x - 2][channel]
        )
        return numerator / 12.0
    return d2(field, height, width, y, x, axis, channel)


def cross_d2(field, height, width, y, x, channel):
    ym, yp = max(y - 1, 0), min(y + 1, height - 1)
    xm, xp = max(x - 1, 0), min(x + 1, width - 1)
    return 0.25 * (
        field[yp][xp][channel] - field[yp][xm][channel]
        - field[ym][xp][channel] + field[ym][xm][channel]
    )


def as_rgb(value, y, x):
    if isinstance(value, (int, float)):
        color = [float(value)] * 3
    elif value and isinstance(value[0], list):
        color = [float(v) for v in value[y][x]]
    else:
        color = [float(v) for v in value]
    return [clamp(c, 0.0, 1.0) for c in color]


def sigma_values(sigma):
    if isinstance(sigma, (int, float)):
        return [float(sigma)] * 3
    return [float(v) for v in sigma]


def invert_2x2(matrix):
    a, b = matrix[0]
    c, d = matrix[1]
    det = a * d - b * c
    if abs(det) < 1.0e-18:
        return [[1.0, 0.0], [0.0, 1.0]]
    inv_det = 1.0 / det
    return [[d * inv_det, -b * inv_det], [-c * inv_det, a * inv_det]]


def multiply_2x2(a, b):
    return [
        [
            a[0][0] * b[0][0] + a[0][1] * b[1][0],
            a[0][0] * b[0][1] + a[0][1] * b[1][1],
        ],
        [
            a[1][0] * b[0][0] + a[1][1] * b[1][0],
            a[1][0] * b[0][1] + a[1][1] * b[1][1],
        ],
    ]


def run(req):
    base_dir = os.path.dirname(os.path.abspath(req.get("_request_path", "")))
    normals_raw = load_map(req["normals"], base_dir)
    thickness_raw = load_map(req["thick"], base_dir)
    height, width, _ = map_dims(normals_raw)
    _, _, thickness_channels = map_dims(thickness_raw)
    if thickness_channels == 1:
        thickness_grid = resample_map(thickness_raw, height, width)
        thickness = [[[value] for value in row] for row in thickness_grid]
    else:
        thickness = resample_map(thickness_raw, height, width)

    normals = []
    for y in range(height):
        row = []
        for x in range(width):
            n = normals_raw[y][x]
            length = math.sqrt(sum(v * v for v in n))
            row.append([0.0, 0.0, 1.0] if length <= 1.0e-12 else [v / length for v in n])
        normals.append(row)

    light = req["light_dir"]
    light_len = math.sqrt(sum(v * v for v in light))
    light = [v / light_len for v in light] if light_len > 0 else [0.0, 0.0, 1.0]
    lateral_sigma = float(req.get("sigma", req.get("scatter_sigma", 1.0)))
    thickness_sigma = sigma_values(
        req.get("sigma_thickness", req.get("transmission_sigma", req.get("sigma", 1.0)))
    )
    albedo_input = req.get("albedo", 1.0)
    tile_size = max(1, int(req.get("tile_size", 8)))
    seed = req.get("seed", 0)

    uv = req.get("uv")
    if uv is not None:
        uv = resample_map(uv, height, width)
        uv_scale = req.get("uv_scale", [1.0, 1.0])
        if isinstance(uv_scale, (int, float)):
            uv_scale = [float(uv_scale), float(uv_scale)]
    else:
        physical_size = req.get("physical_size")
        texel_size = req.get("texel_size")
        if texel_size is not None:
            dy, dx = float(texel_size[0]), float(texel_size[1])
        elif physical_size is not None:
            dy = float(physical_size[0]) / max(1, height)
            dx = float(physical_size[1]) / max(1, width)
        else:
            dy = dx = 1.0

    trans = [[0.0] * width for _ in range(height)]
    scatter = [[0.0] * width for _ in range(height)]
    direct = [[0.0] * width for _ in range(height)]
    bend = [[0.0] * width for _ in range(height)]
    thick_out = [[0.0] * width for _ in range(height)]
    curv = [[0.0] * width for _ in range(height)]
    curvature = [[0.0] * width for _ in range(height)]
    geom_curv = [[0.0] * width for _ in range(height)]
    aniso_curv = [[0.0] * width for _ in range(height)]
    thin_mask = [[0] * width for _ in range(height)]
    rgba = [[[0.0, 0.0, 0.0, 0.0] for _ in range(width)] for _ in range(height)]
    scatter_rgb = [[[0.0, 0.0, 0.0] for _ in range(width)] for _ in range(height)]
    direct_rgb = [[[0.0, 0.0, 0.0] for _ in range(width)] for _ in range(height)]

    def metric(y, x):
        if uv is None:
            return [[dy * dy, 0.0], [0.0, dx * dx]]
        du_row = d1(uv, height, width, y, x, 0, 0)
        du_col = d1(uv, height, width, y, x, 1, 0)
        dv_row = d1(uv, height, width, y, x, 0, 1)
        dv_col = d1(uv, height, width, y, x, 1, 1)
        su, sv = uv_scale
        u0, u1 = su * du_row, su * du_col
        v0, v1 = sv * dv_row, sv * dv_col
        return [
            [u0 * u0 + v0 * v0, u0 * u1 + v0 * v1],
            [u0 * u1 + v0 * v1, u1 * u1 + v1 * v1],
        ]

    def compute_pixel(y, x):
        g = metric(y, x)
        inv_g = invert_2x2(g)
        n_u = [d1(normals, height, width, y, x, 0, c) for c in range(3)]
        n_v = [d1(normals, height, width, y, x, 1, c) for c in range(3)]

        mu = sum(normals[y][x][c] * light[c] for c in range(3))
        grad_mu = [
            sum(n_u[c] * light[c] for c in range(3)),
            sum(n_v[c] * light[c] for c in range(3)),
        ]
        t_uu_pixel = d2_wide(thickness, height, width, y, x, 0, 0)
        t_vv_pixel = d2_wide(thickness, height, width, y, x, 1, 0)
        t_uv_pixel = cross_d2(thickness, height, width, y, x, 0)
        if uv is None:
            inv_g = [
                [1.0 / (dy * dy), 0.0],
                [0.0, 1.0 / (dx * dx)],
            ]
        t_uu, t_vv, t_uv = t_uu_pixel, t_vv_pixel, t_uv_pixel
        lap_t = (
            inv_g[0][0] * t_uu + inv_g[0][1] * t_uv
            + inv_g[1][0] * t_uv + inv_g[1][1] * t_vv
        )

        cg0 = inv_g[0][0] * grad_mu[0] + inv_g[0][1] * grad_mu[1]
        cg1 = inv_g[1][0] * grad_mu[0] + inv_g[1][1] * grad_mu[1]
        q2 = grad_mu[0] * cg0 + grad_mu[1] * cg1
        direction_norm2 = g[0][0] * cg0 * cg0 + 2.0 * g[0][1] * cg0 * cg1 + g[1][1] * cg1 * cg1
        direction_norm = math.sqrt(max(0.0, direction_norm2))
        direction = [cg0 / direction_norm, cg1 / direction_norm] if direction_norm > 1.0e-12 else [0.0, 0.0]
        dn_ds = [n_u[c] * direction[0] + n_v[c] * direction[1] for c in range(3)]
        k_directional = math.sqrt(sum(v * v for v in dn_ds))

        jtj = [
            [sum(n_u[c] * n_u[c] for c in range(3)),
             sum(n_u[c] * n_v[c] for c in range(3))],
            [sum(n_u[c] * n_v[c] for c in range(3)),
             sum(n_v[c] * n_v[c] for c in range(3))],
        ]
        rms_trace = (
            inv_g[0][0] * jtj[0][0] + inv_g[0][1] * jtj[1][0]
            + inv_g[1][0] * jtj[0][1] + inv_g[1][1] * jtj[1][1]
        )
        k_rms = math.sqrt(max(0.0, 0.5 * rms_trace))
        k_gradient = math.sqrt(max(0.0, q2 / max(1.0e-12, 1.0 - mu * mu)))
        # A finite pixel grid damps the sampled normal gradient near grazing
        # angles. Blend toward the stable first-mean-curvature estimator there.
        stability = clamp(1.0 - mu * mu, 0.0, 1.0)
        k_gradient = stability * k_gradient + (1.0 - stability) * k_rms
        physical_mu_grad = [cg0, cg1]
        t_u = d1(thickness, height, width, y, x, 0, 0)
        t_v = d1(thickness, height, width, y, x, 1, 0)
        physical_t_grad = [
            t_u * inv_g[0][0] + t_v * inv_g[0][1],
            t_u * inv_g[1][0] + t_v * inv_g[1][1],
        ]
        pixel_t_hess = [[t_uu, t_uv], [t_uv, t_vv]]
        if uv is not None:
            hess_transform = [
                [inv_g[0][0], inv_g[0][1]],
                [inv_g[1][0], inv_g[1][1]],
            ]
        else:
            hess_transform = [
                [1.0 / dy, 0.0],
                [0.0, 1.0 / dx],
            ]
        physical_t_hess = multiply_2x2(
            multiply_2x2(hess_transform, pixel_t_hess), hess_transform
        )
        mu_norm2 = max(1.0e-12, physical_mu_grad[0] ** 2 + physical_mu_grad[1] ** 2)
        t_norm2 = (
            physical_t_grad[0] * physical_t_grad[0]
            + physical_t_grad[1] * physical_t_grad[1]
        )
        graph_denominator = (1.0 + t_norm2) ** 1.5
        t_graph_directional = abs(
            physical_mu_grad[0] * physical_t_hess[0][0] * physical_mu_grad[0]
            + 2.0 * physical_mu_grad[0] * physical_t_hess[0][1] * physical_mu_grad[1]
            + physical_mu_grad[1] * physical_t_hess[1][1] * physical_mu_grad[1]
        ) / (mu_norm2 * graph_denominator)
        k_thickness = max(
            abs(lap_t) / (4.0 * graph_denominator),
            t_graph_directional,
        )
        k_effective = max(k_gradient, k_thickness)

        if thickness_channels == 1:
            optical_t = max(0.0, float(thickness[y][x][0]))
        else:
            pixel_thickness = thickness[y][x]
            optical_t = max(0.0, sum(LUMA[c] * pixel_thickness[c] for c in range(3)))
        thick_out[y][x] = optical_t
        curv[y][x] = lap_t
        curvature[y][x] = k_effective
        geom_curv[y][x] = k_rms
        aniso_curv[y][x] = k_directional
        thin_mask[y][x] = 1 if optical_t < 0.1 else 0

        if mu < 0.0 and k_effective > 1.0e-12 and lateral_sigma > 0.0:
            arc_distance = math.asin(clamp(-mu, 0.0, 1.0)) / k_effective
            profile = math.exp(-arc_distance / lateral_sigma)
        else:
            profile = 0.0
        bend[y][x] = profile

        if mu < 0.0:
            color = as_rgb(albedo_input, y, x)
            direct_px = [0.0] * 3
            scatter_px = [0.0] * 3
            total_px = [0.0] * 3
            for c in range(3):
                t_slab = clamp(math.exp(-optical_t / max(1.0e-12, thickness_sigma[c])), 0.0, 1.0)
                unscattered = t_slab * (-mu)
                scattered = color[c] * t_slab * (1.0 + mu) * profile
                direct_px[c] = unscattered
                scatter_px[c] = clamp(scattered, 0.0, 1.0)
                total_px[c] = clamp(unscattered + scatter_px[c], 0.0, 1.0)
            direct_rgb[y][x] = direct_px
            scatter_rgb[y][x] = scatter_px
            direct[y][x] = sum(LUMA[c] * direct_px[c] for c in range(3))
            scatter[y][x] = sum(LUMA[c] * scatter_px[c] for c in range(3))
            trans[y][x] = sum(LUMA[c] * total_px[c] for c in range(3))
            rgba[y][x] = [f32(total_px[c]) for c in range(3)] + [f32(clamp(trans[y][x], 0.0, 1.0))]

    tiles = [
        (y, x)
        for y in range(0, height, tile_size)
        for x in range(0, width, tile_size)
    ]
    random.Random(seed).shuffle(tiles)
    for y0, x0 in tiles:
        for y in range(y0, min(height, y0 + tile_size)):
            for x in range(x0, min(width, x0 + tile_size)):
                compute_pixel(y, x)

    dark_zero = True
    for y in range(height):
        for x in range(width):
            mu = sum(normals[y][x][c] * light[c] for c in range(3))
            if mu >= 0.0 and (trans[y][x] != 0.0 or scatter[y][x] != 0.0):
                dark_zero = False

    roi = req.get("roi", [0, 0, width, height])
    rx, ry, rw, rh = [int(v) for v in roi]
    rx, ry = clamp(rx, 0, width), clamp(ry, 0, height)
    rw, rh = clamp(rw, 0, width - rx), clamp(rh, 0, height - ry)
    count = max(1, rw * rh)
    roi_rgb = [[], [], []]
    scatter_values, trans_values, thick_values = [], [], []
    for y in range(ry, ry + rh):
        for x in range(rx, rx + rw):
            for c in range(3):
                roi_rgb[c].append(rgba[y][x][c])
            scatter_values.append(scatter[y][x])
            trans_values.append(trans[y][x])
            thick_values.append(thick_out[y][x])
    mean_rgb = [math.fsum(values) / count for values in roi_rgb]
    summary = {
        "roi": [rx, ry, rw, rh],
        "sample_count": rw * rh,
        "linear_rgb_mean": mean_rgb,
        "linear_mean": sum(LUMA[c] * mean_rgb[c] for c in range(3)),
        "trans_mean": math.fsum(trans_values) / count,
        "scatter_mean": math.fsum(scatter_values) / count,
        "scatter_min": min(scatter_values) if scatter_values else 0.0,
        "scatter_max": max(scatter_values) if scatter_values else 0.0,
        "scatter_sum": math.fsum(scatter_values),
        "thickness_mean": math.fsum(thick_values) / count,
        "energy_max": max(
            (max(rgba[y][x][:3]) for y in range(height) for x in range(width)),
            default=0.0,
        ),
    }

    packed = bytearray()
    for row in rgba:
        for pixel in row:
            packed.extend(struct.pack("<ffff", *(f32(v) for v in pixel)))
    digest = hashlib.sha256(packed).hexdigest()

    return {
        "trans": trans,
        "scatter": scatter,
        "direct": direct,
        "bend": bend,
        "scatter_rgb": scatter_rgb,
        "direct_rgb": direct_rgb,
        "rgba": rgba,
        "curv": curv,
        "curvature": curvature,
        "anisotropic_curv": aniso_curv,
        "geom_curv": geom_curv,
        "thick": thick_out,
        "thin_mask": thin_mask,
        "dark_zero": dark_zero,
        "digest": digest,
        "rgba_digest": digest,
        "summary": summary,
    }


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: python3 sss.py req.json output.json")
    with open(sys.argv[1], "r", encoding="utf-8") as handle:
        req = json.load(handle)
    req["_request_path"] = os.path.abspath(sys.argv[1])
    with open(sys.argv[2], "w", encoding="utf-8") as handle:
        json.dump(run(req), handle, separators=(",", ":"))


if __name__ == "__main__":
    main()


