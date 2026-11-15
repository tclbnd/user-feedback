# BSDF 多 lobe 采样与 MIS 权重
`python3 mis.py req.json output.json`
req.json: `{"scene":"whiteness","lobes":["lambert","ggx"],"n":4096,"seed":5,"rough":0.3}`
## 实现
- 统一球面坐标约定: 局部系 n=+z, cosθ=w·n; 背面(wi·n<=0 或 wo·n<=0) pdf=0/eval=0 剔除。
- lobe: `lambert` 余弦采样; `ggx` GGX 微表面 + Smith 相关遮蔽, VNDF(Heitz 2018) 可见法线
  采样保证掠射角收敛与能量守恒; `mirror` 理想镜面 delta lobe。各 lobe 均实现 sample/pdf/eval。
- MIS 平衡启发式: BSDF 混合采样(按能量权重分层, jittered-grid 分层采样)与光源(环境光)采样,
  w=pa/(pa+pb); delta 路径 MIS 权重恒 1, pa+pb=0 退化路径权重记 0, 均单独计数。
- 白炉参考能量: lambert/mirror 解析, ggx 单次散射方向反照率由 256x256 分层求积给出。
- 确定性: 采样序列由 (seed,pixel,strategy) 派生的 counter-based SplitMix64 流生成,
  与线程数/分块/执行顺序无关; 统计按 (thread,tile) 记录各 lobe 样本数与能量份额,
  乱序执行与交换分块/线程后像素摘要逐位一致。

## 场景
- `whiteness`: 白炉逐像素吸收能量偏差 (<=0.5%), 支持 `wo_cos_list` 掠射扫描。
- `single_lobe`: 单 lobe 出射辐照度与解析/求积参考对照 (<=2%), 含 GGX NDF 归一化积分。
- `mixed_roi`: 混合材质 4 固定种子 ROI 线性均值与 MIS 权重统计对照 16x 高样本基准。

## 输出
`{"energy_err","mis_weight","counts":{lobe:n},"energy_share","degenerate":{"delta_paths","mis0","culled"},"thread_tile","replayable","digest","pass", ...}`

~~缺陷基线: 单一采样策略,无 MIS,无 pdf 求值.~~ 已修复。
