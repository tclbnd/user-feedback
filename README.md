# 渐进三角网格

Python 3.9+。运行 `python3 progressive.py encode demo.json stream.json` 和 `python3 progressive.py decode stream.json out.json --level 1`。输入 {mesh,targets,max_error}，mesh 有 vertices=[{id,p:[x,y,z]}] 和 faces=[{id,v:[三个顶点 ID],uv:[三个逐角 UV],material}]。顶点和面 ID 唯一，面绕序有意义。targets 为由小到大的目标面数，max_error 为允许的几何误差。保留边界环、材质/UV 接缝和硬边；合法折叠不能使三角形翻面或产生重复面、非流形边。可在需要时提前停止并说明原因。

stream 至少含 base（最粗网格）、splits（从粗到细的局部可逆记录）与 levels（按粗到细的 {count,split}，split 为要回放的记录数量）。最后一级完全恢复原 mesh 的顶点、面 ID 与逐角属性。允许附加随机访问检查点及统计。decode 的 level 为 levels 零基下标。输出 vertices/faces 按 ID 排序，levels 的 count 真实等于解码面数。输入顶点或面列表重排不应改变规范化层级。所有样例为合成网格。
