# 三维分簇光源裁剪与视锥相交判定

`python3 cluster.py input.json output.json`

input.json: `{"grid":[gx,gy,gz],"bounds":[[x,y,z]min,[x,y,z]max],"lights":[{"pos":[x,y,z],"r":f,"type":"point|spot","dir":[..],"half":rad}...],"seed":1}`

模型约定：
- 簇=均匀网格包围盒;光源球(r)或聚光锥(dir,half)与簇盒保守相交即入该簇列表
- 聚光影响域按半角收缩:有效半径 r_eff = r·sin(half) 之外仍按球判(简化:锥近似为收缩球)
- 光源输入顺序不改变簇分配;空簇正常输出
- 输出 `{"clusters":[[light_id...]每簇],"counts":[每簇数],"digest":"sha1"}`

缺陷基线:只按球心落格(球跨格漏),spot 不收缩。
