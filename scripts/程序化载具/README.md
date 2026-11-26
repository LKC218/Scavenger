# 程序化载具编译、归档与视觉验收工具

本目录维护全部 129 款美军空地载具从 V0 基线冻结、V1 样本闭环、V2 全库批编译与视觉自修、V3 `.scvveh` HTTP Range 归档到 V6 兵牌插画批拍的完整自动化工具链。早期 V5 独立单车展示台与 `验证V5全矩阵.py` 已正式移除；129 车武器库（`/armory`）与混编演训场（`/dev/battlefield`）已全面升级并统一收口至主服务 **`http://127.0.0.1:8800`**，全链路双浏览器验收统一使用 [验证主服务全链路.py](../验证主服务全链路.py)。

## 脚本清单与职责

| 脚本 | 阶段 | 依赖 | 核心行为 |
| --- | --- | --- | --- |
| [公共配置.py](公共配置.py) | 公共 | Python 标准库 | 维护批次路径、129 车正式库只读加载、角色分类、黄金/痛点样本与受保护端口清单 |
| [冻结基线与盘点.py](冻结基线与盘点.py) | V0 | Python、固定 `7za.exe` | 一次性从 `模型.7z` 恢复 129 份原件至临时缓存并核验 SHA256，产出 `基线凭证.json`、`vehicle-rig-rules.json` 与 `全库贴图哈希表.json` |
| [编译载具.mjs](编译载具.mjs) | V1/V2 | Node 22、`@gltf-transform`、`meshoptimizer`、`sharp`、`ktx2-encoder` | 离线语义拆分（含旋翼连通分量剥离与黄金装配复用）、防花屏 1024/384 KTX2 图集与跨车共享去重、14/12-bit 量化同源 LOD0/1/2 及 OBB/轮距/名牌包络自动派生 |
| [验证静态结构.mjs](验证静态结构.mjs) | V1/V2 | Node 22、`@gltf-transform`、`meshoptimizer` | 逐车逐档校验 GLB 魔数、量化/Meshopt/KTX2 扩展、零空节点、外置贴图头与 Meshopt 解码零 NaN |
| [验证全库视觉.py](验证全库视觉.py) | V1/V2 | Playwright、可见 Chrome、`numpy`、`Pillow` | 全自动六视角双盲白色剪影 IoU、感知前景 SSIM、跨级色差、法线翻转黑面检测、可动部件旋转差图验真与最多 2 轮自动收敛自修 |
| [打包载具归档.py](打包载具归档.py) | V3 | Python 标准库 | 封装 3 个 8 字节对齐 `.scvveh` 分层归档包与 `≤ 64 KiB` 定长二进制索引 `vehicles-index.scvi` |
| [验证归档回读.py](验证归档回读.py) | V3 | Python 标准库 | 发起真实 HTTP `206 Partial Content` Range 请求逐条核验全部归档条目字节长度与 SHA256 |
| [预览服务.py](预览服务.py) | 内部工具 | Python 标准库 | 仅作为 V1–V3 离线编译/回读脚本的临时内部 Range 回环辅助模块（日常武器库与混编场请统一访问 `127.0.0.1:8800`） |
| [生成验收报告.py](生成验收报告.py) | 报告 | Python 标准库 | 汇总归档与最差 Top 15 联系表，生成离线自包含 HTML 与 Markdown 验收报告 |
| [运行全流程.py](运行全流程.py) | V0–V3 | Python 标准库 | 一键按阶段串行调度编译、归档、静态校验与六视角视觉闭环 |
| [生成兵牌插画.py](生成兵牌插画.py) | V6 | Playwright、可见 Chrome、Pillow | 复用 `.scvveh` 与 `miniature_sand` 材质离线批拍全部 129 车前斜 3/4 透明底兵牌特写 WebP（`192×264`），输出至 `V6-兵牌插画/` 并经 `/portraits/<slug>.webp` 分发 |

## 常用命令

```powershell
# 一键从 V0 执行至 V3 离线编译与归档核验
python -X utf8 -B scripts/程序化载具/运行全流程.py --from-stage V0 --to-stage V3

# 启动唯一正式主服务浏览 129 车新版武器库（http://127.0.0.1:8800/armory）与混编场（/dev/battlefield）
python -X utf8 -B scripts/启动主服务.py --open
```
