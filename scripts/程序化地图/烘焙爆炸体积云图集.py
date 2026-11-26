#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scavenger P0 写实爆炸 8×8 多通道体积云图集离线确定性烘焙脚本。

核心算法（纯 Python + NumPy + SciPy + Pillow，零 Blender 依赖）：
1. 预计算 64^3 周期可平铺多尺度 3D FBM 场（低频团块 + 高频絮丝）、解析中心差分 3D 梯度
   与无散度 3D Curl 旋度涡旋场；
2. 构建 1 颗偏心主核心 + 9 颗黄金螺旋（Golden Spiral）非对称分布亚球，使用多项式
   Smooth-Min 融合并按三段式图集节律（0..14 紧凑膨胀 -> 15..43 翻滚菜花 -> 44..63
   絮状撕裂消散）施加角动量翻滚与 Curl 平流；
3. 对每帧 128×128 像素执行 56 步带确定性黄金比低差异子步抖动的 Front-to-Back 体积光线步进，积分：
   - Alpha（平滑边缘密度，单帧有效半径 r2d <= 0.87，四周保留 >= 8.32px 零透明区）
   - 切线空间体积梯度法线 N * 0.5 + 0.5（半球钳位后执行二次单位归一化，透明区填充中性法线 (128, 128, 255)）
   - 黑体辐射热通道 Heat（爆心 + Smooth-Min 缝隙指示器 + 2D Cavity 凹谷）
   - 光学自遮蔽厚度 Thickness/AO（外薄边与缝隙低、厚实凸瓣中心高）
   - 多尺度连续腐蚀噪声 Erode（全格无边界接缝，压缩动态范围消除饱和死区，供运行时 smoothstep 柔边溶解）

输出至 public/assets/vfx-explosion/：
- smoke_vol_normal_alpha.png (1024×1024 8-bit RGBA PNG)
- smoke_vol_heat_thick.png   (1024×1024 8-bit RGBA PNG, A=255)
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter, map_coordinates

SEED = 20261123
ATLAS_SIZE = 1024
GRID_COLS = 8
GRID_ROWS = 8
TOTAL_FRAMES = GRID_COLS * GRID_ROWS
CELL_SIZE = ATLAS_SIZE // GRID_COLS  # 128
VOL_GRID = 64
RAY_STEPS = 56
MAX_R2D = 0.87  # 0.87 * 64 = 55.68 px -> 四周 >= 8.32 px 安全零透明区
MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MiB 硬上限

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = ROOT / "public" / "assets" / "vfx-explosion"


def smoothstep(edge0: float | np.ndarray, edge1: float | np.ndarray, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def smin_poly(a: np.ndarray, b: np.ndarray, k: float) -> np.ndarray:
    if k <= 1e-6:
        return np.minimum(a, b)
    h = np.maximum(k - np.abs(a - b), 0.0) / k
    return np.minimum(a, b) - h * h * k * 0.25


def make_periodic_octave(rng: np.random.Generator, freq: int, grid_size: int) -> np.ndarray:
    coarse = rng.standard_normal((freq, freq, freq), dtype=np.float64)
    # 先用 np.pad(..., mode="wrap") 周期延拓 3 格，再采样 [3, 3 + freq)，
    # 彻底规避 SciPy map_coordinates(mode="wrap") 按 N-1 折叠产生的中心十字跳变接缝。
    padded = np.pad(coarse, pad_width=3, mode="wrap")
    coords_1d = np.linspace(3.0, 3.0 + float(freq), grid_size, endpoint=False)
    gx, gy, gz = np.meshgrid(coords_1d, coords_1d, coords_1d, indexing="ij")
    out = map_coordinates(padded, [gx, gy, gz], order=3, mode="nearest")
    std = float(np.std(out))
    if std > 1e-8:
        out = (out - float(np.mean(out))) / std
    return out.astype(np.float32)


def make_periodic_fbm(rng: np.random.Generator, freqs: tuple[int, ...], weights: tuple[float, ...], grid_size: int) -> np.ndarray:
    acc = np.zeros((grid_size, grid_size, grid_size), dtype=np.float32)
    for f, w in zip(freqs, weights):
        acc += float(w) * make_periodic_octave(rng, f, grid_size)
    std = float(np.std(acc))
    if std > 1e-8:
        acc = (acc - float(np.mean(acc))) / std
    return acc.astype(np.float32)


def central_grad_3d(field: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    step = 2.0 / field.shape[0]
    dx = (np.roll(field, -1, axis=0) - np.roll(field, 1, axis=0)) / (2.0 * step)
    dy = (np.roll(field, -1, axis=1) - np.roll(field, 1, axis=1)) / (2.0 * step)
    dz = (np.roll(field, -1, axis=2) - np.roll(field, 1, axis=2)) / (2.0 * step)
    return dx.astype(np.float32), dy.astype(np.float32), dz.astype(np.float32)


def sample_periodic_trilinear(vol: np.ndarray, px: np.ndarray, py: np.ndarray, pz: np.ndarray, scale: float) -> np.ndarray:
    """在 [-1, 1]^3 空间以周期方式三线性采样 3D 体数据，向量化且零边界接缝。"""
    g = vol.shape[0]
    u = np.mod((px * 0.5 + 0.5) * scale * g, g)
    v = np.mod((py * 0.5 + 0.5) * scale * g, g)
    w = np.mod((pz * 0.5 + 0.5) * scale * g, g)

    x0 = np.floor(u).astype(np.int32) % g
    y0 = np.floor(v).astype(np.int32) % g
    z0 = np.floor(w).astype(np.int32) % g
    x1 = (x0 + 1) % g
    y1 = (y0 + 1) % g
    z1 = (z0 + 1) % g

    fx = (u - np.floor(u)).astype(np.float32)
    fy = (v - np.floor(v)).astype(np.float32)
    fz = (w - np.floor(w)).astype(np.float32)

    c000 = vol[x0, y0, z0]
    c100 = vol[x1, y0, z0]
    c010 = vol[x0, y1, z0]
    c110 = vol[x1, y1, z0]
    c001 = vol[x0, y0, z1]
    c101 = vol[x1, y0, z1]
    c011 = vol[x0, y1, z1]
    c111 = vol[x1, y1, z1]

    c00 = c000 * (1.0 - fx) + c100 * fx
    c10 = c010 * (1.0 - fx) + c110 * fx
    c01 = c001 * (1.0 - fx) + c101 * fx
    c11 = c011 * (1.0 - fx) + c111 * fx

    c0 = c00 * (1.0 - fy) + c10 * fy
    c1 = c01 * (1.0 - fy) + c11 * fy
    return c0 * (1.0 - fz) + c1 * fz


def precompute_noise_volumes(seed: int) -> dict[str, object]:
    rng = np.random.default_rng(seed)
    n_low = make_periodic_fbm(rng, (2, 4, 8), (0.55, 0.30, 0.15), VOL_GRID)
    n_hi = make_periodic_fbm(rng, (6, 10, 15), (0.52, 0.32, 0.16), VOL_GRID)
    dn_low = central_grad_3d(n_low)
    dn_hi = central_grad_3d(n_hi)

    # 3 个独立标量势场 -> 无散度 Curl 涡旋速度场
    psi_x = make_periodic_fbm(rng, (2, 4, 6), (0.60, 0.28, 0.12), VOL_GRID)
    psi_y = make_periodic_fbm(rng, (2, 4, 6), (0.60, 0.28, 0.12), VOL_GRID)
    psi_z = make_periodic_fbm(rng, (2, 4, 6), (0.60, 0.28, 0.12), VOL_GRID)

    _, dpx_dy, dpx_dz = central_grad_3d(psi_x)
    dpy_dx, _, dpy_dz = central_grad_3d(psi_y)
    dpz_dx, dpz_dy, _ = central_grad_3d(psi_z)

    curl_x = dpz_dy - dpy_dz
    curl_y = dpx_dz - dpz_dx
    curl_z = dpy_dx - dpx_dy

    for comp in (curl_x, curl_y, curl_z):
        std = float(np.std(comp))
        if std > 1e-8:
            comp /= std

    return {
        "n_low": n_low,
        "n_hi": n_hi,
        "dn_low": dn_low,
        "dn_hi": dn_hi,
        "curl": (curl_x, curl_y, curl_z),
    }


def build_lobe_rig(seed: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """构建 1 颗偏心主核心球 + 9 颗黄金螺旋非对称分布副瓣球。"""
    rng = np.random.default_rng(seed + 17)
    centers = np.zeros((10, 3), dtype=np.float32)
    radii = np.zeros(10, dtype=np.float32)
    spins = np.zeros(10, dtype=np.float32)

    # lobe 0: 微偏心主核心
    centers[0] = np.array([-0.04, -0.02, 0.03], dtype=np.float32)
    radii[0] = 0.45
    spins[0] = 0.85

    golden_angle = np.pi * (3.0 - np.sqrt(5.0))
    for i in range(1, 10):
        idx = i - 1
        y = 1.0 - (idx + 0.5) * (2.0 / 9.0)
        r_ring = np.sqrt(max(0.0, 1.0 - y * y))
        theta = golden_angle * idx + rng.uniform(-0.32, 0.32)
        dist = 0.29 + 0.11 * rng.uniform(0.0, 1.0)
        cx = np.cos(theta) * r_ring * dist
        cy = y * dist * 0.92 + 0.04  # 略向上偏，还原浮升外翻
        cz = np.sin(theta) * r_ring * dist
        centers[i] = np.array([cx, cy, cz], dtype=np.float32)
        radii[i] = 0.33 - 0.013 * idx + rng.uniform(-0.015, 0.015)
        spins[i] = rng.uniform(0.75, 1.45)

    return centers, radii, spins


def bake_atlases(seed: int = SEED) -> tuple[np.ndarray, np.ndarray, dict[str, object]]:
    t0 = time.perf_counter()
    vols = precompute_noise_volumes(seed)
    t_noise = time.perf_counter() - t0

    base_centers, base_radii, lobe_spins = build_lobe_rig(seed)

    # 单帧像素网格坐标 (x right, y up, 对应 Three.js flipY=true 下 PNG 顶行为 +y, 底行为 -y)
    lin = (np.arange(CELL_SIZE, dtype=np.float32) + 0.5) / CELL_SIZE * 2.0 - 1.0
    gx2, gy2 = np.meshgrid(lin, -lin, indexing="xy")  # row 0 = 顶 (+y), row 127 = 底 (-y)
    idx_1d = np.arange(CELL_SIZE, dtype=np.float32)
    ix2, iy2 = np.meshgrid(idx_1d, idx_1d, indexing="xy")
    r2d_grid = np.sqrt(gx2 * gx2 + gy2 * gy2).astype(np.float32)
    active_mask = r2d_grid <= MAX_R2D
    px_act = gx2[active_mask]
    py_act = gy2[active_mask]
    ix_act = ix2[active_mask]
    iy_act = iy2[active_mask]
    r2d_act = r2d_grid[active_mask]
    m_pix = px_act.shape[0]

    # Front-to-Back 步进 z: +0.88 -> -0.88 (朝向相机为 +Z)
    # 叠加基于像素坐标 (ix, iy) 的确定性 R2 黄金比低差异子步抖动 (±0.5 * dz)，消除固定切片台地
    z_steps = np.linspace(0.88, -0.88, RAY_STEPS, dtype=np.float32)
    dz = float(abs(z_steps[1] - z_steps[0]))
    r2_hash = np.mod(ix_act * 0.7548776662466927 + iy_act * 0.5698402909980532, 1.0).astype(np.float32)
    z_jitter = (r2_hash - 0.5) * dz

    sx_full = np.broadcast_to(px_act[:, None], (m_pix, RAY_STEPS))
    sy_full = np.broadcast_to(py_act[:, None], (m_pix, RAY_STEPS))
    sz_full = z_steps[None, :] + z_jitter[:, None]

    # 全格 128x128 连续腐蚀噪声坐标
    erode_x = gx2
    erode_y = gy2

    atlas_na = np.zeros((ATLAS_SIZE, ATLAS_SIZE, 4), dtype=np.uint8)
    atlas_na[:, :, 0] = 128
    atlas_na[:, :, 1] = 128
    atlas_na[:, :, 2] = 255
    atlas_na[:, :, 3] = 0

    atlas_ht = np.zeros((ATLAS_SIZE, ATLAS_SIZE, 4), dtype=np.uint8)
    atlas_ht[:, :, 3] = 255

    frame_metrics: list[dict[str, float]] = []

    n_low: np.ndarray = vols["n_low"]  # type: ignore[assignment]
    n_hi: np.ndarray = vols["n_hi"]  # type: ignore[assignment]
    dn_low = vols["dn_low"]  # type: ignore[assignment]
    dn_hi = vols["dn_hi"]  # type: ignore[assignment]
    curl = vols["curl"]  # type: ignore[assignment]

    # 偏心翻滚斜轴 (归一化)
    roll_axis = np.array([0.74, 0.36, -0.566], dtype=np.float32)
    roll_axis /= np.linalg.norm(roll_axis)

    for frame in range(TOTAL_FRAMES):
        t = frame / float(TOTAL_FRAMES - 1)
        col = frame % GRID_COLS
        row = frame // GRID_COLS

        # 三段式 64 帧图集演化节律
        # 1) 紧凑膨胀期 (t: 0..0.22) -> 2) 饱满菜花翻滚期 (t: 0.22..0.68) -> 3) 丝状撕裂消散期 (t: 0.68..1.0)
        env_scale = 0.54 + 0.46 * float(smoothstep(0.0, 0.28, np.array(t)))
        ang_roll = 1.30 * t
        adv_amp = 0.065 + 0.085 * float(smoothstep(0.05, 0.85, np.array(t)))
        w1 = 0.125 + 0.040 * t
        w2 = 0.060 + 0.030 * t
        erode_bias = 0.24 * float(smoothstep(0.58, 1.0, np.array(t)))
        late_fade = 1.0 - 0.38 * float(smoothstep(0.68, 1.0, np.array(t)))
        cool_fade = 1.0 - 0.80 * float(smoothstep(0.18, 0.96, np.array(t)))
        early_boost = 1.0 - float(smoothstep(0.0, 0.32, np.array(t)))

        # Rodrigues 旋转矩阵绕斜轴翻滚并外翻
        ca = np.cos(ang_roll)
        sa = np.sin(ang_roll)
        kx, ky, kz = roll_axis
        k_mat = np.array([[0, -kz, ky], [kz, 0, -kx], [-ky, kx, 0]], dtype=np.float32)
        rot_mat = np.eye(3, dtype=np.float32) * ca + sa * k_mat + (1.0 - ca) * np.outer(roll_axis, roll_axis)

        c_t = (base_centers @ rot_mat.T) * env_scale
        # 末期奇偶瓣差异化萎缩
        dissolve_phase = float(smoothstep(0.66, 1.0, np.array(t)))
        r_t = np.zeros(10, dtype=np.float32)
        for i in range(10):
            pulse = 1.0 + 0.09 * np.sin(t * np.pi * 2.4 * lobe_spins[i] + i * 1.1)
            odd_shrink = 1.0 - (0.34 if (i % 2 == 1) else 0.14) * dissolve_phase
            # 外翻微推：各瓣沿中心径向略外扩再随末期收缩
            c_t[i] *= (1.0 + 0.12 * np.sin(t * np.pi * 0.85))
            c_t[i, 1] += 0.045 * t  # 轻微浮力抬升
            r_t[i] = base_radii[i] * env_scale * pulse * odd_shrink

        # Smooth-Min 10 颗亚球 SDF 与梯度
        k_smin = 0.16 * env_scale
        d_list = np.empty((10, m_pix, RAY_STEPS), dtype=np.float32)
        gx_list = np.empty((10, m_pix, RAY_STEPS), dtype=np.float32)
        gy_list = np.empty((10, m_pix, RAY_STEPS), dtype=np.float32)
        gz_list = np.empty((10, m_pix, RAY_STEPS), dtype=np.float32)

        for i in range(10):
            dx_i = sx_full - c_t[i, 0]
            dy_i = sy_full - c_t[i, 1]
            dz_i = sz_full - c_t[i, 2]
            dist_i = np.sqrt(dx_i * dx_i + dy_i * dy_i + dz_i * dz_i + 1e-8)
            d_list[i] = dist_i - r_t[i]
            inv_d = 1.0 / dist_i
            gx_list[i] = dx_i * inv_d
            gy_list[i] = dy_i * inv_d
            gz_list[i] = dz_i * inv_d

        d_min = np.min(d_list, axis=0)
        d_smin = d_list[0].copy()
        for i in range(1, 10):
            d_smin = smin_poly(d_smin, d_list[i], k_smin)

        seam = np.maximum(0.0, d_min - d_smin)

        # 平滑 SDF 解析法线权重
        w_sdf = np.exp(-14.0 * np.maximum(0.0, d_list - d_min[None, :, :]))
        w_sum = np.sum(w_sdf, axis=0) + 1e-6
        sdf_gx = np.sum(w_sdf * gx_list, axis=0) / w_sum
        sdf_gy = np.sum(w_sdf * gy_list, axis=0) / w_sum
        sdf_gz = np.sum(w_sdf * gz_list, axis=0) / w_sum

        # 无散度 Curl 平流坐标（时间平移确保每帧翻滚连续流动）
        px_adv = sx_full + 0.24 * t
        py_adv = sy_full - 0.35 * t
        pz_adv = sz_full + 0.16 * t
        cx = sample_periodic_trilinear(curl[0], px_adv, py_adv, pz_adv, 1.0)
        cy = sample_periodic_trilinear(curl[1], px_adv, py_adv, pz_adv, 1.0)
        cz = sample_periodic_trilinear(curl[2], px_adv, py_adv, pz_adv, 1.0)

        qx = sx_full + adv_amp * cx + 0.18 * t
        qy = sy_full + adv_amp * cy - 0.42 * t
        qz = sz_full + adv_amp * cz + 0.12 * t

        v_low = sample_periodic_trilinear(n_low, qx, qy, qz, 1.05)
        v_hi = sample_periodic_trilinear(n_hi, qx, qy, qz, 1.25)

        d_turb = d_smin - (w1 * v_low + w2 * v_hi) + erode_bias
        r_fade = smoothstep(MAX_R2D, 0.72, r2d_act)[:, None]
        sigma = smoothstep(0.14, -0.20, d_turb) * r_fade * late_fade

        # 局部 3D 解析法线（温和收敛陡背向高频噪声梯度，保留多瓣凸凹并避免大面积翻向 -Z）
        gl_x = sample_periodic_trilinear(dn_low[0], qx, qy, qz, 1.05)
        gl_y = sample_periodic_trilinear(dn_low[1], qx, qy, qz, 1.05)
        gl_z = sample_periodic_trilinear(dn_low[2], qx, qy, qz, 1.05)
        gh_x = sample_periodic_trilinear(dn_hi[0], qx, qy, qz, 1.25)
        gh_y = sample_periodic_trilinear(dn_hi[1], qx, qy, qz, 1.25)
        gh_z = sample_periodic_trilinear(dn_hi[2], qx, qy, qz, 1.25)

        loc_nx = sdf_gx - 0.40 * w1 * gl_x - 0.22 * w2 * gh_x
        loc_ny = sdf_gy - 0.40 * w1 * gl_y - 0.22 * w2 * gh_y
        loc_nz = sdf_gz - 0.40 * w1 * gl_z - 0.22 * w2 * gh_z
        n_len = np.sqrt(loc_nx * loc_nx + loc_ny * loc_ny + loc_nz * loc_nz + 1e-8)
        loc_nx /= n_len
        loc_ny /= n_len
        loc_nz /= n_len

        # Front-to-Back 体积透射积分
        step_alpha = 1.0 - np.exp(-4.0 * sigma * dz)
        one_minus = np.clip(1.0 - step_alpha, 0.0, 1.0)
        transmittance = np.ones_like(step_alpha)
        if RAY_STEPS > 1:
            transmittance[:, 1:] = np.cumprod(one_minus[:, :-1], axis=1)
        weights = transmittance * step_alpha

        alpha_acc = np.clip(np.sum(weights, axis=1), 0.0, 1.0)
        acc_nx = np.sum(weights * loc_nx, axis=1)
        acc_ny = np.sum(weights * loc_ny, axis=1)
        acc_nz = np.sum(weights * loc_nz, axis=1)

        # 低 Alpha 区域渐混向中性法线 (0, 0, 1)，保证背景绝对平整
        na_mix = smoothstep(0.01, 0.25, alpha_acc)
        fin_nx = acc_nx * na_mix
        fin_ny = acc_ny * na_mix
        fin_nz = acc_nz * na_mix + (1.0 - na_mix) * 1.0
        fin_len1 = np.sqrt(fin_nx * fin_nx + fin_ny * fin_ny + fin_nz * fin_nz + 1e-8)
        fin_nx /= fin_len1
        fin_ny /= fin_len1
        z_raw = fin_nz / fin_len1
        # 平滑 C1 半球折叠 + 温和向 +Z 轻度偏置，避免单值硬拍平死区；随后必须执行二次三维单位归一化
        fin_nz = np.maximum(0.05, 0.06 + 0.5 * (z_raw + np.sqrt(z_raw * z_raw + 0.10 * 0.10)) + 0.08 * na_mix)
        fin_len2 = np.sqrt(fin_nx * fin_nx + fin_ny * fin_ny + fin_nz * fin_nz + 1e-8)
        fin_nx /= fin_len2
        fin_ny /= fin_len2
        fin_nz /= fin_len2

        # 前向等效表面深度 -> 2D 高斯局部凹谷 Cavity
        z_front_act = np.sum(weights * sz_full, axis=1) / (alpha_acc + 1e-4)
        z_front_img = np.zeros((CELL_SIZE, CELL_SIZE), dtype=np.float32)
        alpha_img = np.zeros((CELL_SIZE, CELL_SIZE), dtype=np.float32)
        z_front_img[active_mask] = z_front_act
        alpha_img[active_mask] = alpha_acc

        z_blur = gaussian_filter(z_front_img, sigma=4.0, mode="constant", cval=0.0)
        w_blur = gaussian_filter(np.where(active_mask, 1.0, 0.0).astype(np.float32), sigma=4.0, mode="constant", cval=0.0)
        z_blur_norm = z_blur / np.maximum(w_blur, 1e-3)
        cavity_img = np.clip((z_blur_norm - z_front_img) * 4.2, 0.0, 1.0) * smoothstep(0.05, 0.35, alpha_img)
        cavity_act = cavity_img[active_mask]

        # Thickness / AO (G): 外薄边与深缝隙处低、凸瓣中心高
        raw_thick = 1.0 - np.exp(-2.15 * np.sum(sigma, axis=1) * dz)
        thick_act = np.clip(raw_thick * (1.0 - 0.38 * cavity_act) * smoothstep(0.0, 0.32, alpha_acc), 0.0, 1.0)

        # Heat (R): 3D 核心热度 + smin 缝隙 + 2D Cavity 凹谷，随帧冷却
        core_dist3d = np.sqrt(sx_full * sx_full + sy_full * sy_full + sz_full * sz_full)
        core_heat_vol = np.exp(-3.4 * core_dist3d * core_dist3d)
        seam_heat_vol = np.clip(seam * 7.5, 0.0, 1.0)
        heat_vol = np.clip(
            core_heat_vol * (0.45 + 0.75 * early_boost) + seam_heat_vol * 0.85,
            0.0,
            1.0,
        )
        heat_acc = np.sum(weights * heat_vol, axis=1) / (alpha_acc + 1e-3)
        heat_act = np.clip(
            (heat_acc * 0.72 + cavity_act * 0.58 + early_boost * 0.42 * alpha_acc) * cool_fade,
            0.0,
            1.0,
        ) * smoothstep(0.02, 0.20, alpha_acc)

        # Erode (B): 整格 128x128 连续多尺度噪声（平滑 tanh 压缩动态范围至 [0.02, 0.98]，零饱和死区且无圆边突变）
        ez_slice = np.full_like(erode_x, 0.25 * np.sin(t * np.pi * 2.0))
        er_low = sample_periodic_trilinear(n_low, erode_x + 0.14 * t, erode_y - 0.22 * t, ez_slice, 1.25)
        er_hi = sample_periodic_trilinear(n_hi, erode_x - 0.09 * t, erode_y + 0.17 * t, -ez_slice, 1.65)
        er_raw = 0.17 * er_low + 0.11 * er_hi
        erode_img = np.clip(0.50 + 0.48 * np.tanh(er_raw / 0.28), 0.0, 1.0)

        # 写入对应图块 (row 0 = PNG 顶行)
        y0, y1 = row * CELL_SIZE, (row + 1) * CELL_SIZE
        x0, x1 = col * CELL_SIZE, (col + 1) * CELL_SIZE

        cell_na = np.zeros((CELL_SIZE, CELL_SIZE, 4), dtype=np.uint8)
        cell_na[:, :, 0] = 128
        cell_na[:, :, 1] = 128
        cell_na[:, :, 2] = 255
        cell_na[:, :, 3] = 0

        nx_u8 = np.clip(np.round((fin_nx * 0.5 + 0.5) * 255.0), 0, 255).astype(np.uint8)
        ny_u8 = np.clip(np.round((fin_ny * 0.5 + 0.5) * 255.0), 0, 255).astype(np.uint8)
        nz_u8 = np.clip(np.round((fin_nz * 0.5 + 0.5) * 255.0), 0, 255).astype(np.uint8)
        a_u8 = np.clip(np.round(alpha_acc * 255.0), 0, 255).astype(np.uint8)

        cell_na[active_mask, 0] = nx_u8
        cell_na[active_mask, 1] = ny_u8
        cell_na[active_mask, 2] = nz_u8
        cell_na[active_mask, 3] = a_u8
        atlas_na[y0:y1, x0:x1] = cell_na

        cell_ht = np.zeros((CELL_SIZE, CELL_SIZE, 4), dtype=np.uint8)
        cell_ht[active_mask, 0] = np.clip(np.round(heat_act * 255.0), 0, 255).astype(np.uint8)
        cell_ht[active_mask, 1] = np.clip(np.round(thick_act * 255.0), 0, 255).astype(np.uint8)
        cell_ht[:, :, 2] = np.clip(np.round(erode_img * 255.0), 0, 255).astype(np.uint8)
        cell_ht[:, :, 3] = 255
        atlas_ht[y0:y1, x0:x1] = cell_ht

        frame_metrics.append({
            "frame": float(frame),
            "alpha_max": float(np.max(a_u8)),
            "alpha_mean": float(np.mean(a_u8)),
            "heat_max": float(np.max(cell_ht[:, :, 0])),
            "thick_max": float(np.max(cell_ht[:, :, 1])),
        })

    t_total = time.perf_counter() - t0
    stats = {
        "seed": seed,
        "noise_sec": round(t_noise, 3),
        "total_sec": round(t_total, 3),
        "frame_metrics": frame_metrics,
    }
    return atlas_na, atlas_ht, stats


def verify_atlases(out_dir: Path) -> dict[str, object]:
    na_path = out_dir / "smoke_vol_normal_alpha.png"
    ht_path = out_dir / "smoke_vol_heat_thick.png"
    for p in (na_path, ht_path):
        if not p.is_file():
            raise FileNotFoundError(f"缺失图集文件: {p}")
        sz = p.stat().st_size
        if sz <= 0 or sz >= MAX_FILE_BYTES:
            raise ValueError(f"文件体积越界: {p.name} = {sz} bytes (上限 {MAX_FILE_BYTES})")

    img_na = Image.open(na_path)
    img_ht = Image.open(ht_path)
    if img_na.size != (ATLAS_SIZE, ATLAS_SIZE) or img_na.mode != "RGBA":
        raise ValueError(f"normal_alpha 格式异常: size={img_na.size}, mode={img_na.mode}")
    if img_ht.size != (ATLAS_SIZE, ATLAS_SIZE) or img_ht.mode != "RGBA":
        raise ValueError(f"heat_thick 格式异常: size={img_ht.size}, mode={img_ht.mode}")

    arr_na = np.asarray(img_na)
    arr_ht = np.asarray(img_ht)

    # 校验全 64 帧四周 8px 透明区 Alpha === 0，且 A===0 像素法线为中性 (128, 128, 255)
    pad = 8
    border_alpha_max = 0
    for f in range(TOTAL_FRAMES):
        col = f % GRID_COLS
        row = f // GRID_COLS
        y0, y1 = row * CELL_SIZE, (row + 1) * CELL_SIZE
        x0, x1 = col * CELL_SIZE, (col + 1) * CELL_SIZE
        cell_a = arr_na[y0:y1, x0:x1, 3]
        bmax = int(max(
            np.max(cell_a[:pad, :]),
            np.max(cell_a[-pad:, :]),
            np.max(cell_a[:, :pad]),
            np.max(cell_a[:, -pad:]),
        ))
        if bmax > border_alpha_max:
            border_alpha_max = bmax
    if border_alpha_max != 0:
        raise ValueError(f"外围 {pad}px 安全留白存在非零 Alpha: {border_alpha_max}")

    zero_mask = arr_na[:, :, 3] == 0
    bad_norm = np.any(arr_na[zero_mask, :3] != np.array([128, 128, 255], dtype=np.uint8))
    if bad_norm:
        raise ValueError("存在 Alpha=0 像素未填充中性法线 (128, 128, 255)")

    if not np.all(arr_ht[:, :, 3] == 255):
        raise ValueError("heat_thick Alpha 通道未全部锁定 255")

    # 检查关键帧分布与热演化冷却趋势
    def cell_slice(arr: np.ndarray, f: int) -> np.ndarray:
        c, r = f % GRID_COLS, f // GRID_COLS
        return arr[r * CELL_SIZE:(r + 1) * CELL_SIZE, c * CELL_SIZE:(c + 1) * CELL_SIZE]

    f0_heat = int(np.max(cell_slice(arr_ht, 0)[:, :, 0]))
    f16_heat = int(np.max(cell_slice(arr_ht, 16)[:, :, 0]))
    f48_heat = int(np.max(cell_slice(arr_ht, 48)[:, :, 0]))
    f63_alpha = int(np.max(cell_slice(arr_na, 63)[:, :, 3]))
    if f0_heat < 200 or f16_heat < 140 or f48_heat >= f0_heat:
        raise ValueError(f"热通道演化曲线异常: f0={f0_heat}, f16={f16_heat}, f48={f48_heat}")
    if f63_alpha < 80:
        raise ValueError(f"末帧残留絮丝 Alpha 过低: f63_alpha_max={f63_alpha}")

    # 校验烟雾像素 (Alpha > 32) 切线法线单位化与半球钳位拍平占比
    smoke_mask = arr_na[:, :, 3] > 32
    if not np.any(smoke_mask):
        raise ValueError("烟雾有效像素 (Alpha > 32) 为空")
    nx_f = (arr_na[:, :, 0].astype(np.float32) / 255.0) * 2.0 - 1.0
    ny_f = (arr_na[:, :, 1].astype(np.float32) / 255.0) * 2.0 - 1.0
    nz_f = (arr_na[:, :, 2].astype(np.float32) / 255.0) * 2.0 - 1.0
    n_norm = np.sqrt(nx_f * nx_f + ny_f * ny_f + nz_f * nz_f)
    min_norm = float(np.min(n_norm[smoke_mask]))
    nz_flat_ratio = float(np.mean(arr_na[smoke_mask, 2] <= 131))
    if min_norm < 0.96:
        raise ValueError(f"切线法线存在非单位化像素: min_norm={min_norm:.5f} (< 0.96)")
    if nz_flat_ratio >= 0.03:
        raise ValueError(f"切线法线 Nz<=131 拍平占比过高: nz_flat_ratio={nz_flat_ratio:.4f} (>= 0.03)")

    # 校验全 64 帧内相邻 1px 原始 Alpha 最大跳变（防止体积切片陡边硬棱）
    max_1px_alpha_jump = 0
    for f in range(TOTAL_FRAMES):
        ca = cell_slice(arr_na, f)[:, :, 3].astype(np.int16)
        dx_max = int(np.max(np.abs(ca[:, 1:] - ca[:, :-1])))
        dy_max = int(np.max(np.abs(ca[1:, :] - ca[:-1, :])))
        f_jump = max(dx_max, dy_max)
        if f_jump > max_1px_alpha_jump:
            max_1px_alpha_jump = f_jump
    if max_1px_alpha_jump >= 75:
        raise ValueError(f"原始 Alpha 帧内 1px 最大跳变过大: {max_1px_alpha_jump} (>= 75)")

    # 校验 Erode 通道 0 / 255 饱和死区占比
    erode_ch = arr_ht[:, :, 2]
    erode_0_ratio = float(np.mean(erode_ch == 0))
    erode_255_ratio = float(np.mean(erode_ch == 255))
    if erode_0_ratio >= 0.01 or erode_255_ratio >= 0.01:
        raise ValueError(
            f"Erode 通道饱和死区占比超限: erode_0={erode_0_ratio:.4f}, erode_255={erode_255_ratio:.4f} (>= 0.01)"
        )

    return {
        "normal_alpha_bytes": na_path.stat().st_size,
        "heat_thick_bytes": ht_path.stat().st_size,
        "border_8px_alpha_max": border_alpha_max,
        "nz_min_in_smoke": int(np.min(arr_na[smoke_mask, 2])),
        "min_norm": round(min_norm, 5),
        "nz_flat_ratio": round(nz_flat_ratio, 5),
        "max_1px_alpha_jump": max_1px_alpha_jump,
        "erode_0_ratio": round(erode_0_ratio, 5),
        "erode_255_ratio": round(erode_255_ratio, 5),
        "heat_max_f0_f16_f48": [f0_heat, f16_heat, f48_heat],
        "thick_max_f16": int(np.max(cell_slice(arr_ht, 16)[:, :, 1])),
        "erode_min_max": [int(np.min(erode_ch)), int(np.max(erode_ch))],
        "alpha_max_f63": f63_alpha,
    }


def export_preview_sheet(out_dir: Path, preview_dir: Path) -> Path:
    """导出代表帧 (0, 15, 31, 47, 63) 多通道对比拼图至 tmp 目录供目视核验。"""
    preview_dir.mkdir(parents=True, exist_ok=True)
    arr_na = np.asarray(Image.open(out_dir / "smoke_vol_normal_alpha.png"))
    arr_ht = np.asarray(Image.open(out_dir / "smoke_vol_heat_thick.png"))

    sample_frames = [0, 15, 31, 47, 63]
    # 5 列 (sample_frames) × 5 行 (NormalRGB, Alpha, HeatR, ThickG, ErodeB)
    sheet = Image.new("RGB", (len(sample_frames) * CELL_SIZE, 5 * CELL_SIZE), (18, 20, 24))
    for col_i, f in enumerate(sample_frames):
        c, r = f % GRID_COLS, f // GRID_COLS
        y0, y1 = r * CELL_SIZE, (r + 1) * CELL_SIZE
        x0, x1 = c * CELL_SIZE, (c + 1) * CELL_SIZE
        na = arr_na[y0:y1, x0:x1]
        ht = arr_ht[y0:y1, x0:x1]

        panels = [
            Image.fromarray(na[:, :, :3], mode="RGB"),
            Image.fromarray(np.repeat(na[:, :, 3:4], 3, axis=2), mode="RGB"),
            Image.fromarray(np.stack([ht[:, :, 0], ht[:, :, 0] // 2, np.zeros_like(ht[:, :, 0])], axis=2), mode="RGB"),
            Image.fromarray(np.repeat(ht[:, :, 1:2], 3, axis=2), mode="RGB"),
            Image.fromarray(np.repeat(ht[:, :, 2:3], 3, axis=2), mode="RGB"),
        ]
        for row_i, p_img in enumerate(panels):
            sheet.paste(p_img, (col_i * CELL_SIZE, row_i * CELL_SIZE))

    out_path = preview_dir / "atlas_check.png"
    sheet.save(out_path, format="PNG")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="离线确定性烘焙 8×8 体积云爆炸图集")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="输出目录（默认 public/assets/vfx-explosion）")
    parser.add_argument("--seed", type=int, default=SEED, help="确定性随机种子")
    parser.add_argument("--verify", action="store_true", help="对输出目录内图集执行通道/边距/大小/演化校验")
    parser.add_argument("--verify-only", action="store_true", help="跳过烘焙，仅校验已有图集")
    parser.add_argument("--preview-out", type=Path, default=None, help="可选导出 5 帧×5 通道抽检拼图目录")
    args = parser.parse_args()

    out_dir = args.out_dir
    if not args.verify_only:
        out_dir.mkdir(parents=True, exist_ok=True)
        atlas_na, atlas_ht, stats = bake_atlases(seed=args.seed)
        na_path = out_dir / "smoke_vol_normal_alpha.png"
        ht_path = out_dir / "smoke_vol_heat_thick.png"
        Image.fromarray(atlas_na, mode="RGBA").save(na_path, format="PNG", optimize=True, compress_level=9)
        Image.fromarray(atlas_ht, mode="RGBA").save(ht_path, format="PNG", optimize=True, compress_level=9)
        print(
            f"[烘焙完成] seed={stats['seed']} 耗时={stats['total_sec']}s (噪声场={stats['noise_sec']}s) -> "
            f"{na_path.name} ({na_path.stat().st_size / 1024:.1f} KB), "
            f"{ht_path.name} ({ht_path.stat().st_size / 1024:.1f} KB)"
        )

    if args.verify or args.verify_only:
        v = verify_atlases(out_dir)
        print(f"[验证通过] {v}")

    if args.preview_out is not None:
        preview_file = export_preview_sheet(out_dir, args.preview_out)
        print(f"[抽检拼图] {preview_file}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
