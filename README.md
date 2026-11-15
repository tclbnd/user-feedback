# BSDF 多 lobe 采样与 MIS 权重
`python3 mis.py req.json output.json`
req.json: `{"scene":"whiteness","lobes":["lambert","ggx"],"n":4096,"seed":5,"rough":0.3}`
- lambert 余弦采样, ggx GGX 分布采样; 各返回 dir+pdf; 背面(w·n<0)pdf=0
- MIS 平衡启发式: w=pa/(pa+pb); delta 策略(bra=0)退化路径单独记录
- 白炉测试: 能量守恒(出射辐照=入射±0.5%); 输出 `{"energy_err":v,"mis_weight":v,"counts":{l:n},"replayable":true,"digest"}`
- 乱序执行后重放摘要一致.
缺陷基线: 单一采样策略,无 MIS,无 pdf 求值.
