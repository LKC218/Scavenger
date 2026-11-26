# 实战 HUD、Live PiP 小窗与战术小地图工程经验

本文沉淀 `/play`（9km 实战）在底部三舱 HUD 重构、场景内 3D Live PiP 实时小窗、左下角战术小地图无级缩放及局部高清矢量 LOD 性能优化过程中通过微基准与双浏览器端到端验收确立的可复用工程结论，配合 [项目开发经验](项目开发经验.md) 与 [全局UI-UX规范](../全局UI-UX规范.md) 使用。

---

## 一、Canvas2D 矢量战术地图性能优化与 Skia 描边陷阱（`2628ms → 0.13ms`）

### 1. 问题根因
在视距缩小至 `<= 3600m` 时启用 R1 局部矢量高清层重绘公路网与建筑。初版实现把视口内 36,523 个道路三角形塞入同一条 `ctx.beginPath()` 路径后调用 **`ctx.stroke()`（并开启 `ctx.lineJoin = 'round'`）**，每帧在主循环直接触发 Chromium Skia CPU 巨型路径圆角求并，同时逐栋对数千座建筑调用 `ctx.save() / translate() / rotate() / fillRect() / strokeRect() / restore()`：
- Chrome 无窗口基准实测单帧高达 **`2628.3ms`**（左上角跌至 `0FPS`）；
- 主线程长期锁死直接导致自动化点击按钮 30 秒超时。

### 2. 四层优化方案
1. **底层静态像素托底 + 次像素碎面剔除**：[战术小地图.mjs](../../src/程序化地图/战术小地图.mjs) 将 `terrainCanvas` 升至 `1024×1024`；进入 `_drawVectorRoadsInViewport()` 前按 `pxPerM` 计算三角形包围盒像素跨度，对 `bwPx < 0.55 && bhPx < 0.55` 的次像素碎面直接跳过。
2. **移除 Skia 路径描边，改为分批纯 `fill()`**：完全删除 `ctx.stroke()`、`ctx.lineWidth` 与 `ctx.lineJoin = 'round'`，按每 `512` 个三角形一组执行 `beginPath()` + `ctx.fill()` 提交，避免单路径压爆 Skia Active Edge Table。
3. **建筑按屏幕像素尺码分级批绘**：在 `_drawVectorBuildingsInViewport()` 中，将 `pw <= 3.6 && pd <= 3.6` 的小建筑按 `major` 分两组连续调用 `ctx.fillRect()`，彻底省去数千次矩阵堆栈保存与描边；仅对大中型清晰建筑（`pw > 3.6 || pd > 3.6`）和动态建筑（`_dynamicBuildings`）执行旋转 `fillRect` + `strokeRect`。
4. **动画期顺滑过渡 + 停稳后视口矢量缓存（`vectorCacheCanvas`）**：
   - 缩放或平移未收敛期间（`Math.abs(targetRangeM - currentRangeM) > 8 || Δcenter > 2.5`）直接复用 `1024×1024` 离屏 `bakedCanvas3km` 做单次 `drawImage()`，保证 144Hz 阻尼缩放过程零掉帧；
   - 收敛停稳后按 `${targetW}x${targetH}:${dpr}:${_buildingVersion}:${minX},${minZ},${viewRangeM}` 缓存键重建一次离屏 `vectorCacheCanvas`（包含同倍率慢速平移 `140ms` 防抖，但当画布尺寸变化时绕过防抖立即重绘防拉伸糊图），后续每帧仅执行单次位块拷贝 `ctx.drawImage(this.vectorCacheCanvas, ...)`。

### 3. 实测性能与避坑清单
- **实测基准**（Chrome Headless）：`3KM` 首次建矢量缓存从 `2628.3ms` 降至 **`45.2ms`**，缓存命中稳态帧仅 **`0.13ms`**；`1.5KM` 首次仅 **`9.3ms`**；`M` 键展开 `468×468` 大号战术图 `1.5KM` 首次 **`8.9ms`**、稳态 **`0.78ms`**。
- **严禁 GPU 回读**：离屏图层叠加禁止对主 Canvas 或 WebGL 画布调用 `getImageData()`，必须维护独立离屏缓存再用 `drawImage()` 合成。
- **命令式切档立即失效节流**：`snapAnimation()`、`_buildingVersion` 变更（`bakeBaseLayer()` / `refreshBuildings()`）须同步重置 `_lastVectorBuildMs = 0`，确保调用 `setMinimapZoomRange(..., true)` 第 1 帧即可得到 `vectorLodActive === true`。

---

## 二、场景内实时 3D Live PiP 载具状态小窗

模块入口：[实时实况镜头.mjs](../../src/程序化地图/实时实况镜头.mjs)、调用接入：[预览入口.html](../../src/程序化地图/预览入口.html)。

1. **纯载具零拷贝原子隔离渲染**：
   - 不克隆第二套载具模型，也不新建第二个 `WebGLRenderer`（避免多上下文与纹理翻倍）；
   - 在 `renderBeforeMainFrame()` 渲染主画面前，以 `collectVisibleAncestorChain()` 与 `isolateUnitBranchInScene()` 临时隐藏除 `unit.node` 及顶层光照/方向辅助节点（`isPreservedTopLevelHelper`）外的全部 9km 地形、道路、植被、建筑与友军节点，同时在 `try...finally` 内临时关闭 `renderer.shadowMap.enabled`（跳过 2048px 太阳阴影贴图重算）并置灰蓝军工纯底 `0x161d28`、清除 `scene.fog`，利用 `renderer.setScissor` 渲染至小区域再拷贝至 `#hud-pip-canvas`，默认 `30 FPS` 节流，整体仅耗时零点几毫秒。
2. **球面最短弧方位角防抖跟拍**：
   - 反复点击相反方向目的地时，直接对相机直角坐标做线性插值会产生直线穿模，而欧拉角差跨 `±PI` 会产生 360° 甩头跳动；
   - 改用 `stepPipSmoothAzimuth()` 沿 `wrapAngleRad()` 计算最短有向弧长进行指数阻尼推进，围绕载体局部前向（`+Z`）保持固定球面半径与俯仰角锁心跟拍，`distSpan` 保持稳定无跳动。
3. **静态 `0 FPS` 省电模式与斜前标准姿态**：
   - `toggleStaticMode()` 支持一键在 `● LIVE ↔ ■ 静态` 间切换；切入静态或切换选中新单位时仅按标准斜前 `3/4`（`front`）姿态绘制 `1` 帧即冻结（`staticDirty` 归零后后续每帧直接跳过 WebGL 渲染，增量帧数为 `0`），并禁用视角切换按钮（显示 `已冻结`），切回 LIVE 自动恢复原追踪视角。

---

## 三、无级锚定缩放、世界坐标 MGRS 网格与原地大号展开防跳位

1. **以鼠标归一化坐标为锚点的无级缩放**：
   - `computeAnchoredZoomViewport()` 先计算当前鼠标下对应的世界坐标 `(worldX, worldZ)`，缩放到 `nextRangeM` 后反推新的 `minX / minZ` 并钳位至地图边界 `bounds`，保证指哪放哪不漂移；
   - `#hud-minimap-box` 的 `wheel` 监听必须在捕获阶段/当前节点调用 `preventDefault()` 与 `stopPropagation()`，严禁滚轮事件穿透到主 3D 场景相机造成双端同时缩放。
2. **世界坐标绑定动态 MGRS 网格与军用比例尺**：
   - 随 `viewRangeM` 自动三级细化 MGRS 步长：`> 4800m` 用 `1500m`（`A~F / 1~6` 主格），`2100m ~ 4800m` 用 `500m`（含子格 `A.1`），`<= 2100m` 用 `250m`；顶部列标与左侧行标自动剔除靠近左上角倍率徽章（`x < 86` / `y < 30`）及底部坐标条的重叠区；
   - 右下动态比例尺由 `computeMinimapScaleBar(viewRangeM, width)` 以 `targetM = range * 0.26` 从标准刻度 `[100, 200, 250, 500, 1000, 1500, 2000, 3000]` 匹配最整洁读数（如 `9KM -> 2KM`、`3KM -> 1KM`、`1.2KM -> 250M`）。
3. **原地向右上展开大号战术图（`M` 键 / `[⛶]`）防卡片坞跳位**：
   - [预览入口.html](../../src/程序化地图/预览入口.html) 的 `setMinimapExpanded(true)` 在给 `.scv-radar-dock` 添加 `.is-expanded-map` 并将其变为绝对定位前，先读取并锁定父级占位容器 `.scv-bl-wing` 的当前像素 `width / height`；展开后的地图在原左下角原地向上向右扩张至 `min(468px, calc(100vh - 220px), calc(100vw - 460px))`；
   - 这使中间弹性卡片坞不会在展开瞬间向左猛跳、收起时不会回弹抽搐；同时严格遵守 [全局UI-UX规范](../全局UI-UX规范.md) 禁止使用 `transform: scale()`，并将展开状态注册进 `escStack`，确保按 `Esc` 优先收回大号战术图而不误触 4A 暂停菜单。

---

## 四、实战底栏三舱协同、纯型号清洗与 Code SSOT V2.2 约束

1. **纯军标型号命名与同型聚合浮卡**：
   - 卡片坞（`aggregateDeckUnitsBySlug()`）、部署目录与右侧信息栏统一使用清洗后的英文军标型号（去除中文外号及 `US_` 前缀，多台同类以 `#01 / #02` 编号），彻底杜绝在战术面板混入冗余中文昵称；
   - 同型多车聚合卡片角标 `×N ▾` 向上弹出独立外置浮卡 `#hud-deck-popover`，避免被卡片坞横向滚动条 `overflow: hidden/auto` 裁切，并独立注册进 `escStack`。
2. **紧致布局、窄屏与超宽屏双画幅防碰撞**：
   - 右舱顶部把 Live PiP 帧与核心遥测（状态/航速/坐标/高程）+ ROE 满宽下拉收拢在紧致两栏内，武器条横贯中排，底部保持 2×3 大图标指令舱并移除冗余副标题，在有限高度内实现与左侧雷达舱齐平（高度差控制在十余像素内）；
   - 每个交互改版必须同时通过 `1208×680`（16:9 窄窗下三舱无重叠、雷达顶栏微按钮不溢出）与 `2560×1080`（21:9 超宽 `Hor+` 对齐）。
3. **Code SSOT V2.2 与双层渲染硬规范**：
   - 全部下拉控件必须通过 [交互控件.mjs](../../src/界面规范/交互控件.mjs) 的 `bindUiSelect()` 实现（底部控件务必向上 `.is-dropup` 弹出），严禁出现原生 `<select>`；
   - 常驻 `.ui-pod` HUD 底座采用实底半透明面板（零 `backdrop-filter`），3D 场景严格保持 `680p + 复古微缩 Stylized` 基线（见 [渲染风格与分辨率基线规范](../渲染风格与分辨率基线规范.md)），HUD 与名牌保持原生 DPR 高清矢量层不被 3D 滤镜污染。

---

## 五、纯规则单测、双浏览器 E2E 与旧预研收口流程

| 层级 | 工具 | 覆盖职责与关键校验口径 |
| --- | --- | --- |
| 纯数学规则单测 | [验证交互规则.mjs](../../scripts/交互系统/验证交互规则.mjs) | 在 Node ESM 无浏览器环境下秒级校验 `39` 条纯规则（锚定缩放、比例尺、MGRS 网格、球面角最短弧、信号型号清洗、队形、寻路、LIFO ESC 等） |
| 性能微基准 | 临时只读 Playwright 脚本（存个人 scratch） | 遇帧率异常先独立量化首帧 `cold` 与命中 `warm` 耗时，确认根因后再重改渲染管线 |
| 全仓结构静态门禁 | [检查项目结构.py](../../scripts/检查项目结构.py) | 强校验白名单路由、Code SSOT V2.2 令牌/禁止原生 `<select>`、`docs/界面预览/` 登记与全部文档相对链接 |
| 双浏览器 HUD / E2E | [验证实战HUD.py](../../scripts/程序化地图/验证实战HUD.py)、[验证操作交互.py](../../scripts/交互系统/验证操作交互.py) | 实跑 Chrome + Edge 双通道：覆盖 16:9 与 21:9 布局、滚轮不穿透、矢量 LOD、静态 `0` 帧冻结、反复换向 `distSpan = 0.0000m`、M 键展开/Esc 收回并逐张人工审阅截图 |

- **实装完及时清退旧对比页**：当某项 UI/渲染预研已合并入 `/play` 并通过工程师验收后，立即清理 `docs/界面预览/` 过期对照页、废弃对比路由（如曾清退 `/compare`、`/res-compare`）与 HTML/MJS 中 `?embed=1` 死分支，重启 `127.0.0.1:8800` 验证旧路由严格返回 `404` 后再跑 [检查项目结构.py](../../scripts/检查项目结构.py) 清零全仓断链。

---

## 六、Windows 终端、脚本与跨会话清理协作注意点

1. **PowerShell 5.1 命令规范**：环境默认是 Windows PowerShell 5.1，禁止使用 bash 风格的 `&&` 或 heredoc `<<'PY'`；多条命令拆成独立调用或独立命令执行。
2. **中文 URL 请求编码**：Python `urllib.request` 请求含中文文件名的本地路由（如旧页面 `404` 核对）前必须先经 `urllib.parse.quote()` 编码，否则底层 HTTP 客户端会因非 ASCII 字符抛错。
3. **清理工具防误删边界**：开始或续跑任何修改轮次前须用 [清理工具.py](../../scripts/维护清理/清理工具.py) `protect --task <独立任务名> --path <读写路径...>` 登记全部读写范围（注意参数为 `--path` 而非 `--write`）；严禁在工程师明确确认具体新版通过前提前执行 `accept` 或 `close`，也不得擅自改动其他并发会话的任务范围。

---

## 相关源码与规范入口

| 模块 / 规范 | 路径 |
| --- | --- |
| 战术小地图引擎（无级缩放 / 分桶矢量 LOD / 缓存） | [战术小地图.mjs](../../src/程序化地图/战术小地图.mjs) |
| 场景内实时实况 PiP 相机控制器（纯载具隔离 / 球面防跳 / 静态 0 FPS） | [实时实况镜头.mjs](../../src/程序化地图/实时实况镜头.mjs) |
| 实战 HUD 主入口与 3D 模拟衔接 | [预览入口.html](../../src/程序化地图/预览入口.html) |
| 实战 HUD 样式表（三舱 / 展开地图 / 指令舱） | [实战HUD.css](../../src/程序化地图/实战HUD.css) |
| 交互系统快捷键与 ESC 状态机接入 | [入口接入.mjs](../../src/交互系统/入口接入.mjs) |
| 共享控件库（`bindUiSelect` / `createEscStack`） | [交互控件.mjs](../../src/界面规范/交互控件.mjs) |
| 全局 UI/UX 规范与渲染基线 | [全局UI-UX规范](../全局UI-UX规范.md)、[渲染风格与分辨率基线规范](../渲染风格与分辨率基线规范.md) |
