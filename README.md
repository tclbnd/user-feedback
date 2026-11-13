# 离线光谱路径回放

Python 3.9+。执行 `python3 spectral.py demo.json out.json --tile 4 --workers 1`。wavelengths（nm）与 xyz_basis（每个波长的三分量）同长度。materials 为名称到 {ior:[每波长的正折射率],absorb:[每波长非负系数]}，空气 IOR=1。rays 每项 {angle,events,lights}，angle 是初始相对固定参考轴的有符号角度（度）。events 为有序 enter/exit 界面（含 material 和 normal，normal 是本界面的有符号参考法线角度），或 distance（当前介质中给定长度）。enter 将指定介质压栈；exit 应弹出栈顶并回到下一层。角度先相对 normal 做 Snell；透射更新角度。检测全反射、报告并反射，栈保持原样。距离按当前吸收系数 Beer–Lambert。Fresnel 反射可报告，透射的 transmission 只计吸收，不在这里重复计界面 Fresnel。界面几何通过事件给定，离线不做模型求交。

lights 为面积光采样约化数据，每项有 spectrum、weight（该积分分支真实权重）、pdf（提议概率，和为 1）。给定 spp 和 seed，按可复现的每像素种子独立采样波长和光源分支；估计器权重除以联合提议概率。xyz 为 sum(basis×transmission×按真实分支权重求和的 spectrum)/波长数。返回每个 rays 项 transmission、exit_angles、tir、xyz，image 是全体 XYZ，stats 可报告方差/能量。spp=0 是确定性全枚举参考。支持 --tile 和 --workers，给定 seed 的 image 在两种排程下相同。
