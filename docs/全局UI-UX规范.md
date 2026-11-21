# 全局 UI/UX 规范 V2.1（暗夜映画高反差 × 生化警戒黄黑 × 轻量化战术指令）

> 本规范为项目所有运行期网页端三维界面与工具台的**强制单一设计基准（Code SSOT V2.1）**，与源码目录 [src/界面规范/](../src/界面规范/README.md) 及活体规范路由 `http://127.0.0.1:8766/` 保持代码同源，由 [检查项目结构.py](../scripts/检查项目结构.py) 自动校验同步性；离线规范设计与分阶段演进见 [全局UI-UX规范化-实施计划](全局UI-UX规范化-实施计划.md) 及 [全局设计系统规范台](界面预览/全局设计系统/README.md)。

---

## 一、核心风格定位、锁定基因与活体规范路由

- **风格定位**：**暗夜映画高反差 × 生化警戒黄黑 × 轻量化战术指令**，与项目唯一 [渲染风格与分辨率基线规范](渲染风格与分辨率基线规范.md)（`stylized-680p-v1`：`16:9 1208×680` / `21:9 1588×680`，3D 画布带 `sepia(0.06)` 轻暖微缩滤镜）协同设计。
- **已锁定视觉基因**：
  - **配色**：`A3 烟炭双金融合`（微冷烟炭暗底与暖调 3D 战场拉开层次；日常主高亮采用香槟金 `#f0d372`，高危警戒与排程预警采用工业琥珀黄 `#ffb800`）；
  - **按钮**：`B2 战术微描边精工`（`1px` 清晰轮廓线，悬停点亮主金边与主金字；3D 边缘热键采用 L1 无底框硬黑外描边；每屏限唯一 L3 主 CTA）；
  - **倒角**：`C4 外柔内锐`（容器 `--ui-r-surface: 12px` / `--ui-r-surface-lg: 16px`，交互控件 `--ui-r-control: 5px` / `--ui-r-sm: 4px`，徽章胶囊 `--ui-r-pill: 9999px`）；
  - **材质性能分级**：实时战斗常驻 HUD 一律使用高透暗实底（`--ui-bg-pod`，**禁止大面积 `backdrop-filter`**）以守住 100 载具 + 500 丧尸 60FPS 预算；暂停菜单、系统设置与模态弹窗允许使用 `--ui-bg-overlay-glass` 毛玻璃景深。
- **活体规范路由**：
  - 独立固定路由：`http://127.0.0.1:8766/`（别名 `/ui-spec`），由 [启动规范路由.py](../scripts/界面规范/启动规范路由.py) 白名单挂载，不加载地图大包，秒开交互核验；
  - 业务候选同源挂载路由：后续新版候选同步提供 `/ui-spec` 子路由，全部界面共用 `/ui/*` 共享样式与图标模块。

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
| **固定翼攻击机通道（100m）** | `--ui-ch-plane`、`--ui-ch-plane-inner`、`--ui-ch-plane-fill` | `#b197fc` / `#dcd0ff` | 攻击机/战机/无人机地面十字锚点、100m 盘旋圆轨、高空航路与名牌 |
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
- **共享交互引擎与 4C 三行安全区骨架**：由 [交互控件.mjs](../src/界面规范/交互控件.mjs) 统一导出 `createEscStack`（严格 LIFO 回退栈）、`bindUiSelect`/`bindAllUiSelects`（自动底沿余量探测 `.is-dropup` 向上弹出 + 键鼠双控）与 `createUiSizeController`（三档尺码与画幅联动）；实战 HUD 采用 `.ui-hud-grid`（三行安全区）＋ `.ui-cat-rail`（极窄分类轨）＋ `.ui-hud-bottom`（底中完全贯通双翼）＋ `.ui-cmd-grid`（3×2 命令盘）标准骨架。
- **五级 Z-Index 架构**：`--ui-z-world: 100`（世界内标绘） → `--ui-z-hud: 200`（战斗常驻 HUD） → `--ui-z-drawer: 300`（抽屉/轮盘） → `--ui-z-overlay: 400`（全屏菜单/模态） → `--ui-z-system: 500`（Toast/系统警报）；严禁业务代码随意写 `z-index: 9999`。
- **三档动效时长**：`--ui-dur-micro: 100ms`（微反馈）、`--ui-dur-standard: 200ms`（控件切换）、`--ui-dur-overlay: 300ms`（浮层进出），强制尊重 `prefers-reduced-motion`。

---

## 四、武器库 12 兵种剪影、4 枚情景警示与 19 枚功能图标源

所有兵种剪影、情景警示与 HUD 线性图标唯一由 [图标与标绘.mjs](../src/界面规范/图标与标绘.mjs) 导出，严禁在其他源码中重写 `UNIT_SYMBOLS` 或硬编码 SVG 路径：

1. **`VEHICLE_SILHOUETTES`（12 大武器库兵种 ＋ 3 个兼容别名，`viewBox="0 0 24 24"`，`fill-rule="evenodd"`）**：
   - **地面 7 类（覆盖 77 款在库地面车型）**：`mbt`（主战坦克）、`ifv`（步战/装甲车）、`recon`（侦察/轻型突击）、`arty`（自行火炮/火箭）、`aa`（野战防空/导弹）、`atgm`（反坦克导弹车）、`supply`（后勤补给/工程）；
   - **空中 5 类（覆盖 52 款在库空中机型）**：`attack_heli`（武装直升机）、`trans_heli`（运输/倾转机）、`cas`（对地攻击/炮艇）、`fighter`（制空战斗/轰炸）、`uav`（侦察/察打无人机）；
   - **向下兼容别名**：`tank`（映射 `mbt`）、`heli`（映射 `attack_heli`）、`plane`（映射 `cas`）。
2. **`HAZARD_ICONS`（4 枚现实原型情景警示图标，由 `createHazardIconSvg` 渲染）**：
   - `biohazard`（ISO 7010 标准三环尖爪）、`radiation`（ISO 7010 标准三叶草）、`gasmask`（军用防毒面具）、`horde`（尸群高危骷髅警示）。
3. **`HUD_ICONS`（19 枚统一 `1.75px` 圆角线宽功能图标）**：
   - `move`、`attack`、`stop`、`rally`、`airstrike`、`repair`、`shield`、`lock`、`alert`、`drawer`、`focus`、`soundOn`、`soundOff`、`pause`、`play`、`reset`、`layers`、`compass`、`copy`。

---

## 五、按钮三档、游戏状态机、双画幅安全区与双层高清

1. **按钮三档与无底框外描边铁律**：
   - **L1 无底框硬黑外描边（`.ui-prompt-btn`）**：3D 边缘高频微操必须采用 `paint-order: stroke fill` 与 `--ui-outlined-stroke-w: 3.4px` 外扩黑描边，按钮与文本强制 `white-space: nowrap; flex-shrink: 0` 防止窄窗竖排折行，键帽 `.ui-keycap` 强制声明 `-webkit-text-stroke: 0; text-shadow: none` 防糊团；
   - **L2 微描边常规按钮（`.ui-btn`）** 与 **L3 唯一主 CTA（`.ui-btn--primary`）**：每屏/每弹窗限 1 个主 CTA。
2. **游戏专属与定制表单组件规范**：
   - 指令槽（`.ui-cmd-cell`）强制顶部预留 `>= 17px` 热键/充能角标安全区，确保任何尺码下 `.c-kbd` 与 `.c-txt` 物理不重叠；支持就绪、扇形冷却遮罩（`.ui-cd-mask` ＋ 等宽倒计时）、充能低量预警（`.is-low`）、耗尽禁点（`.is-empty`）、未解锁斜纹暗幕（`.is-locked`）与紧急脉冲（`.is-alerting`）；
   - 耐久进度条支持受击延迟白条（`.ui-hp-trail`）、反应甲增益段（`.ui-hp-bonus`）与低血危急脉冲（`.is-critical`）；
   - **严禁直接裸用浏览器原生 `<select>` 或未定制样式的 `<input type="checkbox|radio|range">`**，所有下拉选择、单/复选与滑块必须消费单源 `.ui-select`（深烟炭不透底浮层＋香槟金选中左标与勾号＋LIFO `ESC` 优先收起）、`.ui-check`、`.ui-radio` 与 `.ui-range`（垂直白棒滑块）；
   - 5 类离线 SVG 键鼠光标（`.ui-cur-default` / `.ui-cur-move` / `.ui-cur-attack` / `.ui-cur-invalid` / `.ui-cur-action`）与全局 `ESC` LIFO 逐层回退栈。
3. **`16:9` / `21:9` 双画幅安全区与双层分离高清渲染**：
   - 严格绑定 `680p` 双画幅（`1208×680` 与 `1588×680 Hor+`）：四周保留 `24px` 安全边距，中央 `60%` 严禁常驻实心面板，`21:9` 下推荐核心 HUD 收束在安全横向跨度内；
   - 底层 3D 画布独立施加 `pixelated` 与复古微缩暖滤镜，**外围 HUD、贴地双环 SVG、车底去框名牌与线框幽灵叠加层始终按屏幕原生 DPR 无滤镜矢量锐利渲染**，密集编队遵循 `GroundNameplateCollision` 原地降级与 `Tab` 循环规则。

---

## 六、历史冻结红线与分批落地路线

- **严禁破坏历史 SHA-256 冻结端口**：`8767`、`8768`、`8769`、`8780`、`8781`、`8784`、`8811`～`8814`、`8794`、`8797`/`8798`、`8809`/`8810` 等全部保持源码与摘要原样不变。
- **当前进度**：阶段 1（原子令牌 V001）、阶段 2（组件库与 32 枚图标 V002）、**阶段 4A/4B/4C-V002 离线全流程三部曲（均已审阅通过）** 及 **Code SSOT V2.1 单源收口（回流沉淀三档原生物理尺码、共享交互控件 `.mjs` 与三行安全区 HUD 骨架至 `8766` 活体规范路由）** 已完成实施；当前阶段 5（方案 A）已完成 [程序化地图运行模块](../src/程序化地图/README.md) 真 3D 主入口的 V2.1 实装并纳入 `UI_SPEC_ADOPTED_MODULES` 自动守卫，正于独立端口 `127.0.0.1:8821` 进行双浏览器端到端验收（等待工程师实景确认）。
