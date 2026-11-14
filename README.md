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
