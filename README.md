# 确定性粒子模拟与深度排序混合
`python3 sim.py req.json output.json`
req.json: `{"n":64,"steps":300,"seed":7,"force":[0,-9.8],"bounds":[0,64],"threads":4,"chunk":16}`
- 每步 v+=f*dt,p+=v*dt,出界反弹(rest=0.5); 线程/分块划分不影响逐粒子结果(定数)
- 深度排序(降序)+premult alpha 混合输出帧摘要
- 输出 `{"traj":[[x,y]],"blend":[r,g,b,a],"digest","order":[]}`
缺陷基线: 位置依赖循环顺序累加(浮点求和乱序)+按提交顺序 alpha 混合.

## 修复对照

| 缺陷基线 | 本实现 |
| --- | --- |
| 每步 `rng.shuffle(order)` 后按随机序遍历积分，循环顺序影响结果 | 逐粒子纯函数更新，Q30 定点整数，遍历顺序无关 |
| `sum(p[0] for p in pos)` 浮点乱序求和 | 全部归约改为整数加法（可交换可结合） |
| 无碰撞/力场，`blend` 为占位常量 | 确定性网格 + Jacobi 整数归约碰撞；可选定点 swirl 力场 |
| 按提交顺序 alpha 混合、无排序 | 视空间深度降序 + id 升序全序排序；预乘 alpha 自后向前合成 |
| 无逐帧统计 | 每帧 `counts` / `sort_keys` / `blend` / `blend_stats` |

## 实现 (R06)

### 1. 可复现的更新管线 (`sim.py`)
* 全部状态（位置/速度/深度层）以 **Q30 定点** 存储；单粒子更新是自身整数的纯函数，
  因此更新批次怎么切（`threads` / `chunk`）都不可能改变任何一个分量。
* **力场**（可选 swirl，见 `field`）与重力都在同一套定点运算中求值，无浮点求和。
* **碰撞**是 **Jacobi (gather) 松弛**（`collision_iterations` 轮，默认 4，固定确定性轮数）：
  每一轮广相用确定性均匀网格（格边长＝碰撞直径），候选对规范化为 `(i, j), i < j`
  并去重、按索引排序；窄相每个对算出整数位移/冲量增量，再用**整数加法**归约到
  每粒子累加器，最后统一施加（带位移/速度上限，避免重叠堆叠爆炸）。
  整数加法可交换可结合，因此广相分块、窄相分片数和**执行顺序**都不影响结果。
* 每轮后做一次逐粒子墙体约束（纯自身整数），保证修正不把粒子推出世界。

### 2. 稳定深度排序 + 预乘 alpha 混合
* 视空间深度 = 相机基向量与 (p - eye) 的点积（定点），`near..far` + 视口裁剪决定可见性。
* 排序键 = 深度降序，**同深度以粒子 id 升序**为确定次序 → 全序，稳定可复现。
* 混合为 back-to-front `over`，源色为**预乘 alpha**（`src = color * a`）。
  绘制批次只把连续样本分组，组内组间仍严格按序，因此 `draw_batch` 不改变结果。

### 3. 输出
`sim.py` 除 `traj`/`vel`/`depth`/`blend`/`order`/`digest`/`render_digest` 外，还给出
`frames[]`，每帧包含
`counts`（total/visible/culled/updated/collision_candidates/contacts/capped）、
`sort_keys`（`[depth_key, id]`）、`blend` 与 `blend_stats`
（layers/coverage/alpha/sum_alpha/tie_pairs/tie_groups/max_tie_group/draw_batches/depth_min/max）。
另有 `state_digest` / `render_digest` 便于逐帧比对。

### 可选请求字段
`z_bounds`（深度层范围，默认 `[0,16]`）、`radius`、`restitution`、
`collision_restitution`、`collision_iterations`、`draw_batch`、`dt`、
`camera`（eye/tilt/half/near/far）、`scramble`（**打乱 worker 派发与归约顺序**，
用于对抗性验证）、`depth_quant_bits`（量化排序键以**注入同深度并列**）、
`dup_layers`（强制层循环，制造完全并列）、
`field`（`{"type":"swirl","strength":..,"scale":..,"center":[..]}`）、
`particles`（显式粒子表，含 `a`/`color`，用于解析合成对照）。

## 验收 (`verify.py`)

`python3 verify.py` 全部通过时退出码为 0：

1. **单线程参考逐帧比对**：`threads=1, chunk=n` 为参考，与 `threads=1/2/4/8` ×
   多种分块、以及 4 种**打乱派发/归约顺序**的对抗调度（`scramble`，含单线程版）
   各跑 300 步，逐帧逐分量比对位置/速度（定点整数完全相等，`max|Δ|=0`），
   逐帧 `state_digest`/`render_digest`、排序键、绘制序、每帧混合值全部一致。
2. **解析合成**：对重叠粒子串用闭式 `over`（后缀乘积 + `math.fsum`，自身与求和顺序无关）
   与 `sim.composite` 对比，RGBA 最大通道误差 ≤ 2e-6（实测 ~3e-17）；
   并验证 draw_batch ∈ {1,2,3,7,16,40,64} 结果**逐位相同**；再做端到端校验：
   输出帧的 `blend` 与其 `alpha`/`color`/`order` 的闭式结果一致。
3. **同深度并列**：`dup_layers`（层循环）、`depth_quant_bits`（键量化）、
   全同深度 48 层堆叠、完全重合 16 粒子四类注入，排序仍为全序
   （深度降序、同深度 id 升序），跨 1/4/8 线程与不同分块复现，键序列逐帧相同。
4. **反向对照**：1 ulp 状态扰动、改变绘制序、换种子、丢一帧均被检测；
   打乱深度序的合成与 straight-alpha（未预乘）合成误差分别为 1.4e-1 / 5.6e-1，
   远超 2e-6 阈值 —— 证明阈值不会误判通过。
