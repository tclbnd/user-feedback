# 切线空间重建与镜像UV旋向

```
python3 tangent.py mesh.json output.json
python3 -m unittest -v tests_tangent     # 验收测试
```

`mesh.json`: `{"pos":[[x,y,z]],"uv":[[u,v]],"tris":[[a,b,c]]}`
`uv` 支持逐顶点（长度 = 顶点数）或逐角（长度 = 3 × 三角形数）两种布局。

## 算法

- **逐角**求解 dPos/dUV 系统：`T_tri = (e1·dv2 − e2·dv1)/det`，
  `B_tri = (e2·du1 − e1·du2)/det`，`det = du1·dv2 − dv1·du2`；
  `h = sign(det)` 记录该角的手性。
- 按 **(顶点, 手性)** 分组累积：UV 接缝两侧参数化不同但曲面相同，必须共享
  同一组以保证切线连续；镜像岛的 `h` 相反，与正常岛分组分离，因此同一顶点
  不会因两份贡献相互抵消（这正是"镜像岛法线贴图凹凸反向"的根因）。
- 权重采用角权重：切线用 UV 空间角（Mikktspace 做法，消除四边形拆成两个
  三角形带来的偏置），顶点法线用位置空间角，并且**法线按顶点累积、不参与
  手性分裂**（法线是纯几何量，不能依赖 UV 图）。
- Gram-Schmidt 正交化：`T = normalize(T_raw − N·dot(N, T_raw))`，输出
  `w = h`，因此 `B = w · cross(N, T)`；镜像侧 `T` 反向、`N` 不变，故
  `cross(N, T)` 反向、`w` 同时变号，两侧 `|cross(N, T)| = 1`。
- 零面积 UV 三角形（`|det| ≤ 1e-12·|du1|·|du2|` 或 UV 边为零）记为
  `degenerate`，只贡献法线；无可用切线的分组回退到约定基：
  `ref = (|N.x| ≤ 0.9 ? (1,0,0) : (0,1,0))`，`T = normalize(ref − N·dot(N,ref))`，
  `w = h`（法线本身为零时 `T = (1,0,0)`，`w = 1`）。
- 输出前把每个三角形循环旋转到规范角序、并按可比较全序求和，因此**遍历
  三角形顺序（含起始角旋转）不影响结果**，`digest` 逐位一致。

## 输出

`{"tangents":[[x,y,z,w]], "bitangent_sign":[w], "corner_vertex":[vid],`
` "split_vertices":n, "degenerate":d, "degenerate_corners":3d,`
` "fallback_keys":k, "fallback_corners":c, "uv_mode":"vertex|corner",`
` "split_tangents":[[vid,h,tx,ty,tz,w,n_valid,n_degen]], "digest":"sha256"}`

## 验收结果（`tests_tangent.py`）

- 解析圆柱：闭合无缝网格逐角切线误差 ≈ 8.5e-7°，30% 镜像岛同样 ≈ 8.5e-7°
  （远优于 0.5° 要求）；逐顶点/逐角两种 UV 布局均通过。
- 镜像平面：两侧 `cross(N, T)` 方向相反（点积 = −1）且模长均为 1，
  `w` 分别为 +1 / −1。
- 含 30% 镜像岛的模型随机重排面序 + 每面随机起始角，20 次全部
  `digest` 与逐组摘要逐位一致。
- 零面积 UV 面片回退到约定基（`T = +X`，`w = +1`），`degenerate` 计数正确。
