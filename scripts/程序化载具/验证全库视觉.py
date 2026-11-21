"""第二道自动闸门：全自动六视角双盲视觉核验 + 白色剪影 IoU + 前景 SSIM + 动作旋转验真 + 自动收敛重编。"""
from __future__ import annotations

import argparse
import base64
import io
import json
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from playwright.sync_api import sync_playwright

def _load_cn_font(size: int = 13):
    for fp in (
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/msyhbd.ttc",
    ):
        if Path(fp).is_file():
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                pass
    return ImageFont.load_default()

from 公共配置 import (
    BATCH_ID,
    ROOT,
    V0_DIR,
    V1_DIR,
    V2_DIR,
    now_iso,
    read_json,
    save_json,
)
from 预览服务 import start_server

VIEWS = ["closeup_fl", "closeup_br", "side", "top", "tactical_near", "tactical_mid"]
VIEW_LABELS = {
    "closeup_fl": "前左45°特写",
    "closeup_br": "后右45°特写",
    "side": "正侧剪影",
    "top": "正顶俯视",
    "tactical_near": "48°近景",
    "tactical_mid": "48°中景",
}


def decode_png_rgb(data_url: str) -> np.ndarray:
    b64 = data_url.split(",", 1)[1]
    img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    return np.asarray(img, dtype=np.uint8)


def compute_iou(sil_ref: np.ndarray, sil_opt: np.ndarray) -> float:
    m1 = sil_ref[:, :, 0] > 128
    m2 = sil_opt[:, :, 0] > 128
    inter = int(np.logical_and(m1, m2).sum())
    union = int(np.logical_or(m1, m2).sum())
    return float(inter / union) if union > 0 else 1.0


def compute_fg_ssim(rgb_ref: np.ndarray, rgb_opt: np.ndarray, mask: np.ndarray) -> float:
    """在前景掩码有效区域内计算感知平滑(r=1.0)后的 8x8 分块亮度 SSIM，过滤1px亚像素锯齿噪点同时敏感捕获贴图错位/糊图/破洞。"""
    s1 = np.asarray(Image.fromarray(rgb_ref).filter(ImageFilter.GaussianBlur(radius=1.0)), dtype=np.float32)
    s2 = np.asarray(Image.fromarray(rgb_opt).filter(ImageFilter.GaussianBlur(radius=1.0)), dtype=np.float32)
    y1 = 0.299 * s1[:, :, 0] + 0.587 * s1[:, :, 1] + 0.114 * s1[:, :, 2]
    y2 = 0.299 * s2[:, :, 0] + 0.587 * s2[:, :, 1] + 0.114 * s2[:, :, 2]

    ys, xs = np.where(mask)
    if len(ys) < 64:
        return 1.0
    y0, y1_idx = max(0, int(ys.min()) - 4), min(rgb_ref.shape[0], int(ys.max()) + 4)
    x0, x1_idx = max(0, int(xs.min()) - 4), min(rgb_ref.shape[1], int(xs.max()) + 4)

    p1 = y1[y0:y1_idx, x0:x1_idx]
    p2 = y2[y0:y1_idx, x0:x1_idx]
    pm = mask[y0:y1_idx, x0:x1_idx]

    h, w = p1.shape
    bh, bw = h // 8, w // 8
    if bh == 0 or bw == 0:
        return 1.0
    p1 = p1[: bh * 8, : bw * 8].reshape(bh, 8, bw, 8).transpose(0, 2, 1, 3).reshape(-1, 64)
    p2 = p2[: bh * 8, : bw * 8].reshape(bh, 8, bw, 8).transpose(0, 2, 1, 3).reshape(-1, 64)
    pm = pm[: bh * 8, : bw * 8].reshape(bh, 8, bw, 8).transpose(0, 2, 1, 3).reshape(-1, 64)

    # 仅统计包含前景像素的块
    valid = pm.sum(axis=1) >= 8
    if not np.any(valid):
        return 1.0
    a = p1[valid]
    b = p2[valid]
    mu_a = a.mean(axis=1)
    mu_b = b.mean(axis=1)
    va = ((a - mu_a[:, None]) ** 2).mean(axis=1)
    vb = ((b - mu_b[:, None]) ** 2).mean(axis=1)
    cov = ((a - mu_a[:, None]) * (b - mu_b[:, None])).mean(axis=1)
    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2
    ssim_map = ((2 * mu_a * mu_b + c1) * (2 * cov + c2)) / ((mu_a ** 2 + mu_b ** 2 + c1) * (va + vb + c2))
    return float(np.clip(ssim_map.mean(), 0.0, 1.0))


def compute_color_drift_and_black_anomaly(
    rgb_ref: np.ndarray, rgb_opt: np.ndarray, sil_ref: np.ndarray, sil_opt: np.ndarray
) -> tuple[float, float]:
    m_ref = sil_ref[:, :, 0] > 128
    m_opt = sil_opt[:, :, 0] > 128
    inter = np.logical_and(m_ref, m_opt)
    if inter.sum() < 32:
        return 0.0, 0.0
    mean_ref = rgb_ref[m_ref].mean(axis=0) / 255.0
    mean_opt = rgb_opt[m_opt].mean(axis=0) / 255.0
    drift = float(np.linalg.norm(mean_opt - mean_ref) / np.sqrt(3.0))

    # 异常纯黑像素检测：原版亮度 > 36 而新版跌到 < 8（典型法线翻转/镜像黑面）
    lum_ref = rgb_ref[inter].mean(axis=1)
    lum_opt = rgb_opt[inter].mean(axis=1)
    anomaly = np.logical_and(lum_ref > 38, lum_opt < 8)
    black_ratio = float(anomaly.sum() / max(1, len(lum_ref)))
    return drift, black_ratio


def build_contact_sheet(
    slug: str,
    name: str,
    metrics: dict,
    ref_imgs: dict[str, np.ndarray],
    lod0_imgs: dict[str, np.ndarray],
    lod1_imgs: dict[str, np.ndarray],
    lod2_imgs: dict[str, np.ndarray],
    out_png: Path,
) -> None:
    """生成单车 4 行 × 6 列（原版 / LOD0 / LOD1 / LOD2 × 6视角）高清对比联系表。"""
    tw, th = 280, 158
    header_h = 42
    row_labels = ["高清原件基准", "新版 LOD0(1024+法线)", "新版 LOD1(中景/影)", "新版 LOD2(远景壳)"]
    rows_data = [ref_imgs, lod0_imgs, lod1_imgs, lod2_imgs]
    W = tw * len(VIEWS)
    H = header_h + th * len(rows_data)

    font_title = _load_cn_font(14)
    font_tag = _load_cn_font(12)
    canvas = Image.new("RGB", (W, H), (22, 26, 24))
    draw = ImageDraw.Draw(canvas)
    title = (
        f"{name} ({slug}) | "
        f"LOD0 IoU={metrics['lod0_iou_mean']:.3f} SSIM={metrics['lod0_ssim_mean']:.3f} | "
        f"LOD1 IoU={metrics['lod1_iou_mean']:.3f} | LOD2 IoU={metrics['lod2_iou_mean']:.3f} | "
        f"Drift={metrics['lod0_color_drift']*100:.1f}% Black={metrics['black_anomaly']*100:.2f}% | "
        f"{'PASS' if metrics['passed'] else 'REVIEW'}"
    )
    draw.text((12, 11), title, font=font_title, fill=(235, 242, 225))

    for ri, rdata in enumerate(rows_data):
        for ci, vn in enumerate(VIEWS):
            arr = rdata.get(vn)
            if arr is None:
                continue
            im = Image.fromarray(arr).resize((tw, th), Image.Resampling.BILINEAR)
            x, y = ci * tw, header_h + ri * th
            canvas.paste(im, (x, y))
            draw.rectangle([x, y, x + tw - 1, y + th - 1], outline=(56, 66, 60))
            if ri == 0:
                draw.text((x + 6, y + 4), VIEW_LABELS[vn], font=font_tag, fill=(220, 232, 180))
            if ci == 0:
                draw.text((x + 6, y + th - 20), row_labels[ri], font=font_tag, fill=(255, 220, 140))

    out_png.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_png, optimize=True)


def evaluate_vehicle(page, v_entry: dict, meta: dict, shots_dir: Path, save_sheet: bool = True) -> dict:
    slug = meta["slug"]
    bounds = meta["bounds"]

    # 1. 加载从 模型.7z 恢复的高清原件作为真正视觉保真基准（同源带法线高模对照，客观衡量 SSIM 与轮廓）
    orig_file_name = Path(v_entry["original_cache_path"]).name
    ref_url = f"/orig/{orig_file_name}"
    page.evaluate("([u, b]) => window.__VEH_VIS__.loadReference(u, b)", [ref_url, bounds])

    ref_rgb = {}
    ref_sil = {}
    for vn in VIEWS:
        ref_rgb[vn] = decode_png_rgb(page.evaluate("v => window.__VEH_VIS__.capture(v, false)", vn))
        ref_sil[vn] = decode_png_rgb(page.evaluate("v => window.__VEH_VIS__.capture(v, true)", vn))

    # 2. 分别加载并采集新版 LOD0 / LOD1 / LOD2
    lod_rgb = {0: {}, 1: {}, 2: {}}
    lod_sil = {0: {}, 1: {}, 2: {}}
    for lv in (0, 1, 2):
        page.evaluate("([s, l, b]) => window.__VEH_VIS__.loadOptimized(s, l, b)", [slug, lv, bounds])
        for vn in VIEWS:
            lod_rgb[lv][vn] = decode_png_rgb(page.evaluate("v => window.__VEH_VIS__.capture(v, false)", vn))
            lod_sil[lv][vn] = decode_png_rgb(page.evaluate("v => window.__VEH_VIS__.capture(v, true)", vn))

    # 3. 计算全部量化视觉指标
    per_view = {}
    l0_ious, l1_ious, l2_ious = [], [], []
    l0_ssims, l0_drifts, l1_drifts, l2_drifts, blacks = [], [], [], [], []

    for vn in VIEWS:
        iou0 = compute_iou(ref_sil[vn], lod_sil[0][vn])
        iou1 = compute_iou(ref_sil[vn], lod_sil[1][vn])
        iou2 = compute_iou(ref_sil[vn], lod_sil[2][vn])
        fg_mask = np.logical_or(ref_sil[vn][:, :, 0] > 128, lod_sil[0][vn][:, :, 0] > 128)
        ssim0 = compute_fg_ssim(ref_rgb[vn], lod_rgb[0][vn], fg_mask)
        d0, blk0 = compute_color_drift_and_black_anomaly(ref_rgb[vn], lod_rgb[0][vn], ref_sil[vn], lod_sil[0][vn])
        d1, _ = compute_color_drift_and_black_anomaly(ref_rgb[vn], lod_rgb[1][vn], ref_sil[vn], lod_sil[1][vn])
        d2, _ = compute_color_drift_and_black_anomaly(ref_rgb[vn], lod_rgb[2][vn], ref_sil[vn], lod_sil[2][vn])

        l0_ious.append(iou0)
        l1_ious.append(iou1)
        l2_ious.append(iou2)
        l0_ssims.append(ssim0)
        l0_drifts.append(d0)
        l1_drifts.append(d1)
        l2_drifts.append(d2)
        blacks.append(blk0)
        per_view[vn] = {
            "lod0_iou": round(iou0, 4),
            "lod1_iou": round(iou1, 4),
            "lod2_iou": round(iou2, 4),
            "lod0_ssim": round(ssim0, 4),
            "lod0_drift": round(d0, 4),
            "lod1_drift": round(d1, 4),
            "lod2_drift": round(d2, 4),
            "black_anomaly": round(blk0, 5),
        }

    # 4. 动态可动部件动作视觉验真（炮塔/炮管/主尾旋翼/机炮/雷达）：三视角取最大可见位移防遮挡误判
    motion_checks = []
    movable_slots = [s for s in meta["slots"] if s["slot"] != "hull"]
    if movable_slots:
        page.evaluate("([s, l, b]) => window.__VEH_VIS__.loadOptimized(s, l, b)", [slug, 0, bounds])
        mot_views = ("closeup_fl", "closeup_br", "side")
        base_sils = {
            v: decode_png_rgb(page.evaluate("v => window.__VEH_VIS__.capture(v, true)", v))
            for v in mot_views
        }
        base_fgs = {v: int((arr[:, :, 0] > 128).sum()) for v, arr in base_sils.items()}
        for sl in movable_slots:
            sname, axis = sl["slot"], sl["axis"]
            test_deg = 25 if sname == "barrel" else 75
            ok_set = page.evaluate("([n, a, d]) => window.__VEH_VIS__.setSlotAngle(n, a, d)", [sname, axis, test_deg])
            best_diff = 0
            best_ratio = 0.0
            for v in mot_views:
                rot_sil = decode_png_rgb(page.evaluate("v => window.__VEH_VIS__.capture(v, true)", v))
                dp = int(np.logical_xor(base_sils[v][:, :, 0] > 128, rot_sil[:, :, 0] > 128).sum())
                dr = dp / max(1, base_fgs[v])
                if dp > best_diff:
                    best_diff = dp
                    best_ratio = dr
            # 复位
            page.evaluate("([n, a, d]) => window.__VEH_VIS__.setSlotAngle(n, a, d)", [sname, axis, 0])
            # 旋转必须引发可观测差异（非焊死）且不至于把整个车身甩飞（diff_ratio < 0.85）
            motion_ok = bool(ok_set and best_diff >= 10 and best_ratio < 0.85)
            motion_checks.append({
                "slot": sname,
                "axis": axis,
                "test_deg": test_deg,
                "diff_pixels": best_diff,
                "diff_ratio": round(best_ratio, 4),
                "passed": motion_ok,
            })

    # 核心通过判定（以 6 视角均值/下限结合：考虑到新版补回了 1024 高清贴图+法线图，SSIM 会带有合理光照细节增强）
    lod0_iou_mean = float(np.mean(l0_ious))
    lod0_iou_min = float(np.min(l0_ious))
    lod1_iou_mean = float(np.mean(l1_ious))
    lod1_iou_min = float(np.min(l1_ious))
    lod2_iou_mean = float(np.mean(l2_ious))
    lod0_ssim_mean = float(np.mean(l0_ssims))
    lod0_drift_mean = float(np.mean(l0_drifts))
    lod1_drift_mean = float(np.mean(l1_drifts))
    lod2_drift_mean = float(np.mean(l2_drifts))
    black_max = float(np.max(blacks))
    motion_all_ok = all(m["passed"] for m in motion_checks) if motion_checks else True

    # 判定规则（以 2048 高清原件为基准：1024 ETC1S + 256 UASTC 对照高频迷彩的客观感知 SSIM 下限 0.800；严守三档轮廓、零黑面与动作验真）
    lod0_ok = lod0_iou_mean >= 0.965 and lod0_iou_min >= 0.935 and lod0_ssim_mean >= 0.800 and black_max < 0.005
    passed = (
        lod0_ok
        and lod1_iou_mean >= 0.850
        and lod2_iou_mean >= 0.750
        and lod0_drift_mean <= 0.080
        and motion_all_ok
    )

    metrics = {
        "id": meta["id"],
        "slug": slug,
        "name": meta["name"],
        "role": meta["role"],
        "quality_status": meta["quality_status"],
        "retry_level": meta["retry_level"],
        "lod0_iou_mean": round(lod0_iou_mean, 4),
        "lod0_iou_min": round(lod0_iou_min, 4),
        "lod1_iou_mean": round(lod1_iou_mean, 4),
        "lod1_iou_min": round(lod1_iou_min, 4),
        "lod2_iou_mean": round(lod2_iou_mean, 4),
        "lod0_ssim_mean": round(lod0_ssim_mean, 4),
        "lod0_color_drift": round(lod0_drift_mean, 4),
        "lod1_color_drift": round(lod1_drift_mean, 4),
        "lod2_color_drift": round(lod2_drift_mean, 4),
        "black_anomaly": round(black_max, 5),
        "motion_checks": motion_checks,
        "motion_passed": motion_all_ok,
        "passed": passed,
        "per_view": per_view,
        "contact_sheet": f"联系表/{slug}.png" if save_sheet else None,
    }

    if save_sheet:
        build_contact_sheet(
            slug,
            meta["name"],
            metrics,
            ref_rgb,
            lod_rgb[0],
            lod_rgb[1],
            lod_rgb[2],
            shots_dir / f"{slug}.png",
        )
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=V1_DIR)
    parser.add_argument("--route-base", default="/v1")
    parser.add_argument("--source", choices=("loose", "archive"), default="loose")
    parser.add_argument("--port", type=int, default=8824)
    parser.add_argument("--auto-repair", action="store_true", help="未达标车型自动触发最多2轮收紧重编并复测")
    args = parser.parse_args()

    target_dir = args.dir.resolve()
    baseline = read_json(V0_DIR / "基线凭证.json")
    v_by_id = {v["id"]: v for v in baseline["vehicles"]}
    meta_dir = target_dir / "meta"
    meta_files = sorted(meta_dir.glob("*.json"))
    shots_dir = target_dir / "视觉验证" / "联系表"
    shots_dir.mkdir(parents=True, exist_ok=True)

    # 启动独立回环静态服务供 Playwright 渲染
    httpd = start_server(args.port, verbose=False)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    print(f"[视觉测试] 启动本地夹具服务 http://127.0.0.1:{args.port}/fixture?source={args.source}&base={args.route_base}")

    t_start = time.perf_counter()
    results = []
    repaired_ids = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                channel="chrome",
                headless=False,
                args=[
                    "--disable-gpu-vsync",
                    "--disable-background-timer-throttling",
                    "--disable-renderer-backgrounding",
                ],
            )
            ctx = browser.new_context(viewport={"width": 1208, "height": 680}, device_scale_factor=1)
            page = ctx.new_page()
            page.on("pageerror", lambda err: print("  [PAGEERROR]", err))
            page.on("console", lambda msg: print(f"  [CONSOLE {msg.type}]", msg.text) if msg.type in ("error", "warning") else None)
            page.on("requestfailed", lambda req: print("  [REQFAIL]", req.url, req.failure))
            page.goto(
                f"http://127.0.0.1:{args.port}/fixture?source={args.source}&base={args.route_base}",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            page.wait_for_function("() => window.__VEH_VIS__ && window.__VEH_VIS__.ready === true", timeout=30000)
            page.evaluate("() => window.__VEH_VIS__.init()")

            for idx, mf in enumerate(meta_files, 1):
                meta = read_json(mf)
                v_entry = v_by_id[meta["id"]]
                res = evaluate_vehicle(page, v_entry, meta, shots_dir, save_sheet=True)

                # 自动收紧重编闭环：精准聚焦于几何轮廓破损、异常黑面或明显感知退化(SSIM<0.80)，防止误杀高轮廓车型撑爆归档包体
                lod0_needs_repair = (
                    res["lod0_iou_mean"] < 0.965
                    or res["lod0_iou_min"] < 0.935
                    or res["lod0_ssim_mean"] < 0.800
                    or res["black_anomaly"] >= 0.005
                )
                if args.auto_repair and lod0_needs_repair:
                    for retry_lv in (1, 2):
                        print(
                            f"  [自动自修] {meta['name']} LOD0未达标(IoU={res['lod0_iou_mean']:.3f}, SSIM={res['lod0_ssim_mean']:.3f}, Black={res['black_anomaly']:.4f}) -> 启动第 {retry_lv} 轮收紧重编..."
                        )
                        subprocess.run(
                            [
                                "node",
                                str(ROOT / "scripts/程序化载具/编译载具.mjs"),
                                "--ids",
                                meta["id"],
                                "--out",
                                str(target_dir),
                                "--retry-level",
                                str(retry_lv),
                                "--force",
                            ],
                            check=True,
                        )
                        page.evaluate("() => window.__VEH_VIS__.clearCache()")
                        meta = read_json(mf)
                        res = evaluate_vehicle(page, v_entry, meta, shots_dir, save_sheet=True)
                        still_bad = (
                            res["lod0_iou_mean"] < 0.965
                            or res["lod0_iou_min"] < 0.935
                            or res["lod0_ssim_mean"] < 0.800
                            or res["black_anomaly"] >= 0.005
                        )
                        if not still_bad:
                            repaired_ids.append({"id": meta["id"], "name": meta["name"], "retry_level": retry_lv})
                            break

                results.append(res)
                tag = "PASS" if res["passed"] else "WARN"
                print(
                    f"[{idx:3d}/{len(meta_files)}] {tag} {meta['name'][:22]:22s} | "
                    f"LOD0 IoU={res['lod0_iou_mean']:.3f}(min {res['lod0_iou_min']:.3f}) SSIM={res['lod0_ssim_mean']:.3f} | "
                    f"LOD1 IoU={res['lod1_iou_mean']:.3f} | LOD2 IoU={res['lod2_iou_mean']:.3f} | "
                    f"Drift={res['lod0_color_drift']*100:4.1f}% Blk={res['black_anomaly']*100:4.2f}% Mot={'OK' if res['motion_passed'] else 'FAIL'}"
                )
                # 每处理 15 辆清理一次浏览器细节缓存控制显存水位
                if idx % 15 == 0:
                    page.evaluate("() => window.__VEH_VIS__.clearCache()")

            browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()

    # 按综合下限排序提取最差 Top 15 优先人工巡检清单
    ranked = sorted(results, key=lambda r: (r["passed"], r["lod0_iou_mean"], r["lod0_ssim_mean"]))
    passed_cnt = sum(1 for r in results if r["passed"])
    summary = {
        "schema_version": 1,
        "batch_id": BATCH_ID,
        "checked_at": now_iso(),
        "elapsed_sec": round(time.perf_counter() - t_start, 2),
        "target_dir": target_dir.relative_to(ROOT).as_posix(),
        "source_mode": args.source,
        "total": len(results),
        "passed": passed_cnt,
        "need_review": len(results) - passed_cnt,
        "auto_repaired": repaired_ids,
        "aggregate": {
            "lod0_iou_mean_avg": round(float(np.mean([r["lod0_iou_mean"] for r in results])), 4),
            "lod0_iou_min_overall": round(float(np.min([r["lod0_iou_min"] for r in results])), 4),
            "lod1_iou_mean_avg": round(float(np.mean([r["lod1_iou_mean"] for r in results])), 4),
            "lod2_iou_mean_avg": round(float(np.mean([r["lod2_iou_mean"] for r in results])), 4),
            "lod0_ssim_mean_avg": round(float(np.mean([r["lod0_ssim_mean"] for r in results])), 4),
            "black_anomaly_max": round(float(np.max([r["black_anomaly"] for r in results])), 5),
            "motion_passed_all": all(r["motion_passed"] for r in results),
        },
        "worst_top15": [
            {
                "id": r["id"],
                "slug": r["slug"],
                "name": r["name"],
                "passed": r["passed"],
                "lod0_iou_mean": r["lod0_iou_mean"],
                "lod0_ssim_mean": r["lod0_ssim_mean"],
                "lod1_iou_mean": r["lod1_iou_mean"],
                "lod2_iou_mean": r["lod2_iou_mean"],
                "black_anomaly": r["black_anomaly"],
                "motion_passed": r["motion_passed"],
                "contact_sheet": r["contact_sheet"],
            }
            for r in ranked[:15]
        ],
        "results": results,
    }
    save_json(target_dir / "视觉验证" / "视觉核验汇总.json", summary)
    print(
        f"[视觉测试] 完成！耗时 {summary['elapsed_sec']}s | 通过={passed_cnt}/{len(results)} | 自修恢复={len(repaired_ids)} | "
        f"均LOD0 IoU={summary['aggregate']['lod0_iou_mean_avg']:.4f} | 均SSIM={summary['aggregate']['lod0_ssim_mean_avg']:.4f}"
    )


if __name__ == "__main__":
    main()
