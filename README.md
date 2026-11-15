# PCSS 可变半影软阴影

`python3 pcss.py req.json output.json`

## 请求

`req.json`:

```json
{"shadow":"d.json","query":[{"x":10,"y":10,"z":0.9}],"light_size":0.1,"blocker_r":8}
```

- `shadow`：阴影贴图，可以是文件路径、行列表、扁平 `data`+`width`/`height`，
  或 `{"width","height","data"}` 对象。
- `query`：查询点，`x`/`y` 是纹素坐标，`z` 是受光深度；可选 `id`。
- `light_size`：光源尺寸（阴影贴图纹素单位）。
- `blocker_r`：遮挡物搜索半窗宽（纹素，默认 8）。
- `min_kernel` / `max_kernel`：滤波半径夹取范围（默认 1 / 16）。
- `depth_bias` / `near_depth` / `far_depth`：遮挡阈值与深度分类（默认 1e-6 / 0 / 1）。
- `adaptive_search`：半影超出搜索窗时自动扩展重搜（默认开）。
- `kernel_shape`：`disk`（圆盘面光源，默认）或 `box`。
- `order` / `tile` / `threads` / `seed`：遮挡搜索的遍历顺序、分块、线程数；
  **都不影响结果**。

## 算法

1. **遮挡物搜索**：在 `blocker_r` 窗口内找 `d < z - depth_bias` 的纹素。
   窗口会裁剪到贴图内（部分覆盖记入 `coverage`）；深度 `<= near_depth` 的纹素
   视为穿透近裁剪面，钳到近平面后按最近遮挡物参与平均（避免漏光）；
   `>= far_depth` / `NaN` / `None` 视为空。贴图外的查询标记 `offmap` 并判全亮。
2. **半影半径**：`R = light_size * (z - avg_blocker) / avg_blocker`，夹取到
   `[min_kernel, max_kernel]`。`z → avg` 时接触硬化；`avg → 0` 时取最大核。
   **零遮挡时直接全亮，`taps = 0`，不做任何滤波采样**（用最小深度金字塔做
   遮挡存在性判定，开销与贴图面积无关）。
3. **可变核滤波**：在半径 `R` 的核上取确定性的黄金角螺旋样本，可见样本占比即
   光照值。过渡带宽 `2R` 就是解析半影宽 `2*light_size*(z-avg)/avg`，因此近处
   硬、远处软，不再"远近同软硬"。

若 `R` 超过搜索窗，会自动扩展一次（`search_r`），避免远场半影被窗口截断而出现
条带。

## 输出

```json
{"lit":[{"x","y","z","v","kernel","blockers","avg_depth","coverage","taps",
         "penetrating","search_r","penumbra_width","status","id"}],
 "avg_depth":{"<id 或下标>": 平均遮挡深度},
 "digest":"掩码+半径的 sha256", "kernel_digest":"仅半径分布",
 "blockers_digest":"遮挡/采样统计", "stats":{...}, "params":{...}}
```

`stats` 含分像素遮挡统计（`lit`/`penumbra`/`umbra`/`no_blocker`/`offmap`、
`blockers_total`、`taps_total`、`penetrating_hits`、`coverage_min`）与核半径
分布（`kernel_min`/`kernel_max`/`kernel_mean`/`kernel_distribution`）。

## 确定性

遮挡计数与深度求和用整数（定点 2^24）累加，滤波用整数样本计数，摘要按规范顺序
`(y, x, z, id)` 序列化。因此改变块遍历顺序（`row`/`col`/`snake`/`reverse`/
`tile*`/`random`）、分块大小或线程数，`digest`、`kernel_digest`、
`blockers_digest` 与全部半径统计都保持一致。

## 验收

```
python3 check_pcss.py -v
```

对照解析遮挡几何（圆盘面光源 + 半无限遮挡板）检查：

- **A**：远场可见度剖面偏差 ≤ 0.05；半影宽与解析差 ≤ 10%；半径精确等于
  `light_size*(z-avg)/avg`；接触硬化区 `v=0.5` 边缘误差 ≤ 1 纹素；远近核半径
  显著不同；无条带跳变。
- **B**：65 种遍历/分块/线程组合的掩码、半径与遮挡统计完全一致。
- **C**：无遮挡平面全亮且零采样（含 1M 纹素贴图 + 2000 查询的扩展性检查）；
  角落查询部分覆盖；完全屏外查询全亮；穿透近裁剪面深度判为遮挡。

