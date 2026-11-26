# 程序化大地图与平滑过渡运行模块

本目录承载《断箭》（Broken Arrow）Airbase 9km × 9km 大地图按路线 B（程序化地形与草丛 + 球形法线低模立体植被 + 距离环带 Bayer 连续溶解过渡 + `.scvmap` HTTP Range 单包归档 + 可调模块化建筑与自动物理派生）的独立运行时源码。

## 核心职责与文件入口

| 文件 | 职责说明 |
| --- | --- |
| [渲染基线.mjs](渲染基线.mjs) | 项目唯一风格化渲染基线配置源（`680p + 复古微缩 Stylized`：`16:9 = 1208×680` / `21:9 = 1588×680` / `full = round_even(680×vw/vh)×680` 三态 Hor+、`antialias: true`、`pixelated` + 暖调微缩滤镜、`bloomThreshold: 1.35` 680p 阈值 HDR Bloom、`tiltShiftDof` 仅远景 3D 深度自然景深与轻空气薄雾及缩放高度衰减 `360m → 1200m`、UI 双层分离） |
| [预览入口.html](预览入口.html) | 独立主试玩入口，唯一锁定 `680p + 复古微缩 Stylized` 与 `HalfFloat MSAA 4×` 680p HDR Bloom（支持 `/settings` 与暂停菜单一键开关，主渲染器启用 `preserveDrawingBuffer: true` 防 Windows 合成闪黑，模块版本 `?v=v30-rclick-fw-manual`），已接入 Code SSOT V2.2（`/ui/*`）**全战竖长条透明底兵牌插画坞**（`/portraits/<slug>.webp` + 兵种渐变背板 + 血条/弹药微条 + 同视图 Keyed 增量发牌/抽卡离场动画 + `rtb` 撤离中标记 + 向上展开单车矩阵）、左下 `1:1` 144Hz 真实地形战术雷达与**默认折叠左侧单位详细参数面板**（快捷键 `I`，对标《全战 40K》展示 HP/速度/高度/盘旋半径/视野、三档 10 格火力条及武器射程/爆破/伤害）、左上 **4A 五大兵种配额增援竖轨**与 129 车部署抽屉、右下 **3A 离屏纯载具隔离实况窗**（含 0 单位待命画面；固定翼以等高只读「手动精确打击 · 禁止自动开火」徽条替代 ROE 下拉）+ **全页统一方案 B 竖向翻页命令栏**（攻击/机动全页严格等高，上 2×2 深槽双行战术卡 + 下 2×1 扁平视角/辅助条，带 `translateY` 方向性切页过渡与滑块指示条，固定翼机动页锁定 `M 高速直飞 / X 停航待命 / R 视野复位 / T 撤离战区`，含空选待命补齐占位格与撤离全链路防跟随死锁）与全图固定翼通用底座 V2.6 |
| [实时实况镜头.mjs](实时实况镜头.mjs) | 右下 3A 场景内 Live PiP 纯载具离屏隔离跟拍相机引擎（主场景复用主 `WebGLRenderer` + 独立 HDR/Output 离屏 `WebGLRenderTarget` 与 ACES/sRGB Y翻转后处理、零污染主屏默认帧缓冲杜绝交替清屏黑闪、30 FPS 节流零额外 WebGLContext、球面最短弧方位角阻尼、静态冻结 0 FPS 省电模式、固定翼 2D 光电人工地平线/开火微振与 240m 远裁剪） |
| [实战HUD.css](实战HUD.css) | 程序化大地图真 3D 实战 HUD 布局扩展表（全战竖长条兵牌坞、底部兵牌抽卡滑出 `scvCardDealOut` / 发牌入位 `scvCardDealIn` / 数量徽标闪烁 `scvCountBump` / `rtb` 撤离中与 `winchester` 弹罄角标、默认折叠左侧单位详情卡、右下角全页统一方案 B 翻页命令菜单与切页退场层在三档物理尺码下的等高对齐布局、#airlab-debug-panel 避让顶栏安全定位），严格复用 `/ui/令牌.css` 与 `/ui/组件.css`，不重复声明 `--ui-ch-*`/`--ui-state-*` 且禁用容器 `transform: scale()` |
| [战术小地图.mjs](战术小地图.mjs) | 9km 实战左下 `1:1` 战术雷达引擎（`1024×1024` 离屏军图地貌 + 真实公路跑道骨架、异步分片烘焙 `bakeBaseLayerAsync` / `refreshBuildingsAsync` 与紧凑 TypedArray 道路空间分桶、`9KM↔1KM` 无级滚轮缩放、原生 DPR 矢量高清 LOD、`fog-of-war-v1` 10Hz 双通道战争迷雾遮罩 + 电战热区 + UAV 侦察虚线圈/Bingo 预警 + 失联幽灵标、144Hz 同步旋转聚焦黄框及右键跨区下令）与底部兵牌聚合纯规则 |
| [战争迷雾引擎.mjs](战争迷雾引擎.mjs) | 9km 战场战争迷雾统一双通道权威数据引擎（`fog-of-war-v1`：零 Three.js/DOM 依赖、`256×256` RGBA8 `prevRGBA`/`currRGBA` 10Hz 胶囊扫掠刷新、`ba-source-ssot-v1` 无人机高低空/Bingo 返航与固定翼窄走廊/AC-130 光电/EW 态势热区分级派生、可选 `512×512` 惰性 `8×8` 纯建筑 OBB 遮挡位图与高程缓存、静止地面单位非空 `unitId` 射线复用与开阔地零 `atan2` 快速光栅、敌情显隐与失联幽灵记忆） |
| [场景运行.mjs](场景运行.mjs) | 9km 大地图统一运行时主循环、全 129 车纯军标型号与包围盒缓存、5 大分类独立配额（合计 `MAX_HERO_UNITS = 50`）129 车 `.scvveh` 即时/落点动态部署、多段任务队列、1 DC 固定翼贴地投影软影层、名牌 DOM 脏值比对与 `ResizeObserver` 尺寸缓存及混编己方阵营过滤驱动 |
| [载具动效控制.mjs](载具动效控制.mjs) | 129 车通用刚体姿态求解（支持固定翼通用底座大迎角俯冲拉起与失控翻滚姿态透传）及 37 车已有旋翼、炮塔/炮管与雷达枢轴驱动 |
| [载具空间音效.mjs](载具空间音效.mjs) | 9km 战场载具 3D 空间引擎混音控制器（8 声部软限幅、三兵种保底、固定翼 `flightAttitude` 油门工况联动 + 连续多普勒与机头/尾喷前后半球指向性、全局 `engineDuckBus` 重爆超压背景音闪避）、通用战场物理声学传播总线（`battlefieldBus`：全局唯一共享 3.6s `ConvolverNode` + Near `<260m` / Mid `260–680m` / Far `>680m` 三档固定距离分桶与每桶 5 档预建静态 `StereoPanner` 路由 + 干声直入重爆 `subPunchInput` + `spanGain` 协同与 UI 旁路）及 `AirEventAudioEngine` / `BattlefieldEventAudioEngine`（内置 RTS 虚拟监听点、相机右向量相对声像、`small`/`medium`/`heavy`/`massive` 四档声压平滑无断崖衰减、超远距对数声速延迟延展、40ms 防抖/`±5%` 随机音高/`1/√n` 并发保护器、`SFX_Afterburner` 加力声层、坠机动态俯冲追踪、重磅航弹下落呼啸弹体追踪、同源复用 GAU-8/A V2 四层实时距离与多普勒声源、地面弹着马赫锥裂响与二阶欠阻尼弹簧微震） |
| [数值基底与倍率规范.mjs](数值基底与倍率规范.mjs) | 唯一战斗数值基底与 `GLOBAL_BALANCE_SCALES`（`ba-source-ssot-v1`，默认全 `1.0×` 原站直出，含固定翼 5 大原型族 `FIXED_WING_FUEL_PROFILES`、`fuelDurationScale` 与 `rearmCooldownScale`），提供 `deriveAircraftStatsFromSourceRef` / `deriveWeaponStatsFromSourceRef` 与同量纲 `ZOMBIE_TARGET_BASE_STATS`，严禁绕过倍率表私自写死派生值 |
| [固定翼底座.mjs](固定翼底座.mjs) | 32 架固定翼（26 `fixed_wing` + 6 `uav`）5 大原型族 Unity Prefab 组件化底座（强制经 `GLOBAL_BALANCE_SCALES` 派生实战值、真实弹速、留空燃油与整备冷却）、取消实战血量改用工况分档燃油（`1.0×` 巡航 / `1.2×` 入航对准 / `1.45×` 突击 / `1.85×` 加力）与动态 `10%–18%` Bingo Fuel 强制返航整备闭环（含 `setFuelConsumptionPaused` 调试冻结开关）、Winchester 全对地弹罄状态与可召回自动 RTB、Hydra 70 空巢保留与三挂点轮流发射、统一 L1 连续盘旋与能量机动、2+2+2 双发脉冲热诱弹、坠毁渐进滚转与弹射弧/拖痕触发、前方三点地形前瞻防撞、共享油门/加力状态与 `heatShimmerProfile` 喷口热浪推流、A-10C 精准硬点与 4 门独立对地武器、真实气动飞行姿态、战区撤离（`issueEvacuate`/`cancelEvacuate`）、盘旋切线赶路、高低空切换与纯手操精准打击锁定 |
| [写实爆炸特效.mjs](写实爆炸特效.mjs) | 《Defilade》对标写实爆炸与飞行特效引擎 V3.0（P1 + P0 + P2 + 池 7 固定翼喷口热浪折射锥 `heatShimmerScene` 1 DrawCall，含 0 新增 DC 弹射短烟弧与撞地拖痕弹坑，兼容 680p HDR Bloom、Heat Shimmer、Tilt-Shift Macro DoF 与直渲 sRGB）：保持主 `vfxRoot` ≤ 6 DrawCall、零 `PointLight`、零热路径 GC 与全部既有 API 兼容 |
| [三维战术标绘.mjs](三维战术标绘.mjs) | 3D 空间线基础组件与纯几何计算函数库（`evaluateRangeState`/`sampleAltitudeDropLine3D`/`sampleArcCorridor3D`/`buildDashedSegments3D`）；V2.2 方案 2A 默认配置 `enabled: false`（0 DrawCall），将斜向空间线交由原生 DPR 高清标绘层按 3D 端点精准投影绘制以消灭 680p 像素锯齿 |
| [固定翼调试模式.mjs](固定翼调试模式.mjs) | 复用 `/play?mode=air-lab&unit=US_A10` 的严格隔离单机调试模式 V2.6：提供独立可拖拽、可折叠「AIR LAB 调试控制台」（`#airlab-debug-panel`，与右下角纯粹实战「攻击/机动」双页签命令盘解耦，正常 `/play` 零 DOM 注入；含靶区/姿态工况/撤离与呼回/4 档曳光/战术燃油与弹罄调试按钮组、高空投影聚焦、`0.45s` 尸群击飞倒地淡出与 `<=18s` 靶车余烟及实时微遥测）及 `window.__SCV_AIRLAB__` / `window.__SCAVENGER_AIR_LAB__` 只读遥测与 `setTracerColorMode` |
| [高度支撑.mjs](高度支撑.mjs) | `2m` 双线性主地面高度图（`height-2m.scvh`）+ 81 处立体双层/桥梁/隧道精确补丁（`multilayer-patches.scvp`）混合查询与 `querySupport` 兼容接口 |
| [空间索引解码Worker.mjs](空间索引解码Worker.mjs) | `SCVH` 2m 高度场单职责 Web Worker（`ready` 握手 + 独立线程执行 `decodeSCVHHeightfield`、`2251×2251` GPU 打包高度纹理 RGBA8 生成与 `4m` 半分辨率 `roleGridHalf` 降采样 + `Set` 去重零拷贝 Transfer 回传及异常原缓冲回退） |
| [程序化地形.mjs](程序化地形.mjs) | 四级嵌套 CDLOD 地形网格、基于 `splat-control.scvs`（支持直接 `ArrayBuffer` 重载）的 12 层生态材质程序化混合着色器（融合方案 A `256×256` 地表微缩草绒、2m 精确路边柔边与柔风草浪）、`syncPackedHeightTextureAsync` 零拷贝高度打包接管、`terrainTier` 滞回底座过渡、9km 外围 **方案 A+B 混搭动态战术沙盘**（3D 实体地层剖面金属边框 + 5.5km 深烟炭 `fwidth()` 抗锯齿双级等高线/MGRS 坐标网/外扩高程扫描脉冲）、程序化道路带状网格与水面构建 |
| [立体植被与草丛.mjs](立体植被与草丛.mjs) | 8 种层叠松枝云杉/多球心碎叶阔叶树与野灌木（禁用双面法线翻转 + 次表面透光 + 同步逐棵渐缩 DepthMaterial 树影退场）、全不透明实心微缩中景代理、10m 精准远景实心集群林冠壳（硬抠除建筑占地）与 **方案 A 风格化高性能草地**（固定 `162m` 滑窗消除 `115m` 瞬移、`120–175m` 平滑压低淡出、支持 `uLinearOutput` 与 680p HDR Bloom `OutputPass` 自动联动消除双伽马发白） |
| [自动物理派生.mjs](自动物理派生.mjs) | 任意旋转建筑的凸包/OBB 碰撞盒实时生成、纯建筑 OBB 块级 2D SAT 遮挡光栅化与编辑变更通知（`onOcclusionDirty`）、4 米局部 A* 寻路网格（OBB 反向包围盒光栅化 + TypedArray 最小二叉堆 + `inOpen` 去重 + 共线拐点抽稀）与直升机前向 30 米越障安全高度包络查询 |
| [地图编辑器.mjs](地图编辑器.mjs) | 模块化建筑双插槽按需实例化、**远景形态分流**（方正建筑 1 DC 合批包络盒带 `aInstAnchor` 锚点 + 岩石/油罐 `far-kit`）、**方案 1 同锚点实体平滑交接与原型就绪锁**（`840m` 提前预热、GLB 解码完前锁定远景不隐退，0 空窗 0 半边重影）、浏览器内 `?mode=editor` 编辑与地形笔刷 |
| [过渡动画管线.mjs](过渡动画管线.mjs) | 方案 1 唯一 LOD 过渡动画参数与驱动源（`LOD_TRANSITION_CONFIG`、`uSmoothSpan` 时间缓动、`uBuildingNearReady` 就绪锁、430–820m 极轻高空微雾），每帧零延迟同步相机参数消灭 45ms 顿闪 |
| [平滑过渡与加载.mjs](平滑过渡与加载.mjs) | `spatial-index.scvi` 定长二进制索引解析、`.scvmap` 归档包 `206 Partial Content` Range 并发合并读取（支持 `{ cacheSlice: false }` 独占缓冲模式与全精度升级期低优先级支撑页避让调度）、视锥环带预取、地形 `terrainTier` 滞回联动与 LRU 显存预算管理 |

## 架构与保护边界

- 本模块与现行 `src/地图验证/` 及 `src/战术演训/` 完全解耦并行，不反向修改既有共享模块。
- 默认通过 `scripts/程序化地图/预览服务.py` 在独立端口 `127.0.0.1:8820` 提供只读回环服务，不抢占 `8767`、`8768`、`8769`、`8780`、`8781`、`8784`、`8811`–`8814` 等既有端口。
- 实施计划见 [程序化大地图重建与分级平滑过渡-实施计划](../../docs/地图与载具/程序化大地图重建与分级平滑过渡-实施计划.md)，阶段验证报告见 [程序化大地图重建与平滑过渡验证报告-V001](../../reports/程序化地图/程序化大地图重建与平滑过渡验证报告-V001.md)。
- 固定翼通用底座与 1 号样板机规格见 [固定翼通用底座与A-10C完整设计卡](../../docs/地图与载具/固定翼通用底座与A-10C完整设计卡.md)，已落地接入 `固定翼底座.mjs`、`写实爆炸特效.mjs` 与 `固定翼调试模式.mjs`（`/play?mode=air-lab&unit=US_A10`）。
- 写实爆炸特效升级（P1+P0+P2 对标《Defilade》）完整实施计划见 [写实爆炸特效升级-实施计划](../../docs/地图与载具/写实爆炸特效升级-实施计划.md)，P0 离线 `8×8` 体积云图集资产见 [体积云图集说明](../../public/assets/vfx-explosion/README.md)。
- 网页端四阶段性能优化完整方案（Phase 1 迷雾 OBB 缓存与热路径零回流、Phase 2 切近景预热与 4×4 远景分块、Phase 3 闸门驱动后台升级分片、Phase 4 核心玩法后近景 OBB 裁剪与阴影节流）见 [网页端性能优化-实施计划](../../docs/网页端性能优化-实施计划.md)。
