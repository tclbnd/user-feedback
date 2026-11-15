# 次表面散射曲率厚度近似
`python3 sss.py req.json output.json`
req.json: `{"thick":"t.json","light_dir":[x,y,z],"normals":"n.json","seed":1}`
t.json/n.json=32x32 厚度/法线图. 曲率=∇²厚度; 透光量=exp(-thick/σ)*max(0,-n·l)
- 背向像素(n·l>0)透射=0; 薄区(thick<0.1)饱和夹取; 输出 `{"trans":[[v]],"curv":[[c]],"dark_zero":true,"digest"}`
缺陷基线: 法线衰减当透光(与厚度无关),无背向零值.
