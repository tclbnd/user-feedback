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

- `eval`：union=min(a,b)，inter=max(a,b)，sub=max(a,-b)。**CSG 组合距离非精确**（inter/sub 高估真实距离）→ 步进需「回退」：若一步跨过表面（d 由正变负且无命中），回退半步重试；薄壁（|d|<0.03 两侧异号）强制最小步长 0.005。`eval_order` 改变子树求值顺序不得改变距离结果（union 对 nan 处理一致）。
- 命中分类：像素输出 `hit` + `which`（归属基元 index，按树前序编号）；公共面上两基元 |d_a-d_b|<1e-6 时归属按 pre-order 先者，**全局一致**。
- 输出 `img.pgm`（hit=255+步骤热度调试 `steps.pgm`）与 `stats.json`：`{"hits":n,"steps_hist":{...},"ownership":[…逐像素归属]}`。

现有实现缺陷：纯球追踪无回退（薄壁穿透）、无 ownership 记录、eval_order 改变 union 参数顺序时 min 绑定不同（浮点 min 结合顺序影响位级结果）。
