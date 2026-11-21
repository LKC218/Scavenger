"""B1阶段：构建2m步距uint16分块高度图、多层/道路3D支撑补丁、Top-4 Splatmap + 12层TextureArray图集，以及道路样条与道面网格。"""
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
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/断箭"))
from 分块全图 import role_for

BATCH_DIR = ROOT / "local-data/断箭/批次/BA-26273833-012/Airbase全图/V001-01a1a602"
LAYERS_DIR = ROOT / "local-data/断箭/批次/BA-26273833-010/原始贴图/地表数组"
WORK_DIR = ROOT / "local-data/程序化地图/V001-fcf46de3"
B1_DIR = WORK_DIR / "B1-地形与道路"

N_GRID = 4501  # 9000m / 2.0m + 1
STEP = 2.0
MIN_X, MAX_X = -4425.0, 4575.0
MIN_Z, MAX_Z = -4150.0, 4850.0
MIN_H, MAX_H = -40.0, 120.0
GEOM_DT = np.dtype([("v", "<f4", (9,)), ("s", "<u4"), ("r", "<u4")])


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


def rasterize_triangles_to_grid(v0, v1, v2, role_val, target_h, target_r, target_ny):
    min_x = np.minimum(np.minimum(v0[:, 0], v1[:, 0]), v2[:, 0])
    max_x = np.maximum(np.maximum(v0[:, 0], v1[:, 0]), v2[:, 0])
    min_z = np.minimum(np.minimum(v0[:, 2], v1[:, 2]), v2[:, 2])
    max_z = np.maximum(np.maximum(v0[:, 2], v1[:, 2]), v2[:, 2])

    ix0 = np.clip(np.ceil((min_x - MIN_X - 1e-5) / STEP).astype(np.int32), 0, N_GRID - 1)
    ix1 = np.clip(np.floor((max_x - MIN_X + 1e-5) / STEP).astype(np.int32), 0, N_GRID - 1)
    iz0 = np.clip(np.ceil((min_z - MIN_Z - 1e-5) / STEP).astype(np.int32), 0, N_GRID - 1)
    iz1 = np.clip(np.floor((max_z - MIN_Z + 1e-5) / STEP).astype(np.int32), 0, N_GRID - 1)

    valid_box = (ix1 >= ix0) & (iz1 >= iz0)
    area = (v1[:, 0] - v0[:, 0]) * (v2[:, 2] - v0[:, 2]) - (v1[:, 2] - v0[:, 2]) * (v2[:, 0] - v0[:, 0])
    valid_box &= np.abs(area) > 1e-9

    indices = np.flatnonzero(valid_box)
    if len(indices) == 0:
        return
    v0, v1, v2 = v0[indices], v1[indices], v2[indices]
    ix0, ix1, iz0, iz1 = ix0[indices], ix1[indices], iz0[indices], iz1[indices]
    inv_area = 1.0 / area[indices]

    nx = (v1[:, 1] - v0[:, 1]) * (v2[:, 2] - v0[:, 2]) - (v1[:, 2] - v0[:, 2]) * (v2[:, 1] - v0[:, 1])
    ny = (v1[:, 2] - v0[:, 2]) * (v2[:, 0] - v0[:, 0]) - (v1[:, 0] - v0[:, 0]) * (v2[:, 2] - v0[:, 2])
    nz = (v1[:, 0] - v0[:, 0]) * (v2[:, 1] - v0[:, 1]) - (v1[:, 1] - v0[:, 1]) * (v2[:, 0] - v0[:, 0])
    norm_len = np.maximum(np.sqrt(nx * nx + ny * ny + nz * nz), 1e-12)
    tri_ny = np.abs(ny) / norm_len

    w = ix1 - ix0 + 1
    h = iz1 - iz0 + 1
    small = (w <= 16) & (h <= 16)
    if np.any(small):
        s_idx = np.flatnonzero(small)
        max_w = int(w[s_idx].max())
        max_h = int(h[s_idx].max())
        sv0, sv1, sv2 = v0[s_idx], v1[s_idx], v2[s_idx]
        six0, six1, siz0, siz1 = ix0[s_idx], ix1[s_idx], iz0[s_idx], iz1[s_idx]
        sinv = inv_area[s_idx]
        sny = tri_ny[s_idx]
        for dz in range(max_h):
            for dx in range(max_w):
                m = (six0 + dx <= six1) & (siz0 + dz <= siz1)
                if not np.any(m):
                    continue
                k = np.flatnonzero(m)
                gx = six0[k] + dx
                gz = siz0[k] + dz
                px = MIN_X + gx * STEP
                pz = MIN_Z + gz * STEP
                ax, ay, az = sv0[k, 0], sv0[k, 1], sv0[k, 2]
                bx, by, bz = sv1[k, 0], sv1[k, 1], sv1[k, 2]
                cx, cy, cz = sv2[k, 0], sv2[k, 1], sv2[k, 2]
                ia = sinv[k]
                wb = ((px - ax) * (cz - az) - (pz - az) * (cx - ax)) * ia
                wc = ((bx - ax) * (pz - az) - (bz - az) * (px - ax)) * ia
                wa = 1.0 - wb - wc
                inside = (wa >= -1e-6) & (wb >= -1e-6) & (wc >= -1e-6)
                if not np.any(inside):
                    continue
                gx, gz = gx[inside], gz[inside]
                hy = wa[inside] * ay[inside] + wb[inside] * by[inside] + wc[inside] * cy[inside]
                ny_val = sny[k[inside]]
                order = np.argsort(hy)
                gx, gz, hy, ny_val = gx[order], gz[order], hy[order], ny_val[order]
                cur = target_h[gz, gx]
                better = hy >= cur - 1e-5
                gx, gz, hy, ny_val = gx[better], gz[better], hy[better], ny_val[better]
                target_h[gz, gx] = hy
                if target_r is not None:
                    target_r[gz, gx] = role_val
                if target_ny is not None:
                    target_ny[gz, gx] = np.clip(np.round(ny_val * 255.0), 0, 255).astype(np.uint8)

    for i in np.flatnonzero(~small):
        gx_arr = np.arange(ix0[i], ix1[i] + 1)
        gz_arr = np.arange(iz0[i], iz1[i] + 1)
        xx, zz = np.meshgrid(MIN_X + gx_arr * STEP, MIN_Z + gz_arr * STEP)
        ax, ay, az = v0[i]
        bx, by, bz = v1[i]
        cx, cy, cz = v2[i]
        ia = inv_area[i]
        wb = ((xx - ax) * (cz - az) - (zz - az) * (cx - ax)) * ia
        wc = ((bx - ax) * (zz - az) - (bz - az) * (xx - ax)) * ia
        wa = 1.0 - wb - wc
        inside = (wa >= -1e-6) & (wb >= -1e-6) & (wc >= -1e-6)
        if np.any(inside):
            hy = wa[inside] * ay + wb[inside] * by + wc[inside] * cy
            sub_h = target_h[iz0[i]:iz1[i] + 1, ix0[i]:ix1[i] + 1]
            better = np.zeros_like(inside)
            better[inside] = hy >= sub_h[inside] - 1e-5
            if np.any(better):
                sub_h[better] = wa[better] * ay + wb[better] * by + wc[better] * cy
                if target_r is not None:
                    target_r[iz0[i]:iz1[i] + 1, ix0[i]:ix1[i] + 1][better] = role_val
                if target_ny is not None:
                    target_ny[iz0[i]:iz1[i] + 1, ix0[i]:ix1[i] + 1][better] = int(np.clip(round(tri_ny[i] * 255.0), 0, 255))


def build_heightfield_and_support(b1_dir: Path, ds: dict) -> dict:
    t0 = time.time()
    hf_dir = b1_dir / "heightfield"
    hf_dir.mkdir(parents=True, exist_ok=True)

    source_dir = BATCH_DIR / "表面来源/full-r01"
    chunks_dir = BATCH_DIR / "全图分块/r02"

    height_grid = np.full((N_GRID, N_GRID), -1e9, dtype=np.float32)
    bg_grid = np.full((N_GRID, N_GRID), -1e9, dtype=np.float32)
    role_grid = np.zeros((N_GRID, N_GRID), dtype=np.uint8)
    ny_grid = np.full((N_GRID, N_GRID), 255, dtype=np.uint8)
    bg_ny_grid = np.full((N_GRID, N_GRID), 255, dtype=np.uint8)

    role_batches = {1: [], 3: [], 2: [], 4: []}
    for part in ds["parts"]:
        with np.load(source_dir / part["file"]) as a:
            pos = a["vertices"][:, [0, 2, 1]].astype(np.float32)
            pos[:, 2] *= -1.0
            tris = a["triangles"]
            slots = a["material_indices"]
        for slot, key in enumerate(part["materials"]):
            role = role_for(part, ds["materials"][key])
            if role in role_batches:
                sel = tris[slots == slot]
                if len(sel):
                    role_batches[role].append(pos[sel])

    for r in (1, 3, 2):
        for faces in role_batches[r]:
            rasterize_triangles_to_grid(faces[:, 0], faces[:, 1], faces[:, 2], r, height_grid, role_grid, ny_grid)
    for faces in role_batches[4]:
        rasterize_triangles_to_grid(faces[:, 0], faces[:, 1], faces[:, 2], 4, bg_grid, None, bg_ny_grid)

    mask_bg = (height_grid < -1e8) & (bg_grid > -1e8)
    height_grid[mask_bg] = bg_grid[mask_bg]
    role_grid[mask_bg] = 4
    ny_grid[mask_bg] = bg_ny_grid[mask_bg]

    q_grid = np.clip(np.round((height_grid - MIN_H) / (MAX_H - MIN_H) * 65534.0), 0, 65534).astype(np.uint16)
    q_grid[height_grid < -1e8] = 65535
    dq_grid = MIN_H + (q_grid.astype(np.float64) / 65534.0) * (MAX_H - MIN_H)

    # Save global compressed uint16 heightmap (SCVH format)
    header = struct.pack("<4sIIIffffff", b"SCVH", 1, N_GRID, N_GRID, MIN_X, MIN_Z, STEP, STEP, MIN_H, MAX_H)
    delta_x = np.diff(q_grid.astype(np.uint16), axis=1, prepend=np.uint16(0))
    delta_xz = np.diff(delta_x, axis=0, prepend=np.uint16(0)).astype(np.uint16)
    scvh_raw = header + delta_xz.tobytes() + role_grid.tobytes() + ny_grid.tobytes()
    scvh_comp = zlib.compress(scvh_raw, level=6)
    scvh_path = hf_dir / "global-height-uint16.scvh"
    scvh_path.write_bytes(scvh_comp)

    # Copy coarse-height.bin (513x513 BACH format)
    src_coarse = BATCH_DIR / "粗层/r01/粗层高度.bin"
    dst_coarse = hf_dir / "粗层高度.bin"
    shutil.copy2(src_coarse, dst_coarse)

    # Identify chunks that have multi-layer verification samples in 样点.json
    samples_list = json.loads((chunks_dir / "支撑核验/r02/样点.json").read_text("utf-8"))
    multilayer_chunks = {s["chunk"] for s in samples_list if s.get("feature") == "下层支撑"}

    # Build 36x36 (250m x 250m) 3D Support Patches (only terrain/road/water support, no B3 building collision!)
    idx_data = json.loads((chunks_dir / "地表索引.json").read_text("utf-8"))
    chunk_map = {c["id"]: c for c in idx_data["chunks"]}

    def bilinear_dq(px_arr, pz_arr):
        gx = (px_arr - MIN_X) / STEP
        gz = (pz_arr - MIN_Z) / STEP
        ix = np.clip(np.floor(gx).astype(np.int32), 0, N_GRID - 2)
        iz = np.clip(np.floor(gz).astype(np.int32), 0, N_GRID - 2)
        ax = gx - ix
        az = gz - iz
        h00 = dq_grid[iz, ix]
        h10 = dq_grid[iz, ix + 1]
        h01 = dq_grid[iz + 1, ix]
        h11 = dq_grid[iz + 1, ix + 1]
        return (h00 * (1.0 - ax) + h10 * ax) * (1.0 - az) + (h01 * (1.0 - ax) + h11 * ax) * az

    tiles_dir = hf_dir / "support-patches"
    tiles_dir.mkdir(parents=True, exist_ok=True)
    tile_records = {}
    total_patch_tris = 0
    total_orig_tris = 0
    total_patch_bytes = 0

    for pz in range(36):
        for px in range(36):
            t_key = f"{px:02}-{pz:02}"
            entries = []
            for z in range(pz * 2, pz * 2 + 2):
                for x in range(px * 2, px * 2 + 2):
                    c_id = f"{x:02}-{z:02}"
                    c_row = chunk_map[c_id]
                    if c_row.get("empty"):
                        bage_patch = struct.pack("<4sIIIIfff", b"BAGE", 1, 0, 8, 0, c_row["bounds"][0] + 62.5, c_row["bounds"][2] + 62.5, 125.0) + bytes(65 * 4)
                        entries.append((z * 72 + x, bage_patch))
                        continue

                    raw = (chunks_dir / c_row["geometry"]).read_bytes()
                    _, _, count, bins, refs, ox, oz, sz = struct.unpack_from("<4sIIIIfff", raw, 0)
                    total_orig_tris += count
                    arr = np.frombuffer(raw, dtype=GEOM_DT, offset=32, count=count)

                    valid_role = np.isin(arr["r"], [1, 2, 3, 4, 5])
                    arr_v = arr[valid_role]
                    if len(arr_v) == 0:
                        bage_patch = struct.pack("<4sIIIIfff", b"BAGE", 1, 0, 8, 0, ox, oz, sz) + bytes(65 * 4)
                        entries.append((z * 72 + x, bage_patch))
                        continue

                    verts = arr_v["v"].reshape(-1, 3, 3)
                    roles = arr_v["r"]

                    wx0, wz0 = verts[:, 0, 0] + ox, verts[:, 0, 2] + oz
                    wx1, wz1 = verts[:, 1, 0] + ox, verts[:, 1, 2] + oz
                    wx2, wz2 = verts[:, 2, 0] + ox, verts[:, 2, 2] + oz
                    wxc, wzc = (wx0 + wx1 + wx2) / 3.0, (wz0 + wz1 + wz2) / 3.0
                    yc = (verts[:, 0, 1] + verts[:, 1, 1] + verts[:, 2, 1]) / 3.0

                    err0 = np.abs(bilinear_dq(wx0, wz0) - verts[:, 0, 1])
                    err1 = np.abs(bilinear_dq(wx1, wz1) - verts[:, 1, 1])
                    err2 = np.abs(bilinear_dq(wx2, wz2) - verts[:, 2, 1])
                    errc = np.abs(bilinear_dq(wxc, wzc) - yc)
                    max_tri_err = np.maximum(np.maximum(err0, err1), np.maximum(err2, errc))

                    # Keep only:
                    # 1) role 2 (road) and role 5 (water)
                    # 2) triangles where 2m uint16 bilinear interpolation error > 0.012m (steep embankments/curbs)
                    # 3) all valid triangles in the 6 multilayer chunks
                    if c_id in multilayer_chunks:
                        keep = np.ones(len(arr_v), dtype=bool)
                    else:
                        keep = np.isin(roles, [2, 5]) | ((roles != 4) & (max_tri_err > 0.012))
                        # Also check if any of the 9 interior grid sample points in this chunk has bilinear_dq deviation > 0.012m
                        tri_min_x = verts[:, :, 0].min(axis=1)
                        tri_max_x = verts[:, :, 0].max(axis=1)
                        tri_min_z = verts[:, :, 2].min(axis=1)
                        tri_max_z = verts[:, :, 2].max(axis=1)
                        for fz in (-50.0, 0.0, 50.0):
                            for fx in (-50.0, 0.0, 50.0):
                                hit_box = (roles != 4) & (tri_min_x <= fx + 0.1) & (tri_max_x >= fx - 0.1) & (tri_min_z <= fz + 0.1) & (tri_max_z >= fz - 0.1)
                                if np.any(hit_box):
                                    # Evaluate triangle plane at (fx, fz) for candidates
                                    cand = np.flatnonzero(hit_box)
                                    ca = verts[cand, 0]
                                    cb = verts[cand, 1]
                                    cc = verts[cand, 2]
                                    area = (cb[:, 0] - ca[:, 0]) * (cc[:, 2] - ca[:, 2]) - (cb[:, 2] - ca[:, 2]) * (cc[:, 0] - ca[:, 0])
                                    valid_a = np.abs(area) > 1e-9
                                    if np.any(valid_a):
                                        cand = cand[valid_a]
                                        ca, cb, cc, area = ca[valid_a], cb[valid_a], cc[valid_a], area[valid_a]
                                        wb = ((fx - ca[:, 0]) * (cc[:, 2] - ca[:, 2]) - (fz - ca[:, 2]) * (cc[:, 0] - ca[:, 0])) / area
                                        wc = ((cb[:, 0] - ca[:, 0]) * (fz - ca[:, 2]) - (cb[:, 2] - ca[:, 2]) * (fx - ca[:, 0])) / area
                                        wa = 1.0 - wb - wc
                                        ins = (wa >= -1e-6) & (wb >= -1e-6) & (wc >= -1e-6)
                                        if np.any(ins):
                                            cand = cand[ins]
                                            th = wa[ins] * ca[ins, 1] + wb[ins] * cb[ins, 1] + wc[ins] * cc[ins, 1]
                                            gh = float(bilinear_dq(np.array([ox + fx]), np.array([oz + fz]))[0])
                                            if np.any(np.abs(th - gh) > 0.012):
                                                keep[cand] = True

                    kept_arr = arr_v[keep]
                    k_count = len(kept_arr)
                    total_patch_tris += k_count

                    lists = [[] for _ in range(64)]
                    if k_count > 0:
                        k_pos = kept_arr["v"].reshape(-1, 3, 3)
                        low = np.clip(np.floor((k_pos[:, :, [0, 2]].min(1) + 62.5) / 125.0 * 8.0), 0, 7).astype(int)
                        high = np.clip(np.floor((k_pos[:, :, [0, 2]].max(1) + 62.5) / 125.0 * 8.0), 0, 7).astype(int)
                        for i_t, (a_b, b_b) in enumerate(zip(low, high)):
                            for bz in range(a_b[1], b_b[1] + 1):
                                for bx in range(a_b[0], b_b[0] + 1):
                                    lists[bz * 8 + bx].append(i_t)
                    offsets = np.asarray([0, *np.cumsum([len(v) for v in lists])], dtype="<u4")
                    ref_arr = np.asarray([i_t for values in lists for i_t in values], dtype="<u4")
                    bage_patch = (
                        struct.pack("<4sIIIIfff", b"BAGE", 1, k_count, 8, len(ref_arr), ox, oz, 125.0)
                        + kept_arr.tobytes()
                        + offsets.tobytes()
                        + ref_arr.tobytes()
                    )
                    entries.append((z * 72 + x, bage_patch))

            payload = bytearray(struct.pack("<4sIII", b"SCVP", 1, len(entries), 12))
            payload.extend(bytes(len(entries) * 12))
            for i_e, (cell_idx, a_bytes) in enumerate(entries):
                ao = len(payload)
                payload.extend(a_bytes)
                struct.pack_into("<3I", payload, 16 + i_e * 12, cell_idx, ao, len(a_bytes))

            comp_payload = zlib.compress(bytes(payload), level=6)
            tile_file = tiles_dir / f"{t_key}.scvp"
            tile_file.write_bytes(comp_payload)
            total_patch_bytes += len(comp_payload)
            tile_records[t_key] = {
                "file": f"heightfield/support-patches/{t_key}.scvp",
                "raw_bytes": len(payload),
                "identity": fingerprint(tile_file),
            }

    return {
        "global_height_file": "heightfield/global-height-uint16.scvh",
        "global_height_identity": fingerprint(scvh_path),
        "coarse_height_file": "heightfield/粗层高度.bin",
        "coarse_height_identity": fingerprint(dst_coarse),
        "tiles_count": len(tile_records),
        "tiles": tile_records,
        "original_bage_triangles": total_orig_tris,
        "patch_triangles": total_patch_tris,
        "triangles_reduction_ratio": round(1.0 - total_patch_tris / max(1, total_orig_tris), 4),
        "support_patches_total_bytes": total_patch_bytes,
        "elapsed_s": round(time.time() - t0, 2),
        "q_grid": q_grid,
        "dq_grid": dq_grid,
        "role_grid": role_grid,
        "ny_grid": ny_grid,
    }


def build_splatmap_and_texture_array(b1_dir: Path, role_grid: np.ndarray) -> dict:
    t0 = time.time()
    splat_dir = b1_dir / "splatmap"
    splat_dir.mkdir(parents=True, exist_ok=True)

    params_src = json.loads((BATCH_DIR / "地表派生/r03/输入地表参数.json").read_text("utf-8"))["data"]
    light_color = params_src["LightColor"][:3]
    layer_meta = []
    TILE_RES = 512
    atlas_img = np.zeros((3 * TILE_RES, 4 * TILE_RES, 4), dtype=np.uint8)

    for i in range(12):
        key = f"_layer{i // 4}{'rgba'[i % 4]}"
        p = params_src[key]
        src_path = LAYERS_DIR / f"底色-{i}.png"
        with Image.open(src_path) as im:
            im_resized = im.convert("RGB").resize((TILE_RES, TILE_RES), Image.Resampling.LANCZOS)
            arr = np.asarray(im_resized, dtype=np.float32) / 255.0
        lin = np.where(arr <= 0.04045, arr / 12.92, ((arr + 0.055) / 1.055) ** 2.4)
        lin = lin * np.array(p["Light"][:3], dtype=np.float32) * np.array(light_color, dtype=np.float32)
        srgb = np.where(lin <= 0.0031308, lin * 12.92, 1.055 * np.maximum(lin, 0.0) ** (1.0 / 2.4) - 0.055)
        rgb_u8 = np.clip(np.round(srgb * 255.0), 0, 255).astype(np.uint8)

        # Set Alpha=255 on atlas_img so Canvas 2D never premultiplies RGB; store micro-detail luma in a separate compact array or compute in shader from RGB
        rgba = np.dstack([rgb_u8, np.full((TILE_RES, TILE_RES), 255, dtype=np.uint8)])
        row, col = i // 4, i % 4
        atlas_img[row * TILE_RES:(row + 1) * TILE_RES, col * TILE_RES:(col + 1) * TILE_RES] = rgba

        layer_meta.append({
            "index": i,
            "key": key,
            "tile_per_3km": p["Tile"],
            "world_uv_scale": [p["Tile"][0] / 3000.0, p["Tile"][1] / 3000.0],
            "angle_rad": float(np.deg2rad(p["Angle"])),
            "smoothness": p["Smoothness"],
            "normal_strength": p["NormalStrange"],
        })

    # Save 12-layer 512x512 TextureArray atlas as high-quality WebP (supported natively by Chrome/Edge createImageBitmap)
    array_atlas_path = splat_dir / "terrain-layers-12x512.webp"
    Image.fromarray(atlas_img, mode="RGBA").save(array_atlas_path, format="WEBP", quality=92, method=4)

    SPLAT_SEC = 384
    SPLAT_TOTAL = 3 * SPLAT_SEC  # 1152
    top4_idx = np.zeros((SPLAT_TOTAL, SPLAT_TOTAL, 4), dtype=np.uint8)
    top4_wgt = np.zeros((SPLAT_TOTAL, SPLAT_TOTAL, 4), dtype=np.uint8)
    macro_rgba = np.zeros((SPLAT_TOTAL, SPLAT_TOTAL, 4), dtype=np.uint8)

    atlas_info = json.loads((BATCH_DIR / "地表派生/r03/地表图集.json").read_text("utf-8"))
    raw_tex_dir = BATCH_DIR / "原始贴图"

    for part in atlas_info["parts"]:
        pid = part["source_key"].split(":")[1]
        bx0, bx1, bz0, bz1 = part["bounds_source_xz"]
        sx = int(bx0 // 3000)
        sz = 2 - int(bz0 // 3000)

        mix_info = json.loads((BATCH_DIR / f"地表派生/r03/地表混合-{pid}.json").read_text("utf-8"))
        cont = mix_info["container"]
        splat_arrs = []
        for skey in ("Splat0", "Splat1", "Splat2"):
            tex_id = cont[skey]["m_PathID"]
            tex_path = raw_tex_dir / f"sharedassets7_assets-{tex_id}.png"
            with Image.open(tex_path) as sim:
                # 严禁对 RGBA 直接调用 PIL.resize（PIL 会将 R,G,B 乘以 Alpha 通道导致 A=0 处前三个生态通道清零！）
                # 必须拆分为 4 个独立单通道 ("L") 分别执行 BOX 降采样与高斯平滑
                ch_list = []
                for ch_im in sim.convert("RGBA").split():
                    c_low = ch_im.resize((256, 256), Image.Resampling.BOX)
                    c_smooth = c_low.filter(ImageFilter.GaussianBlur(radius=1.4)).resize((SPLAT_SEC, SPLAT_SEC), Image.Resampling.BICUBIC)
                    ch_list.append(np.asarray(c_smooth, dtype=np.float32) / 255.0)
                splat_arrs.append(np.stack(ch_list, axis=-1))
        weights_12 = np.concatenate(splat_arrs, axis=-1)
        w_sum_raw = weights_12.sum(axis=-1, keepdims=True)
        # 若源 Splat 存在极少数全 0 像素，回退到通道 0（草地）
        zero_mask = (w_sum_raw[..., 0] < 1e-4)
        if np.any(zero_mask):
            weights_12[zero_mask, 0] = 1.0
            w_sum_raw = weights_12.sum(axis=-1, keepdims=True)
        weights_12 /= np.maximum(w_sum_raw, 1e-6)

        # Map 12 source layers into 8 fixed canonical biome channels so hardware LinearFilter interpolates smoothly with 0 stair-steps:
        # Ch 0: Layer 0 (Lush Grass)
        # Ch 1: Layer 1 (Meadow Grass)
        # Ch 2: Layer 2 + Layer 11 (Dirt / Soil Track)
        # Ch 3: Layer 3 (Sand / Dry Earth)
        # Ch 4: Layer 4 + Layer 5 (Forest Floor / Dark Grass)
        # Ch 5: Layer 6 + Layer 7 (Farmland / Ploughed Field)
        # Ch 6: Layer 8 + Layer 9 (Gravel / Rock Slope)
        # Ch 7: Layer 10 (Asphalt / Concrete Base)
        w8 = np.zeros((SPLAT_SEC, SPLAT_SEC, 8), dtype=np.float32)
        w8[..., 0] = weights_12[..., 0]
        w8[..., 1] = weights_12[..., 1]
        w8[..., 2] = weights_12[..., 2] + weights_12[..., 11]
        w8[..., 3] = weights_12[..., 3]
        w8[..., 4] = weights_12[..., 4] + weights_12[..., 5]
        w8[..., 5] = weights_12[..., 6] + weights_12[..., 7]
        w8[..., 6] = weights_12[..., 8] + weights_12[..., 9]
        w8[..., 7] = weights_12[..., 10]
        w8_sum = np.maximum(w8.sum(axis=-1, keepdims=True), 1e-6)
        w8_u8 = (np.clip(np.round((w8 / w8_sum) * 255.0), 0, 255).astype(np.uint8) // 8) * 8

        with Image.open(BATCH_DIR / "地表派生/r03" / part["file"]) as cim:
            c_low = cim.convert("RGB").resize((256, 256), Image.Resampling.BOX)
            c_res = c_low.filter(ImageFilter.GaussianBlur(radius=1.5)).resize((SPLAT_SEC, SPLAT_SEC), Image.Resampling.BICUBIC)
            c_rgb = np.asarray(c_res, dtype=np.uint8)

        grass_w = (
            weights_12[..., 0] * 0.9
            + weights_12[..., 1] * 1.0
            + weights_12[..., 2] * 0.7
            + weights_12[..., 4] * 0.8
            + weights_12[..., 5] * 0.8
        )
        greenness = np.clip((c_rgb[..., 1].astype(np.float32) * 1.15 - c_rgb[..., 0].astype(np.float32) * 0.65 - c_rgb[..., 2].astype(np.float32) * 0.4) / 60.0, 0.0, 1.0)
        grass_u8 = (np.clip(np.round(np.clip(grass_w * 0.6 + greenness * 0.6, 0.0, 1.0) * 255.0), 0, 255).astype(np.uint8) // 8) * 8

        r0, r1 = sz * SPLAT_SEC, (sz + 1) * SPLAT_SEC
        c0, c1 = sx * SPLAT_SEC, (sx + 1) * SPLAT_SEC
        top4_idx[r0:r1, c0:c1] = w8_u8[..., :4]
        top4_wgt[r0:r1, c0:c1] = w8_u8[..., 4:8]
        macro_rgba[r0:r1, c0:c1, :3] = c_rgb
        macro_rgba[r0:r1, c0:c1, 3] = grass_u8

    role_down = np.asarray(
        Image.fromarray(role_grid).resize((SPLAT_TOTAL, SPLAT_TOTAL), Image.Resampling.NEAREST)
    )
    non_grass_mask = np.isin(role_down, [2, 3, 5])
    grass_channel = macro_rgba[..., 3].copy()
    grass_channel[non_grass_mask] = 0
    # Force Alpha=255 on macro_rgba so WebP and Canvas 2D never premultiply or zero out RGB!
    macro_rgba[..., 3] = 255

    # Save bit-exact top4_idx (4B), top4_wgt (4B), and grass_channel (1B) in lossless binary SCVS format (no Canvas 2D alpha premultiplication!)
    scvs_header = struct.pack("<4sIII", b"SCVS", 1, SPLAT_TOTAL, SPLAT_TOTAL)
    scvs_raw = scvs_header + top4_idx.tobytes() + top4_wgt.tobytes() + grass_channel.tobytes()
    scvs_comp = zlib.compress(scvs_raw, level=9)
    scvs_path = splat_dir / "splat-control.scvs"
    scvs_path.write_bytes(scvs_comp)

    macro_path = splat_dir / "macro-tint.webp"
    Image.fromarray(macro_rgba[..., :3], mode="RGB").save(macro_path, format="WEBP", quality=92, method=4)

    meta_path = splat_dir / "splat-config.json"
    meta = {
        "schema": "scavenger-splatmap-config-v1",
        "bounds": {"minX": MIN_X, "maxX": MAX_X, "minZ": MIN_Z, "maxZ": MAX_Z},
        "array_atlas": {"file": "splatmap/terrain-layers-12x512.webp", "tile_size": TILE_RES, "layers": 12, "grid": [4, 3], "identity": fingerprint(array_atlas_path)},
        "splat_control": {"file": "splatmap/splat-control.scvs", "resolution": SPLAT_TOTAL, "identity": fingerprint(scvs_path)},
        "macro_tint": {"file": "splatmap/macro-tint.webp", "resolution": SPLAT_TOTAL, "identity": fingerprint(macro_path)},
        "layers": layer_meta,
        "light_color": light_color,
        "elapsed_s": round(time.time() - t0, 2),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return meta


def build_road_splines_and_surfaces(b1_dir: Path, ds: dict) -> dict:
    t0 = time.time()
    roads_dir = b1_dir / "roads"
    roads_dir.mkdir(parents=True, exist_ok=True)
    source_dir = BATCH_DIR / "表面来源/full-r01"

    splines = []
    road_parts = [p for p in ds["parts"] if p["category"] == "road"]
    for part in road_parts:
        with np.load(source_dir / part["file"]) as a:
            pos = a["vertices"][:, [0, 2, 1]].astype(np.float32)
            pos[:, 2] *= -1.0
            uv = a["uv0"].astype(np.float32)
            tris = a["triangles"]
        f_pos = pos[tris]
        f_uv = uv[tris]
        cross_points = []
        for ti in range(0, len(tris), 2):
            tri_v = f_pos[ti]
            tri_u = f_uv[ti, :, 0]
            left = tri_v[tri_u < 0.2]
            right = tri_v[tri_u > 0.8]
            if len(left) >= 1 and len(right) >= 1:
                p_l = left[0]
                p_r = right[0]
                mid = (p_l + p_r) * 0.5
                width = float(np.linalg.norm(p_r[[0, 2]] - p_l[[0, 2]]))
                if 2.0 <= width <= 60.0:
                    cross_points.append((float(mid[0]), float(mid[1]), float(mid[2]), round(width, 2)))

        current_line = []
        for pt in cross_points:
            if not current_line:
                current_line.append(pt)
            else:
                prev = current_line[-1]
                d = math.hypot(pt[0] - prev[0], pt[2] - prev[2])
                if 1.5 <= d <= 40.0:
                    current_line.append(pt)
                elif d > 40.0:
                    if len(current_line) >= 2:
                        step = max(1, len(current_line) // 24)
                        pts = current_line[::step]
                        if pts[-1] != current_line[-1]:
                            pts.append(current_line[-1])
                        splines.append({
                            "id": f"road-spline-{len(splines)+1:04d}",
                            "source_key": part["source_key"],
                            "material": part["materials"][0],
                            "width": round(float(np.median([p[3] for p in current_line])), 2),
                            "points": [[round(p[0], 2), round(p[1], 3), round(p[2], 2)] for p in pts],
                        })
                    current_line = [pt]
        if len(current_line) >= 2:
            step = max(1, len(current_line) // 24)
            pts = current_line[::step]
            if pts[-1] != current_line[-1]:
                pts.append(current_line[-1])
            splines.append({
                "id": f"road-spline-{len(splines)+1:04d}",
                "source_key": part["source_key"],
                "material": part["materials"][0],
                "width": round(float(np.median([p[3] for p in current_line])), 2),
                "points": [[round(p[0], 2), round(p[1], 3), round(p[2], 2)] for p in pts],
            })

    roads_json_path = roads_dir / "roads.json"
    roads_json_data = {
        "schema": "scavenger-road-splines-v1",
        "spline_count": len(splines),
        "total_control_points": sum(len(s["points"]) for s in splines),
        "splines": splines,
    }
    roads_json_path.write_text(json.dumps(roads_json_data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")

    bin_buf = bytearray(struct.pack("<4sII", b"SCVR", 1, len(splines)))
    for sp in splines:
        pts = sp["points"]
        bin_buf.extend(struct.pack("<fI", sp["width"], len(pts)))
        for p in pts:
            bin_buf.extend(struct.pack("<fff", p[0], p[1], p[2]))
    roads_bin_path = roads_dir / "roads.bin"
    roads_bin_path.write_bytes(zlib.compress(bytes(bin_buf), level=6))

    from 地图GLB import write_glb
    raw_road_dir = roads_dir / "raw"
    raw_road_dir.mkdir(parents=True, exist_ok=True)
    (raw_road_dir / "贴图").mkdir(exist_ok=True)

    materials, primitives, textures = {}, [], {}
    for part in ds["parts"]:
        if part["category"] not in ("road", "direct"):
            continue
        with np.load(source_dir / part["file"]) as data:
            vertices = data["vertices"][:, [0, 2, 1]].copy()
            vertices[:, 2] *= -1
            normals = data["normals"][:, [0, 2, 1]].copy()
            normals[:, 2] *= -1
            triangles, slots = data["triangles"], data["material_indices"]
            uv = data["uv0"].copy()
            uv[:, 1] = 1.0 - uv[:, 1]

        for slot, key in enumerate(part["materials"]):
            m = ds["materials"][key]
            r_role = role_for(part, m)
            if r_role == 7:
                continue
            if key not in materials:
                record = {
                    "name": m["name"],
                    "source_key": key,
                    "color": m.get("color", [1, 1, 1, 1]),
                    "alpha_mode": m["alpha_mode"],
                    "alpha_cutoff": m["alpha_cutoff"],
                    "double_sided": m["double_sided"],
                    "render_role": r_role,
                }
                if m.get("basecolor"):
                    image = Path(m["basecolor"])
                    digest = sha256_file(image)
                    if digest not in textures:
                        target = raw_road_dir / "贴图" / f"{digest}.png"
                        with Image.open(image) as pixels:
                            pixels = pixels.convert("RGBA")
                            pixels.thumbnail((512, 512), Image.Resampling.LANCZOS)
                            pixels.save(target)
                        textures[digest] = target.relative_to(raw_road_dir).as_posix()
                    record["image"] = textures[digest]
                    info = next(v for v in m["textures"].values() if v["file"] == m["basecolor"])
                    record["scale"] = [info["scale"]["x"], info["scale"]["y"]]
                    record["offset"] = [info["offset"]["x"], info["offset"]["y"]]
                materials[key] = record
            faces = triangles[slots == slot]
            if len(faces):
                face_normals = normals[faces].copy()
                geometric = np.cross(vertices[faces[:, 1]] - vertices[faces[:, 0]], vertices[faces[:, 2]] - vertices[faces[:, 0]])
                geometric /= np.maximum(np.linalg.norm(geometric, axis=1, keepdims=True), 1e-30)
                face_normals = np.where((np.linalg.norm(face_normals, axis=2) < 1e-8)[:, :, None], geometric[:, None], face_normals)
                face_normals /= np.maximum(np.linalg.norm(face_normals, axis=2, keepdims=True), 1e-30)
                primitives.append({
                    "material": key,
                    "attributes": {
                        "POSITION": vertices[faces].reshape(-1, 3),
                        "NORMAL": face_normals.reshape(-1, 3),
                        "TEXCOORD_0": uv[faces].reshape(-1, 2),
                    },
                    "extras": {"source_key": part["source_key"], "category": part["category"], "render_role": r_role},
                })

    raw_glb_path = raw_road_dir / "roads-raw.glb"
    write_glb(raw_glb_path, primitives, materials)

    opt_glb_path = roads_dir / "道面与标线.glb"
    node_script = """
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';

const root = path.resolve(process.argv[1]);
const inputGlb = path.resolve(process.argv[2]);
const outputGlb = path.resolve(process.argv[3]);
const batchDir = path.resolve(process.argv[4]);

const modules = path.join(root, 'local-data/tools/网页资源优化/1/node_modules');
const load = p => import(pathToFileURL(path.join(modules, p)).href);
const require = createRequire(path.join(modules, '../package.json'));
const sharp = require('sharp');
const {NodeIO} = await load('@gltf-transform/core/dist/index.js');
const {ALL_EXTENSIONS, EXTMeshoptCompression, KHRTextureBasisu} = await load('@gltf-transform/extensions/dist/index.js');
const {weld, dedup} = await load('@gltf-transform/functions/dist/index.js');
const {MeshoptEncoder, MeshoptDecoder} = await load('meshoptimizer/index.js');
const {encodeToKTX2} = await load('ktx2-encoder/dist/node/index.js');
await Promise.all([MeshoptEncoder.ready, MeshoptDecoder.ready]);

const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({'meshopt.encoder': MeshoptEncoder, 'meshopt.decoder': MeshoptDecoder});
const sha = b => crypto.createHash('sha256').update(b).digest('hex');
const decoder = async b => {
  const {data, info} = await sharp(b).ensureAlpha().raw().toBuffer({resolveWithObject: true});
  return {data: new Uint8Array(data), width: info.width, height: info.height};
};

const doc = await io.read(inputGlb);
await doc.transform(dedup(), weld());

for (const texture of doc.getRoot().listTextures()) {
  const image = texture.getImage();
  const info = await sharp(image).metadata();
  const size = 512;
  const scale = Math.min(1, size / Math.max(info.width, info.height));
  const width = 2 ** Math.ceil(Math.log2(Math.max(1, Math.round(info.width * scale))));
  const height = 2 ** Math.ceil(Math.log2(Math.max(1, Math.round(info.height * scale))));
  const key = sha(Buffer.concat([Buffer.from(image), Buffer.from(`|${width}:${height}|ETC1S200mip`)]));
  const reused = [
    path.join(batchDir, '远景运行/r08/编码缓存', key + '.ktx2'),
    path.join(batchDir, '远景运行/r01/编码缓存', key + '.ktx2'),
    path.join(batchDir, '全图运行/r01/编码缓存', key + '.ktx2'),
  ].find(p => fs.existsSync(p));
  let ktx2Bytes;
  if (reused) {
    ktx2Bytes = fs.readFileSync(reused);
  } else {
    const png = await sharp(image).resize(width, height, {fit: 'fill', kernel: 'cubic'}).png().toBuffer();
    ktx2Bytes = await encodeToKTX2(new Uint8Array(png), {imageDecoder: decoder, isUASTC: false, qualityLevel: 200, generateMipmap: true, isInputSRGB: true, isSetKTX2SRGBTransferFunc: true, enableDebug: false});
  }
  texture.setImage(new Uint8Array(ktx2Bytes)).setMimeType('image/ktx2');
}

doc.createExtension(KHRTextureBasisu).setRequired(true);
doc.createExtension(EXTMeshoptCompression).setRequired(true).setEncoderOptions({method: EXTMeshoptCompression.EncoderMethod.QUANTIZE});
await io.write(outputGlb, doc);
"""
    subprocess.run(
        ["node", "--input-type=module", "-e", node_script, str(ROOT), str(raw_glb_path), str(opt_glb_path), str(BATCH_DIR)],
        cwd=ROOT,
        check=True,
    )
    shutil.rmtree(raw_road_dir)

    water_dir = b1_dir / "water"
    water_dir.mkdir(parents=True, exist_ok=True)
    (water_dir / "贴图").mkdir(exist_ok=True)
    src_water = BATCH_DIR / "水面运行/r02"
    shutil.copy2(src_water / "水面.glb", water_dir / "水面.glb")
    for f in (src_water / "贴图").glob("*"):
        shutil.copy2(f, water_dir / "贴图" / f.name)

    return {
        "roads_json": {"file": "roads/roads.json", "identity": fingerprint(roads_json_path), "spline_count": len(splines)},
        "roads_bin": {"file": "roads/roads.bin", "identity": fingerprint(roads_bin_path)},
        "roads_surface_glb": {"file": "roads/道面与标线.glb", "identity": fingerprint(opt_glb_path)},
        "water_glb": {"file": "water/水面.glb", "identity": fingerprint(water_dir / "水面.glb")},
        "elapsed_s": round(time.time() - t0, 2),
    }


def main() -> None:
    only_hf = "--height-only" in sys.argv and (B1_DIR / "地形与道路清单.json").exists()
    only_splat = "--splat-only" in sys.argv and (B1_DIR / "地形与道路清单.json").exists()
    only_roads = "--roads-only" in sys.argv and (B1_DIR / "地形与道路清单.json").exists()
    if not only_hf and not only_splat and not only_roads:
        if B1_DIR.exists():
            shutil.rmtree(B1_DIR)
        B1_DIR.mkdir(parents=True, exist_ok=True)

    ds = json.loads((BATCH_DIR / "表面来源/full-r01/直接表面.json").read_text("utf-8"))
    if only_roads:
        prev = json.loads((B1_DIR / "地形与道路清单.json").read_text("utf-8"))
        hf_res = prev["heightfield"]
        hf_res["global_height_file"] = hf_res["global_file"]
        hf_res["global_height_identity"] = hf_res["global_identity"]
        hf_res["coarse_height_file"] = hf_res["coarse_file"]
        hf_res["coarse_height_identity"] = hf_res["coarse_identity"]
        splat_res = prev["splatmap"]
        road_res = build_road_splines_and_surfaces(B1_DIR, ds)
    elif only_splat:
        prev = json.loads((B1_DIR / "地形与道路清单.json").read_text("utf-8"))
        hf_res = prev["heightfield"]
        hf_res["global_height_file"] = hf_res["global_file"]
        hf_res["global_height_identity"] = hf_res["global_identity"]
        hf_res["coarse_height_file"] = hf_res["coarse_file"]
        hf_res["coarse_height_identity"] = hf_res["coarse_identity"]
        raw_scvh = zlib.decompress((B1_DIR / hf_res["global_file"]).read_bytes())
        count = N_GRID * N_GRID
        role_grid = np.frombuffer(raw_scvh, dtype=np.uint8, offset=40 + count * 2, count=count).reshape((N_GRID, N_GRID))
        splat_res = build_splatmap_and_texture_array(B1_DIR, role_grid)
        road_res = prev["roads"]
    elif only_hf:
        hf_res = build_heightfield_and_support(B1_DIR, ds)
        prev = json.loads((B1_DIR / "地形与道路清单.json").read_text("utf-8"))
        splat_res = prev["splatmap"]
        road_res = prev["roads"]
    else:
        hf_res = build_heightfield_and_support(B1_DIR, ds)
        splat_res = build_splatmap_and_texture_array(B1_DIR, hf_res["role_grid"])
        road_res = build_road_splines_and_surfaces(B1_DIR, ds)

    splat_bytes = (
        splat_res["array_atlas"]["identity"]["bytes"]
        + splat_res["splat_control"]["identity"]["bytes"]
        + splat_res["macro_tint"]["identity"]["bytes"]
    )
    height_and_support_bytes = (
        hf_res["global_height_identity"]["bytes"]
        + hf_res["coarse_height_identity"]["bytes"]
        + hf_res["support_patches_total_bytes"]
    )
    roads_and_water_bytes = (
        road_res["roads_bin"]["identity"]["bytes"]
        + road_res["roads_surface_glb"]["identity"]["bytes"]
        + road_res["water_glb"]["identity"]["bytes"]
    )
    total_b1_bytes = splat_bytes + height_and_support_bytes + roads_and_water_bytes
    total_b1_mib = round(total_b1_bytes / 1048576.0, 3)

    manifest = {
        "schema": "scavenger-procedural-map-b1-terrain-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "bounds": {"minX": MIN_X, "maxX": MAX_X, "minZ": MIN_Z, "maxZ": MAX_Z, "minH": MIN_H, "maxH": MAX_H},
        "grid_step_m": STEP,
        "grid_dimensions": [N_GRID, N_GRID],
        "heightfield": {
            "global_file": hf_res["global_height_file"],
            "global_identity": hf_res["global_height_identity"],
            "coarse_file": hf_res["coarse_height_file"],
            "coarse_identity": hf_res["coarse_height_identity"],
            "tiles_count": hf_res["tiles_count"],
            "tiles": hf_res["tiles"],
            "original_bage_triangles": hf_res["original_bage_triangles"],
            "patch_triangles": hf_res["patch_triangles"],
            "triangles_reduction_ratio": hf_res["triangles_reduction_ratio"],
            "support_patches_total_bytes": hf_res["support_patches_total_bytes"],
        },
        "splatmap": splat_res,
        "roads": road_res,
        "budget_accounting": {
            "splat_and_layers_bytes": splat_bytes,
            "splat_and_layers_mib": round(splat_bytes / 1048576.0, 3),
            "height_and_support_bytes": height_and_support_bytes,
            "height_and_support_mib": round(height_and_support_bytes / 1048576.0, 3),
            "roads_and_water_bytes": roads_and_water_bytes,
            "roads_and_water_mib": round(roads_and_water_bytes / 1048576.0, 3),
            "total_b1_bytes": total_b1_bytes,
            "total_b1_mib": total_b1_mib,
            "limit_mib": 35.0,
            "within_budget": total_b1_mib <= 35.0,
        },
    }
    if not manifest["budget_accounting"]["within_budget"]:
        raise RuntimeError(f"B1 地形与道路总体积超出 35 MiB 门限: {total_b1_mib} MiB")

    out_file = B1_DIR / "地形与道路清单.json"
    out_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest["budget_accounting"], ensure_ascii=False))


if __name__ == "__main__":
    main()
