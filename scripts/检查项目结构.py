"""只读检查项目目录边界、文档链接及本地资料排除约定。"""

import json
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
UI_SPEC_ADOPTED_MODULES = ["src/界面规范", "src/主入口", "src/程序化地图", "src/程序化载具", "src/交互系统"]  # 正式全仓防漂移门禁
UI_SEMANTIC_VAR_DEF = re.compile(r"(--ui-(?:ch|state)-[a-z0-9-]+)\s*:")
NATIVE_SELECT_TAG = re.compile(r"<select\b", re.IGNORECASE)
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
        wep_block = re.search(r"WEAPON_ICONS\s*=\s*Object\.freeze\(\{(.*?)\n\}\);", icons_text, flags=re.DOTALL)
        wep_keys = re.findall(r"^\s*([a-z0-9_]+)\s*:\s*Object\.freeze", wep_block.group(1), flags=re.MULTILINE) if wep_block else []
        for wep_key in ("cannon", "rocket", "missile", "bomb"):
            if wep_key not in wep_keys:
                errors.append(f"src/界面规范/图标与标绘.mjs 缺少固定 4 大通用武器剪影键：{wep_key}")
            elif doc_text and wep_key not in doc_text:
                errors.append(f"docs/全局UI-UX规范.md 缺少通用武器剪影键登记：{wep_key}")
        for brand_api in (
            "BRAND_IDENTITY",
            "BRAND_LOGO_LAYERS",
            "createBrandLogoSvg",
            "createBrandLockup",
            "buildBrandFaviconSvgString",
            "drawBrandLogoCanvas",
        ):
            if brand_api not in icons_text:
                errors.append(f"src/界面规范/图标与标绘.mjs 缺少品牌标识程序化导出：{brand_api}")
            elif doc_text and brand_api not in doc_text:
                errors.append(f"docs/全局UI-UX规范.md 缺少品牌标识导出登记：{brand_api}")
        if not (ROOT / "src/界面规范/favicon.svg").is_file():
            errors.append("缺少全站唯一品牌标签图标单源：src/界面规范/favicon.svg")
        for wep_api in ("resolveWeaponIconKind", "createWeaponIconSvg"):
            if wep_api not in icons_text:
                errors.append(f"src/界面规范/图标与标绘.mjs 缺少通用武器图标工厂导出：{wep_api}")
            elif doc_text and wep_api not in doc_text:
                errors.append(f"docs/全局UI-UX规范.md 缺少通用武器图标函数登记：{wep_api}")
        hud_block = re.search(r"HUD_ICONS\s*=\s*Object\.freeze\(\{(.*?)\}\)", icons_text, flags=re.DOTALL)
        hud_keys = re.findall(r"^\s*([A-Za-z0-9_]+)\s*:", hud_block.group(1), flags=re.MULTILINE) if hud_block else []
        for req_hud in ("flare", "altitude", "evacuate", "sprint", "autofire", "follow", "wheel", "stop", "focus"):
            if req_hud not in hud_keys:
                errors.append(f"src/界面规范/图标与标绘.mjs 缺少命令面板升级功能图标键：{req_hud}")
            elif doc_text and req_hud not in doc_text:
                errors.append(f"docs/全局UI-UX规范.md 缺少命令面板功能图标键登记：{req_hud}")

    if spec_html.is_file():
        html_text = spec_html.read_text(encoding="utf-8")
        for ref in ("/ui/令牌.css", "/ui/组件.css", "/ui/场内视觉.css"):
            if ref not in html_text:
                errors.append(f"活体规范页缺少共享样式同源引用 {ref}：src/界面规范/index.html")
        if "640px" in html_text or "ui-pod--chamfer" in html_text:
            errors.append("活体规范页禁止保留已淘汰的 640px 基线或 ui-pod--chamfer 死类名：src/界面规范/index.html")
    if spec_js.is_file():
        js_text = spec_js.read_text(encoding="utf-8")
        if "图标与标绘.mjs" not in js_text:
            errors.append("活体规范页脚本必须同源导入 图标与标绘.mjs：src/界面规范/规范页.mjs")
        if "交互控件.mjs" not in js_text:
            errors.append("活体规范页脚本必须同源导入 交互控件.mjs：src/界面规范/规范页.mjs")
        # V2.2 三向同步防滞后门禁：核心源新增的实战基元、视距标绘原语与偏好键必须在 /ui-spec 与总规范文档同步呈现
        spec_combined = (spec_html.read_text(encoding="utf-8") if spec_html.is_file() else "") + "\n" + js_text
        ctrl_text = ctrl_js.read_text(encoding="utf-8") if ctrl_js.is_file() else ""
        for comp_cls in ("ui-brand-lockup", "ui-banner-card", "ui-rating-pips", "ui-cmd-shell-paged", "ui-cmd-viewport", "ui-cmd-pager", "ui-cmd-pager-indicator", "ui-cmd-wep-grid", "ui-cmd-wep-card", "ui-cmd-util-bar", "ui-cmd-util-btn", "ui-wheel-svg", "ui-back-home", "ui-vd-card", "ui-vd-hover"):
            if comp_cls not in spec_combined:
                errors.append(f"活体规范台缺少 V2.2 实战基元演示（防滞后门禁）：{comp_cls}")
            if doc_text and comp_cls not in doc_text:
                errors.append(f"docs/全局UI-UX规范.md 缺少 V2.2 实战基元登记：{comp_cls}")
        for brand_ref in ("BRAND_IDENTITY", "createBrandLogoSvg", "createBrandLockup", "drawBrandLogoCanvas"):
            if brand_ref not in spec_combined:
                errors.append(f"活体规范台缺少品牌标识程序化体系同步引用（防滞后门禁）：{brand_ref}")
        for ctrl_api in ("syncCmdPagerIndicator", "runCmdPageTransition", "renderTacticalWheel", "resolveWheelSectorFromVector", "clampWheelVisualCenter", "renderVehicleDetailCard", "renderVehicleHoverCard", "attachDeckHoverCardController"):
            if ctrl_api not in ctrl_text:
                errors.append(f"src/界面规范/交互控件.mjs 缺少翻页/轮盘交互工具导出：{ctrl_api}")
            if ctrl_api not in js_text:
                errors.append(f"活体规范台脚本缺少翻页/轮盘交互工具调用（防滞后门禁）：{ctrl_api}")
            if doc_text and ctrl_api not in doc_text:
                errors.append(f"docs/全局UI-UX规范.md 缺少翻页/轮盘交互工具登记：{ctrl_api}")
        for wep_ref in ("WEAPON_ICONS", "createWeaponIconSvg", "resolveWeaponIconKind"):
            if wep_ref not in spec_combined:
                errors.append(f"活体规范台缺少 4 大通用武器图标同步引用（防滞后门禁）：{wep_ref}")
        for prim_fn in ("resolveZoomIconTier", "clusterScreenUnits", "drawZoomTierUnitIcon", "drawGroundOrbitZone", "drawAirTurnArc"):
            if prim_fn not in js_text:
                errors.append(f"活体规范台脚本缺少 V2.2 标绘原语同源演练（防滞后门禁）：{prim_fn}")
            if doc_text and prim_fn not in doc_text:
                errors.append(f"docs/全局UI-UX规范.md 缺少 V2.2 标绘原语登记：{prim_fn}")
        if "vehicleAudioEnabled" not in spec_combined:
            errors.append("活体规范台缺少独立载具空间引擎音偏好演示（防滞后门禁）：vehicleAudioEnabled")
        if doc_text and "vehicleAudioEnabled" not in doc_text:
            errors.append("docs/全局UI-UX规范.md 缺少 vehicleAudioEnabled 偏好键登记")
        if "dofMode" not in spec_combined:
            errors.append("活体规范台缺少微缩移轴景深偏好演示（防滞后门禁）：dofMode")
        if doc_text and "dofMode" not in doc_text:
            errors.append("docs/全局UI-UX规范.md 缺少 dofMode 偏好键登记")
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
        for req_mod, req_files in (
            ("src/主入口", [ROOT / "src/主入口/index.html", ROOT / "src/主入口/主入口.mjs"]),
            ("src/程序化地图", [ROOT / "src/程序化地图/预览入口.html"]),
        ):
            if mod_rel == req_mod and all(p.is_file() for p in req_files):
                req_txt = "\n".join(p.read_text(encoding="utf-8") for p in req_files)
                req_rel = req_files[0].relative_to(ROOT).as_posix()
                for ui_ref in ("/ui/令牌.css", "/ui/组件.css", "/ui/场内视觉.css", "/ui/交互控件.mjs", "/ui/图标与标绘.mjs"):
                    if ui_ref not in req_txt:
                        errors.append(f"正式运行入口缺少 UI 规范同源引用 {ui_ref}：{req_rel}")
        for f in sorted(mod_dir.rglob("*")):
            if not f.is_file() or f.name in SKIP:
                continue
            f_rel = f.relative_to(ROOT).as_posix()
            if f.suffix.lower() == ".html":
                if NATIVE_SELECT_TAG.search(f.read_text(encoding="utf-8")):
                    errors.append(f"已接入模块禁止裸用浏览器原生 <select>（须用 .ui-select）：{f_rel}")
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

    for token in ("stylized-680p-v1", "680", "1208", "1588", "pixelated", "contrast(1.08) saturate(1.10) sepia(0.06)", "antialias: true", "bloomThreshold", "tiltShiftDof"):
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


def check_balance_scales_sync(errors, nav_text):
    """校验全局数值基底与倍率规范（GLOBAL_BALANCE_SCALES）文档、代码 SSOT 与固定翼底座的同步性。"""
    spec_doc = ROOT / "docs/地图与载具/数值基底与全局倍率规范.md"
    src_cfg = ROOT / "src/程序化地图/数值基底与倍率规范.mjs"
    fw_base = ROOT / "src/程序化地图/固定翼底座.mjs"
    agents_doc = ROOT / "AGENTS.md"

    for p in (spec_doc, src_cfg, fw_base):
        if not p.is_file():
            errors.append(f"缺少数值基底规范核心文件：{p.relative_to(ROOT).as_posix()}")
            return

    doc_text = spec_doc.read_text(encoding="utf-8")
    cfg_text = src_cfg.read_text(encoding="utf-8")
    fw_text = fw_base.read_text(encoding="utf-8")
    agents_text = agents_doc.read_text(encoding="utf-8") if agents_doc.is_file() else ""

    for token in ("GLOBAL_BALANCE_SCALES", "ba-source-ssot-v1", "projectileSpeedScale", "fuelDurationScale", "rearmCooldownScale", "FIXED_WING_FUEL_PROFILES", "FOG_RECON_PROFILES", "deriveAircraftStatsFromSourceRef", "deriveWeaponStatsFromSourceRef", "deriveUnitReconProfile"):
        if token not in cfg_text:
            errors.append(f"数值基底配置源缺少核心符号 {token}：src/程序化地图/数值基底与倍率规范.mjs")
    for token in ("数值基底与倍率规范.mjs", "GLOBAL_BALANCE_SCALES", "deriveAircraftStatsFromSourceRef", "deriveWeaponStatsFromSourceRef"):
        if token not in fw_text:
            errors.append(f"固定翼底座必须同源导入并调用 {token}：src/程序化地图/固定翼底座.mjs")

    for token in ("GLOBAL_BALANCE_SCALES", "ba-source-ssot-v1", "brokenarrowstats.com", "projectileSpeedScale", "fuelDurationScale", "rearmCooldownScale", "FIXED_WING_FUEL_PROFILES", "FOG_RECON_PROFILES", "deriveUnitReconProfile"):
        if token not in doc_text:
            errors.append(f"docs/地图与载具/数值基底与全局倍率规范.md 缺少规范登记：{token}")

    if "数值基底与倍率规范.mjs" not in nav_text or "数值基底与全局倍率规范.md" not in nav_text:
        errors.append("docs/apps-code-map.md 缺少数值基底与全局倍率规范登记")
    if "GLOBAL_BALANCE_SCALES" not in agents_text or "docs/地图与载具/数值基底与全局倍率规范.md" not in agents_text:
        errors.append("AGENTS.md 缺少 GLOBAL_BALANCE_SCALES 全局数值基底强制条款")


def check_daily_worklog_naming(errors):
    """校验每日开发日志目录、总看板入口与按月归档 JSON 命名规范。"""
    worklog_root = ROOT / "reports/开发日志"
    if not worklog_root.exists():
        return
    board_html = worklog_root / "每日开发看板.html"
    data_root = worklog_root / "数据"
    if not board_html.is_file():
        errors.append("缺少每日开发日志唯一总看板入口：reports/开发日志/每日开发看板.html")
    if not data_root.is_dir():
        errors.append("缺少每日开发日志按月数据目录：reports/开发日志/数据/")

    for child in sorted(worklog_root.iterdir()):
        if child.name not in {"每日开发看板.html", "数据"}:
            errors.append(
                f"reports/开发日志/ 根层仅允许 每日开发看板.html 与 数据/，发现多余条目：{child.relative_to(ROOT).as_posix()}"
            )

    month_re = re.compile(r"^\d{4}-\d{2}$")
    day_re = re.compile(r"^(\d{4}-\d{2}-\d{2})-开发日志\.json$")
    if data_root.is_dir():
        for mdir in sorted(data_root.iterdir()):
            rel_m = mdir.relative_to(ROOT).as_posix()
            if not mdir.is_dir() or not month_re.fullmatch(mdir.name):
                errors.append(f"开发日志数据目录仅允许 YYYY-MM 按月子目录：{rel_m}")
                continue
            for item in sorted(mdir.iterdir()):
                rel_f = item.relative_to(ROOT).as_posix()
                match = day_re.fullmatch(item.name)
                if not item.is_file() or not match:
                    errors.append(f"每日开发日志数据文件必须命名为 YYYY-MM-DD-开发日志.json：{rel_f}")
                    continue
                date_str = match.group(1)
                if not date_str.startswith(mdir.name + "-"):
                    errors.append(f"开发日志日期与所在月份目录不匹配：{rel_f}")
                    continue
                try:
                    payload = json.loads(item.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    errors.append(f"每日开发日志 JSON 无法解析：{rel_f}（{exc}）")
                    continue
                if payload.get("date") != date_str:
                    errors.append(f"开发日志 JSON 内 date 字段与文件名不一致：{rel_f}")
                if payload.get("mode") not in {"curated", "auto-draft"}:
                    errors.append(f"开发日志 JSON 内 mode 仅允许 curated 或 auto-draft：{rel_f}")


def main():
    errors = []
    required = [
        "AGENTS.md", "README.md", ".gitignore", "docs/apps-code-map.md",
        "docs/项目结构与开发约定.md", "docs/界面预览/README.md",
        "docs/全局UI-UX规范.md", "docs/渲染风格与分辨率基线规范.md",
        "docs/地图与载具/数值基底与全局倍率规范.md",
        "src/程序化地图/渲染基线.mjs",
        "src/程序化地图/数值基底与倍率规范.mjs",
        "src/界面规范/令牌.css", "src/界面规范/组件.css", "src/界面规范/场内视觉.css",
        "src/界面规范/图标与标绘.mjs", "src/界面规范/favicon.svg", "src/界面规范/交互控件.mjs", "src/界面规范/index.html",
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
    check_balance_scales_sync(errors, nav_text)
    check_daily_worklog_naming(errors)
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
