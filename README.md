# LSCM 网格参数化接缝与畸变
`python3 unwrap.py mesh.json output.json`
mesh.json: `{"pos":[[x,y,z]],"tris":[[a,b,c]]}`
- 最小接缝集切开非盘状拓扑; 共形参数化(最小角度畸变); 缝合边共享顶点 UV 连续
- 输出 `{"uv":[[u,v]],"seams":[[a,b]],"islands":n,"energy":v,"angle_err":v,"digest"}`
缺陷基线: 按三角形逐一投影到局部平面(每面一岛,无接缝概念).
