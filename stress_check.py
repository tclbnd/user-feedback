"""边界压力与对照检查（validate.py 之外的补充，不参与正式验收输出）。"""
import math

import fog
from validate import (reference_fine, reference_analytic, audit_coverage,
                      build_fixed_rays)


def main():
    # 1) 极厚雾 + 零长度区间 + 表面与块边界重合 + 极短区间
    stress = {"step": 0.3, "rays": [
        {"near": 0, "far": 10, "surface_color": 0.2,
         "medium": [{"a": 1, "b": 9, "rho": [50, 50], "light": [1, 1]}]},
        {"near": 4, "far": 4, "surface_t": 4, "surface_color": 0.6,
         "medium": []},
        {"near": 0, "far": 6, "surface_t": 3.0, "surface_color": 0.4,
         "medium": [{"a": 0, "b": 3.0, "rho": [0.7, 0.7], "light": [0.8, 0.8]},
                    {"a": 3.0, "b": 6, "rho": [2, 2], "light": [0.5, 0.5]}]},
        {"near": 0.0001, "far": 0.0002, "surface_color": 0.9,
         "medium": [{"a": 0, "b": 1, "rho": [0.3, 0.9], "light": [0.2, 0.7],
                     "light_extinction": 0.4}]},
    ]}
    for i, ray in enumerate(stress["rays"]):
        for flag in (True, False):
            r = fog.integrate_ray(ray, 0.3, flag, trace=True)
            assert all(math.isfinite(r[k])
                       for k in ("transmit", "scatter", "color")), i
            assert audit_coverage(ray, r, 0.3), ("coverage", i)
        ref = reference_fine(ray, 4000)
        got = fog.integrate_ray(ray, 0.3, True)
        assert abs(got["transmit"] - ref["transmit"]) <= 0.005, (i, got, ref)
        assert abs(got["color"] - ref["color"]) <= 0.01, (i, got, ref)
    print("stress ok:",
          [fog.integrate_ray(r, 0.3, True)["transmit"] for r in stress["rays"]])

    # 2) 不同步长下物理量一致（变化的只有步数）
    ray = build_fixed_rays()[10]
    plain = dict((k, v) for k, v in ray.items() if k not in ("name", "kind"))
    base = fog.integrate_ray(plain, 0.25, True)
    for s in (0.07, 0.13, 0.5, 1.0):
        r = fog.integrate_ray(plain, s, True)
        assert abs(r["transmit"] - base["transmit"]) < 1e-12, (s, r)
        assert abs(r["scatter"] - base["scatter"]) < 1e-10, (s, r)
    print("step-invariance ok")

    # 3) 跳过/不跳过差异图必须全黑
    body = " ".join(open("outputs/scene_diff.ppm").read().splitlines()[4:])
    assert set(body.split()) == {"0"}, "diff image not black"
    print("diff ppm all-black ok")

    # 4) 分段均匀体与解析解直接比对
    an_ray = {"near": 1, "far": 9, "surface_t": 7.2, "surface_color": 0.3,
              "medium": [{"a": 2, "b": 5, "rho": [0.8, 0.8],
                          "light": [0.6, 0.6]},
                         {"a": 5, "b": 8, "rho": [1.3, 1.3],
                          "light": [0.4, 0.4]}]}
    ref = reference_analytic(an_ray)
    got = fog.integrate_ray(an_ray, 0.25, False)
    assert abs(ref["transmit"] - got["transmit"]) < 1e-12
    assert abs(ref["color"] - got["color"]) < 1e-10
    print("analytic cross-check T=%.10f C=%.10f"
          % (got["transmit"], got["color"]))

    # 5) 复现“粗中点步进吞薄雾”：证明本实现边界截断的必要性
    thin = {"near": 0, "far": 100, "surface_color": 0.5,
            "medium": [{"a": 10, "b": 10.5, "rho": [0.02, 0.02],
                        "light": [1, 1]}]}
    fine = reference_fine(thin, 4000)
    trans, sc, x, step = 1.0, 0.0, 0.0, 4.0
    while x < 100:
        mid = x + step / 2
        rho = light = 0.0
        for blk in thin["medium"]:
            if blk["a"] <= mid < blk["b"]:
                rho, light = blk["rho"][0], blk["light"][0]
        att = math.exp(-rho * step)
        sc += trans * (1 - att) * light
        trans *= att
        x += step
    print("thin fog: fine scatter=%.6f coarse-midpoint=%.6f "
          "(粗步进相对误差 %.1f%%：薄层中点密度被错误套到整个步长)"
          % (fine["scatter"], sc, 100 * abs(sc / fine["scatter"] - 1)))
    new = fog.integrate_ray(thin, 0.25, True)
    assert abs(new["scatter"] - fine["scatter"]) < 1e-5
    print("new integrator thin fog scatter=%.6f ok" % new["scatter"])

    # 表面在雾前：粗步进不按 surface_t 截断，会把遮挡后的雾漏到前面
    occluded = {"near": 0, "far": 10, "surface_t": 2.0, "surface_color": 0.2,
                "medium": [{"a": 3.0, "b": 7.0, "rho": [1.0, 1.0],
                            "light": [0.9, 0.9]}]}
    correct = fog.integrate_ray(occluded, 0.25, True)
    trans, sc, x = 1.0, 0.0, 0.0
    while x < 10:
        mid = x + step / 2
        rho = light = 0.0
        for blk in occluded["medium"]:
            if blk["a"] <= mid < blk["b"]:
                rho, light = blk["rho"][0], blk["light"][0]
        att = math.exp(-rho * step)
        sc += trans * (1 - att) * light
        trans *= att
        x += step
    print("occluded: correct scatter=%.6f transmit=%.3f | "
          "coarse-midpoint leak scatter=%.6f"
          % (correct["scatter"], correct["transmit"], sc))
    assert correct["scatter"] == 0.0 and correct["transmit"] == 1.0
    assert sc > 0.0  # 旧算法确实漏光


if __name__ == "__main__":
    main()