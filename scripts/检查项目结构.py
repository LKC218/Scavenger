"""只读检查项目目录边界、文档链接及本地资料排除约定。"""

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
SKIP = {
    "local-data", "tmp", "temp", "private", "health-data", ".git",
    "node_modules", "dist", "coverage", ".vite", ".venv", "venv",
    "__pycache__", ".idea", "credentials.local.json", "secrets.local.json",
}
RAW_SUFFIXES = {".bundle", ".unity3d", ".gts", ".gtp", ".exe", ".dll"}
TEXT_SUFFIXES = {".md", ".json", ".py", ".js", ".css", ".html"}
PRIVATE_PATH = re.compile(r"\b[A-Za-z]:[\\/](?:SteamLibrary|Users)[\\/]", re.IGNORECASE)
REMOTE_HTML_DEP = re.compile(r"<(?:script|link)\b[^>]+(?:src|href)\s*=\s*['\"]https?://", re.IGNORECASE)
LINK = re.compile(r"\[[^\]\n]*\]\(([^)\n]+)\)")
UI_SPEC_ADOPTED_MODULES = ["src/界面规范", "src/程序化地图"]  # 阶段五已接入模块防漂移守卫
UI_SEMANTIC_VAR_DEF = re.compile(r"(--ui-(?:ch|state)-[a-z0-9-]+)\s*:")
SILHOUETTE_LITERAL_FINGERPRINT = "M2.2 14.4 L4.2 11.8"


def formal_files(directory):
    """逐层跳过本地数据和缓存，避免扫描游戏与第三方工具。"""
    for path in sorted(directory.iterdir()):
        if path.name in SKIP:
            continue
        if path.name.startswith(".env") and not path.name.endswith(".example"):
            continue
        if path.relative_to(ROOT).as_posix() == ".vscode/settings.json":
            continue
        if path.is_symlink():
            yield path
        elif path.is_dir():
            yield from formal_files(path)
        elif path.is_file():
            yield path


def check_preview_categories(preview_root, errors):
    """校验 docs/界面预览/ 下分类目录的命名、README 与总索引登记。"""
    if not preview_root.is_dir():
        return
    index_path = preview_root / "README.md"
    index_text = index_path.read_text(encoding="utf-8") if index_path.is_file() else ""
    for sub in sorted(preview_root.iterdir()):
        if not sub.is_dir() or sub.name in SKIP:
            continue
        if " " in sub.name:
            errors.append(f"界面预览分类目录不得包含空格：{sub.relative_to(ROOT).as_posix()}")
        sub_readme = sub / "README.md"
        if not sub_readme.is_file():
            errors.append(f"界面预览分类目录缺少 README.md：{sub.relative_to(ROOT).as_posix()}")
        elif index_text and f"{sub.name}/README.md" not in index_text:
            errors.append(f"docs/界面预览/README.md 缺少分类目录登记：{sub.name}")


def check_preview_html(path, relative, text, preview_root, errors):
    """校验 docs/ 下 HTML 预览必须归类存放、在 README 登记且离线自包含。"""
    if " " in path.name:
        errors.append(f"界面预览 HTML 文件名不得包含空格：{relative}")
    in_category = path.is_relative_to(preview_root) and len(path.relative_to(preview_root).parts) >= 2
    if not in_category:
        errors.append(f"docs 下的 HTML 预览须放入 docs/界面预览/<分类目录>/：{relative}")
    else:
        category = preview_root / path.relative_to(preview_root).parts[0]
        readme = category / "README.md"
        if readme.is_file() and path.name not in readme.read_text(encoding="utf-8"):
            errors.append(f"界面预览分类 README.md 缺少该预览功能登记：{relative}")
    if 'lang="zh-CN"' not in text and "lang='zh-CN'" not in text:
        errors.append(f"界面预览 HTML 须声明 zh-CN：{relative}")
    if REMOTE_HTML_DEP.search(text):
        errors.append(f"界面预览 HTML 不得引用远程脚本或样式表：{relative}")


def check_ui_spec_sync(errors, nav_text):
    """校验全局 UI/UX 共享设计源、文档注册表与已接入模块的同源一致性。"""
    doc_path = ROOT / "docs/全局UI-UX规范.md"
    readme_path = ROOT / "docs/README.md"
    tokens_path = ROOT / "src/界面规范/令牌.css"
    icons_path = ROOT / "src/界面规范/图标与标绘.mjs"
    spec_html = ROOT / "src/界面规范/index.html"
    spec_css = ROOT / "src/界面规范/规范页.css"
    spec_js = ROOT / "src/界面规范/规范页.mjs"
    ctrl_js = ROOT / "src/界面规范/交互控件.mjs"

    if doc_path.is_file():
        if "全局UI-UX规范.md" not in nav_text:
            errors.append("docs/apps-code-map.md 缺少全局 UI/UX 规范文档登记：全局UI-UX规范.md")
        if readme_path.is_file() and "全局UI-UX规范.md" not in readme_path.read_text(encoding="utf-8"):
            errors.append("docs/README.md 缺少全局 UI/UX 规范文档入口：全局UI-UX规范.md")

    doc_text = doc_path.read_text(encoding="utf-8") if doc_path.is_file() else ""
    if tokens_path.is_file():
        tokens_text = tokens_path.read_text(encoding="utf-8")
        semantic_vars = sorted(set(UI_SEMANTIC_VAR_DEF.findall(tokens_text)))
        if not semantic_vars:
            errors.append("src/界面规范/令牌.css 未定义核心语义色令牌（--ui-ch-* / --ui-state-*）")
        for var_name in semantic_vars:
            if doc_text and var_name not in doc_text:
                errors.append(f"docs/全局UI-UX规范.md 缺少语义令牌登记：{var_name}")

    if icons_path.is_file():
        icons_text = icons_path.read_text(encoding="utf-8")
        if "evenodd" not in icons_text:
            errors.append("src/界面规范/图标与标绘.mjs 必须使用 fill-rule='evenodd' 负空间剪影")
        block = re.search(r"VEHICLE_SILHOUETTES\s*=\s*Object\.freeze\(\{(.*?)\}\)", icons_text, flags=re.DOTALL)
        keys = re.findall(r"^\s*([a-z0-9_]+)\s*:", block.group(1), flags=re.MULTILINE) if block else []
        for req_key in ("tank", "supply", "heli", "plane", "mbt", "ifv", "recon", "arty", "aa", "atgm", "attack_heli", "trans_heli", "cas", "fighter", "uav"):
            if req_key not in keys:
                errors.append(f"src/界面规范/图标与标绘.mjs 缺少必备军事剪影键：{req_key}")
        for key in keys:
            if doc_text and key not in doc_text:
                errors.append(f"docs/全局UI-UX规范.md 缺少军事剪影键登记：{key}")
        for haz_key in ("biohazard", "radiation", "gasmask", "horde"):
            if haz_key not in icons_text:
                errors.append(f"src/界面规范/图标与标绘.mjs 缺少情景警示图标键：{haz_key}")
            elif doc_text and haz_key not in doc_text:
                errors.append(f"docs/全局UI-UX规范.md 缺少情景警示图标键登记：{haz_key}")

    if spec_html.is_file():
        html_text = spec_html.read_text(encoding="utf-8")
        for ref in ("/ui/令牌.css", "/ui/组件.css", "/ui/场内视觉.css"):
            if ref not in html_text:
                errors.append(f"活体规范页缺少共享样式同源引用 {ref}：src/界面规范/index.html")
    if spec_js.is_file():
        js_text = spec_js.read_text(encoding="utf-8")
        if "图标与标绘.mjs" not in js_text:
            errors.append("活体规范页脚本必须同源导入 图标与标绘.mjs：src/界面规范/规范页.mjs")
        if "交互控件.mjs" not in js_text:
            errors.append("活体规范页脚本必须同源导入 交互控件.mjs：src/界面规范/规范页.mjs")
    for p in (spec_html, spec_css, spec_js, ctrl_js):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT).as_posix()
        txt = p.read_text(encoding="utf-8")
        if UI_SEMANTIC_VAR_DEF.search(txt):
            errors.append(f"活体规范页禁止私自重定义核心语义色令牌：{rel}")
        if SILHOUETTE_LITERAL_FINGERPRINT in txt:
            errors.append(f"活体规范页禁止内联硬编码载具剪影路径：{rel}")

    for mod_rel in UI_SPEC_ADOPTED_MODULES:
        mod_dir = ROOT / mod_rel
        if not mod_dir.is_dir():
            errors.append(f"已接入 UI 规范白名单目录不存在：{mod_rel}")
            continue
        mod_html = mod_dir / "index.html"
        if mod_html.is_file() and "/ui/令牌.css" not in mod_html.read_text(encoding="utf-8"):
            errors.append(f"已接入模块 index.html 必须同源引入 /ui/令牌.css：{mod_rel}/index.html")
        play_hud_html = ROOT / "src/程序化地图/预览入口.html"
        if mod_rel == "src/程序化地图" and play_hud_html.is_file():
            play_hud_txt = play_hud_html.read_text(encoding="utf-8")
            for ui_ref in ("/ui/令牌.css", "/ui/组件.css", "/ui/场内视觉.css", "/ui/交互控件.mjs", "/ui/图标与标绘.mjs"):
                if ui_ref not in play_hud_txt:
                    errors.append(f"主试玩入口缺少 UI 规范同源引用 {ui_ref}：src/程序化地图/预览入口.html")
        for f in sorted(mod_dir.rglob("*")):
            if not f.is_file() or f.name in SKIP:
                continue
            f_rel = f.relative_to(ROOT).as_posix()
            if f.suffix.lower() == ".css" and f.name != "令牌.css":
                if UI_SEMANTIC_VAR_DEF.search(f.read_text(encoding="utf-8")):
                    errors.append(f"已接入模块非共享令牌样式表禁止重定义通道/状态令牌：{f_rel}")
            if f.suffix.lower() in {".mjs", ".js"} and f.name != "图标与标绘.mjs":
                f_txt = f.read_text(encoding="utf-8")
                if "UNIT_SYMBOLS" in f_txt or SILHOUETTE_LITERAL_FINGERPRINT in f_txt:
                    errors.append(f"已接入模块禁止私自声明 UNIT_SYMBOLS 或硬编码载具剪影路径：{f_rel}")


def check_render_baseline_sync(errors, nav_text):
    """校验唯一 680p + 复古微缩 Stylized 渲染基线文档、代码配置源与主试玩入口的强制同步性。"""
    spec_doc = ROOT / "docs/渲染风格与分辨率基线规范.md"
    src_cfg = ROOT / "src/程序化地图/渲染基线.mjs"
    play_html = ROOT / "src/程序化地图/预览入口.html"
    agents_doc = ROOT / "AGENTS.md"

    for p in (spec_doc, src_cfg, play_html):
        if not p.is_file():
            errors.append(f"缺少渲染基线核心文件：{p.relative_to(ROOT).as_posix()}")
            return

    doc_text = spec_doc.read_text(encoding="utf-8")
    cfg_text = src_cfg.read_text(encoding="utf-8")
    html_text = play_html.read_text(encoding="utf-8")
    agents_text = agents_doc.read_text(encoding="utf-8") if agents_doc.is_file() else ""

    for token in ("stylized-680p-v1", "680", "1208", "1588", "pixelated", "contrast(1.08) saturate(1.10) sepia(0.06)", "antialias: true"):
        if token not in cfg_text:
            errors.append(f"渲染基线配置源缺少冻结参数 {token}：src/程序化地图/渲染基线.mjs")
        if token not in doc_text:
            errors.append(f"docs/渲染风格与分辨率基线规范.md 缺少冻结参数登记：{token}")

    if "渲染基线.mjs" not in html_text or "applyStylized680pCanvas" not in html_text:
        errors.append("主试玩入口必须同源导入并调用 渲染基线.mjs：src/程序化地图/预览入口.html")
    if "btn-res-1080" in html_text or "btn-res-640" in html_text:
        errors.append("主试玩入口禁止保留旧 1080p/640×364 多分辨率切换按钮：src/程序化地图/预览入口.html")
    if "渲染风格与分辨率基线规范.md" not in nav_text:
        errors.append("docs/apps-code-map.md 缺少渲染基线规范登记：docs/渲染风格与分辨率基线规范.md")
    if "docs/渲染风格与分辨率基线规范.md" not in agents_text:
        errors.append("AGENTS.md 缺少 680p 复古微缩 Stylized 强制渲染基线条款")


def main():
    errors = []
    required = [
        "AGENTS.md", "README.md", ".gitignore", "docs/apps-code-map.md",
        "docs/项目结构与开发约定.md", "docs/界面预览/README.md",
        "docs/全局UI-UX规范.md", "docs/渲染风格与分辨率基线规范.md",
        "src/程序化地图/渲染基线.mjs",
        "src/界面规范/令牌.css", "src/界面规范/组件.css", "src/界面规范/场内视觉.css",
        "src/界面规范/图标与标绘.mjs", "src/界面规范/交互控件.mjs", "src/界面规范/index.html",
        "src/界面规范/规范页.css", "src/界面规范/规范页.mjs", "src/界面规范/README.md",
        "scripts/界面规范/启动规范路由.py", "scripts/界面规范/验证界面规范.py", "scripts/界面规范/README.md",
        "scripts/脚本维护说明.md", "reports/报告维护说明.md",
        "research/断箭/来源台账.json", "research/断箭/观察记录.md",
        "research/断箭/目录核验.json",
    ]
    for name in required:
        if not (ROOT / name).is_file():
            errors.append(f"缺少入口或说明：{name}")
    ignore = ROOT / ".gitignore"
    rules = ignore.read_text(encoding="utf-8").splitlines() if ignore.is_file() else []
    for rule in ["/local-data/", "/tmp/", "/temp/", "__pycache__/", ".env", ".env.*", "*.key"]:
        if rule not in rules:
            errors.append(f"缺少本地资料排除规则：{rule}")
    navigation = ROOT / "docs/apps-code-map.md"
    nav_text = navigation.read_text(encoding="utf-8") if navigation.is_file() else ""
    preview_root = ROOT / "docs/界面预览"
    check_preview_categories(preview_root, errors)
    check_ui_spec_sync(errors, nav_text)
    check_render_baseline_sync(errors, nav_text)
    paths = list(formal_files(ROOT))
    links = 0
    scripts = 0
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        if path.is_symlink():
            errors.append(f"正式目录包含符号链接，请明确其输入边界：{relative}")
            continue
        if path.suffix.lower() in RAW_SUFFIXES:
            errors.append(f"原始游戏资源或第三方程序应存入本地目录：{relative}")
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeError, OSError) as error:
            errors.append(f"无法按 UTF-8 读取 {relative}：{error}")
            continue
        if PRIVATE_PATH.search(text):
            errors.append(f"正式文本包含本机 Steam 或用户目录绝对路径：{relative}")
        if path.suffix.lower() == ".html" and path.is_relative_to(ROOT / "docs"):
            check_preview_html(path, relative, text, preview_root, errors)
        if path.suffix == ".py" and path.is_relative_to(ROOT / "scripts"):
            scripts += 1
            if f"../{relative})" not in nav_text:
                errors.append(f"Python 脚本缺少文件导航：{relative}")
        if path.suffix != ".md":
            continue
        # 代码块中的示例链接不作为文档引用处理。
        markdown = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
        for match in LINK.finditer(markdown):
            url = match.group(1).strip().strip("<>")
            target = urlsplit(url)
            if target.scheme or target.netloc or not target.path:
                continue
            links += 1
            destination = (path.parent / unquote(target.path)).resolve()
            if not destination.is_relative_to(ROOT):
                errors.append(f"文档链接超出项目：{relative} → {url}")
            elif any(part in SKIP for part in destination.relative_to(ROOT).parts):
                errors.append(f"正式文档链接依赖本地数据或缓存：{relative} → {url}")
            elif not destination.exists():
                errors.append(f"文档本地链接不存在：{relative} → {url}")
    if errors:
        print("项目结构检查未通过：\n" + "\n".join(f"- {error}" for error in errors))
        return 1
    print(f"项目结构检查通过：{len(paths)} 个正式文件、{scripts} 个 Python 脚本、{links} 条本地文档链接。")
    print("仅检查目录、导航和排除约定，不代表游戏资源提取或模型验收通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
