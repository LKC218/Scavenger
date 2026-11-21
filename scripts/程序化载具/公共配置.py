"""程序化载具优化与流式渲染（V001）公共路径、配置与工具函数。"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BATCH_ID = "V001-b4570e71"
WORK_DIR = ROOT / "local-data" / "程序化载具" / BATCH_ID
TMP_DIR = ROOT / "tmp" / f"程序化载具-{BATCH_ID}"

V0_DIR = WORK_DIR / "V0-基线与规则"
V1_DIR = WORK_DIR / "V1-样本验证"
V2_DIR = WORK_DIR / "V2-全库编译"
V3_DIR = WORK_DIR / "V3-归档打包"
V4_DIR = WORK_DIR / "V4-预览服务"
V5_DIR = WORK_DIR / "V5-全矩阵验证"
REPORT_DIR = ROOT / "reports" / "程序化载具"

ORIGINALS_CACHE_DIR = TMP_DIR / "originals"
TEX_CACHE_DIR = TMP_DIR / "tex-cache"

TOOLS_OPT_DIR = ROOT / "local-data" / "tools" / "网页资源优化" / "1"
SEVEN_ZIP_EXE = TOOLS_OPT_DIR / "node_modules" / "7zip-bin" / "win" / "x64" / "7za.exe"
THREE_DIR = ROOT / "local-data" / "tools" / "three" / "0.187.0"

DEFAULT_PORT = 8822
PROTECTED_PORTS = {8766, 8767, 8768, 8769, 8780, 8781, 8784, 8794, 8797, 8798, 8811, 8812, 8813, 8814, 8820}

# V1 极限痛点样本（覆盖坦克基准、34 DrawCall 直升机、517 节点战机、12万面运输机、11.5万面卡车、固定翼基准）
V1_SAMPLE_IDS = [
    "model:US_M1A2",
    "model:US_AH64E",
    "model:US_F15E",
    "model:US_MC130H",
    "model:US_LMTV_Warpig",
    "model:US_A10",
    "model:US_AH64D",  # 额外纳入黄金旋翼基准，确保 M1A2/AH-64D/A-10C 三大黄金基准在 V1 即全检
]

# 明确已知的旋翼机型（含倾转旋翼）
ROTORCRAFT_IDS = {
    "model:US_AH-1W Super_Cobra",
    "model:US_AH-1Z Viper",
    "model:US_AH6",
    "model:US_AH64D",
    "model:US_AH64E",
    "model:US_AH64L",
    "model:US_Ch47",
    "model:US_CH53",
    "model:US_CH53K",
    "model:US_CV-22_b",
    "model:US_MH47",
    "model:US_MH6",
    "model:US_MH60M_IDAP",
    "model:US_MH60M_SOAR",
    "model:US_MH60X",
    "model:US_MV-22_b",
    "model:US_OH58Kiowa",
    "model:US_RAH66",
    "model:US_UH-1Y",
    "model:US_UH60M",
}

UAV_IDS = {
    "model:US_MQ1B",
    "model:US_MQ1C",
    "model:US_MQ9",
    "model:US_RQ170",
    "model:US_RQ4B",
    "model:US_RQ7",
}

# 4 款已知 partial 待修参考件：严禁擅自升级为 passed
PARTIAL_IDS = {
    "model:US_m1288_base",
    "model:US_LAV_AT-M_body",
    "model:US_AC130J",
    "model:US_AC130U",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fingerprint(path: Path) -> dict:
    st = path.stat()
    try:
        rel = path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        rel = str(path)
    return {
        "path": rel,
        "bytes": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "sha256": sha256_file(path),
    }


def save_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_formal_vehicles() -> tuple[dict, list[dict], dict, Path]:
    """从正式资料库（revision 7）只读加载全部 129 款载具、原件映射与版本目录。"""
    p1 = str(ROOT / "scripts" / "断箭")
    p2 = str(ROOT / "scripts" / "资源库")
    if p1 not in sys.path:
        sys.path.insert(0, p1)
    if p2 not in sys.path:
        sys.path.insert(0, p2)
    from 资料库核心 import current, query
    from 统一资源 import read

    pointer, catalog, routes, base = current()
    originals = read(base / "原件映射.json")
    r1 = query(catalog, routes, "", "model", None, None, None, 200, 0)
    r2 = query(catalog, routes, "", "model", None, None, None, 200, 200)
    rows = r1["results"] + r2["results"]
    vehicles = [r for r in rows if r.get("category") in ("ground", "air")]
    vehicles.sort(key=lambda x: (0 if x["category"] == "ground" else 1, x["id"].lower()))
    return pointer, vehicles, originals, base


def vehicle_short_id(full_id: str) -> str:
    return full_id.split(":", 1)[1] if ":" in full_id else full_id


def safe_slug(full_id: str) -> str:
    s = vehicle_short_id(full_id)
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in s)
