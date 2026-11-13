# 大气散射预计算查找表

Python 3.9+，标准库。`python3 atmo.py config.json output.json`

config.json：`{"lut_size":16,"samples":32,"seed":1}`。构建两张查找表：

- **透射表 T**：`T[i][j]`，坐标 (cos_zenith, altitude)，值为从该点沿 zenith 方向到大气顶的光学透射率。大气：球形地球 R=6360km，标高 H=8km，密度 ρ(h)=exp(-h/H)，Rayleigh β=5.5e-3/km。T=exp(-τ)，τ=沿路径密度积分。地平线附近路径弯曲用折射近似（等效半径放大 1.15）；表外查询夹取并双线插值。
- **内散射表 S**：`S[i][j]` 单重内散射亮度 = T(点→太阳) × T(点→视线方向) × 相位(μ)·β·ρ(h)·ds 积分，相位 Rayleigh P(μ)=3/(16π)·(1+μ²)，地球遮挡（路径阴影测试）置 0。多散射用近似项 ×1.05 补偿。

要求：

- 固定 `lut_size`/`samples` 下输出**逐字节可复现**；表构建的分块顺序不影响最终数据。
- output.json：`{"T":[[...]],"S":[[...]],"meta":{"lut_size":n,"samples":m,"checksum":"sha256前16"}}`，checksum 对 T+S 逐元素 round(9位) 序列化计算。
- 查询函数 CLI：`python3 atmo.py --query lut.json T cz alt` 与 `--query lut.json S mu h`，边界外夹取。

现有实现缺陷：透射表把路径当直线积分（无球面几何），地平线 τ 完全错；内散射无相位与遮挡；查询端点外不外插。验收用解析光学深度与高样本参考对照。

## 实现说明（已修复）

- **透射表**：球面几何光线-球体求交（等效半径 R_EF=1.15·R_E 近似折射弯曲），固定样本中点法沿路径积分 τ=β∫ρ ds；近地点低于 R_EF 判定撞地，T=0。
- **内散射表**：太阳天顶余弦固定 0.7，视线取太阳-竖直平面内 θ_v=θ_s+arccos μ；L = 1.05·P(μ)·β·∫T(视点→Q)·T(Q→太阳)·ρ ds，Q→太阳段与地球相交（阴影测试）时贡献为 0；视线撞地则在地面处截断积分。
- **可复现**：所有求积为固定样本数中点法，无随机性（`seed` 仅为兼容保留）；每个纹素是 (i,j) 的纯函数，config 可选 `"order":"forward|reverse|strided"` 改变求值顺序，结果逐纹素一致。
- **查询**：坐标夹取到表范围后双线性插值，边界处连续。

## 复跑命令与实测结果

```
echo '{"lut_size":16,"samples":32,"seed":1}' > config.json
python3 atmo.py config.json output.json     # 构建 LUT
python3 atmo.py --query output.json T 1.0 0 # -> 0.957073419（解析 exp(-0.044)=0.95696...）
python3 atmo.py --query output.json S 0.5 10
python3 verify.py                            # 验收自测，退出码 0
```

`verify.py` 实测（lut_size=16, samples=32）：

- 透射率：常密度近似 τ 相对误差 ≤ 4.5e-8；垂直指数路径 ≤ 1.0e-4；高空斜路径（对 20000 样本参考）≤ 2.3e-7。均远优于 0.5% 阈值。
- 内散射：对 2048×256 高样本参考，表节点相对误差 ≤ 8.4e-4，双线性插值查询 ≤ 1.1e-2，均优于 3% 阈值。
- 复现性：两次 CLI 构建逐字节一致；forward/reverse/strided 三种构建顺序 checksum 相同（70c75fc65c2e6385）。
- 查询：表外夹取返回边界值；跨节点平面最大跳变 7.5e-8（≈2ε·斜率，无不连续）。
