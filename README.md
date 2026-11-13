# 八叉树体数据等值面

Python 3.9+。执行 `python3 surface.py demo.json output.json`。输入 field 为 sphere、shell、torus，半径参数见 field 函数，domain=[lo,hi] 表示立方域，center 默认原点。阈值 tolerance 越小需要越细，max_depth≤7，seed 用于测试叶插入顺序。可在距等值面足够远的节点终止，较粗与较细叶面相邻深度差≤1，等值面附近按误差细分。可使用跨粗细面的统一重构格、模板或局部填充，但最终顶点在相邻块共享身份。薄壳须保留内外两层。近零符号使用统一规则。提供 OBJ 导出入口或命令。

输出 {vertices,faces,stats}，faces 为三个零基顶点下标，保证共享边配对且方向相反。统计 leaf_depths（所有叶按深度计数）。无重复面或零面积面。输出 seed 变化时允许排列变化，但规范化坐标和三角形相同。为三种阈值输出实际细化分布。无外部依赖。
