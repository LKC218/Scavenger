"""核验丧尸群体分享包的文件身份、本机服务和可选的浏览器冒烟。"""
from argparse import ArgumentParser
import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


RUNTIME_SOURCES = [
    "src/丧尸群体测试/index.html",
    "src/丧尸群体测试/界面.css",
    "src/丧尸群体测试/入口.mjs",
    "src/丧尸群体测试/群体.mjs",
    "src/丧尸群体测试/角色.mjs",
    "src/丧尸群体测试/角色配置.mjs",
    "src/丧尸群体测试/场景.mjs",
    "src/丧尸群体测试/统计.mjs",
    "src/资源加载/压缩模型加载.mjs",
    "scripts/发布服务.py",
]
PRIVATE_PATH = re.compile(
    r"(?i)(?:[A-Z]:[\\/](?:Users|项目)[\\/]|\\\\[^\\\s]+\\[^\\\s]+)"
)
FORBIDDEN_DOCUMENT_WORDS = ("初学者", "新手", "小白")
USAGE_GUIDE_MARKERS = (
    "这是开发项目",
    "WorkBuddy",
    "新建任务",
    "选择工作空间",
    "打开本地文件夹",
)
USAGE_GUIDE_IMAGE = "assets/WorkBuddy操作截图.png"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def code_digest(root):
    value = hashlib.sha256()
    for relative in RUNTIME_SOURCES:
        path = root / relative
        value.update(relative.encode("utf-8"))
        value.update(b"\0")
        value.update(path.read_bytes())
        value.update(b"\0")
    return value.hexdigest()


def read_checksums(path):
    rows = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        expected, relative = raw.split("  ", 1)
        rows[relative] = expected
    return rows


def verify_checksums(root):
    checksum_path = root / "SHA256SUMS.txt"
    if not checksum_path.is_file():
        raise ValueError("缺少 SHA256SUMS.txt")
    recorded = read_checksums(checksum_path)
    actual_files = {
        path.relative_to(root).as_posix(): path
        for path in root.rglob("*")
        if path.is_file() and path != checksum_path
    }
    if set(recorded) != set(actual_files):
        missing = sorted(set(actual_files) - set(recorded))
        stale = sorted(set(recorded) - set(actual_files))
        raise ValueError(f"文件清单不一致，缺少={missing[:5]}，多余={stale[:5]}")
    for relative, expected in recorded.items():
        if digest(actual_files[relative]) != expected:
            raise ValueError(f"文件摘要变化：{relative}")
    return len(recorded)


def verify_manifest(root):
    manifest_path = root / "data" / "发布资源清单.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if manifest.get("schema") != 1:
        raise ValueError("资源清单协议无效")
    if manifest.get("code_sha256") != code_digest(root):
        raise ValueError("源码摘要不匹配")
    models = manifest.get("models") or []
    if len(models) != 13:
        raise ValueError("角色数量不是十三款")
    expected_ids = {"model:zombie-runtime"} | {
        f"model:zombie-{index:02d}" for index in range(1, 13)
    }
    if {model.get("id") for model in models} != expected_ids:
        raise ValueError("稳定ID不完整")
    if {model.get("source_mesh_index") for model in models} != set(range(13)):
        raise ValueError("源网格编号不完整")
    verified_assets = 0
    for model in models:
        variants = model.get("variants") or []
        if {variant.get("resolution") for variant in variants} != {256, 512}:
            raise ValueError(f"角色双档缺失：{model.get('id')}")
        for variant in variants:
            path = (root / variant["path"]).resolve()
            if not path.is_relative_to(root):
                raise ValueError("资源路径越界")
            if (
                not path.is_file()
                or path.stat().st_size != variant["bytes"]
                or digest(path) != variant["sha256"]
            ):
                raise ValueError(f"资源摘要不匹配：{variant['path']}")
            verified_assets += 1
    return manifest, verified_assets


def verify_public_text(root):
    checked = 0
    for path in root.rglob("*.md"):
        parts = set(path.relative_to(root).parts)
        if "研究资料" in parts:
            continue
        text = path.read_text(encoding="utf-8")
        if any(word in text for word in FORBIDDEN_DOCUMENT_WORDS):
            raise ValueError(f"说明文档出现不允许的受众措辞：{path}")
        if PRIVATE_PATH.search(text):
            raise ValueError(f"说明文档包含本机绝对路径：{path}")
        checked += 1
    for relative in ("data/发布资源清单.json", "版本与状态.json"):
        path = root / relative
        if PRIVATE_PATH.search(path.read_text(encoding="utf-8")):
            raise ValueError(f"公开元数据包含本机绝对路径：{relative}")
    return checked


def verify_usage_guide(root):
    path = root / "使用指南.html"
    if not path.is_file():
        raise ValueError("缺少使用指南.html")
    text = path.read_text(encoding="utf-8")
    missing = [marker for marker in USAGE_GUIDE_MARKERS if marker not in text]
    if missing:
        raise ValueError("使用指南缺少关键步骤：" + "；".join(missing))
    if any(word in text for word in FORBIDDEN_DOCUMENT_WORDS):
        raise ValueError("使用指南出现不允许的受众措辞")
    if PRIVATE_PATH.search(text):
        raise ValueError("使用指南包含本机绝对路径")
    for match in re.finditer(
        r"""(?i)\b(?:src|href)\s*=\s*["']([^"']+)""",
        text,
    ):
        target = match.group(1).strip()
        if target.startswith(("http://", "https://", "//")):
            raise ValueError(f"使用指南引用外部依赖：{target}")
    if 'lang="zh-CN"' not in text:
        raise ValueError("使用指南未声明中文页面")
    if USAGE_GUIDE_IMAGE not in text:
        raise ValueError("使用指南未引用 WorkBuddy 操作截图")
    image_path = (root / USAGE_GUIDE_IMAGE).resolve()
    if not image_path.is_relative_to(root) or not image_path.is_file():
        raise ValueError("WorkBuddy 操作截图缺失或路径越界")
    image = image_path.read_bytes()
    if not image.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("WorkBuddy 操作截图不是有效 PNG")
    return {
        "file": "使用指南.html",
        "bytes": len(text.encode("utf-8")),
        "required_steps": len(USAGE_GUIDE_MARKERS),
        "offline": True,
        "image": {
            "path": USAGE_GUIDE_IMAGE,
            "bytes": len(image),
            "sha256": hashlib.sha256(image).hexdigest(),
        },
    }


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_json(url, deadline):
    last_error = None
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1) as response:
                return json.load(response)
        except (OSError, URLError, ValueError) as exc:
            last_error = exc
            time.monotonic()
        time.sleep(0.1)
    raise RuntimeError(f"服务未在截止时间前就绪：{last_error}")


def verify_runtime(root, browser_channel=None, resolution=256):
    port = free_port()
    with tempfile.TemporaryDirectory(prefix="scavenger-horde-check-") as data_dir:
        command = [
            sys.executable,
            "-X",
            "utf8",
            "-B",
            str(root / "scripts" / "发布服务.py"),
            "--serve",
            "--no-open",
            "--port",
            str(port),
            "--resolution",
            str(resolution),
            "--data-dir",
            data_dir,
        ]
        creation_flags = (
            subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )
        process = subprocess.Popen(
            command,
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=creation_flags,
        )
        try:
            deadline = time.monotonic() + 20
            status = wait_json(
                f"http://127.0.0.1:{port}/api/status",
                deadline,
            )
            if (
                status.get("app") != "scavenger-horde"
                or status.get("resolution") != resolution
            ):
                raise ValueError("服务状态身份无效")
            with urlopen(
                f"http://127.0.0.1:{port}/manifest.json",
                timeout=5,
            ) as response:
                manifest = json.load(response)
            if len(manifest.get("models") or []) != 13:
                raise ValueError("服务清单角色数量无效")
            if manifest.get("asset", {}).get("resolution") != resolution:
                raise ValueError("服务资源档位无效")
            for model in manifest["models"]:
                if model.get("asset", {}).get("resolution") != resolution:
                    raise ValueError(f"角色资源档位无效：{model.get('id')}")
                request = Request(
                    f"http://127.0.0.1:{port}{model['url']}",
                    method="HEAD",
                )
                with urlopen(request, timeout=10) as response:
                    if response.status != 200:
                        raise ValueError(f"资源不可读取：{model['url']}")
                    if int(response.headers.get("Content-Length", "0")) != model["asset"]["bytes"]:
                        raise ValueError(f"资源长度不一致：{model['url']}")

            browser_result = None
            if browser_channel:
                from playwright.sync_api import sync_playwright

                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(
                        channel=browser_channel,
                        headless=True,
                    )
                    page = browser.new_page(viewport={"width": 1280, "height": 720})
                    errors = []
                    page.on(
                        "pageerror",
                        lambda exc: errors.append(f"pageerror:{exc}"),
                    )
                    page.on(
                        "console",
                        lambda message: errors.append(f"console:{message.text}")
                        if message.type == "error"
                        else None,
                    )
                    try:
                        page.goto(
                            f"http://127.0.0.1:{port}/",
                            wait_until="networkidle",
                            timeout=120000,
                        )
                        page.wait_for_function(
                            "() => window.horde?.ready",
                            timeout=120000,
                        )
                        snapshot = page.evaluate("() => horde.snapshot()")
                        if snapshot["health"]["count"] != 500:
                            raise ValueError("浏览器默认角色数量不是500")
                        if len(snapshot["animation"]["models"]) != 13:
                            raise ValueError("浏览器实际加载角色不是十三款")
                        if snapshot["animation"]["independentSkeletons"] != 500:
                            raise ValueError("浏览器独立骨架数量无效")
                        if errors:
                            raise ValueError("浏览器控制台存在错误：" + "；".join(errors[:3]))
                        browser_result = {
                            "browser": browser.version,
                            "count": snapshot["health"]["count"],
                            "models": len(snapshot["animation"]["models"]),
                            "independentSkeletons": snapshot["animation"][
                                "independentSkeletons"
                            ],
                        }
                    finally:
                        browser.close()
            return {
                "status": status,
                "models": len(manifest["models"]),
                "assets_checked": len(manifest["models"]),
                "browser": browser_result,
            }
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def verify_rules(root):
    result = subprocess.run(
        ["node", "tests/验证群体规则.mjs"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    parsed = json.loads(result.stdout)
    if not parsed.get("passed"):
        raise ValueError("群体规则检查未通过")
    return len(parsed.get("checks", []))


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    parser.add_argument("--skip-hashes", action="store_true")
    parser.add_argument("--rules", action="store_true")
    parser.add_argument(
        "--resolution",
        type=int,
        choices=(256, 512),
        default=256,
        help="服务加载与浏览器冒烟使用的 GLB 贴图档位",
    )
    parser.add_argument(
        "--browser-channel",
        choices=("chrome", "msedge"),
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    manifest, assets = verify_manifest(root)
    hash_count = None if args.skip_hashes else verify_checksums(root)
    document_count = verify_public_text(root)
    usage_guide = verify_usage_guide(root)
    runtime = verify_runtime(root, args.browser_channel, args.resolution)
    rule_checks = verify_rules(root) if args.rules else None
    receipt = {
        "passed": True,
        "package": manifest["package"],
        "hash_files": hash_count,
        "assets_checked": assets,
        "public_documents_checked": document_count,
        "usage_guide": usage_guide,
        "runtime": runtime,
        "resolution": args.resolution,
        "rule_checks": rule_checks,
    }
    text = json.dumps(receipt, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
