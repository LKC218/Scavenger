"""B2阶段：构建8种定制RTS球形法线低模蓬松树GLB、8种中景立体冠层代理GLB、基于34.3万棵树真实分布的实心3D远景集群林冠壳GLB、近景GPU草丛图集及空间分块索引。"""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import struct
import subprocess
import sys
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/断箭"))
from 地图GLB import write_glb

BATCH_DIR = ROOT / "local-data/断箭/批次/BA-26273833-012/Airbase全图/V001-01a1a602"
WORK_DIR = ROOT / "local-data/程序化地图/V001-fcf46de3"
B1_DIR = WORK_DIR / "B1-地形与道路"
B2_DIR = WORK_DIR / "B2-植被与草丛"

MIN_X, MAX_X = -4425.0, 4575.0
MIN_Z, MAX_Z = -4150.0, 4850.0
CHUNK_M = 125.0
GRID_N = 72


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def fingerprint(path: Path) -> dict:
    st = path.stat()
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "bytes": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "sha256": sha256_file(path),
    }


def read_uncompressed_glb_bounds(glb_path: Path) -> tuple[np.ndarray, np.ndarray]:
    raw = glb_path.read_bytes()
    j_len = struct.unpack_from("<I", raw, 12)[0]
    j = json.loads(raw[20 : 20 + j_len])
    prim = j["meshes"][0]["primitives"][0]
    acc = j["accessors"][prim["attributes"]["POSITION"]]
    return np.array(acc["min"], dtype=np.float32), np.array(acc["max"], dtype=np.float32)


def extract_species_palette(src_dir: Path, name: str) -> dict:
    """为 8 种林木与灌木提供适配线性渲染与 48° RTS 俯视角的明朗自然写实调色板（避免 sRGB->Linear 后发黑）。"""
    with Image.open(src_dir / "工程-正面.png") as pim:
        arr = np.asarray(pim.convert("RGB").resize((256, 256), Image.Resampling.LANCZOS), dtype=np.float32) / 255.0
    bg = arr[0, 0]
    dist = np.linalg.norm(arr - bg[None, None, :], axis=-1)
    fg = arr[dist > 0.06]

    # 物种基准自然色（温润写实、收紧明暗反差杜绝发白迷彩斑，同时避免线性空间发黑）
    presets = {
        "Forest_Oak_3":      {"base": [0.27, 0.41, 0.17], "light": [0.39, 0.52, 0.23], "dark": [0.18, 0.29, 0.12], "bark": [0.36, 0.28, 0.21]},
        "Forest_Spruce_4":   {"base": [0.24, 0.41, 0.22], "light": [0.35, 0.53, 0.29], "dark": [0.15, 0.28, 0.16], "bark": [0.35, 0.26, 0.19]},
        "Forest_Spruce_3":   {"base": [0.27, 0.44, 0.24], "light": [0.38, 0.56, 0.31], "dark": [0.17, 0.30, 0.17], "bark": [0.36, 0.27, 0.20]},
        "City_Maple_Small":  {"base": [0.33, 0.46, 0.20], "light": [0.46, 0.58, 0.26], "dark": [0.22, 0.33, 0.14], "bark": [0.40, 0.32, 0.24]},
        "City_Apple":        {"base": [0.31, 0.46, 0.21], "light": [0.44, 0.59, 0.28], "dark": [0.21, 0.33, 0.14], "bark": [0.38, 0.30, 0.22]},
        "City_Spruce_Small": {"base": [0.26, 0.44, 0.24], "light": [0.37, 0.57, 0.32], "dark": [0.17, 0.31, 0.18], "bark": [0.34, 0.26, 0.19]},
        "Bush_4":            {"base": [0.31, 0.44, 0.20], "light": [0.43, 0.55, 0.26], "dark": [0.21, 0.32, 0.14], "bark": [0.35, 0.28, 0.20]},
        "Bush_5":            {"base": [0.34, 0.47, 0.21], "light": [0.46, 0.58, 0.28], "dark": [0.23, 0.34, 0.15], "bark": [0.36, 0.29, 0.21]},
    }
    p = presets.get(name, presets["Forest_Oak_3"]).copy()
    if len(fg) > 100:
        green_mask = (fg[:, 1] > fg[:, 0] * 0.92) & (fg[:, 1] > fg[:, 2] * 1.05) & (fg[:, 1] > 0.22) & (fg[:, 1] < 0.78)
        if np.any(green_mask):
            src_mean = fg[green_mask].mean(axis=0)
            blended = np.array(p["base"]) * 0.85 + src_mean * 0.15
            p["base"] = np.maximum(blended, np.array(p["base"]) * 0.94).tolist()
    return p


def _draw_rotated_leaf(draw: ImageDraw.ImageDraw, cx: float, cy: float, rx: float, ry: float, ang: float, color: tuple[int, int, int, int]) -> None:
    """绘制一片带尖梢的自然菱椭圆叶片多边形。"""
    ca, sa = math.cos(ang), math.sin(ang)
    pts = []
    # 6 顶点尖叶轮廓
    local_pts = [
        (-rx, 0.0),
        (-rx * 0.35, -ry * 0.92),
        (rx * 0.42, -ry * 0.78),
        (rx * 1.08, 0.0),
        (rx * 0.42, ry * 0.78),
        (-rx * 0.35, ry * 0.92),
    ]
    for lx, ly in local_pts:
        pts.append((cx + lx * ca - ly * sa, cy + lx * sa + ly * ca))
    draw.polygon(pts, fill=color)


def generate_species_foliage_atlas(palette: dict, kind: str, size: int = 512, seed: int = 42) -> Image.Image:
    """
    生成 512x512 高精自然枝叶剪影与树皮图集（彻底移除实心黑圆盘内胆）：
    - 左侧通栏 [0..95, 0..511]：纵向粗糙树皮纹理 (Alpha=255)，UV u in [0.02..0.16]；
    - 右侧 2x2 四个独立 204x256 大格：
      Slot 0: x=[104..304], y=[4..252]
      Slot 1: x=[308..508], y=[4..252]
      Slot 2: x=[104..304], y=[260..508]
      Slot 3: x=[308..508], y=[260..508]
      - conifer: 绘制从底部枝基向上方扇形展开的真实羽状松枝扇（带主木枝、14对羽枝与密集松针尖，自然微透空）；
      - broadleaf: 绘制带分叉细枝与 360+ 片细碎锯齿叶片的蓬松不规则自然叶簇；
      - shrub: 绘制低矮散开、细小明亮小叶组成的野灌木枝叶簇。
    """
    rng = np.random.default_rng(seed)
    base_rgb = np.array(palette["base"], dtype=np.float32) * 255.0
    light_rgb = np.array(palette["light"], dtype=np.float32) * 255.0
    dark_rgb = np.array(palette["dark"], dtype=np.float32) * 255.0
    bark_rgb = np.array(palette["bark"], dtype=np.float32) * 255.0

    # 背景透明像素预填明亮树叶主色，杜绝 mipmap 采样出现黑边
    img = Image.new("RGBA", (size, size), (int(base_rgb[0]), int(base_rgb[1]), int(base_rgb[2]), 0))
    draw = ImageDraw.Draw(img)

    # 1. 左侧树皮通栏 [0..95, 0..511]
    draw.rectangle((0, 0, 95, size - 1), fill=(int(bark_rgb[0]), int(bark_rgb[1]), int(bark_rgb[2]), 255))
    for _ in range(240):
        bx = int(rng.uniform(2, 92))
        by = int(rng.uniform(0, size - 16))
        bh = int(rng.uniform(18, 64))
        shade = float(rng.uniform(0.76, 1.24))
        c = tuple(int(np.clip(v * shade, 24, 240)) for v in bark_rgb) + (255,)
        draw.line([(bx, by), (bx + int(rng.uniform(-2, 2)), min(size - 1, by + bh))], fill=c, width=int(rng.integers(2, 5)))

    slots = [
        (104, 4, 304, 252),
        (308, 4, 508, 252),
        (104, 260, 304, 508),
        (308, 260, 508, 508),
    ]

    def lerp_col(t: float, jitter: float = 0.06) -> tuple[int, int, int, int]:
        tt = float(np.clip(t + rng.uniform(-jitter, jitter), 0.0, 1.0))
        if tt < 0.45:
            k = tt / 0.45
            arr = dark_rgb * (1.0 - k) + base_rgb * k
        else:
            k = (tt - 0.45) / 0.55
            arr = base_rgb * (1.0 - k) + light_rgb * k
        return (int(np.clip(arr[0], 0, 255)), int(np.clip(arr[1], 0, 255)), int(np.clip(arr[2], 0, 255)), 255)

    for x0, y0, x1, y1 in slots:
        w = x1 - x0
        h = y1 - y0
        cx = (x0 + x1) * 0.5
        cy = (y0 + y1) * 0.5

        if kind == "conifer":
            # 针叶松枝扇：基部在 (cx, y1 - 10)，枝梢向顶部 (cx, y0 + 12) 与左右自然展开
            bx, by = cx, y1 - 10.0
            tx, ty = cx + float(rng.uniform(-6, 6)), y0 + 14.0
            # 主木枝与次级分叉枝
            twig_col = tuple(int(np.clip(v * 0.92, 25, 220)) for v in bark_rgb) + (255,)
            draw.line([(bx, by), (tx, ty)], fill=twig_col, width=5)

            # 15 对由下到上逐渐收窄的羽状侧松枝
            n_pairs = 15
            for p_idx in range(n_pairs):
                frac = 0.08 + 0.86 * (p_idx / (n_pairs - 1))
                sx = bx + (tx - bx) * frac
                sy = by + (ty - by) * frac
                # 轮廓包络：下宽上尖，呈自然羽毛扇形
                env = math.sin(min(1.0, frac * 1.08) * math.pi) ** 0.72 * (1.12 - 0.55 * frac)
                branch_len = w * 0.46 * env * float(rng.uniform(0.84, 1.14))
                for side in (-1.0, 1.0):
                    ang = -math.pi * 0.5 + side * float(rng.uniform(0.58, 1.08)) * (1.15 - 0.45 * frac)
                    ex = sx + math.cos(ang) * branch_len
                    ey = sy + math.sin(ang) * branch_len
                    draw.line([(sx, sy), (ex, ey)], fill=lerp_col(0.25 + 0.35 * frac), width=3)

                    # 沿每根侧枝密植细碎松针束
                    n_needles = max(10, int(branch_len * 0.42))
                    for ni in range(n_needles):
                        nf = ni / max(1, n_needles - 1)
                        px = sx + (ex - sx) * nf
                        py = sy + (ey - sy) * nf
                        n_len = float(rng.uniform(11.0, 20.0)) * (1.15 - 0.45 * nf)
                        for nside in (-1.0, 1.0):
                            n_ang = ang + nside * float(rng.uniform(0.42, 0.88))
                            nx = px + math.cos(n_ang) * n_len
                            ny = py + math.sin(n_ang) * n_len
                            # 梢端与外缘明度明显提亮，彻底告别黑帽死暗
                            bright = 0.28 + 0.42 * frac + 0.30 * nf
                            draw.line([(px, py), (nx, ny)], fill=lerp_col(bright, 0.10), width=3)

            # 顶部中心补充明亮新梢松针簇
            for ni in range(28):
                ang = -math.pi * 0.5 + float(rng.uniform(-0.85, 0.85))
                r_l = float(rng.uniform(12.0, 30.0))
                px = tx + float(rng.uniform(-10, 10))
                py = ty + float(rng.uniform(0, 22))
                draw.line([(px, py), (px + math.cos(ang) * r_l, py + math.sin(ang) * r_l)], fill=lerp_col(0.82, 0.12), width=3)

        elif kind == "shrub":
            # 野灌木碎叶枝丛：多向放射细枝 + 340 片细小圆润嫩绿/橄榄绿叶片，边缘蓬松透气
            twig_col = tuple(int(np.clip(v * 0.95, 30, 220)) for v in bark_rgb) + (255,)
            for b in range(7):
                ang = (b / 7.0) * 2.0 * math.pi + float(rng.uniform(-0.25, 0.25))
                r_b = float(rng.uniform(46.0, 74.0))
                draw.line([(cx, cy + 12), (cx + math.cos(ang) * r_b, cy + math.sin(ang) * r_b * 0.9)], fill=twig_col, width=3)

            # 3 个偏心小叶瓣核心，塑造不规则蓬松灌木团（不画圆盘）
            sub_centers = [
                (cx, cy, 74.0),
                (cx - 28, cy + 10, 56.0),
                (cx + 28, cy + 6, 56.0),
                (cx + float(rng.uniform(-14, 14)), cy - 26, 54.0),
            ]
            # 细腻融合三层画法：减小明暗跳变、缩小单叶尺寸，塑造真实浓密细碎灌木叶感（绝无斑点感）
            for pass_idx, (n_leaves, b_lo, b_hi, sz_mul) in enumerate([
                (145, 0.18, 0.46, 1.04),
                (195, 0.36, 0.68, 0.90),
                (140, 0.55, 0.84, 0.78),
            ]):
                for _ in range(n_leaves):
                    scx, scy, srad = sub_centers[int(rng.integers(0, len(sub_centers)))]
                    ang = float(rng.uniform(0, 2.0 * math.pi))
                    rr = float((rng.uniform(0.0, 1.0) ** 0.62) * srad)
                    lx = np.clip(scx + math.cos(ang) * rr, x0 + 12, x1 - 12)
                    ly = np.clip(scy + math.sin(ang) * rr * 1.05, y0 + 12, y1 - 12)
                    leaf_rx = float(rng.uniform(6.8, 12.0)) * sz_mul
                    leaf_ry = float(rng.uniform(4.2, 7.6)) * sz_mul
                    l_ang = ang + float(rng.uniform(-0.85, 0.85))
                    top_boost = np.clip(0.5 - (ly - cy) / h, -0.18, 0.20)
                    bright = float(rng.uniform(b_lo, b_hi)) + top_boost * 0.24
                    _draw_rotated_leaf(draw, lx, ly, leaf_rx, leaf_ry, l_ang, lerp_col(bright, 0.06))

        else:
            # 阔叶乔木枝叶簇：分叉木枝骨架 + 5 个偏心蓬松叶簇 + 520 片细密融合尖叶，写实浓郁无斑块感
            twig_col = tuple(int(np.clip(v * 0.88, 25, 215)) for v in bark_rgb) + (255,)
            for b in range(5):
                ang = -math.pi * 0.5 + (b - 2) * 0.55 + float(rng.uniform(-0.18, 0.18))
                r_b = float(rng.uniform(52.0, 78.0))
                draw.line([(cx, cy + 28), (cx + math.cos(ang) * r_b, cy + 28 + math.sin(ang) * r_b)], fill=twig_col, width=4)

            sub_centers = [
                (cx, cy - 6, 76.0),
                (cx - 32, cy + 14, 58.0),
                (cx + 32, cy + 10, 58.0),
                (cx + float(rng.uniform(-18, 18)), cy - 34, 56.0),
                (cx + float(rng.uniform(-20, 20)), cy + 32, 52.0),
            ]
            for pass_idx, (n_leaves, b_lo, b_hi, sz_mul) in enumerate([
                (165, 0.16, 0.45, 1.05),
                (215, 0.36, 0.68, 0.90),
                (145, 0.54, 0.84, 0.76),
            ]):
                for _ in range(n_leaves):
                    scx, scy, srad = sub_centers[int(rng.integers(0, len(sub_centers)))]
                    ang = float(rng.uniform(0, 2.0 * math.pi))
                    rr = float((rng.uniform(0.0, 1.0) ** 0.60) * srad)
                    lx = np.clip(scx + math.cos(ang) * rr, x0 + 12, x1 - 12)
                    ly = np.clip(scy + math.sin(ang) * rr * 1.06, y0 + 12, y1 - 12)
                    leaf_rx = float(rng.uniform(7.5, 13.5)) * sz_mul
                    leaf_ry = float(rng.uniform(4.5, 8.0)) * sz_mul
                    l_ang = ang + float(rng.uniform(-0.9, 0.9))
                    top_boost = np.clip(0.5 - (ly - cy) / h, -0.18, 0.22)
                    bright = float(rng.uniform(b_lo, b_hi)) + top_boost * 0.26
                    _draw_rotated_leaf(draw, lx, ly, leaf_rx, leaf_ry, l_ang, lerp_col(bright, 0.06))

    return img


def generate_opaque_mid_foliage_texture(palette: dict, kind: str, size: int = 256, seed: int = 42) -> Image.Image:
    """
    生成 256x256 全不透明中景树冠肌理贴图 (Alpha=255)，彻底消除中景树木 Mipmap 透明黑渗问题。
    """
    rng = np.random.default_rng(seed)
    base_rgb = np.array(palette["base"], dtype=np.float32) * 255.0
    light_rgb = np.array(palette["light"], dtype=np.float32) * 255.0
    dark_rgb = np.array(palette["dark"], dtype=np.float32) * 255.0

    # 用略微提亮的基地色填满不透明画布 (Alpha=255)
    bg_fill = tuple(int(np.clip(v * 0.95, 20, 240)) for v in base_rgb) + (255,)
    img = Image.new("RGBA", (size, size), bg_fill)
    draw = ImageDraw.Draw(img)

    def lerp_col(t: float, jitter: float = 0.08) -> tuple[int, int, int, int]:
        tt = float(np.clip(t + rng.uniform(-jitter, jitter), 0.0, 1.0))
        if tt < 0.45:
            k = tt / 0.45
            arr = dark_rgb * (1.0 - k) + base_rgb * k
        else:
            k = (tt - 0.45) / 0.55
            arr = base_rgb * (1.0 - k) + light_rgb * k
        return (int(np.clip(arr[0], 0, 255)), int(np.clip(arr[1], 0, 255)), int(np.clip(arr[2], 0, 255)), 255)

    if kind == "conifer":
        # 多层次纵向与斜向交叠松针簇笔触，上部稍提亮、下部保持饱满翠绿
        for layer_idx, (count, b_lo, b_hi, len_lo, len_hi, w_px) in enumerate([
            (320, 0.22, 0.52, 12.0, 24.0, 4),
            (360, 0.42, 0.76, 10.0, 20.0, 3),
            (240, 0.62, 0.92, 8.0, 16.0, 3),
        ]):
            for _ in range(count):
                px = float(rng.uniform(0, size))
                py = float(rng.uniform(0, size))
                v_boost = float(np.clip(1.0 - py / size, 0.0, 1.0)) * 0.16
                ang = -math.pi * 0.5 + float(rng.uniform(-0.95, 0.95))
                rl = float(rng.uniform(len_lo, len_hi))
                col = lerp_col(float(rng.uniform(b_lo, b_hi)) + v_boost, 0.08)
                draw.line([(px, py), (px + math.cos(ang) * rl, py + math.sin(ang) * rl)], fill=col, width=w_px)
    else:
        # 阔叶/灌木全覆盖密集碎叶肌理（0 透明孔洞）
        for count, b_lo, b_hi, rx_lo, rx_hi in [
            (340, 0.20, 0.52, 9.0, 16.0),
            (380, 0.42, 0.78, 7.0, 13.0),
            (240, 0.60, 0.94, 5.5, 10.5),
        ]:
            for _ in range(count):
                lx = float(rng.uniform(0, size))
                ly = float(rng.uniform(0, size))
                rx = float(rng.uniform(rx_lo, rx_hi))
                ry = rx * float(rng.uniform(0.55, 0.80))
                ang = float(rng.uniform(0, math.pi * 2.0))
                col = lerp_col(float(rng.uniform(b_lo, b_hi)), 0.08)
                _draw_rotated_leaf(draw, lx, ly, rx, ry, ang, col)
    return img


def _ensure_outward_winding(pos_arr: np.ndarray, uv_arr: np.ndarray, center: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """逐三角形检验法线绕向，内翻三角形自动对调顶点序以保证 double_sided=False 时无内翻黑面。"""
    p = pos_arr.copy()
    u = uv_arr.copy()
    for i in range(0, len(p), 3):
        p0, p1, p2 = p[i], p[i + 1], p[i + 2]
        n = np.cross(p1 - p0, p2 - p0)
        c = (p0 + p1 + p2) / 3.0 - center
        if float(np.dot(n, c)) < 0.0:
            p[i + 1], p[i + 2] = p2.copy(), p1.copy()
            u1_copy = u[i + 1].copy()
            u[i + 1] = u[i + 2]
            u[i + 2] = u1_copy
    return p, u


def make_solid_conifer_mid_proxy(
    crown_center: np.ndarray,
    b_min: np.ndarray,
    b_max: np.ndarray,
    seed: int = 101,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    构建全不透明实心微缩层叠松塔中景云杉（3 层 6 棱柔拱锥冠，共 54 面），
    轮廓与近景层叠云杉一致，彻底告别 460m 透明交叉卡发黑糊块问题。
    """
    rng = np.random.default_rng(seed)
    size = b_max - b_min
    height = float(size[1])
    max_r = float(max(size[0], size[2]) * 0.46)
    cx, cz = float(crown_center[0]), float(crown_center[2])

    tiers = [
        # (y_bot_frac, y_top_frac, r_bot_frac, r_mid_frac, sides, yaw_offset)
        (0.14, 0.56, 1.00, 0.62, 6, 0.0),
        (0.38, 0.78, 0.78, 0.48, 6, 0.52),
        (0.62, 0.99, 0.50, 0.28, 6, 1.04),
    ]

    all_pos, all_uv, centers_list = [], [], []
    for ti, (yb_f, yt_f, rb_f, rm_f, sides, yaw0) in enumerate(tiers):
        yb = float(b_min[1] + height * yb_f)
        ym = float(b_min[1] + height * (yb_f * 0.45 + yt_f * 0.55))
        yt = float(b_min[1] + height * yt_f)
        p_top = np.array([cx, yt, cz], dtype=np.float32)
        p_bot = np.array([cx, yb + height * 0.02, cz], dtype=np.float32)
        layer_center = np.array([cx, (yb + yt) * 0.5, cz], dtype=np.float32)
        centers_list.append(layer_center)

        ring_b = []
        ring_m = []
        for si in range(sides):
            ang = yaw0 + (si / sides) * 2.0 * math.pi
            jitter_b = float(rng.uniform(0.90, 1.12))
            jitter_m = float(rng.uniform(0.90, 1.12))
            rb = max_r * rb_f * jitter_b
            rm = max_r * rm_f * jitter_m
            ring_b.append(np.array([cx + math.cos(ang) * rb, yb, cz + math.sin(ang) * rb], dtype=np.float32))
            ring_m.append(np.array([cx + math.cos(ang + 0.16) * rm, ym, cz + math.sin(ang + 0.16) * rm], dtype=np.float32))

        t_pos, t_uv = [], []
        for si in range(sides):
            nxt = (si + 1) % sides
            u0 = si / sides
            u1 = (si + 1) / sides
            # 上锥冠
            t_pos.extend([ring_m[si], p_top, ring_m[nxt]])
            t_uv.extend([[u0, 0.52], [(u0 + u1) * 0.5, 0.05], [u1, 0.52]])
            # 下腰坡（斜裙外张，适合 48° 俯视剪影）
            t_pos.extend([ring_b[si], ring_m[si], ring_m[nxt], ring_b[si], ring_m[nxt], ring_b[nxt]])
            t_uv.extend([[u0, 0.94], [u0, 0.52], [u1, 0.52], [u0, 0.94], [u1, 0.52], [u1, 0.94]])
            # 仅最底层封底防止仰视穿帮
            if ti == 0:
                t_pos.extend([ring_b[nxt], p_bot, ring_b[si]])
                t_uv.extend([[u1, 0.94], [(u0 + u1) * 0.5, 0.98], [u0, 0.94]])

        ep, eu = _ensure_outward_winding(np.array(t_pos, dtype=np.float32), np.array(t_uv, dtype=np.float32), layer_center)
        all_pos.append(ep)
        all_uv.append(eu)

    return np.vstack(all_pos), np.vstack(all_uv), np.array(centers_list, dtype=np.float32)


def make_solid_lobe_mid_proxy(
    lobes: list[tuple[np.ndarray, float, float, float]],
    seed: int = 201,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    构建多瓣闭合实心圆冠体中景代理（每瓣 5 经度 × 3 纬带 = 20 面柔和有机叶团），
    保证 100% 实心无透明洞且表面呈微缩有机蓬松起伏。
    """
    rng = np.random.default_rng(seed)
    all_pos, all_uv, centers_list = [], [], []
    sides = 5

    for li, (lc, rx, ry, rz) in enumerate(lobes):
        centers_list.append(lc)
        p_south = lc + np.array([0.0, -ry * 0.82, 0.0], dtype=np.float32)
        p_north = lc + np.array([0.0, ry * 0.96, 0.0], dtype=np.float32)
        ring_lo, ring_hi = [], []
        yaw_off = li * 0.65 + float(rng.uniform(-0.2, 0.2))
        for si in range(sides):
            a = yaw_off + (si / sides) * 2.0 * math.pi
            j1 = float(rng.uniform(0.90, 1.12))
            j2 = float(rng.uniform(0.88, 1.10))
            ring_lo.append(lc + np.array([math.cos(a) * rx * 0.88 * j1, -ry * 0.28, math.sin(a) * rz * 0.88 * j1], dtype=np.float32))
            ring_hi.append(lc + np.array([math.cos(a + 0.22) * rx * 0.78 * j2, ry * 0.36, math.sin(a + 0.22) * rz * 0.78 * j2], dtype=np.float32))

        l_pos, l_uv = [], []
        for si in range(sides):
            nxt = (si + 1) % sides
            u0 = si / sides
            u1 = (si + 1) / sides
            uc = (u0 + u1) * 0.5
            # 北极顶帽
            l_pos.extend([ring_hi[si], p_north, ring_hi[nxt]])
            l_uv.extend([[u0, 0.32], [uc, 0.04], [u1, 0.32]])
            # 赤道腰带
            l_pos.extend([ring_lo[si], ring_hi[si], ring_hi[nxt], ring_lo[si], ring_hi[nxt], ring_lo[nxt]])
            l_uv.extend([[u0, 0.72], [u0, 0.32], [u1, 0.32], [u0, 0.72], [u1, 0.32], [u1, 0.72]])
            # 南极底盖
            l_pos.extend([ring_lo[nxt], p_south, ring_lo[si]])
            l_uv.extend([[u1, 0.72], [uc, 0.96], [u0, 0.72]])

        ep, eu = _ensure_outward_winding(np.array(l_pos, dtype=np.float32), np.array(l_uv, dtype=np.float32), lc)
        all_pos.append(ep)
        all_uv.append(eu)

    return np.vstack(all_pos), np.vstack(all_uv), np.array(centers_list, dtype=np.float32)


def compute_spherical_normals(
    positions: np.ndarray,
    crown_center: np.ndarray,
    crown_radius: float,
    cluster_centers: np.ndarray | None = None,
    up_bias: float = 0.42,
) -> np.ndarray:
    """将树叶顶点法线重写为从树冠中心与局部叶团中心向外上方辐射的球形法线，消除交叉卡片黑十字阴影。"""
    diff_global = (positions - crown_center[None, :]) / max(crown_radius, 0.5)
    norm_global = diff_global / np.maximum(np.linalg.norm(diff_global, axis=1, keepdims=True), 1e-6)

    if cluster_centers is not None and len(cluster_centers) > 0:
        dists = np.linalg.norm(positions[:, None, :] - cluster_centers[None, :, :], axis=2)
        nearest = np.argmin(dists, axis=1)
        diff_local = positions - cluster_centers[nearest]
        norm_local = diff_local / np.maximum(np.linalg.norm(diff_local, axis=1, keepdims=True), 1e-6)
        blended = norm_global * 0.65 + norm_local * 0.35
    else:
        blended = norm_global

    blended[:, 1] += up_bias
    blended /= np.maximum(np.linalg.norm(blended, axis=1, keepdims=True), 1e-6)
    return blended.astype(np.float32)


def make_tapered_trunk(
    base_center: np.ndarray,
    height: float,
    r_bottom: float,
    r_top: float,
    sides: int = 7,
    with_branches: bool = True,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """构建带可选分叉主枝的锥柱状低模树干（采样左侧树皮通栏 UV u in [0.02..0.15]）。"""
    pos_list = []
    norm_list = []
    uv_list = []

    def add_cylinder_segment(p0, p1, rb, rt, seg_sides):
        axis = p1 - p0
        length = float(np.linalg.norm(axis))
        if length < 1e-4:
            return
        up = axis / length
        ref = np.array([1.0, 0.0, 0.0], dtype=np.float32) if abs(up[0]) < 0.8 else np.array([0.0, 0.0, 1.0], dtype=np.float32)
        tangent = np.cross(up, ref)
        tangent /= np.linalg.norm(tangent)
        bitangent = np.cross(up, tangent)

        angs = np.linspace(0, 2 * math.pi, seg_sides, endpoint=False)
        for i in range(seg_sides):
            a0 = angs[i]
            a1 = angs[(i + 1) % seg_sides]
            n0 = math.cos(a0) * tangent + math.sin(a0) * bitangent
            n1 = math.cos(a1) * tangent + math.sin(a1) * bitangent
            v00 = p0 + n0 * rb
            v10 = p0 + n1 * rb
            v01 = p1 + n0 * rt
            v11 = p1 + n1 * rt
            u0 = 0.02 + (i / seg_sides) * 0.13
            u1 = 0.02 + ((i + 1) / seg_sides) * 0.13
            pos_list.extend([v00, v10, v11, v00, v11, v01])
            norm_list.extend([n0, n1, n1, n0, n1, n0])
            uv_list.extend([
                [u0, 0.06], [u1, 0.06], [u1, 0.90],
                [u0, 0.06], [u1, 0.90], [u0, 0.90],
            ])

    # 微幅自然弯曲主干
    p_mid = base_center + np.array([height * 0.015, height * 0.48, -height * 0.012], dtype=np.float32)
    p_top = base_center + np.array([-height * 0.010, height * 0.88, height * 0.014], dtype=np.float32)
    add_cylinder_segment(base_center, p_mid, r_bottom, (r_bottom + r_top) * 0.56, sides)
    add_cylinder_segment(p_mid, p_top, (r_bottom + r_top) * 0.56, r_top, max(5, sides - 1))

    if with_branches:
        # 4 根向斜上方伸展的主分叉树枝
        for b_idx in range(4):
            ang = b_idx * (2 * math.pi / 4.0) + 0.35 + (b_idx % 2) * 0.22
            b_start = base_center + np.array([0.0, height * (0.42 + 0.09 * b_idx), 0.0], dtype=np.float32)
            b_end = b_start + np.array([
                math.cos(ang) * height * 0.26,
                height * 0.24,
                math.sin(ang) * height * 0.26,
            ], dtype=np.float32)
            add_cylinder_segment(b_start, b_end, r_bottom * 0.42, r_top * 0.35, 4)

    return (
        np.array(pos_list, dtype=np.float32),
        np.array(norm_list, dtype=np.float32),
        np.array(uv_list, dtype=np.float32),
    )


def _add_curved_foliage_card(
    pos_list: list,
    uv_list: list,
    center: np.ndarray,
    right_vec: np.ndarray,
    up_vec: np.ndarray,
    normal_vec: np.ndarray,
    half_w: float,
    half_h: float,
    arch_depth: float,
    uv_rect: tuple[float, float, float, float],
) -> None:
    """
    添加一个 4 三角面（中心隆起、四周自然微弯）的立体拱形枝叶卡片，
    在 48° 俯视角与低角度下均饱满蓬松、无平直硬纸片折痕。
    """
    u0, v0, u1, v1 = uv_rect
    uc, vc = (u0 + u1) * 0.5, (v0 + v1) * 0.5

    p_c = center + normal_vec * arch_depth
    p_bl = center - right_vec * half_w - up_vec * half_h - normal_vec * (arch_depth * 0.28)
    p_br = center + right_vec * half_w - up_vec * half_h - normal_vec * (arch_depth * 0.28)
    p_tr = center + right_vec * half_w + up_vec * half_h - normal_vec * (arch_depth * 0.28)
    p_tl = center - right_vec * half_w + up_vec * half_h - normal_vec * (arch_depth * 0.28)

    uv_c = [uc, vc]
    uv_bl = [u0, v1]
    uv_br = [u1, v1]
    uv_tr = [u1, v0]
    uv_tl = [u0, v0]

    pos_list.extend([
        p_c, p_bl, p_br,
        p_c, p_br, p_tr,
        p_c, p_tr, p_tl,
        p_c, p_tl, p_bl,
    ])
    uv_list.extend([
        uv_c, uv_bl, uv_br,
        uv_c, uv_br, uv_tr,
        uv_c, uv_tr, uv_tl,
        uv_c, uv_tl, uv_bl,
    ])


def make_broadleaf_lobe_cards(
    center: np.ndarray,
    rx: float,
    ry: float,
    rz: float,
    seed: int,
    uv_rects: list[tuple[float, float, float, float]],
    side_cards: int = 5,
) -> tuple[np.ndarray, np.ndarray]:
    """
    为一个次级树冠球心构建无棱角、蓬松自然的弧面碎叶簇集合：
    - 1 片面向正上方微拱的宽叶冠盖 (4 面)，确保 48° 俯视无十字缝；
    - side_cards 片环绕球心呈 35°–68° 外倾的拱形碎叶卡 (每片 4 面)，塑造多瓣有机树冠轮廓。
    """
    rng = np.random.default_rng(seed)
    pos_list = []
    uv_list = []

    # 1. 顶部俯视微拱叶盖（确保 cross(r_top, u_top) 与 n_top 同向朝上）
    yaw0 = float(rng.uniform(0, 2.0 * math.pi))
    c0, s0 = math.cos(yaw0), math.sin(yaw0)
    r_top = np.array([c0, 0.0, s0], dtype=np.float32)
    u_top = np.array([s0, 0.0, -c0], dtype=np.float32)
    n_top = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    _add_curved_foliage_card(
        pos_list, uv_list,
        center + np.array([0.0, ry * 0.42, 0.0], dtype=np.float32),
        r_top, u_top, n_top,
        half_w=rx * 0.92, half_h=rz * 0.92, arch_depth=ry * 0.36,
        uv_rect=uv_rects[int(rng.integers(0, len(uv_rects)))],
    )

    # 2. 环绕外倾的立体碎叶枝卡片
    for i in range(side_cards):
        ang = yaw0 + (i / max(1, side_cards)) * 2.0 * math.pi + float(rng.uniform(-0.22, 0.22))
        ca, sa = math.cos(ang), math.sin(ang)
        out_dir = np.array([ca, 0.0, sa], dtype=np.float32)
        right_v = np.array([-sa, 0.0, ca], dtype=np.float32)
        tilt = float(rng.uniform(0.45, 0.78))  # 面向外上方 28°–52°
        norm_v = np.array([ca * math.cos(tilt), math.sin(tilt), sa * math.cos(tilt)], dtype=np.float32)
        norm_v /= np.linalg.norm(norm_v)
        up_v = np.cross(norm_v, right_v)
        up_v /= np.linalg.norm(up_v)

        offset = np.array([
            ca * rx * float(rng.uniform(0.28, 0.48)),
            ry * float(rng.uniform(-0.18, 0.26)),
            sa * rz * float(rng.uniform(0.28, 0.48)),
        ], dtype=np.float32)
        hw = float(max(rx, rz) * rng.uniform(0.74, 0.96))
        hh = float(ry * rng.uniform(0.84, 1.08))
        _add_curved_foliage_card(
            pos_list, uv_list,
            center + offset,
            right_v, up_v, norm_v,
            half_w=hw, half_h=hh, arch_depth=min(rx, ry) * 0.28,
            uv_rect=uv_rects[(i + seed) % len(uv_rects)],
        )

    return np.array(pos_list, dtype=np.float32), np.array(uv_list, dtype=np.float32)


def make_spruce_bough_crown(
    crown_center: np.ndarray,
    b_min: np.ndarray,
    b_max: np.ndarray,
    uv_rects: list[tuple[float, float, float, float]],
    seed: int = 101,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    构建针叶云杉的 8 轮螺旋错位 3D 拱形辐射松枝羽片冠层 + 顶部蓬松交叉松针尖梢（约 520–600 面）：
    - 彻底告别实心“黑帽圣诞树”圆锥，每根松枝由 2 段弯折（中段微垂、梢端轻翘）的立体双坡 V 拱羽片构成；
    - 枝基采样松枝图集底部，枝梢采样图集顶部明亮新针，层次错落自然。
    """
    rng = np.random.default_rng(seed)
    size = b_max - b_min
    height = float(size[1])
    max_r = float(max(size[0], size[2]) * 0.50)
    cx, cz = float(crown_center[0]), float(crown_center[2])

    pos_list = []
    uv_list = []
    centers_list = []

    # 7 轮从下到上螺旋递减的辐射松枝层（每根松枝 6 三角面立体双坡 V 拱，单棵云杉 ~280 面，兼具立体蓬松度与极致性能）
    tier_specs = [
        # (y_frac, r_frac, n_boughs, droop_frac, width_scale)
        (0.18, 1.04, 7, 0.085, 0.64),
        (0.30, 0.92, 7, 0.078, 0.62),
        (0.42, 0.80, 6, 0.070, 0.60),
        (0.54, 0.67, 6, 0.058, 0.58),
        (0.66, 0.52, 5, 0.046, 0.56),
        (0.77, 0.37, 5, 0.034, 0.55),
        (0.87, 0.23, 4, 0.022, 0.52),
    ]

    for t_idx, (yf, rf, n_boughs, droop_f, w_scale) in enumerate(tier_specs):
        base_y = float(b_min[1] + height * yf)
        centers_list.append(np.array([cx, base_y, cz], dtype=np.float32))
        tier_yaw = t_idx * 0.73 + float(rng.uniform(-0.12, 0.12))

        for b_idx in range(n_boughs):
            ang = tier_yaw + (b_idx / n_boughs) * 2.0 * math.pi + float(rng.uniform(-0.18, 0.18))
            ca, sa = math.cos(ang), math.sin(ang)
            side_v = np.array([-sa, 0.0, ca], dtype=np.float32)

            b_len = max_r * rf * float(rng.uniform(0.86, 1.15))
            b_half_w = max(0.48, b_len * w_scale * float(rng.uniform(0.92, 1.12)))
            y_jit = float(rng.uniform(-0.022, 0.022)) * height
            droop = height * droop_f * float(rng.uniform(0.82, 1.18))
            arch_h = max(0.18, b_half_w * 0.24)  # 羽片中脊隆起，俯视立体无纸片感

            u0, v0, u1, v1 = uv_rects[(t_idx + b_idx) % len(uv_rects)]
            uc = (u0 + u1) * 0.5
            vm = (v0 + v1) * 0.48

            # 枝基、中脊微垂隆起、左右羽翼、微翘枝梢
            p_base = np.array([cx + ca * (b_len * 0.04), base_y + y_jit, cz + sa * (b_len * 0.04)], dtype=np.float32)
            p_mid_c = np.array([cx + ca * (b_len * 0.54), base_y + y_jit - droop * 0.68 + arch_h, cz + sa * (b_len * 0.54)], dtype=np.float32)
            p_mid_l = p_mid_c - side_v * b_half_w - np.array([0.0, arch_h * 1.15, 0.0], dtype=np.float32)
            p_mid_r = p_mid_c + side_v * b_half_w - np.array([0.0, arch_h * 1.15, 0.0], dtype=np.float32)
            p_tip = np.array([cx + ca * b_len, base_y + y_jit - droop * 0.72 + arch_h * 0.65, cz + sa * b_len], dtype=np.float32)
            p_tip_l = p_tip - side_v * (b_half_w * 0.38) - np.array([0.0, arch_h * 0.45, 0.0], dtype=np.float32)
            p_tip_r = p_tip + side_v * (b_half_w * 0.38) - np.array([0.0, arch_h * 0.45, 0.0], dtype=np.float32)

            # 每根松枝 6 个立体三角形（基段双斜翼 2 面 + 梢段双斜翼 4 面）
            pos_list.extend([
                p_base, p_mid_l, p_mid_c,
                p_base, p_mid_c, p_mid_r,
                p_mid_c, p_mid_l, p_tip_l,
                p_mid_c, p_tip_l, p_tip,
                p_mid_c, p_tip, p_tip_r,
                p_mid_c, p_tip_r, p_mid_r,
            ])
            uv_list.extend([
                [uc, v1], [u0, vm], [uc, vm],
                [uc, v1], [uc, vm], [u1, vm],
                [uc, vm], [u0, vm], [u0, v0],
                [uc, vm], [u0, v0], [uc, v0],
                [uc, vm], [uc, v0], [u1, v0],
                [uc, vm], [u1, v0], [u1, vm],
            ])

    # 顶部 3 片交叉竖立的明亮松针尖梢（h = 0.84 ~ 1.00，自然松树顶梢，绝非黑帽圆锥）
    top_y0 = float(b_min[1] + height * 0.82)
    top_y1 = float(b_min[1] + height * 1.00)
    top_w = max(0.55, max_r * 0.24)
    for k in range(3):
        ang = k * (math.pi / 3.0) + 0.2
        ca, sa = math.cos(ang), math.sin(ang)
        u0, v0, u1, v1 = uv_rects[k % len(uv_rects)]
        p_bl = np.array([cx - ca * top_w, top_y0, cz - sa * top_w], dtype=np.float32)
        p_br = np.array([cx + ca * top_w, top_y0, cz + sa * top_w], dtype=np.float32)
        p_tr = np.array([cx + ca * (top_w * 0.55), top_y1, cz + sa * (top_w * 0.55)], dtype=np.float32)
        p_tl = np.array([cx - ca * (top_w * 0.55), top_y1, cz - sa * (top_w * 0.55)], dtype=np.float32)
        pos_list.extend([p_bl, p_br, p_tr, p_bl, p_tr, p_tl])
        uv_list.extend([[u0, v1], [u1, v1], [u1, v0], [u0, v1], [u1, v0], [u0, v0]])

    return (
        np.array(pos_list, dtype=np.float32),
        np.array(uv_list, dtype=np.float32),
        np.array(centers_list, dtype=np.float32),
    )


def build_tree_prototypes(b2_dir: Path, idx_meta: dict, r14_meta: dict) -> list[dict]:
    near_raw_dir = b2_dir / "raw_near"
    mid_raw_dir = b2_dir / "raw_mid"
    near_out_dir = b2_dir / "near-trees"
    mid_out_dir = b2_dir / "mid-proxies"
    for d in (near_raw_dir, mid_raw_dir, near_out_dir, mid_out_dir):
        d.mkdir(parents=True, exist_ok=True)
    (near_raw_dir / "贴图").mkdir(exist_ok=True)
    (mid_raw_dir / "贴图").mkdir(exist_ok=True)

    r14_map = {p["id"]: p for p in r14_meta["prototypes"]}
    tree_protos = idx_meta["prototypes"][125:133]
    records = []

    # 4 个 204x256 独立枝叶格子的 UV 矩形 (u0, v0, u1, v1)
    uv_rects = [
        (106.0 / 512.0, 6.0 / 512.0, 302.0 / 512.0, 250.0 / 512.0),
        (310.0 / 512.0, 6.0 / 512.0, 506.0 / 512.0, 250.0 / 512.0),
        (106.0 / 512.0, 262.0 / 512.0, 302.0 / 512.0, 506.0 / 512.0),
        (310.0 / 512.0, 262.0 / 512.0, 506.0 / 512.0, 506.0 / 512.0),
    ]

    for local_idx, p_entry in enumerate(tree_protos):
        full_id = p_entry["id"]
        short_id = full_id.split(":")[1]
        r14_p = r14_map[full_id]
        name = r14_p["name"]
        is_shrub = r14_p.get("vegetation_kind") == "shrub" or "bush" in name.lower()
        is_spruce = "spruce" in name.lower()
        kind = "shrub" if is_shrub else ("conifer" if is_spruce else "broadleaf")

        src_dir = BATCH_DIR / "独立原型/full-r06" / short_id
        b_min, b_max = read_uncompressed_glb_bounds(src_dir / "原型.glb")
        size = b_max - b_min
        height = float(size[1])
        crown_center = np.array([
            float((b_min[0] + b_max[0]) * 0.5),
            float(b_min[1] + height * (0.45 if is_shrub else 0.56)),
            float((b_min[2] + b_max[2]) * 0.5),
        ], dtype=np.float32)
        crown_radius = float(max(size[0], size[1], size[2]) * 0.48)

        palette = extract_species_palette(src_dir, name)
        atlas_im = generate_species_foliage_atlas(palette, kind=kind, size=512, seed=2000 + local_idx * 53)

        near_tex_rel = f"贴图/{short_id}.png"
        atlas_im.save(near_raw_dir / near_tex_rel)

        mid_tex_rel = f"贴图/{short_id}_mid.png"
        mid_opaque_im = generate_opaque_mid_foliage_texture(palette, kind=kind, size=256, seed=2600 + local_idx * 71)
        mid_opaque_im.save(mid_raw_dir / mid_tex_rel)

        base_center = np.array([crown_center[0], float(max(0.0, b_min[1])), crown_center[2]], dtype=np.float32)

        if is_spruce:
            # 针叶云杉：笔直主干 + 8 轮螺旋错位 3D 立体松枝羽片 + 顶部交叉松针尖梢
            trunk_p, trunk_n, trunk_uv = make_tapered_trunk(
                base_center,
                height=height * 0.90,
                r_bottom=float(max(size[0], size[2]) * 0.052),
                r_top=float(max(size[0], size[2]) * 0.012),
                sides=6,
                with_branches=False,
            )
            leaf_p, leaf_uv, cluster_centers = make_spruce_bough_crown(
                crown_center, b_min, b_max, uv_rects, seed=3100 + local_idx * 17
            )
            leaf_n = compute_spherical_normals(leaf_p, crown_center, crown_radius, cluster_centers, up_bias=0.62)
            near_pos = np.vstack([trunk_p, leaf_p])
            near_norm = np.vstack([trunk_n, leaf_n])
            near_uv = np.vstack([trunk_uv, leaf_uv])

            # 中景云杉代理：全不透明 3 层实心微缩松塔体 (约 60 面，0 透明洞，0 Mipmap 黑渗)
            mid_pos, mid_uv, mid_centers = make_solid_conifer_mid_proxy(
                crown_center, b_min, b_max, seed=3700 + local_idx * 23
            )
            mid_norm = compute_spherical_normals(mid_pos, crown_center, crown_radius, mid_centers, up_bias=0.68)

        elif is_shrub:
            # 灌木丛：4 组高低错落、自然散开的低矮野灌木碎叶枝团（无圆盘硬棱、约 112 面）
            lobe_pos_list, lobe_uv_list, lobe_centers = [], [], []
            rx_all = float(size[0] * 0.48)
            rz_all = float(size[2] * 0.48)
            shrub_lobes = [
                (0.04, 0.56, -0.03, 0.64, 0.44, 5),
                (0.38, 0.42, 0.22, 0.52, 0.36, 4),
                (-0.36, 0.40, 0.28, 0.50, 0.35, 4),
                (-0.08, 0.38, -0.38, 0.54, 0.37, 4),
            ]
            for li, (dxf, yf, dzf, sxz, sy, sc) in enumerate(shrub_lobes):
                lc = np.array([
                    crown_center[0] + dxf * rx_all,
                    float(b_min[1] + yf * height),
                    crown_center[2] + dzf * rz_all,
                ], dtype=np.float32)
                lobe_centers.append(lc)
                lp, luv = make_broadleaf_lobe_cards(
                    lc, rx_all * sxz, height * sy, rz_all * sxz,
                    seed=4200 + local_idx * 31 + li * 7,
                    uv_rects=uv_rects,
                    side_cards=sc,
                )
                lobe_pos_list.append(lp)
                lobe_uv_list.append(luv)
            near_pos = np.vstack(lobe_pos_list)
            near_pos[:, 1] = np.maximum(near_pos[:, 1], float(b_min[1]) + 0.05)
            near_uv = np.vstack(lobe_uv_list)
            near_norm = compute_spherical_normals(near_pos, crown_center, crown_radius, np.array(lobe_centers, dtype=np.float32), up_bias=0.68)

            # 中景灌木代理：2 瓣全不透明柔和微缩圆冠 (40 面)
            mid_shrub_lobes = [
                (np.array([crown_center[0] - rx_all * 0.20, float(b_min[1] + height * 0.46), crown_center[2] + rz_all * 0.10], dtype=np.float32), rx_all * 0.72, height * 0.44, rz_all * 0.72),
                (np.array([crown_center[0] + rx_all * 0.24, float(b_min[1] + height * 0.40), crown_center[2] - rz_all * 0.14], dtype=np.float32), rx_all * 0.62, height * 0.38, rz_all * 0.62),
            ]
            mid_pos, mid_uv, mid_centers = make_solid_lobe_mid_proxy(mid_shrub_lobes, seed=4800 + local_idx * 19)
            mid_pos[:, 1] = np.maximum(mid_pos[:, 1], float(b_min[1]) + 0.04)
            mid_norm = compute_spherical_normals(mid_pos, crown_center, crown_radius, mid_centers, up_bias=0.70)

        else:
            # 阔叶乔木：可见分叉主干 (约 68 面) + 11 个大小不对称、高低错落的蓬松碎叶枝团 (约 308 面)
            trunk_p, trunk_n, trunk_uv = make_tapered_trunk(
                base_center,
                height=height * 0.64,
                r_bottom=float(max(size[0], size[2]) * 0.064),
                r_top=float(max(size[0], size[2]) * 0.022),
                sides=7,
                with_branches=True,
            )
            rx_all = float(size[0] * 0.48)
            rz_all = float(size[2] * 0.48)
            lobe_configs = [
                # (dx_frac, y_frac, dz_frac, scale_xz, scale_y, side_cards) —— 9 个不对称蓬松次级叶团 (单棵阔叶树 ~248 面)
                (0.05, 0.80, -0.04, 0.56, 0.27, 4),
                (-0.26, 0.74, 0.22, 0.48, 0.24, 4),
                (0.28, 0.71, 0.20, 0.46, 0.23, 4),
                (0.18, 0.67, -0.30, 0.48, 0.24, 4),
                (-0.34, 0.61, -0.22, 0.47, 0.23, 4),
                (0.42, 0.55, 0.02, 0.44, 0.22, 4),
                (-0.42, 0.53, 0.14, 0.44, 0.22, 4),
                (0.06, 0.57, 0.42, 0.45, 0.22, 4),
                (-0.10, 0.51, -0.42, 0.44, 0.21, 4),
            ]
            lobe_pos_list, lobe_uv_list, lobe_centers = [], [], []
            for li, (dxf, yf, dzf, sxz, sy, sc) in enumerate(lobe_configs):
                lc = np.array([
                    crown_center[0] + dxf * rx_all,
                    float(b_min[1] + yf * height),
                    crown_center[2] + dzf * rz_all,
                ], dtype=np.float32)
                lobe_centers.append(lc)
                lp, luv = make_broadleaf_lobe_cards(
                    lc,
                    rx=rx_all * sxz,
                    ry=height * sy,
                    rz=rz_all * sxz,
                    seed=5100 + local_idx * 41 + li * 11,
                    uv_rects=uv_rects,
                    side_cards=sc,
                )
                lobe_pos_list.append(lp)
                lobe_uv_list.append(luv)
            leaf_p = np.vstack(lobe_pos_list)
            leaf_uv = np.vstack(lobe_uv_list)
            leaf_n = compute_spherical_normals(leaf_p, crown_center, crown_radius, np.array(lobe_centers, dtype=np.float32), up_bias=0.58)
            near_pos = np.vstack([trunk_p, leaf_p])
            near_norm = np.vstack([trunk_n, leaf_n])
            near_uv = np.vstack([trunk_uv, leaf_uv])

            # 中景阔叶树代理：3 瓣闭合实心圆冠体 (60 面，无透明镂空黑洞)
            mid_broad_lobes = [
                (np.array([crown_center[0] + rx_all * 0.04, float(b_min[1] + height * 0.72), crown_center[2] - rz_all * 0.03], dtype=np.float32), rx_all * 0.72, height * 0.34, rz_all * 0.72),
                (np.array([crown_center[0] + rx_all * 0.34, float(b_min[1] + height * 0.56), crown_center[2] + rz_all * 0.26], dtype=np.float32), rx_all * 0.58, height * 0.28, rz_all * 0.58),
                (np.array([crown_center[0] - rx_all * 0.34, float(b_min[1] + height * 0.53), crown_center[2] - rz_all * 0.24], dtype=np.float32), rx_all * 0.58, height * 0.28, rz_all * 0.58),
            ]
            mid_pos, mid_uv, mid_centers = make_solid_lobe_mid_proxy(mid_broad_lobes, seed=5900 + local_idx * 17)
            mid_norm = compute_spherical_normals(mid_pos, crown_center, crown_radius, mid_centers, up_bias=0.66)

        near_tri_count = len(near_pos) // 3
        mat_key = f"mat_{short_id}"
        near_mat_dict = {
            mat_key: {
                "name": name,
                "source_key": mat_key,
                "color": [1.0, 1.0, 1.0, 1.0],
                "roughness": 0.90,
                "image": near_tex_rel,
                "alpha_mode": "MASK",
                "alpha_cutoff": 0.32,
                "double_sided": True,
                "render_role": 8,
            }
        }
        near_prim = [{
            "material": mat_key,
            "attributes": {
                "POSITION": near_pos,
                "NORMAL": near_norm,
                "TEXCOORD_0": near_uv,
            },
            "extras": {
                "asset_id": full_id,
                "lod": "near_layered_foliage_v2",
                "crown_center": crown_center.tolist(),
                "crown_radius": round(crown_radius, 3),
            },
        }]
        write_glb(near_raw_dir / f"{short_id}.glb", near_prim, near_mat_dict)

        mid_tri_count = len(mid_pos) // 3
        mid_mat_dict = {
            mat_key: {
                "name": f"{name}_MidProxy",
                "source_key": mat_key,
                "color": [1.0, 1.0, 1.0, 1.0],
                "roughness": 0.90,
                "image": mid_tex_rel,
                "alpha_mode": "OPAQUE",
                "double_sided": False,
                "render_role": 8,
            }
        }
        mid_prim = [{
            "material": mat_key,
            "attributes": {
                "POSITION": mid_pos,
                "NORMAL": mid_norm,
                "TEXCOORD_0": mid_uv,
            },
            "extras": {
                "asset_id": full_id,
                "lod": "mid_volume_proxy_v2",
                "crown_center": crown_center.tolist(),
                "crown_radius": round(crown_radius, 3),
            },
        }]
        write_glb(mid_raw_dir / f"{short_id}.glb", mid_prim, mid_mat_dict)

        records.append({
            "tree_index": local_idx,
            "prototype_index": 125 + local_idx,
            "id": full_id,
            "short_id": short_id,
            "name": name,
            "kind": kind,
            "bounds_min": [round(float(x), 3) for x in b_min],
            "bounds_max": [round(float(x), 3) for x in b_max],
            "crown_center": [round(float(x), 3) for x in crown_center],
            "crown_radius": round(crown_radius, 3),
            "calibrated_rgb": [round(float(x), 4) for x in palette["base"]],
            "near_triangles": near_tri_count,
            "mid_triangles": mid_tri_count,
            "near_file": f"near-trees/{short_id}.glb",
            "mid_file": f"mid-proxies/{short_id}.glb",
            "slot_policy": {
                "active_slot": "procedural_layered_foliage_v2",
                "original_reference_glb": p_entry["glb"],
                "custom_replacement_slot": f"custom-replacements/trees/{short_id}.glb",
            },
        })

    node_opt_script = """
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';

const root = path.resolve(process.argv[1]);
const inDir = path.resolve(process.argv[2]);
const outDir = path.resolve(process.argv[3]);
const texSize = parseInt(process.argv[4], 10);

const modules = path.join(root, 'local-data/tools/网页资源优化/1/node_modules');
const load = p => import(pathToFileURL(path.join(modules, p)).href);
const require = createRequire(path.join(modules, '../package.json'));
const sharp = require('sharp');
const {NodeIO} = await load('@gltf-transform/core/dist/index.js');
const {ALL_EXTENSIONS, EXTMeshoptCompression, KHRTextureBasisu} = await load('@gltf-transform/extensions/dist/index.js');
const {dedup} = await load('@gltf-transform/functions/dist/index.js');
const {MeshoptEncoder, MeshoptDecoder} = await load('meshoptimizer/index.js');
const {encodeToKTX2} = await load('ktx2-encoder/dist/node/index.js');
await Promise.all([MeshoptEncoder.ready, MeshoptDecoder.ready]);

const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({'meshopt.encoder': MeshoptEncoder, 'meshopt.decoder': MeshoptDecoder});
const decoder = async b => {
  const {data, info} = await sharp(b).ensureAlpha().raw().toBuffer({resolveWithObject: true});
  return {data: new Uint8Array(data), width: info.width, height: info.height};
};

for (const name of fs.readdirSync(inDir)) {
  if (!name.endsWith('.glb')) continue;
  const doc = await io.read(path.join(inDir, name));
  await doc.transform(dedup());
  for (const texture of doc.getRoot().listTextures()) {
    const image = texture.getImage();
    const {data: rgbaBuf, info} = await sharp(image)
      .resize(texSize, texSize, {fit: 'fill', kernel: 'lanczos3'})
      .ensureAlpha()
      .raw()
      .toBuffer({resolveWithObject: true});
    const w = info.width, h = info.height;
    const pixels = new Uint8Array(rgbaBuf);
    let sumR = 0, sumG = 0, sumB = 0, fgCnt = 0;
    for (let i = 0; i < pixels.length; i += 4) {
      if (pixels[i + 3] >= 128) {
        sumR += pixels[i]; sumG += pixels[i + 1]; sumB += pixels[i + 2]; fgCnt++;
      }
    }
    const avgR = fgCnt > 0 ? Math.round(sumR / fgCnt) : 78;
    const avgG = fgCnt > 0 ? Math.round(sumG / fgCnt) : 118;
    const avgB = fgCnt > 0 ? Math.round(sumB / fgCnt) : 56;
    const src = new Uint8Array(pixels);
    const filled = new Uint8Array(w * h);
    for (let p = 0; p < w * h; p++) filled[p] = pixels[p * 4 + 3] >= 96 ? 1 : 0;
    const nextPixels = new Uint8Array(pixels);
    const nextFilled = new Uint8Array(filled);
    for (let pass = 0; pass < 4; pass++) {
      for (let y = 0; y < h; y++) {
        for (let x = 0; x < w; x++) {
          const idx = y * w + x;
          if (filled[idx]) continue;
          let sr = 0, sg = 0, sb = 0, c = 0;
          for (let dy = -1; dy <= 1; dy++) {
            const ny = y + dy;
            if (ny < 0 || ny >= h) continue;
            for (let dx = -1; dx <= 1; dx++) {
              const nx = x + dx;
              if (nx < 0 || nx >= w) continue;
              const nIdx = ny * w + nx;
              if (filled[nIdx]) {
                sr += src[nIdx * 4]; sg += src[nIdx * 4 + 1]; sb += src[nIdx * 4 + 2]; c++;
              }
            }
          }
          if (c > 0) {
            nextPixels[idx * 4] = Math.round(sr / c);
            nextPixels[idx * 4 + 1] = Math.round(sg / c);
            nextPixels[idx * 4 + 2] = Math.round(sb / c);
            nextFilled[idx] = 1;
          }
        }
      }
      src.set(nextPixels);
      filled.set(nextFilled);
    }
    for (let p = 0; p < w * h; p++) {
      if (!filled[p]) {
        nextPixels[p * 4] = avgR;
        nextPixels[p * 4 + 1] = avgG;
        nextPixels[p * 4 + 2] = avgB;
      }
    }
    const png = await sharp(Buffer.from(nextPixels), {raw: {width: w, height: h, channels: 4}}).png().toBuffer();
    const ktx2Bytes = await encodeToKTX2(new Uint8Array(png), {
      imageDecoder: decoder,
      isUASTC: false,
      qualityLevel: 215,
      generateMipmap: true,
      isInputSRGB: true,
      isSetKTX2SRGBTransferFunc: true,
      enableDebug: false,
    });
    texture.setImage(new Uint8Array(ktx2Bytes)).setMimeType('image/ktx2');
  }
  doc.createExtension(KHRTextureBasisu).setRequired(true);
  doc.createExtension(EXTMeshoptCompression).setRequired(true).setEncoderOptions({method: EXTMeshoptCompression.EncoderMethod.FILTER});
  await io.write(path.join(outDir, name), doc);
}
"""
    subprocess.run(
        ["node", "--input-type=module", "-e", node_opt_script, str(ROOT), str(near_raw_dir), str(near_out_dir), "512"],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        ["node", "--input-type=module", "-e", node_opt_script, str(ROOT), str(mid_raw_dir), str(mid_out_dir), "256"],
        cwd=ROOT,
        check=True,
    )
    shutil.rmtree(near_raw_dir)
    shutil.rmtree(mid_raw_dir)

    for rec in records:
        rec["near_identity"] = fingerprint(b2_dir / rec["near_file"])
        rec["mid_identity"] = fingerprint(b2_dir / rec["mid_file"])
    return records


def build_spatial_tree_index_and_canopy(b2_dir: Path, idx_meta: dict, ds: dict) -> tuple[dict, dict]:
    """
    1) 从 5,167 个 BAIP 实例页提取全部 343,234 棵树的精确变换，编码为 72x72 分块空间索引包 trees-spatial.scvt；
    2) 同时根据 343,234 棵树的真实世界位置与高度，结合 B1 地面高程场，生成覆盖全图所有树林的实心 3D 蓬松林冠穹顶网格
       (forest-canopy-clusters.glb)，彻底解决原游戏 ForestConstrainV2 只有外圈围墙而林区中心空心的问题！
    """
    inst_dir = b2_dir / "instances"
    inst_dir.mkdir(parents=True, exist_ok=True)
    src_pages_dir = BATCH_DIR / "实例分页/r01"

    baip_dt = np.dtype([
        ("p", "<u4"),
        ("g", "<u4"),
        ("si", "<u4"),
        ("f", "<u4"),
        ("m", "<f4", (16,)),
        ("b", "<f4", (6,)),
    ])
    tree_rec_dt = np.dtype([
        ("proto", "<u1"),
        ("yaw", "<u1"),
        ("sxz", "<u1"),
        ("sy", "<u1"),
        ("lx", "<u2"),
        ("lz", "<u2"),
        ("wy", "<i2"),
    ])

    # 每种树的基准树高 (m)
    proto_base_h = np.array([19.5, 19.5, 19.5, 7.2, 9.8, 7.5, 4.8, 6.2], dtype=np.float32)

    # 901x901 (10m 精准步距) 全图林冠包络网格，显著收紧远景树林边界溢出
    CANOPY_N = 901
    CANOPY_STEP = 9000.0 / (CANOPY_N - 1)  # 10.0m
    canopy_top_h = np.zeros((CANOPY_N, CANOPY_N), dtype=np.float32)
    canopy_density = np.zeros((CANOPY_N, CANOPY_N), dtype=np.float32)

    page_map = {p["id"]: p for p in idx_meta["pages"]}
    cell_table = bytearray(GRID_N * GRID_N * 8)
    records_chunks = []
    current_offset = 0
    total_trees = 0
    per_proto_counts = [0] * 8

    for cz in range(GRID_N):
        for cx in range(GRID_N):
            cell_idx = cz * GRID_N + cx
            cid = f"{cx:02}-{cz:02}"
            p_info = page_map.get(cid)
            if not p_info or p_info["count"] == 0:
                struct.pack_into("<II", cell_table, cell_idx * 8, current_offset, 0)
                continue

            raw = (src_pages_dir / p_info["file"]).read_bytes()
            arr = np.frombuffer(raw, dtype=baip_dt, offset=16, count=p_info["count"])
            tree_mask = (arr["p"] >= 125) & (arr["p"] <= 132)
            t_arr = arr[tree_mask]
            n_t = len(t_arr)
            if n_t == 0:
                struct.pack_into("<II", cell_table, cell_idx * 8, current_offset, 0)
                continue

            m = t_arr["m"]
            proto_local = (t_arr["p"] - 125).astype(np.uint8)
            for pl in range(8):
                per_proto_counts[pl] += int(np.sum(proto_local == pl))

            sxz = np.hypot(m[:, 0], m[:, 8])
            sy = np.abs(m[:, 5])
            yaw = np.arctan2(m[:, 8], m[:, 0])

            # 仅将高大森林乔木 (proto_local < 3: Oak_3, Spruce_4, Spruce_3) 计入远景常驻集群林冠壳密度场，
            # 避免路边零星矮灌木 (Bush_4/5) 或城市孤立小树膨胀覆盖农田与草地！
            tall_mask = proto_local < 3
            if np.any(tall_mask):
                gx_c = np.clip(np.round((m[tall_mask, 12] - MIN_X) / CANOPY_STEP).astype(np.int32), 0, CANOPY_N - 1)
                gz_c = np.clip(np.round((m[tall_mask, 14] - MIN_Z) / CANOPY_STEP).astype(np.int32), 0, CANOPY_N - 1)
                tree_h = proto_base_h[proto_local[tall_mask]] * sy[tall_mask]
                np.add.at(canopy_density, (gz_c, gx_c), 1.0)
                np.maximum.at(canopy_top_h, (gz_c, gx_c), tree_h)

            yaw_u8 = np.clip(np.round((yaw + math.pi) / (2.0 * math.pi) * 255.0), 0, 255).astype(np.uint8)
            sxz_u8 = np.clip(np.round(sxz * 64.0), 1, 255).astype(np.uint8)
            sy_u8 = np.clip(np.round(sy * 64.0), 1, 255).astype(np.uint8)

            cell_min_x = MIN_X + cx * CHUNK_M
            cell_min_z = MIN_Z + cz * CHUNK_M
            lx_u16 = np.clip(np.round((m[:, 12] - cell_min_x) / CHUNK_M * 65535.0), 0, 65535).astype(np.uint16)
            lz_u16 = np.clip(np.round((m[:, 14] - cell_min_z) / CHUNK_M * 65535.0), 0, 65535).astype(np.uint16)
            wy_i16 = np.clip(np.round(m[:, 13] * 200.0), -32767, 32767).astype(np.int16)

            packed = np.empty(n_t, dtype=tree_rec_dt)
            packed["proto"] = proto_local
            packed["yaw"] = yaw_u8
            packed["sxz"] = sxz_u8
            packed["sy"] = sy_u8
            packed["lx"] = lx_u16
            packed["lz"] = lz_u16
            packed["wy"] = wy_i16

            records_chunks.append(packed.tobytes())
            struct.pack_into("<II", cell_table, cell_idx * 8, current_offset, n_t)
            current_offset += n_t
            total_trees += n_t

    header = struct.pack("<4sIIIIfff", b"SCVT", 1, GRID_N, GRID_N, total_trees, MIN_X, MIN_Z, CHUNK_M)
    raw_scvt = header + bytes(cell_table) + b"".join(records_chunks)
    comp_scvt = zlib.compress(raw_scvt, level=9)
    scvt_path = inst_dir / "trees-spatial.scvt"
    scvt_path.write_bytes(comp_scvt)

    spatial_res = {
        "file": "instances/trees-spatial.scvt",
        "total_trees": total_trees,
        "per_prototype_counts": per_proto_counts,
        "raw_bytes": len(raw_scvt),
        "compressed_bytes": len(comp_scvt),
        "identity": fingerprint(scvt_path),
    }

    # --- 构建实心 3D 远景集群林冠壳 (forest-canopy-clusters.glb) ---
    canopy_dir = b2_dir / "canopy"
    canopy_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = canopy_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "贴图").mkdir(exist_ok=True)

    # 读取 B1 全局高度场获取每个 15m 格点的精确地面高度
    scvh_raw = zlib.decompress((B1_DIR / "heightfield/global-height-uint16.scvh").read_bytes())
    w_hf, h_hf, min_x_hf, min_z_hf, sx_hf, sz_hf, min_h_hf, max_h_hf = struct.unpack_from("<IIffffff", scvh_raw, 8)
    d1 = np.mod(np.cumsum(np.frombuffer(scvh_raw, dtype=np.uint16, offset=40, count=w_hf * h_hf).reshape((h_hf, w_hf)).astype(np.uint32), axis=0), 65536)
    dq = np.mod(np.cumsum(d1, axis=1), 65536)
    ground_2m = min_h_hf + (np.minimum(dq, 65534) / 65534.0) * (max_h_hf - min_h_hf)

    gx_idx = np.clip(np.round(np.linspace(0, w_hf - 1, CANOPY_N)).astype(np.int32), 0, w_hf - 1)
    gz_idx = np.clip(np.round(np.linspace(0, h_hf - 1, CANOPY_N)).astype(np.int32), 0, h_hf - 1)
    ground_15m = ground_2m[np.ix_(gz_idx, gx_idx)].astype(np.float32)

    # 10m 精准高斯平滑林木密度场（半径 1.05 ≈ 10.5m，彻底消除 33m 级林缘膨胀溢出）
    d_blur = np.asarray(
        Image.fromarray(np.clip(canopy_density * 42.0, 0, 255).astype(np.uint8), mode="L").filter(ImageFilter.GaussianBlur(radius=1.05)),
        dtype=np.float32,
    ) / 42.0
    t_dens = np.clip((d_blur - 0.22) / (0.60 - 0.22), 0.0, 1.0)
    cluster_weight = t_dens * t_dens * (3.0 - 2.0 * t_dens)

    # 硬抠除 B3 建筑占地（排除岩石 97-100），杜绝远景绿林吞噬房屋与街区
    b3_scvb = WORK_DIR / "B3-建筑与编辑器/instances/buildings-spatial.scvb"
    if b3_scvb.is_file():
        b_raw = zlib.decompress(b3_scvb.read_bytes())
        bgx, bgz, btot = struct.unpack_from("<III", b_raw, 8)
        boff = 32 + bgx * bgz * 8
        bdt = np.dtype([("proto", "<u2"), ("flags", "<u2"), ("group", "<u2"), ("sidx", "<u2"), ("m3x4", "<f4", (12,))])
        barr = np.frombuffer(b_raw, dtype=bdt, offset=boff, count=btot)
        non_rock = (barr["proto"] < 97) | (barr["proto"] > 100)
        bx = barr["m3x4"][non_rock, 9]
        bz = barr["m3x4"][non_rock, 11]
        in_b = (bx >= MIN_X) & (bx <= MAX_X) & (bz >= MIN_Z) & (bz <= MAX_Z)
        b_ix = np.clip(np.round((bx[in_b] - MIN_X) / CANOPY_STEP).astype(np.int32), 0, CANOPY_N - 1)
        b_iz = np.clip(np.round((bz[in_b] - MIN_Z) / CANOPY_STEP).astype(np.int32), 0, CANOPY_N - 1)
        b_mask = np.zeros((CANOPY_N, CANOPY_N), dtype=np.uint8)
        b_mask[b_iz, b_ix] = 255
        b_mask_soft = np.asarray(Image.fromarray(b_mask, mode="L").filter(ImageFilter.GaussianBlur(radius=1.0)), dtype=np.float32) / 255.0
        cluster_weight *= np.clip(1.0 - b_mask_soft * 2.2, 0.0, 1.0)

    h_raw_u8 = np.clip(np.round((np.maximum(canopy_top_h, np.where(d_blur > 0.22, 15.0, 0.0)) / 24.0) * 255.0), 0, 255).astype(np.uint8)
    h_smooth_f = (np.asarray(Image.fromarray(h_raw_u8, mode="L").filter(ImageFilter.GaussianBlur(radius=1.15)), dtype=np.float32) / 255.0) * 23.0
    # 当 cluster_weight == 0 时高度降到 -3.6m（完全埋入地面以下），使可见地表交线成为精准圆滑曲线！
    signed_canopy_h = np.maximum(h_smooth_f, 15.5) * cluster_weight - (1.0 - cluster_weight) * 3.6

    xx_grid, zz_grid = np.meshgrid(
        MIN_X + np.arange(CANOPY_N, dtype=np.float32) * CANOPY_STEP,
        MIN_Z + np.arange(CANOPY_N, dtype=np.float32) * CANOPY_STEP,
    )
    # 只要顶点高度 > -1.2m 即纳入网格，外圈边缘点在地面下方 -3.6m 斜插闭合，视觉交线 100% 平滑连续！
    active_canopy = signed_canopy_h > 0.15

    # 对活跃边界做 4 轮温和拉普拉斯平滑进一步消除斜边折角
    pad_act = np.pad(active_canopy, 1, mode="constant", constant_values=False)
    nbr_cnt = (
        pad_act[:-2, 1:-1].astype(np.int32)
        + pad_act[2:, 1:-1].astype(np.int32)
        + pad_act[1:-1, :-2].astype(np.int32)
        + pad_act[1:-1, 2:].astype(np.int32)
    )
    smooth_mask = (active_canopy & (nbr_cnt < 4)) | ((~active_canopy) & (nbr_cnt > 0))
    for _ in range(4):
        px = np.pad(xx_grid, 1, mode="edge")
        pz = np.pad(zz_grid, 1, mode="edge")
        avg_x = 0.25 * (px[:-2, 1:-1] + px[2:, 1:-1] + px[1:-1, :-2] + px[1:-1, 2:])
        avg_z = 0.25 * (pz[:-2, 1:-1] + pz[2:, 1:-1] + pz[1:-1, :-2] + pz[1:-1, 2:])
        xx_grid = np.where(smooth_mask, xx_grid * 0.45 + avg_x * 0.55, xx_grid)
        zz_grid = np.where(smooth_mask, zz_grid * 0.45 + avg_z * 0.55, zz_grid)

    fx_hf = np.clip((xx_grid - min_x_hf) / (sx_hf * (w_hf - 1)) * (w_hf - 1), 0, w_hf - 1).astype(np.int32)
    fz_hf = np.clip((zz_grid - min_z_hf) / (sz_hf * (h_hf - 1)) * (h_hf - 1), 0, h_hf - 1).astype(np.int32)
    ground_15m = ground_2m[fz_hf, fx_hf].astype(np.float32)

    # 多尺度自然树冠球包起伏 (在林心饱满起伏，在林缘渐弱平滑插入地下)
    bump = (
        1.55 * (np.sin(xx_grid * 0.21 + 0.5) * np.cos(zz_grid * 0.23 - 0.3) + 0.55 * np.cos(xx_grid * 0.39 - zz_grid * 0.35))
        + 0.75 * np.sin(xx_grid * 0.11 + zz_grid * 0.13)
    )
    world_y_field = ground_15m + signed_canopy_h + bump * np.clip(signed_canopy_h / 10.0, 0.0, 1.0)

    # 计算平滑法线场
    dy_dx = np.gradient(world_y_field, CANOPY_STEP, axis=1)
    dy_dz = np.gradient(world_y_field, CANOPY_STEP, axis=0)
    nx_f = -dy_dx
    ny_f = np.ones_like(world_y_field)
    nz_f = -dy_dz
    n_len = np.maximum(np.sqrt(nx_f * nx_f + ny_f * ny_f + nz_f * nz_f), 1e-6)
    norm_field = np.stack([nx_f / n_len, ny_f / n_len, nz_f / n_len], axis=-1).astype(np.float32)

    # 仅保留至少有 1 个顶点 active_canopy 的网格单元，构建紧凑实心林冠网格
    cell_active = (
        active_canopy[:-1, :-1]
        | active_canopy[:-1, 1:]
        | active_canopy[1:, :-1]
        | active_canopy[1:, 1:]
    )
    cz_arr, cx_arr = np.nonzero(cell_active)

    def make_quad_tris(iz_a, ix_a):
        p00 = np.stack([xx_grid[iz_a, ix_a], world_y_field[iz_a, ix_a], zz_grid[iz_a, ix_a]], axis=-1)
        p10 = np.stack([xx_grid[iz_a, ix_a + 1], world_y_field[iz_a, ix_a + 1], zz_grid[iz_a, ix_a + 1]], axis=-1)
        p01 = np.stack([xx_grid[iz_a + 1, ix_a], world_y_field[iz_a + 1, ix_a], zz_grid[iz_a + 1, ix_a]], axis=-1)
        p11 = np.stack([xx_grid[iz_a + 1, ix_a + 1], world_y_field[iz_a + 1, ix_a + 1], zz_grid[iz_a + 1, ix_a + 1]], axis=-1)

        n00 = norm_field[iz_a, ix_a]
        n10 = norm_field[iz_a, ix_a + 1]
        n01 = norm_field[iz_a + 1, ix_a]
        n11 = norm_field[iz_a + 1, ix_a + 1]

        # 两个三角形: (p00, p01, p10) 和 (p10, p01, p11)
        tris_p = np.stack([p00, p01, p10, p10, p01, p11], axis=1).reshape(-1, 3)
        tris_n = np.stack([n00, n01, n10, n10, n01, n11], axis=1).reshape(-1, 3)
        tris_uv = (tris_p[:, [0, 2]] * 0.025).astype(np.float32)
        return tris_p.astype(np.float32), tris_n.astype(np.float32), tris_uv

    canopy_pos, canopy_norm, canopy_uv = make_quad_tris(cz_arr, cx_arr)
    source_tris = len(canopy_pos) // 3

    # 处理森林冠层贴图 sharedassets7_assets-246.png
    src_canopy_tex = BATCH_DIR / "原始贴图/sharedassets7_assets-246.png"
    with Image.open(src_canopy_tex) as im:
        im_res = im.convert("RGB").resize((512, 512), Image.Resampling.LANCZOS)
        arr = np.asarray(im_res, dtype=np.float32) / 255.0
    mean_c = arr.mean(axis=(0, 1), keepdims=True)
    arr_contrast = np.clip(mean_c + (arr - mean_c) * 1.55, 0.06, 0.96)
    tint = np.array([0.80, 0.94, 0.72], dtype=np.float32)
    arr_tinted = np.clip(arr_contrast * tint[None, None, :], 0.0, 1.0)
    rgba_u8 = np.dstack([
        np.clip(np.round(arr_tinted * 255.0), 0, 255).astype(np.uint8),
        np.full((512, 512), 255, dtype=np.uint8),
    ])
    tex_rel = "贴图/forest_canopy_512.png"
    Image.fromarray(rgba_u8, mode="RGBA").save(raw_dir / tex_rel)

    mat_key = "forest_canopy_cluster_mat"
    materials = {
        mat_key: {
            "name": "ForestCanopyCluster",
            "source_key": mat_key,
            "color": [1.0, 1.0, 1.0, 1.0],
            "image": tex_rel,
            "alpha_mode": "OPAQUE",
            "alpha_cutoff": 0.5,
            "double_sided": True,
            "render_role": 7,
        }
    }
    primitives = [{
        "material": mat_key,
        "attributes": {
            "POSITION": canopy_pos,
            "NORMAL": canopy_norm,
            "TEXCOORD_0": canopy_uv,
        },
        "extras": {
            "category": "forest_canopy_clusters",
            "render_role": 7,
            "active_cells": int(len(cz_arr)),
        },
    }]
    raw_glb = raw_dir / "canopy-raw.glb"
    write_glb(raw_glb, primitives, materials)

    out_glb = canopy_dir / "forest-canopy-clusters.glb"
    node_canopy_script = """
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';

const root = path.resolve(process.argv[1]);
const inputGlb = path.resolve(process.argv[2]);
const outputGlb = path.resolve(process.argv[3]);

const modules = path.join(root, 'local-data/tools/网页资源优化/1/node_modules');
const load = p => import(pathToFileURL(path.join(modules, p)).href);
const require = createRequire(path.join(modules, '../package.json'));
const sharp = require('sharp');
const {NodeIO} = await load('@gltf-transform/core/dist/index.js');
const {ALL_EXTENSIONS, EXTMeshoptCompression, KHRTextureBasisu} = await load('@gltf-transform/extensions/dist/index.js');
const {weld, dedup, simplify} = await load('@gltf-transform/functions/dist/index.js');
const {MeshoptEncoder, MeshoptDecoder, MeshoptSimplifier} = await load('meshoptimizer/index.js');
const {encodeToKTX2} = await load('ktx2-encoder/dist/node/index.js');
await Promise.all([MeshoptEncoder.ready, MeshoptDecoder.ready, MeshoptSimplifier.ready]);

const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({
  'meshopt.encoder': MeshoptEncoder,
  'meshopt.decoder': MeshoptDecoder,
});
const decoder = async b => {
  const {data, info} = await sharp(b).ensureAlpha().raw().toBuffer({resolveWithObject: true});
  return {data: new Uint8Array(data), width: info.width, height: info.height};
};

const doc = await io.read(inputGlb);
await doc.transform(
  dedup(),
  weld({tolerance: 0.01}),
  simplify({simplifier: MeshoptSimplifier, ratio: 0.18, error: 0.035})
);

let finalTris = 0;
for (const m of doc.getRoot().listMeshes()) {
  for (const p of m.listPrimitives()) {
    const idx = p.getIndices();
    finalTris += idx ? idx.getCount() / 3 : p.getAttribute('POSITION').getCount() / 3;
  }
}

for (const texture of doc.getRoot().listTextures()) {
  const image = texture.getImage();
  const png = await sharp(image).resize(512, 512, {fit: 'fill'}).png().toBuffer();
  const ktx2Bytes = await encodeToKTX2(new Uint8Array(png), {
    imageDecoder: decoder,
    isUASTC: false,
    qualityLevel: 215,
    generateMipmap: true,
    isInputSRGB: true,
    isSetKTX2SRGBTransferFunc: true,
    enableDebug: false,
  });
  texture.setImage(new Uint8Array(ktx2Bytes)).setMimeType('image/ktx2');
}

doc.createExtension(KHRTextureBasisu).setRequired(true);
doc.createExtension(EXTMeshoptCompression).setRequired(true).setEncoderOptions({method: EXTMeshoptCompression.EncoderMethod.FILTER});
await io.write(outputGlb, doc);
console.log(JSON.stringify({finalTris}));
"""
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", node_canopy_script, str(ROOT), str(raw_glb), str(out_glb)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    shutil.rmtree(raw_dir)
    info = json.loads(proc.stdout.strip().splitlines()[-1])
    canopy_res = {
        "file": "canopy/forest-canopy-clusters.glb",
        "source_active_cells": int(len(cz_arr)),
        "source_triangles": source_tris,
        "simplified_triangles": int(info["finalTris"]),
        "identity": fingerprint(out_glb),
    }
    return spatial_res, canopy_res


def build_grass_tuft_atlas(b2_dir: Path) -> dict:
    """生成 512x512 近景程序化 GPU 草丛贴图图集（鲜活温带草丛绿，叶尖通透暖黄绿，底部自然柔和）。"""
    grass_dir = b2_dir / "grass"
    grass_dir.mkdir(parents=True, exist_ok=True)

    size = 512
    img = Image.new("RGBA", (size, size), (112, 134, 68, 0))
    draw = ImageDraw.Draw(img)
    rng = np.random.default_rng(20261118)

    for qy in range(2):
        for qx in range(2):
            ox, oy = qx * 256, qy * 256
            n_blades = 76 if (qx + qy) % 2 == 0 else 62
            for b in range(n_blades):
                base_x = ox + int(rng.uniform(24, 232))
                base_y = oy + 252
                blade_h = float(rng.uniform(125, 230))
                lean = float(rng.uniform(-34, 34))
                width_base = float(rng.uniform(6.0, 10.5))

                steps = 6
                pts_left = []
                pts_right = []
                for s in range(steps + 1):
                    t = s / steps
                    cx = base_x + lean * (t ** 1.5)
                    cy = base_y - blade_h * t
                    w = width_base * (1.0 - 0.86 * (t ** 1.2))
                    pts_left.append((cx - w, cy))
                    pts_right.append((cx + w, cy))

                for s in range(steps):
                    t = (s + 0.5) / steps
                    r = int(np.clip(86 + 76 * t + rng.uniform(-8, 8), 65, 195))
                    g = int(np.clip(108 + 78 * t + rng.uniform(-8, 8), 85, 215))
                    bl = int(np.clip(48 + 36 * t + rng.uniform(-5, 5), 35, 110))
                    poly = [pts_left[s], pts_left[s + 1], pts_right[s + 1], pts_right[s]]
                    draw.polygon(poly, fill=(r, g, bl, 255))

                if b % 9 == 0:
                    tip_x, tip_y = pts_left[-1]
                    draw.ellipse((tip_x - 3, tip_y - 6, tip_x + 3, tip_y + 4), fill=(182, 188, 108, 250))

    arr = np.asarray(img, dtype=np.uint8).copy()
    alpha_im = Image.fromarray(arr[..., 3], mode="L").filter(ImageFilter.GaussianBlur(radius=0.5))
    alpha_arr = np.asarray(alpha_im, dtype=np.uint8)
    mask_empty = alpha_arr < 15
    arr[mask_empty, 0] = 112
    arr[mask_empty, 1] = 134
    arr[mask_empty, 2] = 68
    arr[..., 3] = alpha_arr

    out_path = grass_dir / "grass-tuft-atlas.webp"
    Image.fromarray(arr, mode="RGBA").save(out_path, format="WEBP", quality=92, method=4)
    return {
        "file": "grass/grass-tuft-atlas.webp",
        "resolution": [size, size],
        "quadrants": 4,
        "identity": fingerprint(out_path),
    }


def main() -> None:
    t0 = time.time()
    if B2_DIR.exists():
        shutil.rmtree(B2_DIR)
    B2_DIR.mkdir(parents=True, exist_ok=True)

    idx_meta = json.loads((BATCH_DIR / "实例分页/r01/实例索引.json").read_text("utf-8"))
    r14_meta = json.loads((BATCH_DIR / "运行接入/r14/地图运行.json").read_text("utf-8"))
    ds_meta = json.loads((BATCH_DIR / "表面来源/full-r01/直接表面.json").read_text("utf-8"))

    proto_records = build_tree_prototypes(B2_DIR, idx_meta, r14_meta)
    spatial_res, canopy_res = build_spatial_tree_index_and_canopy(B2_DIR, idx_meta, ds_meta)
    grass_res = build_grass_tuft_atlas(B2_DIR)

    near_bytes = sum(r["near_identity"]["bytes"] for r in proto_records)
    mid_bytes = sum(r["mid_identity"]["bytes"] for r in proto_records)
    canopy_bytes = canopy_res["identity"]["bytes"]
    grass_bytes = grass_res["identity"]["bytes"]
    spatial_bytes = spatial_res["identity"]["bytes"]
    total_b2_bytes = near_bytes + mid_bytes + canopy_bytes + grass_bytes + spatial_bytes
    total_b2_mib = round(total_b2_bytes / (1024 * 1024), 3)

    manifest = {
        "schema": "scavenger-procedural-map-b2-foliage-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "bounds": {"minX": MIN_X, "maxX": MAX_X, "minZ": MIN_Z, "maxZ": MAX_Z},
        "lod_rings": {
            "near_3d_spherical_m": [0.0, 320.0],
            "mid_volume_proxy_m": [250.0, 900.0],
            "far_canopy_clusters_m": [240.0, 9000.0],
            "gpu_grass_tufts_m": [0.0, 135.0],
        },
        "tree_prototypes": proto_records,
        "forest_canopy_clusters": canopy_res,
        "gpu_grass_atlas": grass_res,
        "spatial_tree_index": spatial_res,
        "budget_summary": {
            "near_trees_bytes": near_bytes,
            "near_trees_mib": round(near_bytes / (1024 * 1024), 3),
            "mid_proxies_bytes": mid_bytes,
            "mid_proxies_mib": round(mid_bytes / (1024 * 1024), 3),
            "canopy_clusters_bytes": canopy_bytes,
            "canopy_clusters_mib": round(canopy_bytes / (1024 * 1024), 3),
            "grass_atlas_bytes": grass_bytes,
            "grass_atlas_mib": round(grass_bytes / (1024 * 1024), 3),
            "spatial_index_bytes": spatial_bytes,
            "spatial_index_mib": round(spatial_bytes / (1024 * 1024), 3),
            "total_b2_bytes": total_b2_bytes,
            "total_b2_mib": total_b2_mib,
            "limit_mib": 12.0,
            "within_budget": total_b2_mib <= 12.0,
        },
        "elapsed_s": round(time.time() - t0, 2),
    }

    manifest_path = B2_DIR / "植被与草丛清单.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest["budget_summary"], ensure_ascii=False))
    if not manifest["budget_summary"]["within_budget"]:
        raise RuntimeError(f"B2 植被与草丛总体积超出 12 MiB 门限: {total_b2_mib} MiB")


if __name__ == "__main__":
    main()
