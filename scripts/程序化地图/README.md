# 程序化大地图构建、打包与验证工具

本目录维护程序化大地图重建（路线 B，V001）从 B0 基线冻结、B1 地形与道路构建、B2 立体植被与草丛生成、B3 模块化建筑库与增量编译、B4 `.scvmap` Range 单包归档到 B5 双浏览器全矩阵实测的完整离线与验证工具链。

## 脚本清单与职责

| 脚本 | 阶段 | 输入与依赖 | 行为与输出 |
| --- | --- | --- | --- |
| [冻结基线.py](冻结基线.py) | B0 | Python 标准库；只读核验源全图高度、实例分页、植被图集与 G01 载具资产 | 输出 `B0-基线冻结/基线凭证.json`，确认既有端口与 `revision 7` 零改动 |
| [构建地形与道路.py](构建地形与道路.py) | B1 | Python、`numpy`、`Pillow`；源 BVH 高度页、Splat 控制图与全图表面 | 生成 `height-2m.scvh`、`multilayer-patches.scvp`、`splat-control.scvs`、材质图集与道路水面 GLB（合计 `33.271 MiB <= 35 MiB`） |
| [验证地形与道路.py](验证地形与道路.py) | B1 | Node.js、Playwright、可见 Chrome | 核验 60,237 点高度与多层支撑零状态不匹配、81 处连续射线误差 `<= 0.01m` 及双分辨率 60 FPS 门限 |
| [构建植被与草丛.py](构建植被与草丛.py) | B2 | Python、`numpy`、`Pillow` | 生成 8 种层叠松枝云杉/多球心碎叶阔叶树与野灌木 GLB（KTX2 编码前 4 轮透明边缘 Color Dilation 防黑渗）、8 种全不透明实心微缩中景代理 GLB、10m 精准实心 3D 远景集群林冠壳（硬抠除建筑占地）、草丛图集与 343,234 棵树空间索引（合计 `4.495 MiB <= 12 MiB`） |
| [验证植被与草丛.py](验证植被与草丛.py) | B2 | Playwright、可见 Chrome | 核验五方向 + 48° 俯角实时阴影联系表、近景 `>= 1,500` 棵 3D 树 + 12,000 簇 GPU 草丛、500m–9000m 林冠连续性与双分辨率帧率 |
| [构建模块化建筑与场景.py](构建模块化建筑与场景.py) | B3 | Python、`numpy`、Blender 5.2 后台、Node `gltf-transform`（Meshopt + 量化） | 构建 125 种近/远景模块化建筑库（支持双插槽替换；烘焙负行列式/非均匀节点变换、修正正面绕序及 `MeshoptSimplifier` 保拓扑减面）、93,008 实例空间分块、碰撞越障包、`scene-source.json` 与 Blender 模板（合计 `33.859 MiB <= 35 MiB`） |
| [增量编译地图.py](增量编译地图.py) | B3 | Python 标准库、`numpy` | 十秒级增量打包 CLI（实测 `0.003s`），从 `scene-source.json` 自动重算旋转建筑凸包碰撞、4m 寻路网格与直升机 30m 越障高度包络 |
| [验证建筑与编辑器.py](验证建筑与编辑器.py) | B3 | Playwright、可见 Chrome | 核验 `?mode=editor` 新增/旋转/删除建筑、0.1ms 自动物理派生、M1A2 4m 寻路绕行、AH-64D 30m 提前拉升越障及地形笔刷 |
| [检查建筑外观.py](检查建筑外观.py) | B3/B5 | Playwright、Chrome、Node `gltf-transform`、Pillow | 使用与主场景同源的 `InstancedMesh` + `enhanceBuildingMaterial` + RTS 光照渲染全量 125 栋建筑原型，输出 `5×5` 高清联系表、纯黑/反绕/面数汇总及 `8820` 真实坐标实景复拍 |
| [打包归档.py](打包归档.py) | B4 | Python 标准库 | 按 Morton (Z-order) 空间局部性将全图资产封装为 3 个 `.scvmap` 归档包（`72.930 MiB <= 90 MiB`，核心启动段 `5.555 MiB <= 6 MiB`）与 `29.486 KB` 定长二进制索引 `spatial-index.scvi` |
| [预览服务.py](预览服务.py) | B4/B5/阶段5 | Python 标准库 | 独立只读回环 HTTP 服务（默认 `127.0.0.1:8820`，可通过 `--port 8821` 并行验证），支持 `HTTP Range`（`206 Partial Content`）、`ETag` 缓存、G01 载具路由及同源 `/ui/*` 与 `/ui-spec` 白名单挂载 |
| [验证平滑过渡与归档.py](验证平滑过渡与归档.py) | B4 | Playwright、可见 Chrome | 核验 `70m -> 9000m -> 70m` 连续缩放 0 次回退闪屏、0 次整屏跳变、地面射线 0 空洞与 Range 按需加载 |
| [验证B5全矩阵.py](验证B5全矩阵.py) | B5 | Playwright、原生可见 Chrome 与 Edge | 单进程串行实测唯一 `680p + 复古微缩 Stylized` 基线双画幅（`16:9 1208×680` & `21:9 1588×680`）× 六类负载帧率、双层分离合规校验、5 圈回访零泄漏、20 Mbps 限速启动与 G01 回归 |
| [验证实战HUD.py](验证实战HUD.py) | 阶段5 | Playwright、原生可见 Chrome 与 Edge | 端到端实测真 3D 实装版 Code SSOT V2.1 实战 HUD（`/ui/*` 白名单 HTTP 200、16:9 与 2560×1080 21:9 安全区、三档大尺码切换、右抽屉实时建楼、按 `2` 选车+雷达跳镜+`.is-dropup` 与 LIFO ESC 4A 暂停菜单） |
| [烘焙爆炸体积云图集.py](烘焙爆炸体积云图集.py) | P0 体积云 | Python 3.12、`numpy`、`scipy`、`Pillow` | 离线确定性烘焙 `8×8`（`1024×1024`）多通道体积烟云图集至 `public/assets/vfx-explosion/` 并提供 `--verify` 通道/边距校验 |
| [验证写实爆炸特效.py](验证写实爆炸特效.py) | V3.0 验收 | Playwright、Chrome、`127.0.0.1:8800` 主服务 | 自动覆盖 GBU-31 四阶段关键帧、30mm 红/绿/混/无曳光与接地溅射、Hydra 70、AGM-65、热诱弹、坠毁斜风黑烟明火、21:9/高空 950m/关闭 Bloom 直渲、US_B1B 凝结尾迹及常规 `/play` Live PiP 隔离验证，输出截图与遥测 JSON 至 `tmp/vfx-explosion-v001-7e8ac44c/` |
| [验证空战声学与火箭音效.py](验证空战声学与火箭音效.py) | 声学与火箭验收 | Playwright、Edge、`127.0.0.1:8800` 主服务 | 端到端实测试听台 V2 兼容、核心 `.ogg` HTTP 200、全局唯一 `AudioContext` 与共享 `ConvolverNode`、Near/Mid/Far 三档分桶与 5 档静态声像路由（运行期 0 新建 Panner）、干声直入 Sub-Punch、相机右向量旋转声像与四档声压无断崖衰减、RTS 虚拟监听点、现实声学 V3（喷气引擎连续多普勒与前后半球指向性、超远距对数延迟、航弹下落呼啸追踪、重爆 `engineDuckBus` 超压闪避、坠机动态呼啸推进与释放）、A-10C GAU-8 V2 实时多普勒/停火拖尾与零削波峰值（`<= 1.0`）、Hydra 70 R1 7 连发无叠爆、`dispose()` 断开释放及常规 `/play` 零报错 |
| [验证固定翼底座规则.mjs](验证固定翼底座规则.mjs) | 固定翼底座纯规则单测 | Node.js ESM（无 Three.js/DOM 硬依赖） | 校验 `computeEngineMotionState` 油门/加力联动、`computeL1OrbitCommand` 收敛与协调坡度、能量机动、Hydra 70 三挂点空巢保留、`2+2+2` 热诱弹、坠毁渐增滚转与弹射弧/拖痕触发、`approachCurve` 与拖线耗弹估算、Winchester 自动 RTB 与可召回及三点地形前瞻防撞 |
| [验证网页性能基线.py](验证网页性能基线.py) | 网页性能基线与 Phase 1/3 门禁 | Playwright、Edge/Chrome 硬件 WebGL、`127.0.0.1:8800` 主服务 | 只读采集单次正常冷启动/全精度升级 LongTask 与 JS 堆峰值（`--check-phase3` 强制校验 `postCoreMaxLongTaskMs <= 60ms` 与 `peakJsHeapMiB <= 380MiB`）、`waitForFullPrecision` 单例锁与道面单次提交、热路径强制回流/CSS 查询/静止名牌写 DOM 归零、正式 50 车满编与 100 混编 10Hz 模拟耗时及帧率，以及 `/editor` 放楼/删楼/地形笔刷迷雾实时失效闭环 |

## 常用命令

```powershell
# 启动独立 8820 预览与编辑器服务
python -X utf8 -B scripts/程序化地图/预览服务.py --port 8820

# 十秒级增量编译场景源文件
python -X utf8 -B scripts/程序化地图/增量编译地图.py --source local-data/程序化地图/V001-fcf46de3/B3-建筑与编辑器/scene-source.json

# 重新打包 .scvmap 空间归档与二进制索引
python -X utf8 -B scripts/程序化地图/打包归档.py

# 全量 125 栋模块化建筑真机 WebGL 外观体检与实景复拍
python -X utf8 -B scripts/程序化地图/检查建筑外观.py --phase after --shoot-world

# 运行 B5 双浏览器全矩阵实测
python -X utf8 -B scripts/程序化地图/验证B5全矩阵.py

# 离线确定性烘焙与校验 P0 8×8 爆炸体积云图集
python -X utf8 -B scripts/程序化地图/烘焙爆炸体积云图集.py --verify
```
