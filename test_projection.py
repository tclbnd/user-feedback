"""多投影曲面融合系统的验收夹具（纯标准库 unittest，无需第三方依赖）。

覆盖验收点：
1. 合成标定格点独立投影位置的 RMS 重投影误差 <= 0.5 像素；
2. 平坦重叠 ROI 内常量灰卡的线性亮度绝对误差 <= 0.02（含 8bit 量化）；
3. 缺失格点、窄重叠、曲面边缘、遮挡、单机回退、遮罩归一化、无空洞、
   同输入复跑摘要一致性。
"""
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import projection as P  # noqa: E402


def make_markers(x_coef, y_coef, u_values, v_values, drop=()):
    """按给定二次系数生成合成标定格点，drop 中的 (u,v) 模拟缺失格点。"""
    markers = []
    for v in v_values:
        for u in u_values:
            if (u, v) in drop:
                continue
            basis = P.poly_basis(u, v)
            markers.append({
                "surface": [u, v],
                "screen": [sum(c * b for c, b in zip(x_coef, basis)),
                           sum(c * b for c, b in zip(y_coef, basis))],
            })
    return markers


def rectangular_pixels(u0, u1, v0, v1):
    """规则整数曲面坐标 LUT：列 u 从 u0..u1，行 v 从 v0..v1。"""
    return [[[u, v] for u in range(u0, u1 + 1)]
            for v in range(v0, v1 + 1)]


def curved_pixels(u0, u1, v0, v1):
    """带轻微桶形畸变的非整数曲面坐标 LUT，模拟曲面几何。"""
    out = []
    for v in range(v0, v1 + 1):
        row = []
        for u in range(u0, u1 + 1):
            cu = (u - 0.5 * (u0 + u1)) / max(1.0, (u1 - u0))
            cv = (v - 0.5 * (v0 + v1)) / max(1.0, (v1 - v0))
            row.append([u + 0.18 * cu * cu - 0.05 * cv,
                        v + 0.12 * cv * cv + 0.04 * cu])
        out.append(row)
    return out


def gray_pattern(w, h, value):
    return [[value for _ in range(w)] for _ in range(h)]


class CalibrationTests(unittest.TestCase):
    """验收 1：几何标定精度。"""

    def test_rms_reprojection_below_half_pixel(self):
        # 两台投影各自独立的二次屏幕映射
        x0 = [0.4, 1.03, 0.02, 0.011, 0.004, -0.006]
        y0 = [-0.3, -0.02, 0.98, 0.005, 0.007, 0.009]
        x1 = [6.7, 0.97, -0.03, -0.008, 0.006, 0.004]
        y1 = [0.2, 0.03, 1.02, 0.007, -0.005, 0.012]
        u_values = [0, 2, 4, 6, 8]
        v_values = [0, 2, 4, 6, 8]
        scene = {
            "pattern": gray_pattern(12, 8, 0.4),
            "projectors": [
                {"id": "p0",
                 "markers": make_markers(x0, y0, u_values, v_values),
                 "pixels": rectangular_pixels(0, 7, 0, 7),
                 "black": 0.02, "gain": 0.9, "gamma": 2.2},
                {"id": "p1",
                 "markers": make_markers(x1, y1, u_values, v_values),
                 "pixels": rectangular_pixels(4, 11, 0, 7),
                 "black": 0.03, "gain": 0.8, "gamma": 2.4},
            ],
        }
        out = P.compose(scene)
        for cal, xc, yc in zip(out["calibration"],
                               (x0, x1), (y0, y1)):
            self.assertLessEqual(cal["rms"], 0.5)
            # 系数也应恢复到给定模型
            for got, expect in zip(cal["x"], xc):
                self.assertAlmostEqual(got, expect, places=6)
            for got, expect in zip(cal["y"], yc):
                self.assertAlmostEqual(got, expect, places=6)

    def test_missing_markers_still_calibrates(self):
        x0 = [0.4, 1.03, 0.02, 0.011, 0.004, -0.006]
        y0 = [-0.3, -0.02, 0.98, 0.005, 0.007, 0.009]
        drop = {(0, 0), (4, 2), (8, 6), (2, 8)}
        markers = make_markers(x0, y0,
                               [0, 2, 4, 6, 8], [0, 2, 4, 6, 8], drop)
        self.assertEqual(len(markers), 25 - len(drop))
        _, _, rms = P.fit_polynomial(markers)
        self.assertLessEqual(rms, 0.5)

    def test_degenerate_model_rejected(self):
        # 所有点共线 v=0，设计矩阵秩亏，必须显式报错而非静默出错
        markers = make_markers(
            [0.0, 1.0, 0.0, 0.0, 0.0, 0.0],
            [0.0, 0.1, 0.0, 0.0, 0.0, 0.0],
            [0, 1, 2, 3, 4, 5], [0])
        with self.assertRaises(ValueError):
            P.fit_polynomial(markers)
        # 点数不足也要报错
        with self.assertRaises(ValueError):
            P.fit_polynomial(markers[:5])


class BlendingPhotometryTests(unittest.TestCase):
    """验收 2：融合权重与线性亮度补偿。"""

    def _flat_scene(self, quantize=False, gray=0.45):
        scene = {
            "pattern": gray_pattern(12, 8, gray),
            "projectors": [
                {"id": "p0",
                 "markers": make_markers(
                     [0.0, 1.0, 0.0, 0.0, 0.0, 0.0],
                     [0.0, 0.0, 1.0, 0.0, 0.0, 0.0],
                     range(0, 8), range(0, 8)),
                 "pixels": rectangular_pixels(0, 7, 0, 7),
                 "blocked": [[False] * 8 for _ in range(8)],
                 "black": 0.025, "gain": 0.85, "gamma": 2.0,
                 "quantize": quantize},
                {"id": "p1",
                 "markers": make_markers(
                     [4.0, 1.0, 0.0, 0.0, 0.0, 0.0],
                     [0.0, 0.0, 1.0, 0.0, 0.0, 0.0],
                     range(4, 12), range(0, 8)),
                 "pixels": rectangular_pixels(4, 11, 0, 7),
                 "blocked": [[False] * 8 for _ in range(8)],
                 "black": 0.034, "gain": 0.7, "gamma": 2.4,
                 "quantize": quantize},
            ],
        }
        return scene

    def test_mask_normalization(self):
        out = P.compose(self._flat_scene())
        p0 = {p["id"]: p for p in out["projectors"]}
        # 重叠区 u=4..7：每格权重和必须为 1；独占区权重必须为 1
        for v in range(8):
            for u in range(12):
                if u <= 3:
                    self.assertAlmostEqual(p0["p0"]["weights"][v][u], 1.0)
                elif u >= 8:
                    self.assertAlmostEqual(p0["p1"]["weights"][v][u - 4], 1.0)
                else:
                    s = (p0["p0"]["weights"][v][u]
                         + p0["p1"]["weights"][v][u - 4])
                    self.assertAlmostEqual(s, 1.0, places=9)

    def test_gray_card_continuous_domain(self):
        out = P.compose(self._flat_scene(quantize=False))
        # 平坦重叠 ROI：避开融合坡两端边界列，取 u=5..6 的内部区域
        errors = [abs(out["combined"][v][u] - 0.45)
                  for v in range(1, 7) for u in range(5, 7)]
        self.assertLess(max(errors), 1e-9)

    def test_gray_card_quantized_within_tolerance(self):
        out = P.compose(self._flat_scene(quantize=True))
        errors = [abs(out["combined"][v][u] - 0.45)
                  for v in range(1, 7) for u in range(5, 7)]
        self.assertLess(max(errors), 0.02)
        # 独占区同样满足误差带（单机正向响应链在量化后仍准确）
        solo = [abs(out["combined"][v][u] - 0.45)
                for v in range(1, 7) for u in (1, 10)]
        self.assertLess(max(solo), 0.02)

    def test_black_floor_handling(self):
        # 目标低于活动黑位总和时 residual 截断为 0，只剩黑位，不产生负值
        scene = self._flat_scene(gray=0.01)
        out = P.compose(scene)
        for row in out["combined"]:
            for value in row:
                self.assertIsNotNone(value)
                self.assertGreaterEqual(value, 0.0)
        # 重叠列的输出应等于两台黑位之和
        self.assertAlmostEqual(out["combined"][3][5], 0.025 + 0.034, places=9)

    def test_signal_chain_roundtrip(self):
        scene = self._flat_scene(quantize=False)
        out = P.compose(scene)
        p0 = out["projectors"][0]
        view_black, view_gain, view_gamma = 0.025, 0.85, 2.0
        # 任取一个有效像素，按正向响应链复核亮度非负且信号在 [0,1]
        for row in p0["signal"]:
            for s in row:
                self.assertGreaterEqual(s, 0.0)
                self.assertLessEqual(s, 1.0)
        light = view_black + view_gain * p0["signal"][3][0] ** view_gamma
        self.assertAlmostEqual(light, out["combined"][3][0], places=9)


class EdgeAndFallbackTests(unittest.TestCase):
    """验收 3：缺失格点、窄重叠、曲面边缘、遮挡、回退与确定性。"""

    def test_narrow_overlap_single_column(self):
        # 仅 1 列重叠（u=6），不能出现空洞，权重仍归一
        scene = {
            "pattern": gray_pattern(13, 6, 0.4),
            "projectors": [
                {"id": "a",
                 "markers": make_markers(
                     [0.0, 1.0, 0.0, 0.0, 0.0, 0.0],
                     [0.0, 0.0, 1.0, 0.0, 0.0, 0.0],
                     range(0, 7), range(0, 6)),
                 "pixels": rectangular_pixels(0, 6, 0, 5),
                 "black": 0.02, "gain": 0.9, "gamma": 2.2},
                {"id": "b",
                 "markers": make_markers(
                     [6.0, 1.0, 0.0, 0.0, 0.0, 0.0],
                     [0.0, 0.0, 1.0, 0.0, 0.0, 0.0],
                     range(6, 13), range(0, 6)),
                 "pixels": rectangular_pixels(6, 12, 0, 5),
                 "black": 0.03, "gain": 0.8, "gamma": 2.4},
            ],
        }
        out = P.compose(scene)
        self.assertEqual(out["report"]["overlap_cells"], 6)
        self.assertEqual(out["report"]["covered_cells"], 13 * 6)
        self.assertEqual(out["report"]["invalid_cells"], [])
        pa = {p["id"]: p for p in out["projectors"]}
        for v in range(6):
            self.assertAlmostEqual(
                pa["a"]["weights"][v][6] + pa["b"]["weights"][v][0], 1.0,
                places=9)
        # 窄重叠列也无亮度空洞
        for v in range(6):
            self.assertIsNotNone(out["combined"][v][6])

    def test_curved_lut_and_surface_edge(self):
        # 曲面畸变 LUT：落栅后边缘仍被覆盖，无空洞
        scene = {
            "pattern": gray_pattern(14, 9, 0.35),
            "projectors": [
                {"id": "c0",
                 "markers": make_markers(
                     [0.0, 1.0, 0.0, 0.01, 0.0, 0.0],
                     [0.0, 0.0, 1.0, 0.0, 0.0, 0.01],
                     range(0, 9), range(0, 9)),
                 "pixels": curved_pixels(0, 8, 0, 8),
                 "black": 0.02, "gain": 0.9, "gamma": 2.2},
                {"id": "c1",
                 "markers": make_markers(
                     [5.0, 1.0, 0.0, -0.01, 0.0, 0.0],
                     [0.0, 0.0, 1.0, 0.0, 0.0, 0.01],
                     range(5, 14), range(0, 9)),
                 "pixels": curved_pixels(5, 13, 0, 8),
                 "black": 0.025, "gain": 0.85, "gamma": 2.3},
            ],
        }
        out = P.compose(scene)
        self.assertEqual(out["report"]["covered_cells"], 14 * 9)
        self.assertEqual(out["report"]["invalid_cells"], [])
        for row in out["combined"]:
            self.assertTrue(all(v is not None for v in row))

    def test_blocked_pixels_and_invalid_cells(self):
        blocked = [[False] * 8 for _ in range(8)]
        blocked[0][0] = True
        blocked[7][7] = True
        scene = {
            "pattern": gray_pattern(8, 8, 0.4),
            "projectors": [
                {"id": "solo",
                 "markers": make_markers(
                     [0.0, 1.0, 0.0, 0.0, 0.0, 0.0],
                     [0.0, 0.0, 1.0, 0.0, 0.0, 0.0],
                     range(0, 8), range(0, 8)),
                 "pixels": rectangular_pixels(0, 7, 0, 7),
                 "blocked": blocked,
                 "black": 0.02, "gain": 0.9, "gamma": 2.2},
            ],
        }
        out = P.compose(scene)
        p = out["projectors"][0]
        self.assertEqual(p["signal"][0][0], 0.0)
        self.assertEqual(p["weights"][0][0], 0.0)
        self.assertEqual(p["signal"][7][7], 0.0)
        # 被遮挡格点进入 invalid 清单，combined 为 null
        self.assertIn([0, 0], out["report"]["invalid_cells"])
        self.assertIn([7, 7], out["report"]["invalid_cells"])
        self.assertIsNone(out["combined"][0][0])
        self.assertEqual(out["report"]["projectors"][0]["blocked_pixels"], 2)

    def test_out_of_surface_pixels_are_zero_weight(self):
        # LUT 中部分像素指向曲面网格之外
        pixels = rectangular_pixels(0, 7, 0, 7)
        pixels[0][7] = [9.6, 0.0]  # 网格仅 8 列，越界
        scene = {
            "pattern": gray_pattern(8, 8, 0.4),
            "projectors": [
                {"id": "solo",
                 "markers": make_markers(
                     [0.0, 1.0, 0.0, 0.0, 0.0, 0.0],
                     [0.0, 0.0, 1.0, 0.0, 0.0, 0.0],
                     range(0, 8), range(0, 8)),
                 "pixels": pixels,
                 "black": 0.02, "gain": 0.9, "gamma": 2.2},
            ],
        }
        out = P.compose(scene)
        self.assertEqual(out["projectors"][0]["weights"][0][7], 0.0)
        self.assertEqual(out["projectors"][0]["signal"][0][7], 0.0)

    def test_single_projector_fallback_preserves_render(self):
        scene = {
            "pattern": [[0.1 + 0.05 * u for u in range(6)] for _ in range(5)],
            "projectors": [
                {"id": "only",
                 "markers": make_markers(
                     [0.0, 1.0, 0.0, 0.0, 0.0, 0.0],
                     [0.0, 0.0, 1.0, 0.0, 0.0, 0.0],
                     range(0, 6), range(0, 5)),
                 "pixels": rectangular_pixels(0, 5, 0, 4),
                 "black": 0.02, "gain": 0.9, "gamma": 2.2},
            ],
        }
        out = P.compose(scene)
        self.assertTrue(out["report"]["single_projector_fallback"])
        weights = out["projectors"][0]["weights"]
        self.assertTrue(all(abs(w - 1.0) < 1e-12
                            for row in weights for w in row))
        # 单投影仍做黑位/增益/伽马补偿，目标线性亮度被精确还原
        for v in range(5):
            for u in range(6):
                self.assertAlmostEqual(
                    out["combined"][v][u], scene["pattern"][v][u], places=9)

    def test_deterministic_rerun_and_digest(self):
        scene = {
            "pattern": gray_pattern(12, 8, 0.4),
            "projectors": [
                {"id": "p0",
                 "markers": make_markers(
                     [0.0, 1.0, 0.0, 0.01, 0.0, 0.0],
                     [0.0, 0.0, 1.0, 0.0, 0.0, 0.01],
                     range(0, 8), range(0, 8)),
                 "pixels": rectangular_pixels(0, 7, 0, 7),
                 "black": 0.025, "gain": 0.85, "gamma": 2.0},
                {"id": "p1",
                 "markers": make_markers(
                     [4.0, 1.0, 0.0, -0.01, 0.0, 0.0],
                     [0.0, 0.0, 1.0, 0.0, 0.0, 0.01],
                     range(4, 12), range(0, 8)),
                 "pixels": rectangular_pixels(4, 11, 0, 7),
                 "black": 0.034, "gain": 0.7, "gamma": 2.4},
            ],
        }
        first = json.dumps(P.compose(scene), sort_keys=True, allow_nan=False)
        second = json.dumps(P.compose(scene), sort_keys=True, allow_nan=False)
        self.assertEqual(first, second)
        d1 = P.add_digest(P.compose(scene))["report"]["digest"]
        d2 = P.add_digest(P.compose(scene))["report"]["digest"]
        self.assertEqual(d1, d2)
        self.assertEqual(len(d1), 64)

    def test_cli_end_to_end(self):
        scene = {
            "pattern": gray_pattern(10, 6, 0.4),
            "projectors": [
                {"id": "p0",
                 "markers": make_markers(
                     [0.0, 1.0, 0.0, 0.0, 0.0, 0.0],
                     [0.0, 0.0, 1.0, 0.0, 0.0, 0.0],
                     range(0, 8), range(0, 6)),
                 "pixels": rectangular_pixels(0, 6, 0, 5),
                 "black": 0.02, "gain": 0.9, "gamma": 2.2},
                {"id": "p1",
                 "markers": make_markers(
                     [3.0, 1.0, 0.0, 0.0, 0.0, 0.0],
                     [0.0, 0.0, 1.0, 0.0, 0.0, 0.0],
                     range(3, 10), range(0, 6)),
                 "pixels": rectangular_pixels(3, 9, 0, 5),
                 "black": 0.03, "gain": 0.8, "gamma": 2.4},
            ],
        }
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "projection.py")
        with tempfile.TemporaryDirectory() as tmp:
            in_path = os.path.join(tmp, "in.json")
            out_path = os.path.join(tmp, "out.json")
            with open(in_path, "w") as f:
                json.dump(scene, f)
            result = subprocess.run(
                [sys.executable, script, in_path, out_path, "--digest"],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            with open(out_path) as f:
                payload = json.load(f)
            self.assertIn("digest", payload["report"])
            self.assertEqual(len(payload["projectors"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
