# SDF CSG 稳定布尔渲染

Python 3.9+，标准库。`python3 sdf_render.py scene.json outdir`

scene.json：

```json
{"res":[64,64],"eval_order":"post"|"pre",
 "tree":{"op":"union|inter|sub","a":{...},"b":{...}} |
        {"prim":"sphere","c":[0,0,0],"r":1} |
        {"prim":"box","c":[0,0,0],"h":[0.5,0.5,0.5]} |
        {"prim":"plane","n":[0,1,0],"d":0}}
```

相机固定：ray origin [0,0,4]，朝 -z，像素 (i,j) → NDC x,y∈[-1,1]，fov 使 aspect=1。球追踪到 |d|<1e-4 或步数 >256。

要求：

- `eval`：union=min(a,b)，inter=max(a,b)，sub=max(a,-b)。CSG 组合距离可能不是精确距离；渲染器同时返回归属基元、梯度和局部 Lipschitz 估计。步长受 `|d|/L` 约束，并对一步跨过符号面的情况半步回退重试。进入 `|d|<0.03` 的薄壁带后，步长强制不超过 `0.005`，因此 0.02 厚度不会被跳过。
- `eval_order` 只改变遍历调度，不交换规范的 a/b 操作数；浮点近公共面按前序基元 id 归属，NaN 由有限值优先的确定性规则处理，因此不同求值顺序输出位级一致。
- 命中分类：`stats.json` 的逐像素 `pixels` 输出 `hit`、`category`（`entry`/`exit`/`miss`）、`owner`/`which`（归属基元 index，按树前序编号）、`steps`、回退与细分计数；`ownership` 保留逐像素归属数组。公共面上两基元距离差小于 `1e-6` 时，归属按 pre-order 先者，全局一致。
- 输出 `img.pgm`（hit=255）、`steps.pgm`（逐像素步数热度）与 `stats.json`：包含 `hits`、`entries`、`exits`、`steps_hist`、`ownership` 和完整逐像素诊断。
- 验收测试：`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_sdf_render.py`，覆盖解析基元一致性、求值顺序/随机调度一致性、公共面归属、0.02 薄壁、布尔轮廓豪斯多夫距离。
