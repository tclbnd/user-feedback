# 地形分块 LOD 与接缝

Python 3.9+。执行 `python3 terrain.py demo.json output.json`。输入 frames 每帧含 tiles，每项 {id,x,y,error,ready}，单位世界方块 [x,x+1]×[y,y+1]。每个 tile 已有常驻的低精度祖先覆盖。level 0/1/2 细分为 2/4/8 段。只允许 ready=true 时选 level>0。第一帧 error≥1.2 升，≥2.4 再升。以后从上一帧 level 起，根据同样升阈值和降阈值 0.8、1.8 做滞回。先约束相邻 tile level 差≤1（可降低较细端），之后才生成 mesh。每块每帧只有一个生效覆盖，未加载处用 level 0。

高度为 h(x,y)=0.15x+0.2y+0.7×max(0,1−abs(x−1.35)×2)；共享边要使用完全同一组世界点。粗细间可在较粗侧补边或细侧沿公共边缩并，保持相邻三角面闭合。输出每帧 tiles（按 id），每项 {id,level,bounds,vertices,faces,edges,triangles}。vertices=[x,y,z]，faces 为三角形的零基顶点索引。edges 为 west/east/north/south 的顶点坐标列表，均按另一轴升序。每个 tile 的整个 XY 单位面积应有单层覆盖。允许增加过渡状态及诊断。稳定同一帧中重新排序输入 tile 不影响结果。
