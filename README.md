# PCSS 可变半影软阴影
`python3 pcss.py req.json output.json`
req.json: `{"shadow":"d.json","query":[{x,y,z}],"light_size":0.1,"blocker_r":8}`
- 邻域搜索遮挡物(深度<query.z); 无遮挡→全亮; 平均遮挡深度→penumbra=light_size*(z-avg)/avg
- 滤波半径夹取 [1,16] 纹素; 输出 `{"lit":[{x,y,v,kernel}],"avg_depth":{q:avg},"digest"}`
- 遍历顺序不影响摘要.
缺陷基线: 固定 5px 核+均匀模糊(远近同软硬),零遮挡仍采样.
