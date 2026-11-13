# 保守实例遮挡裁剪

Python 3.9+，运行 `python3 cull.py demo.json output.json`。可选 --no-cull 使用全实例，--workers N 设置块查询并发度。scene 含 size=[宽,高]、near 和 frames。每帧给 occluders（rect 为屏幕整数半开矩形、z 为深度、opaque 为布尔）和 instances（id 唯一正整数、material 字符串、box=[x0,y0,z0,x1,y1,z1] 为相机空间包围体）。每一帧是独立几何位置，上一帧状态只可作为提示，不能替代当前遮挡证据。背景深度无限远。透明遮挡图元不写入遮挡深度。

画面真值由给定 render 得出，保留其绘制规则。投影矩形按 x/z 与 y/z，当前最小可能深度 max(near,z0)。包围体穿过近面时保守保留，可排除完全在近面后方或确定全屏外的实例。像素中心为 x+.5,y+.5。允许整像素上取整做查询范围，只可减少裁剪，不能误删。遇到深度相等按保留处理。用层级最大深度覆盖查询，层级中有任何未遮挡像素就不能证明整块遮挡。可并行查询，在输出前稳定排序。

每帧输出 kept（升序）、batches（按 material 排序，ids 升序）、ids（最终逐像素绘制 ID）、stats（含 hierarchy_queries）。透明遮挡、近面穿越、屏外、薄挡片和遮挡移开都用给定场景验证。不同输入遍历顺序和 worker 数的 kept、batches、ids 完全相同。
