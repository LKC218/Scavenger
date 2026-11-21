# 丧尸群体模拟运行研究包

本包是独立的 Three.js 群体模拟实验，包含十三款真实蒙皮角色、开阔移动、障碍窄通道、性能采样和档案导出。页面只读取本包内固定资源，不访问现有统一资源库、CDN 或其他项目服务。

## 启动

环境要求：

- Windows 10/11，或支持 WebGL 2 的桌面系统。
- Python 3.11 或更高版本，并已加入 `PATH`。
- Google Chrome 或 Microsoft Edge 的较新版本。
- 运行 Node 群体规则检查时，需要 Node.js 18 或更高版本。

Windows：

1. 双击 `启动丧尸群体测试.cmd`。
2. 保持命令窗口开启。
3. 浏览器进入 `http://127.0.0.1:8769/`。
4. 结束时关闭命令窗口，或在窗口中按 `Ctrl+C`。

macOS／Linux：

```sh
sh start.sh
```

端口被占用时可指定其他端口：

```text
python -X utf8 -B scripts/发布服务.py --port 8770
```

页面启动后，在“采样浏览器”中选择实际使用的 Google Chrome 或 Microsoft Edge；进行矩阵测试前必须与真实浏览器保持一致。

## 阅读顺序

1. `使用指南.html`：解压项目并在 WorkBuddy 中添加本地项目文件夹。
2. `开发者阅读说明书.md`：运行流程、模块职责、常见修改入口、测试方法和性能口径。
3. `已知限制.md`：实验范围、未完成验收和不能从现有结果推导的内容。
4. `许可与来源.md`：模型署名、修改说明和 Three.js 许可。
5. `src/丧尸群体测试/README.md`：运行模块的逐文件职责。
6. `reports/丧尸模型/十三款混合群体性能报告.html`：现有样本、条件与结果边界。

## 快速操作

| 操作 | 行为 |
| --- | --- |
| 右键地面 | 设置群体目标 |
| `WASD` | 平移镜头 |
| 滚轮 | 缩放 |
| 中键拖动 | 旋转 |
| `C` | 聚焦群体 |
| `F` | 复位镜头 |
| `空格` | 暂停或继续 |

“快测20秒”“采样60秒”“长测180秒”会锁定配置和镜头。采样完成后，原始 JSON／CSV 同时下载并保存到 `运行数据/样本/`。窗口隐藏或失焦会使样本标记为无效。

## 目录

```text
data/                 可移植发布资源清单
assets/               WorkBuddy 使用指南截图
docs/                 研究说明与项目经验
reports/              离线性能报告
research/             不含本机绝对路径的研究摘要
使用指南.html          WorkBuddy 项目接入三步指南
runtime/assets/       十三款 256／512 GLB
runtime/vendor/       固定 Three.js 0.187.0 运行文件
scripts/              本机服务和包内核验入口
src/                  群体模拟源码
tests/                群体规则检查
运行数据/             启动后生成的观察记录与采样
```

256 档是默认运行规格。使用 512 档启动：

```text
python -X utf8 -B scripts/发布服务.py --resolution 512
```

## 验证

文件身份和运行服务检查：

```text
python -X utf8 -B scripts/核验发布包.py --rules
```

同时执行无窗口浏览器冒烟：

```text
python -X utf8 -B scripts/核验发布包.py --rules --browser-channel chrome
python -X utf8 -B scripts/核验发布包.py --rules --browser-channel chrome --resolution 512
```

`--resolution`可选择 `256` 或 `512`。将 `chrome` 替换为 `msedge` 可检查Edge。浏览器冒烟只检查加载和基础状态，不替代前台性能矩阵、人工动作观感或目标设备验收。
