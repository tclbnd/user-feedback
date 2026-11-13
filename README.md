# 透视纹理与各向异性采样

Python 3.9+。用 `python3 texture.py demo.json output.json`。texture 是线性亮度的方形 2 的幂贴图，循环包裹。samples 给出像素中心 x,y 和三角形 vertices。每个 vertex 为 {x,y,w,u,v}，屏幕坐标已经过投影，1/w 为齐次倒数。由同一屏幕三角形内对 U/w、V/w、1/w 的仿射插值得到透视校正 UV。梯度为 quotient rule 的连续解析导数。输入可给 near；在 1/w 平面上截去 w<near 的部分，只为剩余内点输出。退化或无效采样返回 color=null，gradient=null。不得对用于求导的 UV 预先取模。可选 reference_mode="point" 关闭 Mip 及各向异性，只作 0 级双线性。双线性 texel 中心为 (i+.5)/N。

实现在线性亮度域平均生成 Mip、用 UV 梯度选层和采样方向。限制 tap 数，说明退化脚印回退。输出 results 与 samples 同顺序，每项 {color,gradient,mip,taps}，gradient=[du/dx,du/dy,dv/dx,dv/dy]。均匀平面切换对角线应一致。提供局部参考或调试统计。
