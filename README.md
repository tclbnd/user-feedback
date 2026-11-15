# 次表面散射：曲率/厚度感知近似

运行：

```bash
python3 sss.py req.json output.json
```

## 请求

最小请求保持原接口：

```json
{
  "thick": "t.json",
  "normals": "n.json",
  "light_dir": [0.0, 0.0, 1.0],
  "seed": 1
}
```

`thick` / `normals` 也可以直接给内联数组。常用可选参数：

- `sigma`：一维扩散剖面的平均自由程，默认 `1.0`。
- `sigma_thickness`：沿厚度的 Beer-Lambert 衰减长度；默认与 `sigma` 相同，也可给 RGB 数组。
- `albedo`：散射反照率，可给标量或 RGB。
- `physical_size` / `texel_size`：把屏幕或 UV 像素导数换算为物理距离。
- `uv` 与 `uv_scale`：存在非均匀 UV 拉伸时，用度量张量修正曲率，而不是直接把像素坐标当作等距坐标。
- `tile_size` 与 `seed`：固定随机种子下重排分块；逐像素公式没有分块间状态，结果与分块顺序无关。
- `roi`：`[x, y, width, height]`，用于输出线性色和散射统计。

## 模型

1. 由法线的屏幕空间导数构造 Jacobian，并结合厚度 Hessian/拉普拉斯估计曲率。`uv` 存在时先构造度量张量 `G` 及其逆，因此 UV 拉伸不会被误当成几何曲率。
2. 对背面受光像素 `mu = n·l < 0`，用
   `s = asin(clamp(-mu)) / kappa`
   估计到明暗界线的弧长，并按一维扩散剖面
   `P(s) = exp(-s / sigma)`
   把光照弯向未受光方向。
3. 厚度项采用 Beer-Lambert：
   `T(t) = exp(-t / sigma_t)`。
   直接透射与扩散项使用互补能量分区：
   - 直接项：`T * (-mu)`
   - 散射项：`albedo * T * (1 + mu) * P(s)`

   二者是同一透射能量的方向再分配，因此每个颜色通道满足
   `direct + scatter <= T * (1 + profile) <= 1`；在薄区域还会做最终夹取，避免过曝或颜色穿帮。
4. `mu >= 0` 的背面全暗像素不写入任何透射或散射贡献，并保证 `dark_zero=true`。

## 输出通道

- `rgba`：线性 RGB 总透射，A 为总透射亮度；同时也是摘要哈希的唯一输入。
- `trans`：总透射亮度。
- `direct` / `direct_rgb`：分离的直接透射项。
- `scatter` / `scatter_rgb`：分离的背向散射项。
- `bend`：无量纲扩散剖面权重。
- `curv`：度量感知厚度拉普拉斯。
- `curvature`：实际用于弧长估计的非负有效曲率。
- `anisotropic_curv`、`geom_curv`：方向性与几何曲率诊断通道。
- `thick`、`thin_mask`：厚度与低厚度饱和诊断。
- `summary`：ROI 线性 RGB 均值、总透射、散射均值/极值/总和、厚度均值及能量上界。
- `digest` / `rgba_digest`：按行主序将 float32 RGBA 打包后计算 SHA-256。分块随机重排只改变计算顺序，不改变该摘要。

## 验收测试

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_sss.py
```

测试覆盖：

- 解析球壳半影区与一维扩散参考剖面的相对误差。
- 薄板 Beer-Lambert 透射、薄区饱和及每通道能量守恒。
- 背面全暗像素透射/散射严格为零。
- 非均匀 UV 拉伸下的度量感知曲率。
- 两种厚度图分辨率的 ROI 线性色、厚度和散射统计。
- 固定输入下不同随机分块顺序的 RGBA 与 SHA-256 摘要完全一致。
