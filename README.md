# 透明片段分块合成

Python 3.9+，执行 `python3 composite.py demo.json out.json --tile 4 --budget 16`。场景有 size=[宽,高]、samples（每像素样本数）和 opaque[y][x][sample]。每个不透明样本给出 z（null 表示没有遮挡）及线性预乘 rgba（可为透明背景）。透明输入 fragments 是列表，每条含 x、y、sample、z、id（稳定全局整数）及线性预乘 rgba。数据生成后只读，视为源缓存，可按需反复扫描。z 越小越近。z 大于或等于不透明 z 的片段被遮挡，零 alpha 不参与合成。

每个样本按 (z,id) 从小到大定义近到远顺序。按该顺序进行预乘 OVER，再叠到底层，输出 rgba[y][x] 为各样本四通道均值。线性 resolve 与任何显示编码分开。--tile 是方形处理块边长，--budget 是合成阶段同时驻留的透明片段记录上限。普通样本可走记录缓存及排序，溢出必须准确回退，可使用多次扫描或外存。已读的输入源、背景、输出像素和轻量索引统计不计入片段记录预算，独立副本或合成阶段缓存则计入。不能因超过预算丢片段。

stats 至少含 overflow_pixels（存在一个样本的有效片段数超过 budget 的像素数）和 peak_fragment_records（合成路径实际同时驻留片段记录数），报告独立于提交顺序。可附加其它吞吐计数。预算及 tile 必须为正整数。
