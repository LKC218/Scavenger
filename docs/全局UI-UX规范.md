# 全局 UI/UX 规范 V2.2（暗夜映画高反差 × 生化警戒黄黑 × 轻量化战术指令）

> 本规范为项目所有运行期网页端三维界面与工具台的**唯一权威设计基准（Code SSOT V2.2）**，与源码目录 [src/界面规范/](../src/界面规范/README.md) 及唯一主服务活体规范台 `http://127.0.0.1:8800/ui-spec` 保持代码同源，由 [检查项目结构.py](../scripts/检查项目结构.py) 自动校验全仓一致性；架构整合路线见 [单端口整合与正式开发规范-实施计划](单端口整合与正式开发规范-实施计划.md)。

---

## 一、核心风格定位、锁定基因与活体规范路由

- **风格定位**：**暗夜映画高反差 × 生化警戒黄黑 × 轻量化战术指令**，与项目唯一 [渲染风格与分辨率基线规范](渲染风格与分辨率基线规范.md)（`stylized-680p-v1`：`16:9 1208×680` / `21:9 1588×680`，3D 画布带 `sepia(0.06)` 轻暖微缩滤镜）协同设计。
- **已锁定视觉基因**：
  - **配色**：`A3 烟炭双金融合`（微冷烟炭暗底与暖调 3D 战场拉开层次；常规主高亮采用香槟金 `#f0d372`，高危警戒与排程预警采用工业琥珀黄 `#ffb800`）；
  - **按钮**：`B2 战术微描边精工`（`1px` 清晰轮廓线，悬停点亮主金边与主金字；3D 边缘热键采用 L1 无底框硬黑外描边；每屏限唯一 L3 主 CTA）；
  - **倒角**：`C4 外柔内锐`（容器 `--ui-r-surface: 12px` / `--ui-r-surface-lg: 16px`，交互控件 `--ui-r-control: 5px` / `--ui-r-sm: 4px`，徽章胶囊 `--ui-r-pill: 9999px`）；
  - **材质性能分级**：实时战斗常驻 HUD 一律使用高透暗实底（`--ui-bg-pod`，**禁止大面积 `backdrop-filter`**）以守住 100 载具 + 500 丧尸 60FPS 预算；仅暂停菜单、系统设置与模态确认弹窗允许使用 `--ui-bg-overlay-glass` 毛玻璃景深（`.ui-modal-backdrop`）。
- **唯一活体规范路由**：
  - 统一主端口子路由：`http://127.0.0.1:8800/ui-spec`（由 [启动主服务.py](../scripts/启动主服务.py) 挂载，兼容独立轻测），全部运行界面统一通过 `/ui/*` 同源引用。

---

## 二、三层全局语义色令牌（单一数据源注册表）

所有运行模块的语义颜色必须直接通过 `/ui/令牌.css` 引入，严禁在各模块 CSS/JS 中私自复制或重定义通道/状态颜色常量：

### 1. Tier 1 基础色阶与 Tier 2 表面／文字阶梯

| 令牌名 | 基准值 | 用途边界 |
| --- | --- | --- |
| `--ui-bg-canvas` | `#0d0e13` | 中性微冷烟炭底层画布（`--slate-900`） |
| `--ui-bg-pod` | `rgba(19, 20, 28, 0.92)` | 实时战斗常驻 HUD 高透暗实底（零模糊保 60FPS） |
| `--ui-bg-pod-elevated` | `#14151e` | 顶栏与右侧滑出抽屉不透底深烟炭实底 |
| `--ui-bg-overlay-glass` | 斜向烟炭暖金渐变 | 暂停菜单、系统设置与模态确认浮层毛玻璃底面 |
| `--ui-bg-slot` | `rgba(35, 37, 48, 0.88)` | 常规按钮、卡片、分段器与指令槽底色 |
| `--ui-line-subtle` / `--ui-line-base` / `--ui-line-strong` | `9%` / `19%` / `34%` 纯白透线 | 三级细边框阶梯（B2 常规轮廓与悬停强化） |
| `--ui-text-primary` | `#f5f6f9` | 主标题、呼号与核心数值读数（AAA 对比度） |
| `--ui-text-secondary` | `#b6bac5` | 副文本与说明信息 |
| `--ui-text-muted` | `#838896` | 辅助微标、键帽与静默注释 |
| `--ui-text-inverse` | `#0d0e13` | 香槟金 CTA 与纯白扫光焦点条上的反色墨黑文字 |

### 2. Tier 3 七维零撞色全局语义通道与状态色注册表

| 语义层级 | 令牌名清单 | 主色值 | 适用范围 |
| --- | --- | --- | --- |
| **地面载具通道** | `--ui-ch-ground`、`--ui-ch-ground-inner`、`--ui-ch-ground-fill` | `#38e5c0` / `#9bf5e0` | 坦克/步战/侦察/火炮/防空/反坦克/补给车贴地双环、名牌、地面航路与卡片左标 |
| **旋翼直升机通道（30m）** | `--ui-ch-heli`、`--ui-ch-heli-inner`、`--ui-ch-heli-fill` | `#68c8ff` / `#bae4ff` | 武装与运输直升机贴地双环、30m 垂直虚线、空中幽灵与名牌 |
| **固定翼攻击机通道（234m）** | `--ui-ch-plane`、`--ui-ch-plane-inner`、`--ui-ch-plane-fill` | `#b197fc` / `#dcd0ff` | 攻击机/战机/无人机**贴地主范围圈**（起伏高程采样 + 淡紫微填充 + 双层描边与切线箭头）、地面圆心十字、垂足同步滑动跟踪点、机头 `35°` 空中转向导引虚弧、高空航路与名牌 |
| **选中焦点与主激活** | `--ui-state-select`、`--ui-state-select-inner`、`--ui-state-select-fill` | `#f0d372` / `#f6de86` | 香槟金主 CTA、选中外环、高空机身四角锁定框与键盘焦点环 |
| **警戒 / 队列 / 待定** | `--ui-state-warn`、`--ui-state-warn-fill` | `#ffb800` | 工业琥珀黄：生化警戒灯、分区寻路准备路径与低弹药预警 |
| **敌对 / 拒收 / 高危** | `--ui-state-danger`、`--ui-state-danger-fill` | `#ff5266` | 尸潮红区、障碍拒收红色幽灵、危急脉冲与不可逆危险确认按钮 |
| **生化毒雾 / 洗消** | `--ui-state-toxic`、`--ui-state-toxic-fill` | `#a8f22c` | 酸蚀污染区、防毒面具警示与生化洗消专控按钮 |
| **安全 / 后勤就绪** | `--ui-state-safe`、`--ui-state-safe-fill` | `#3cd69f` | 补给走廊、安全区占领就绪与健康耐久基色 |

---

## 三、字体、4px 空间栅格、尺码、Z-Index 与动效令牌

- **字体栈与等宽铁律**：展示标题 `--ui-font-display`（窄体展示）、主正文 `--ui-font-sans`、遥测/键帽/倒计时 `--ui-font-mono`（强制启用 `font-variant-numeric: tabular-nums`，杜绝高频刷新抖动）。
- **字阶令牌**：`--ui-fs-2xl`（`26px`） / `--ui-fs-xl`（`20px`） / `--ui-fs-lg`（`16px`） / `--ui-fs-md`（`13.5px`） / `--ui-fs-sm`（`12px`） / `--ui-fs-xs`（`11px`） / `--ui-fs-2xs`（`10px`）。
- **4px 空间步长、三档控件高度与 V2.1 原生物理三档尺码令牌组（`--ui-sz-*`）**：`--ui-sp-1`（`4px`）～`--ui-sp-8`（`32px`）；基础控件高度 `--ui-h-compact: 28px` / `--ui-h-standard: 36px` / `--ui-h-primary: 44px`。在战斗 HUD 或高分屏桌面容器标记 `[data-ui-size="compact"|"lg"|"xl"]` 后，由 `/ui/令牌.css` 自动激活 `--ui-sz-*` 物理尺度变量（如 `--ui-sz-btn-h`: `28px / 36px / 40px`、`--ui-sz-select-h`: `25px / 32px / 36px`、`--ui-sz-cmd-cell-h`: `48px / 68px / 78px`、`--ui-sz-rail-w`: `84px / 116px / 128px`、`--ui-sz-mm-w`: `206px / 292px / 336px`）；**严禁对整体 HUD 容器使用 `transform: scale()` 放大**，确保任何桌面尺寸均原生高清锐利，且进入 `16:9` / `21:9` 680p 小窗时自动降回 `compact`。
- **共享交互引擎与 V2.2 全套业务组件**：由 [交互控件.mjs](../src/界面规范/交互控件.mjs) 统一导出 `createEscStack`（严格 LIFO 回退栈）、`bindUiSelect`/`bindAllUiSelects`（`.is-dropup`＋键鼠导航）、`createUiSizeController`、`loadUiPrefs`/`saveUiPrefs`（含独立 `vehicleAudioEnabled` / `vehicleAudioVolume` 载具空间引擎音偏好键与 680p 仅远景 3D 深度自然景深偏好键 `dofMode`：`off` / `soft` / `standard`，与菜单 `sfxEnabled` 完全解耦）、`createToastManager`、`syncCmdPagerIndicator`、`runCmdPageTransition`、**方案 A 连续八扇区战术轮盘引擎**（`TACTICAL_WHEEL_CONFIG`、`resolveWheelSectorFromVector`、`clampWheelVisualCenter`、`canCommitWheelSlot`、`renderTacticalWheel`、`renderTacticalWheelRay`）、**跨页共享载具参数详情组件**（`renderVehicleDetailCard`、`renderVehicleHoverCard`、`attachDeckHoverCardController`）与 Web Audio `playUiSfx`；[组件.css](../src/界面规范/组件.css) 统一提供 `.ui-input`（含 `.is-error`）、`.ui-toggle`、`.ui-wheel` / `.ui-wheel-svg` / `.ui-wheel-slot` / `.ui-wheel-center`（方案 A 8 槽连续扇区军工仪表盘轮盘：按住 `Space` 无限外延甩选 + 轻按单击兼容 + 中心死区与边角自动内推防裁切 + 激活绿弧/悬停金边/冷却弧/禁用斜纹/空槽退隐五态区分 + 零 `backdrop-filter` 与零 UI 提示音）、`.ui-vd-card` / `.ui-vd-hover`（跨页共享载具详情卡与底部兵牌悬停精简浮卡，零 `backdrop-filter` 与零 `transform: scale()`）、`.ui-modal` / `.ui-pause-grid`、`.ui-toast-stack`、`.ui-empty` / `.ui-skeleton`、`.ui-cap-bar` 与 `.ui-hud-grid` 4C 三行骨架。
- **五级 Z-Index 架构**：`--ui-z-world: 100`（世界内标绘） → `--ui-z-hud: 200`（战斗常驻 HUD） → `--ui-z-drawer: 300`（抽屉/轮盘） → `--ui-z-overlay: 400`（全屏菜单/模态） → `--ui-z-system: 500`（Toast/系统警报）；严禁业务代码随意写 `z-index: 9999`。
- **三档动效时长**：`--ui-dur-micro: 100ms`（微反馈）、`--ui-dur-standard: 200ms`（控件切换）、`--ui-dur-overlay: 300ms`（浮层进出），强制尊重 `prefers-reduced-motion`。

---

## 四、品牌标识与 5 大程序化矢量资产族（唯一代码源）

所有品牌标识、兵种剪影、通用武器剪影、情景警示与 HUD 线性图标唯一由 [图标与标绘.mjs](../src/界面规范/图标与标绘.mjs) 与 [favicon.svg](../src/界面规范/favicon.svg) 导出，严禁在其他源码或后端脚本中重写 `UNIT_SYMBOLS`、私自复制品牌 SVG 或硬编码多份浏览器图标：

1. **品牌标识 `BRAND_IDENTITY` / `BRAND_LOGO_LAYERS`（Option B · 生化肃清三楔盾 `bio-purge-triad-shield`，零位图程序化分层）**：
   - **视觉寓意**：以军工护盾包裹 3 枚 120° 向心装甲生化楔片与菱形核心，同步隐喻「生化肃清」「三占领区」与「陆 / 旋翼 / 固定翼三域合围」。
   - **四级光学尺码（Optical LOD）与导出工厂**：
     - `createBrandLogoSvg({ variant, size, tone, animated })`：支持 `mark`（48px+ 多层主标，含 `a3` 烟炭双金、`tridomain` 三域彩蛋、`toxic` 生化洗消与 `mono` 单色）、`sigil`（28~48px `currentColor` 负空间 `evenodd` 顶栏/HUD 徽章，已全站替换原 `focus` 临时占位）、`favicon`（16~24px 内置 `#0d0e13` 暗底板与粗化几何精简版）及 `stencil`（保留 4 处结构桥接缺口的军工漏印版）；
     - `createBrandLockup` ＋ `.ui-brand-lockup`：标准化横/竖版组合标（`SCAVENGER` + `BIO-PURGE // 3-ZONE OPS`），严禁 `transform: scale()`；
     - `buildBrandFaviconSvgString` 与单源静态文件 [favicon.svg](../src/界面规范/favicon.svg)：唯一主服务 [启动主服务.py](../scripts/启动主服务.py)、独立规范路由与地图预览服务统一读取该文件分发 `/favicon.svg`；
     - `drawBrandLogoCanvas`：基于 `Path2D` 离屏缓存，供 2D 战术雷达底纹与 3D 停机坪/载具 `CanvasTexture` 漏印贴花零开销复用；加载锁定动效（`.is-brand-animated`）强制尊重 `prefers-reduced-motion`。
2. **`VEHICLE_SILHOUETTES`（12 大武器库兵种 ＋ 3 个兼容别名，`viewBox="0 0 24 24"`，`fill-rule="evenodd"`）**：
   - **地面 7 类（覆盖 77 款在库地面车型）**：`mbt`（主战坦克）、`ifv`（步战/装甲车）、`recon`（侦察/轻型突击）、`arty`（自行火炮/火箭）、`aa`（野战防空/导弹）、`atgm`（反坦克导弹车）、`supply`（后勤补给/工程）；
   - **空中 5 类（覆盖 52 款在库空中机型）**：`attack_heli`（武装直升机）、`trans_heli`（运输/倾转机）、`cas`（对地攻击/炮艇）、`fighter`（制空战斗/轰炸）、`uav`（侦察/察打无人机）；
   - **向下兼容别名**：`tank`（映射 `mbt`）、`heli`（映射 `attack_heli`）、`plane`（映射 `cas`）。
3. **`WEAPON_ICONS`（固定 4 大类通用武器剪影，`viewBox="0 0 24 24"`，`fill-rule="evenodd"`，由 `createWeaponIconSvg` / `resolveWeaponIconKind` 渲染）**：
   - `cannon`（机炮 / 火炮：三联弹链/膛口轮廓，覆盖 GAU-8/A、30mm 链炮、步战机炮、坦克主炮）；
   - `rocket`（火箭弹 / 火箭巢：双发斜跃无控火箭带尾焰，覆盖 Hydra 70、Zuni、M270 / HIMARS 齐射）；
   - `missile`（精确制导导弹：大翼面引导弹体+导引头分隔线，覆盖 AGM-65、地狱火、TOW、AIM 系列）；
   - `bomb`（航弹 / 制导炸弹：下坠水滴弹体+制导环带+尾翼箱，覆盖 GBU-31 JDAM、Mk-82、SDB、集束航弹）。
4. **`HAZARD_ICONS`（4 枚现实原型情景警示图标，由 `createHazardIconSvg` 渲染）**：
   - `biohazard`（ISO 7010 标准三环尖爪）、`radiation`（ISO 7010 标准三叶草）、`gasmask`（军用防毒面具）、`horde`（尸群高危骷髅警示）。
5. **`HUD_ICONS`（26 枚统一 `1.75px` 圆角线宽功能图标，由 `createHudIconSvg` 渲染）**：
   - **通用基础与播放控制（19 枚）**：`move`、`attack`、`stop`（八角止动标+制动横杠）、`rally`、`airstrike`、`repair`、`shield`、`lock`、`alert`、`drawer`、`focus`（锐利四角取景+十字中心）、`soundOn`、`soundOff`、`pause`、`play`、`reset`、`layers`、`compass`、`copy`；
   - **命令面板专属语义（7 枚）**：`flare`（热诱弹抛洒）、`altitude`（高低空高度层切换）、`evacuate`（破界撤离战区 RTB）、`sprint`（加力高速直飞）、`autofire`（保留功能图标键，固定翼实战已取消自动开火并强制纯手操）、`follow`（目标框持续跟随）、`wheel`（8 槽战术轮盘）。

---

## 五、按钮三档、游戏状态机、双画幅安全区与双层高清

1. **按钮三档与无底框外描边铁律**：
   - **L1 无底框硬黑外描边（`.ui-prompt-btn`）**：3D 边缘高频微操必须采用 `paint-order: stroke fill` 与 `--ui-outlined-stroke-w: 3.4px` 外扩黑描边，按钮与文本强制 `white-space: nowrap; flex-shrink: 0` 防止窄窗竖排折行，键帽 `.ui-keycap` 强制声明 `-webkit-text-stroke: 0; text-shadow: none` 防糊团；
   - **L2 微描边常规按钮（`.ui-btn`）** 与 **L3 唯一主 CTA（`.ui-btn--primary`）**：每屏/每弹窗限 1 个主 CTA。
2. **游戏专属与定制表单组件规范**：
   - 指令槽（`.ui-cmd-cell`）强制顶部预留 `>= 17px` 热键/充能角标安全区，确保任何尺码下 `.c-kbd` 与 `.c-ico`/`.c-txt` 物理不重叠；`/play` 实战命令栏已全面统一为**方案 B 全页等高分区结构**（`.ui-cmd-deck-b`：上区 2×2 宽幅深槽双行战术卡 `.ui-cmd-wep-grid` / `.ui-cmd-wep-card`，攻击页消费 `WEAPON_ICONS` 4 大通用剪影承接型号/弹药/大类/释放方式，机动页、地面/旋翼页及 0 单位待命态统一消费 `HUD_ICONS` 承接态势副文与动作标签，并支持 `.is-weapon-card` / `.is-neutral-cmd` / `.is-ground-cmd` / `.is-heli-cmd` / `.is-danger-cmd` / `.is-standby-slot` 语义修饰 ＋ 下区 2×1 扁平视角/辅助条 `.ui-cmd-util-bar` / `.ui-cmd-util-btn` 固定承接瞬发反制或聚焦/跟随；原 `.ui-cmd-cell` 3×2 格继续保留于物理尺码演示与兼容扩展），完整支持就绪、激活高亮（`.is-active-mode`）、扇形冷却遮罩（`.ui-cd-mask` ＋ 等宽倒计时）、充能低量预警（`.is-low`）、耗尽禁点（`.is-empty`）、未解锁斜纹暗幕（`.is-locked`）与紧急脉冲（`.is-alerting`）；
   - 耐久进度条支持受击延迟白条（`.ui-hp-trail`）、反应甲增益段（`.ui-hp-bonus`）与低血危急脉冲（`.is-critical`）；
   - **严禁直接裸用浏览器原生 `<select>` 或未定制样式的 `<input type="checkbox|radio|range">`**，所有下拉选择、单/复选与滑块必须消费单源 `.ui-select`（深烟炭不透底浮层＋香槟金选中左标与勾号＋LIFO `ESC` 优先收起）、`.ui-check`、`.ui-radio` 与 `.ui-range`（垂直白棒滑块）；
   - 5 类离线 SVG 键鼠光标（`.ui-cur-default` / `.ui-cur-move` / `.ui-cur-attack` / `.ui-cur-invalid` / `.ui-cur-action`）与全局 `ESC` LIFO 逐层回退栈。
3. **`16:9` / `21:9` 双画幅安全区与双层分离高清渲染**：
   - 严格绑定 `680p` 双画幅（`1208×680` 与 `1588×680 Hor+`）：四周保留 `24px` 安全边距，中央 `60%` 严禁常驻实心面板，`21:9` 下推荐核心 HUD 收束在安全横向跨度内；
   - 底层 3D 画布独立施加 `pixelated` 与复古微缩暖滤镜，**外围 HUD、贴地双环 SVG、车底去框名牌与线框幽灵叠加层始终按屏幕原生 DPR 无滤镜矢量锐利渲染**，密集编队遵循 `GroundNameplateCollision` 原地降级与 `Tab` 循环规则。

---

## 六、单端口统一研发与清退历史冗余

- **正式项目单端口收口**：全项目统一由 [启动主服务.py](../scripts/启动主服务.py) 在 **`127.0.0.1:8800`** 对外提供服务；早先多端口并行试验期的旧版 UI/UX 离线原型目录已按「先沉淀进 Code SSOT V2.2、后彻底移除」完成清退，不再保留平行设计入口。
- **4 级视距 `span` 战术图标分层、密集簇聚合与半透柔边航路（Code SSOT 原语）**：[图标与标绘.mjs](../src/界面规范/图标与标绘.mjs) 统一导出 `resolveZoomIconTier`、`clusterScreenUnits`、`drawZoomTierUnitIcon`、`drawGroundOrbitZone` 与 `drawAirTurnArc`：
  - `L0 (span ≤ 260m)`：全量 DOM 贴身名牌（`GroundNameplateCollision` 防叠字），2D 标绘层**不绘制战术图标徽章**，完整保留 680p 复古微缩实景；
  - `L1 (260m < span ≤ 650m)`：2D 高清层绘制 `22×16px` 紧凑 12 类负空间剪影徽章（离屏 `Path2D` / Canvas 精灵预缓存），DOM 名牌仅保留选中或悬停单位呼号；
  - `L2 (650m < span ≤ 2200m)`：隐藏全部 DOM 名牌，2D 高清层绘制 `26×18px` 标准战术筹码 + 同通道 `< 18px` 且 `≥ 3` 辆密集簇聚合（带 `×N` 徽标与悬停放射提示）；
  - `L3 (span > 2200m)`：隐藏全部 DOM 名牌，2D 高清层绘制 `18×14px` 极简战略点标并延续同通道簇聚合；固定翼在全视距采用贴地主范围圈（选中 `0.58` / 未选中 `0.24`）与地面垂足滑动光点，取消高空整圈遮挡；
  - **路径线透明化与稀疏柔边实心三角**：`resolveRouteStyle` 常规选中 `alpha: 0.62`、未选中 `0.28`、预览 `0.76`、追加队列 `0.48`、等待 `0.78`、拒收 `0.90`，配 `softOutline: rgba(8, 10, 16, 0.42)` 与 `24m` 步幅稀疏柔边实心三角 `drawRouteChevrons`；
  - **实战底部方案 B 弹性三舱与放大可读 `1:1` 军图轮廓雷达**：`/play` 底部采用左下 `1:1` 放大军图轮廓离屏烘焙雷达一体舱（`lg: 248px`、`xl/≥1920px: 272~280px`、`compact/≤1380px: 208px`；军图地貌分色提纯 + 真实公路跑道骨架 + `9KM/3KM` 双缓存建筑分级降噪 + 顶栏 `25px` 微按钮 + 内嵌动态指北针 + 底边 `28px` 6 编组槽）＋底中弹性居中紧凑卡片指挥台（`width: fit-content; margin: 0 auto; height <= 156px`，少车贴卡透出 3D 战场、多车自然撑展支持滚轮横滑防误缩放 3D，同型聚合 `×N ▾` 外部上弹单机浮卡）＋右下放大可读主选状态与竖向翻页分区指令舱（右舱宽度 `lg: 416px / ≥1920px·xl: 440px / compact·≤1380px: 356px`，全页锁定方案 B 等高 `140px/156px/172px`：上 2×2 宽幅卡 `48px/54px/60px` + 下 2×1 扁平辅助条 `30px/34px/38px`，切页高度差 `0px`），轻反馈 Toast 置于顶栏中岛正下方净空带。
- **已纳管唯一设计权威**：[src/主入口/](../src/主入口/README.md)、[src/界面规范/](../src/界面规范/README.md)、[src/程序化地图/](../src/程序化地图/README.md)、[src/程序化载具/](../src/程序化载具/README.md) 与 [src/交互系统/](../src/交互系统/README.md) 全部通过 `/ui/*` 同源加载并由 [检查项目结构.py](../scripts/检查项目结构.py) 自动巡检，严禁在运行页面出现裸 `<select>`、通道色重定义或 `transform: scale()` 容器放大。
- **V2.2 实战核心 HUD 扩展基元（沉淀于 [组件.css](../src/界面规范/组件.css) 第 7 节）**：
  - **全战竖长条兵牌（`.ui-banner-card`）**：统一使用 `/portraits/<slug>.webp` 透明底 3/4 模型插画叠加兵种通道语义渐变背板，配顶部 2px 通道条、底部暗色渐层型号栏、血条与弹药微条及选中香槟金外发光框；
  - **10 格火力评定条（`.ui-rating-pips` / `.ui-rating-pip`）与跨页共享载具参数详情组件（`.ui-vd-card` / `.ui-vd-hover`）**：统一覆盖 `/play` 右下主选舱上浮宽体详情卡（方案 A，同宽 `356 / 416 / 440px`、快捷键 `I`，彻底解放左半屏零撞顶栏）、`/play` 底部兵牌悬停精简浮卡（`180ms` 防误闪 + 单机/聚合双态 + 单机列表互斥）与 `/armory` 4B 武器库右侧参数卡（`.is-narrow` 静态满编）；分兵种差异化呈现（地面突出装甲/机动/视野/弹药；旋翼突出 30m 巡航高度与载荷；固定翼对接 `SCAV-6` 零血量改显战术燃油/工况油耗/动态 Bingo 红线与整备冷却），低频工程与基底参数默认折叠；
  - **右下角竖向翻页与方案 B 全页统一分区命令栏（`.ui-cmd-shell-paged` / `.ui-cmd-viewport` / `.ui-cmd-exit-layer` / `.ui-cmd-pager` / `.ui-cmd-pager-indicator` / `.ui-cmd-deck-b` / `.ui-cmd-wep-grid` / `.ui-cmd-wep-card` / `.ui-cmd-util-bar` / `.ui-cmd-util-btn`）**：对标《战锤 40K：战争黎明 4》，正式 `/play` 全兵种与空选待命态均统一使用方案 B 上 2×2 深槽双行卡 + 下 2×1 扁平视角/辅助条，配合就地滑块指示器（`syncCmdPagerIndicator`）与可中断 `translateY` 方向性切页过渡（`runCmdPageTransition`，仅位移与透明度，严格禁用 `scale()` 并尊重 `prefers-reduced-motion`）；固定翼全面取消自动开火模式选择（`#hud-fw-manual-badge` 只读徽条替代 ROE 下拉，机动页 4 卡统一为 `M 高速直飞 / X 停航待命 / R 视野复位 / T 撤离战区`）；`?mode=air-lab`（短路由 `/air-lab`）自动挂载独立可拖拽调试控制台；
  - **全站统一左上角返回大厅胶囊（`.ui-back-home` / `.ui-back-home--hud`）**：全站所有非主壳研发/规范/演训页（`/ui-spec`、`/dev/battlefield`、`/dev/air-audio` 等）统一于顶栏左上角放置 `← 返回大厅` 指向 `/`；`/play`、`/editor`、`/air-lab` 顶栏左上角统一配置紧凑 `.ui-back-home--hud`（`← 大厅`，其中正式 `/play` 战局优先唤起 `ESC` 暂停菜单防误触，`/editor` 与 `/air-lab` 直返 `/`）。

---

## 七、后续同步更新与防滞后门禁（三向同源铁律）

为避免再次出现「核心样式/业务页已升级、活体规范台 `/ui-spec` 掉队」的脱节问题，后续任何涉及 UI/UX 设计系统的变更均须遵循**三向同源同步**：

1. **同一轮内四处齐步更新**：凡向 [src/界面规范/](../src/界面规范/README.md) 新增或重构语义令牌、共享组件基元、标绘原语或偏好键，必须在**同一开发轮次**内同步更新：
   - **核心共享源**：`令牌.css` / `组件.css` / `场内视觉.css` / `图标与标绘.mjs` / `交互控件.mjs`；
   - **活体规范台 `/ui-spec`**：`index.html` + `规范页.css` + `规范页.mjs`（补齐对应可交互演示与 `window.uiSpec` 就绪检查）；
   - **规范文档**：本文件（`docs/全局UI-UX规范.md`）与 [src/界面规范/README.md](../src/界面规范/README.md)；
   - **防滞后门禁**：将新增关键基元或原语键名追加到 [检查项目结构.py](../scripts/检查项目结构.py) 的 `check_ui_spec_sync` 同步清单中。
2. **业务样式回流审查**：在 `/play`（如 `src/程序化地图/实战HUD.css`）或 `/armory` 新增的可复用交互基元（如卡片、状态条、翻页签、弹窗），确认定型后应抽离沉淀至 `src/界面规范/组件.css` 并同步接入 `/ui-spec`，不得长期滞留于单一业务样式表。
3. **交付前强制双验**：修改 UI 规范源或新增基元后，交付前必须运行 `python -B scripts/检查项目结构.py`（静态三向一致性门禁）；若改动涉及 `/ui-spec` 交互逻辑，须在 `127.0.0.1:8800` 运行状态下执行 [验证界面规范.py](../scripts/界面规范/验证界面规范.py) 验证无报错通过。
