# 各向异性高斯泼溅的排序混合与逐瓦片边界

`python3 splat.py input.json output.json`

input.json: `{"w":32,"h":32,"tile":8,"prims":[{"x":f,"y":f,"z":f,"sx":f,"sy":f,"sxy":f,"a":f,"rgb":[r,g,b]}...],"seed":1}`

模型约定：
- 2D 高斯核 G=exp(-0.5·dᵀΣ⁻¹d),覆盖半径按 3σ 分配到所有重叠瓦片
- 每瓦片按 z 键稳定排序后从前到后 alpha 混合:out=Σ a_i·G_i·T_i·rgb,T_i=Π(1-a_j·G_j)
- 跨瓦片边界不得截断高斯贡献；基元输入顺序变化不影响结果
- 深度等值基元保持输入次序稳定
- 输出 `{"pixels":[[r,g,b]*wh],"tile_counts":[[每瓦片基元数]],"digest":"sha1"}`

缺陷基线：画家算法不按深度 + 瓦片边界硬截断。
