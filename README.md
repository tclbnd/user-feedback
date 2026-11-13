# 离线体积雾积分

Python 3.9+，使用标准库。运行 `python3 fog.py demo.json output.json`，或添加 `--no-skip` 禁用空块跳过。JSON 记录逐射线测试数据，背景色和介质源项使用线性亮度。

scene 包含正数 step 与 rays。每条射线含 near、far、surface_color、可选 surface_t 和按 a 升序且不重叠的 medium 块。每个块含半开区间 [a,b)、rho=[起点,终点]、light=[起点,终点]、light_extinction（默认 0）。rho 为非负消光系数，rho 与 light 在块内线性插值。沿射线位置 x 的局部光源亮度还乘 exp(−light_extinction×(x−a))。间隙和两个 rho 端点都为零的块属于空区。只积分 [near, min(far,surface_t))，若上界不超过 near 则无介质贡献。没有 surface_t 时 surface_color 为远处背景。

透射 T=exp(−∫rho)，散射沿当前累积透射对 rho×局部光源积分。输出 transmit、scatter、color=scatter+transmit×surface_color、steps、skipped。steps 记录实际积分子段数，skipped 记录跳过的非零长度空段数量。关闭跳过时，空段也按 step 分段。每段积分不超过 step，并按块边界及表面截断。允许解析积分或确定性数值积分，精度见题面。

## 实现要点（fog.py）

- **统一截断**：积分域固定为 `[near, min(far, surface_t))`。介质块先裁剪到该域，再与块间真空间隙合并成有序、不重叠、首尾恰好覆盖积分域的区间列表，因此跨块边界、短区间与步长变化时不会重复或遗漏；首个不透明表面之后不存在任何雾记录。
- **可变步长**：每个积分子段长度取 `min(step, 到下一个块边界/表面的距离)`，子段完全落在同一个块内。rho 线性，段光学深度用梯形公式（解析精确）；段内散射 `∫T·rho·L` 用 GL8/GL4 对比的确定性自适应 Gauss-Legendre 求积，内部细分不计入 steps。
- **光源衰减**：`light_extinction` 的距离始终相对原始块起点 a 计算，裁剪不改变其语义。
- **空区跳过**：块间间隙与两个 rho 端点均为零的块视为空区。跳过模式下整块空区一次越过并计入 skipped；`--no-skip` 时空区同样按 step 分段、计入 steps，但不改变 T 与 S，两种模式物理量按构造逐位一致。

## 验收（validate.py）

```
python3 validate.py        # 正式验收，写 outputs/
python3 stress_check.py    # 补充压力测试与粗步进反例
```

- 参考解独立于 fog.py：常密度/分段均匀体（le=0）用逐均匀段解析公式；一般线性密度与带 light_extinction 的场景用独立细步长中点积分。
- 16 条固定射线覆盖常密度、分段均匀、线性斜坡、薄/浓切换、真空间隙、短区间、表面落在块内/块边界、表面先于介质等情形；门槛为透射绝对误差 ≤ 0.005、线性色误差 ≤ 0.01。
- 160 射线合成场景（薄雾层 + 浓密地面雾 + 表面遮挡序列）同时跑跳过/不跳过，核对步数下降、有限性与覆盖连续性，并输出三带 PPM（透射/散射/合成）与两模式差异图。
- 当前结果：最大透射误差约 1e-15、最大色误差约 3e-7，合成场景积分步数下降 44.5%，差异图全黑（详见 `outputs/report.txt`）。
