"""离线批拍全部 129 辆美军载具的前斜 3/4 透明底微缩风格兵牌插画（WebP）。

使用说明：
    python -X utf8 -B scripts/程序化载具/生成兵牌插画.py
    python -X utf8 -B scripts/程序化载具/生成兵牌插画.py --only US_A10 US_M1A2 US_AH64D

输出目录：
    local-data/程序化载具/V001-b4570e71/V6-兵牌插画/<slug>.webp
通过唯一正式主服务 `http://127.0.0.1:8800/portraits/<slug>.webp` 只读分发。
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
VEH_DATA_DIR = ROOT / "local-data" / "程序化载具" / "V001-b4570e71"
PORTRAIT_DIR = VEH_DATA_DIR / "V6-兵牌插画"
SUMMARY_JSON = VEH_DATA_DIR / "V2-全库编译" / "编译汇总.json"

PORTRAIT_W = 192
PORTRAIT_H = 264


def ensure_server_alive(base_url: str) -> None:
    opener = build_opener(ProxyHandler({}))
    with opener.open(f"{base_url}/healthz", timeout=4) as r:
        if r.status != 200:
            raise RuntimeError(f"主服务健康检查异常: HTTP {r.status}")


def render_portraits(base_url: str, only_slugs: list[str] | None, force: bool) -> dict:
    PORTRAIT_DIR.mkdir(parents=True, exist_ok=True)
    summary = json.loads(SUMMARY_JSON.read_text(encoding="utf-8-sig"))
    vehicles = summary.get("vehicles", [])
    if only_slugs:
        allow = set(only_slugs)
        vehicles = [v for v in vehicles if v.get("slug") in allow]

    pending = [
        v for v in vehicles
        if force or not (PORTRAIT_DIR / f"{v['slug']}.webp").is_file()
    ]
    if not pending:
        return {"total": len(vehicles), "rendered": 0, "skipped": len(vehicles), "failed": []}

    t0 = time.perf_counter()
    rendered = 0
    failed: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=True,
            args=[
                "--use-gl=angle",
                "--use-angle=d3d11",
                "--enable-webgl",
                "--ignore-gpu-blocklist",
            ],
        )
        ctx = browser.new_context(viewport={"width": 640, "height": 480})
        page = ctx.new_page()
        # 复用主入口已有 importmap（three / three/addons）并在透明离屏 WebGL 画布中逐车渲染
        page.goto(f"{base_url}/armory", wait_until="domcontentloaded", timeout=30000)

        page.evaluate(
            f"""async () => {{
              const THREE = await import('three');
              const {{ ProceduralVehicleLoader }} = await import('/src/程序化载具/载具流式加载器.mjs');
              const {{ buildVehicleRigHierarchy }} = await import('/src/程序化地图/载具动效控制.mjs');
              const {{ createStylizedVehicleEnvMaps }} = await import('/src/程序化载具/载具风格化材质.mjs');

              const W = {PORTRAIT_W}, H = {PORTRAIT_H};
              const renderer = new THREE.WebGLRenderer({{
                antialias: true,
                alpha: true,
                preserveDrawingBuffer: true,
                powerPreference: 'high-performance',
              }});
              renderer.setPixelRatio(1);
              renderer.setSize(W, H, false);
              renderer.outputColorSpace = THREE.SRGBColorSpace;
              renderer.toneMapping = THREE.ACESFilmicToneMapping;
              renderer.toneMappingExposure = 1.24;
              renderer.setClearColor(0x000000, 0);

              const scene = new THREE.Scene();
              const envMaps = createStylizedVehicleEnvMaps(renderer);

              // 主光 + 冷色轮廓补光 + 温暖顶光，突出全战竖版兵牌立体剪影感
              const hemi = new THREE.HemisphereLight(0xf6f8ee, 0x3b4440, 1.45);
              const keyLight = new THREE.DirectionalLight(0xfff1d6, 3.15);
              keyLight.position.set(16, 22, 18);
              const rimLight = new THREE.DirectionalLight(0x9ec8ff, 1.35);
              rimLight.position.set(-18, 12, -16);
              scene.add(hemi, keyLight, rimLight);

              const camera = new THREE.PerspectiveCamera(30, W / H, 0.1, 800);
              const loader = new ProceduralVehicleLoader(renderer, {{
                mode: 'archive',
                archiveBaseUrl: '/v3',
                baseUrl: '/v2',
                envMap: envMaps.sandboxEnv,
                width: W,
                height: H,
              }});
              await loader.loadIndex();

              const stageGroup = new THREE.Group();
              scene.add(stageGroup);

              window.__PORTRAIT_SHOOTER__ = {{
                async capture(slug, role) {{
                  while (stageGroup.children.length > 0) stageGroup.remove(stageGroup.children[0]);
                  const parsed = await loader.loadVehicleLod(slug, 0);
                  const rig = buildVehicleRigHierarchy(parsed, parsed.meta || null, {{ THREE, isScvveh: true }});
                  const unitRoot = new THREE.Group();
                  unitRoot.add(rig.rigBodyGroup);

                  // 为固定翼/旋翼增加微仰与压坡度英姿，更贴合全战兵牌张力
                  if (role === 'fixed_wing' || role === 'uav') {{
                    rig.rigBodyGroup.rotation.set(0.10, 0, -0.34);
                  }} else if (role === 'rotorcraft') {{
                    rig.rigBodyGroup.rotation.set(0.09, 0, -0.10);
                    if (rig.rotorBlurDisc) rig.rotorBlurDisc.visible = false;
                    if (rig.tailBlurDisc) rig.tailBlurDisc.visible = false;
                  }}
                  stageGroup.add(unitRoot);
                  unitRoot.updateMatrixWorld(true);

                  const box = new THREE.Box3().setFromObject(unitRoot);
                  const center = box.getCenter(new THREE.Vector3());
                  const size = box.getSize(new THREE.Vector3());
                  const maxDim = Math.max(size.x, size.y, size.z, 1.0);

                  // 前斜 3/4 英雄视角（+Z 为机头/车头前向）
                  const azDeg = (role === 'fixed_wing' || role === 'uav') ? 34 : (role === 'rotorcraft' ? 40 : 35);
                  const elDeg = (role === 'fixed_wing' || role === 'uav') ? 30 : (role === 'rotorcraft' ? 19 : 18);
                  const az = azDeg * Math.PI / 180;
                  const el = elDeg * Math.PI / 180;

                  // 按投影视锥紧贴特写，占满竖版兵牌横向 ~92%
                  const fovRad = camera.fov * Math.PI / 180;
                  const hFovRad = 2 * Math.atan(Math.tan(fovRad * 0.5) * camera.aspect);
                  const projW = size.x * Math.cos(az) + size.z * Math.sin(az);
                  const projH = size.y * Math.cos(el) + Math.max(size.x, size.z) * Math.sin(el) * 0.65;
                  const distW = (projW * 0.46) / Math.tan(hFovRad * 0.5);
                  const distH = (projH * 0.52) / Math.tan(fovRad * 0.5);
                  const dist = Math.max(distW, distH, maxDim * 0.92) * 0.96;

                  camera.position.set(
                    center.x + Math.sin(az) * Math.cos(el) * dist,
                    center.y + Math.sin(el) * dist,
                    center.z + Math.cos(az) * Math.cos(el) * dist,
                  );
                  camera.lookAt(center.x, center.y - size.y * 0.08, center.z);
                  camera.updateProjectionMatrix();
                  camera.updateMatrixWorld(true);

                  renderer.render(scene, camera);
                  const dataUrl = renderer.domElement.toDataURL('image/webp', 0.90);
                  stageGroup.remove(unitRoot);
                  return dataUrl;
                }},
              }};
            }}"""
        )

        for idx, v in enumerate(pending, 1):
            slug = v["slug"]
            role = v.get("role", "ground")
            try:
                data_url = page.evaluate(
                    "async ([s, r]) => await window.__PORTRAIT_SHOOTER__.capture(s, r)",
                    [slug, role],
                )
                prefix = "data:image/webp;base64,"
                if not data_url.startswith(prefix):
                    raise ValueError(f"非 WebP dataUrl: {data_url[:40]}")
                raw = base64.b64decode(data_url[len(prefix):])
                (PORTRAIT_DIR / f"{slug}.webp").write_bytes(raw)
                rendered += 1
                if idx % 20 == 0 or idx == len(pending):
                    print(f"[{idx}/{len(pending)}] 已生成 {slug}.webp ({len(raw)} bytes)", flush=True)
            except Exception as err:
                failed.append({"slug": slug, "error": str(err)})
                print(f"[失败] {slug}: {err}", flush=True)

        browser.close()

    elapsed = round(time.perf_counter() - t0, 2)
    manifest = {
        "schema_version": 1,
        "width": PORTRAIT_W,
        "height": PORTRAIT_H,
        "total": len(vehicles),
        "rendered": rendered,
        "skipped": len(vehicles) - len(pending),
        "failed_count": len(failed),
        "failed": failed,
        "elapsed_sec": elapsed,
    }
    (PORTRAIT_DIR / "兵牌清单.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="批量渲染 129 车前斜 3/4 透明底兵牌插画 (WebP)")
    parser.add_argument("--base-url", default="http://127.0.0.1:8800", help="主服务地址")
    parser.add_argument("--only", nargs="*", default=None, help="仅渲染指定 slug 列表")
    parser.add_argument("--force", action="store_true", help="强制覆盖已有 WebP")
    args = parser.parse_args()

    ensure_server_alive(args.base_url)
    res = render_portraits(args.base_url, args.only, args.force)
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res["failed_count"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
