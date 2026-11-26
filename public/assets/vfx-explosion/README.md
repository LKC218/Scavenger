# 写实爆炸体积云离线烘焙图集（vfx-explosion）

本目录存放 Scavenger 写实爆炸特效升级（V3.0）运行时使用的 `8×8`（64 帧，单帧 `128×128`，总分辨率 `1024×1024`）多通道体积烟云序列帧贴图资产。

## 资产清单与通道契约

- [smoke_vol_normal_alpha.png](smoke_vol_normal_alpha.png)：`1024×1024` 8-bit RGBA PNG
  - `R, G, B`：切线空间加权体积梯度法线 `N * 0.5 + 0.5`（X 右、Y 上、Z 朝向相机；半球钳位后经二次单位归一化），透明区精确填充中性法线 `(128, 128, 255)`，防止双线性过滤黑边渗色；
  - `A`：56 步带确定性黄金比低差异子步抖动的 Front-to-Back 体积透射累积密度 `Alpha ∈ [0, 1]`，有效包络半径 `r2d <= 0.87`，保证单帧四周保留 `>= 8.32 px` 零透明安全留白。
- [smoke_vol_heat_thick.png](smoke_vol_heat_thick.png)：`1024×1024` 8-bit RGBA PNG（`A = 255`）
  - `R`：黑体辐射热通道 `Heat ∈ [0, 1]`（爆心、Smooth-Min 缝隙与 2D Cavity 凹陷处高热，外凸圆瓣顶冷却，随帧演化冷却）；
  - `G`：光学自遮蔽厚度 `Thickness/AO ∈ [0, 1]`（外薄边与深缝隙处值低，厚实凸瓣中心值高）；
  - `B`：多尺度连续周期腐蚀噪声 `Erode ∈ [0, 1]`（全格连续无圆边突变且动态范围无饱和死区，供运行时年龄加权 `smoothstep` 柔边溶解）。

## Three.js 运行时加载约定

- 必须使用 `flipY = true`、`colorSpace = THREE.NoColorSpace`、`premultiplyAlpha = false`、`generateMipmaps = false`、`minFilter = THREE.LinearFilter`、`magFilter = THREE.LinearFilter`、`ClampToEdgeWrapping`。
- 图集行序约定：`col = frame % 8`（从左到右），`row = floor(frame / 8)`（从 PNG 顶行到 PNG 底行）。

## 来源追溯与使用许可

- **生成脚本**：[烘焙爆炸体积云图集.py](../../../scripts/程序化地图/烘焙爆炸体积云图集.py)（使用本机 Python `numpy` + `scipy` + `Pillow` 确定性程序化体积光线步进生成，固定种子 `SEED = 20261123`，无外部版权素材依赖）。
- **设计依据与实施计划**：[写实爆炸特效升级-实施计划.md](../../../docs/地图与载具/写实爆炸特效升级-实施计划.md)
- **运行时消费者**：[写实爆炸特效.mjs](../../../src/程序化地图/写实爆炸特效.mjs)（通过 `127.0.0.1:8800/public/assets/vfx-explosion/` 只读静态路由加载）。
- **使用范围与许可**：本项目自研程序化离线烘焙资产，仅用于 Scavenger 网页端运行与本地开发调试。
