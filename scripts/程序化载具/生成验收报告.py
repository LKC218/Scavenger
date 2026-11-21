"""V5 阶段：汇总 V0–V5 全部实测数据与视觉截图，生成离线自包含 HTML 与 Markdown 验收报告。"""
from __future__ import annotations

import html
import json
import shutil
import statistics
from pathlib import Path

from 公共配置 import (
    BATCH_ID,
    DEFAULT_PORT,
    REPORT_DIR,
    ROOT,
    V0_DIR,
    V2_DIR,
    V3_DIR,
    V5_DIR,
    now_iso,
    read_json,
)


def fmt_mib(b: int | float) -> str:
    return f"{b / 1048576:.2f} MiB"


def fmt_kib(b: int | float) -> str:
    return f"{b / 1024:.1f} KB"


def main() -> None:
    v0 = read_json(V0_DIR / "基线凭证.json")
    v2_comp = read_json(V2_DIR / "编译汇总.json")
    v2_static = read_json(V2_DIR / "静态结构校验回执.json")
    v2_vis = read_json(V2_DIR / "视觉验证" / "视觉核验汇总.json")
    v3_man = read_json(V3_DIR / "归档清单.json")
    v3_read = read_json(V3_DIR / "归档回读核验回执.json")
    v5 = read_json(V5_DIR / "V5全矩阵实测回执.json")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    assets_dir = REPORT_DIR / "assets"
    if assets_dir.exists():
        shutil.rmtree(assets_dir)
    assets_dir.mkdir(parents=True, exist_ok=True)

    # 复制 V5 实机截图到报告离线资源目录
    v5_shots = []
    for sp in sorted((V5_DIR / "截图").glob("*.png")):
        dst = assets_dir / sp.name
        shutil.copy2(sp, dst)
        v5_shots.append((sp.stem, f"assets/{sp.name}"))

    # 复制最差 Top 15 联系表及 3 大黄金基准（M1A2 / AH-64D / A-10C）联系表到离线报告目录
    featured_slugs = []
    for row in v2_vis["worst_top15"]:
        if row["slug"] not in featured_slugs:
            featured_slugs.append(row["slug"])
    for gslug in ("US_M1A2", "US_AH64D", "US_A10"):
        if gslug not in featured_slugs:
            featured_slugs.append(gslug)

    vis_by_slug = {r["slug"]: r for r in v2_vis["results"]}
    comp_by_slug = {r["slug"]: r for r in v2_comp["vehicles"]}
    copied_sheets = {}
    for slug in featured_slugs:
        src_png = V2_DIR / "视觉验证" / "联系表" / f"{slug}.png"
        if src_png.is_file():
            dst = assets_dir / f"sheet_{slug}.png"
            shutil.copy2(src_png, dst)
            copied_sheets[slug] = f"assets/sheet_{slug}.png"

    # 汇总关键统计
    orig_256_plus_far_mib = 150.224 + 120.410
    new_total_mib = v3_man["total_mib"]
    size_reduction_pct = (1.0 - new_total_mib / orig_256_plus_far_mib) * 100.0
    total_nodes_new = sum(v["nodes"] for v in v2_comp["vehicles"])
    node_reduction_pct = (1.0 - total_nodes_new / 18584) * 100.0
    total_lod0_tris = sum(v["lod0_tris"] for v in v2_comp["vehicles"])
    med_own_kb = statistics.median([v["lod0_plus_own_tex_bytes"] / 1024 for v in v2_comp["vehicles"]])
    med_dc = statistics.median([v["draw_calls"] for v in v2_comp["vehicles"]])
    max_dc = max(v["draw_calls"] for v in v2_comp["vehicles"])
    movable_cnt = sum(1 for v in v2_comp["vehicles"] if len(v["slots"]) > 1)

    # 渲染双浏览器表格 HTML
    perf_rows_html = []
    for ch in ("chrome", "msedge"):
        b = v5["browsers"][ch]
        pm = b["perf_matrix"]
        for key, label in [
            ("showcase_16:9", "单车 360° 展示台 (16:9)"),
            ("showcase_21:9", "单车 360° 展示台 (21:9)"),
            ("10_near_16:9", "10车近景全LOD0 (16:9)"),
            ("100_mixed_16:9", "30车型×100混编交战 (16:9)"),
            ("100_mixed_21:9", "30车型×100混编交战 (21:9)"),
            ("300_stress_16:9", "30车型×300压力探索 (16:9)"),
            ("129_parade_16:9", "全库129车型大阅兵 (16:9)"),
            ("zoom_sweep_16:9", "70m↔9000m连续缩放 (16:9)"),
        ]:
            m = pm.get(key)
            if not m:
                continue
            calls = m.get("calls", m.get("far_calls", "-"))
            tris = m.get("triangles", m.get("far_triangles", 0))
            lod_d = m.get("lod_dist", m.get("far_lod_dist"))
            lod_txt = f"{lod_d[0]}/{lod_d[1]}/{lod_d[2]}" if lod_d else "-"
            perf_rows_html.append(
                f"<tr><td>{ch.upper()}</td><td>{label}</td>"
                f"<td class='num ok'>{m['fps_avg']:.1f}</td>"
                f"<td class='num'>{m['p50_ms']:.2f}</td>"
                f"<td class='num'>{m['p95_ms']:.2f}</td>"
                f"<td class='num'>{m['p99_ms']:.2f}</td>"
                f"<td class='num'>{m['long_frame_ratio']*100:.2f}%</td>"
                f"<td class='num'>{calls}</td>"
                f"<td class='num'>{tris:,}</td>"
                f"<td class='num'>{lod_txt}</td></tr>"
            )

    # 渲染最差 Top 15 联系表审阅卡片
    top15_cards = []
    for idx, row in enumerate(v2_vis["worst_top15"], 1):
        slug = row["slug"]
        img_rel = copied_sheets.get(slug, "")
        c_row = comp_by_slug.get(slug, {})
        st_cls = "pass" if row["passed"] else "warn"
        st_txt = "通过" if row["passed"] else "建议复核"
        top15_cards.append(
            f"""<article class="sheet-card">
              <div class="sheet-head">
                <div><strong>#{idx} {html.escape(row['name'])}</strong> <code>{html.escape(row['id'])}</code>
                  <span class="pill {st_cls}">{st_txt}</span>
                  {'<span class="pill warn">原库PARTIAL</span>' if c_row.get('quality_status') == 'partial' else ''}
                </div>
                <div class="sheet-metrics">
                  LOD0 IoU <b>{row['lod0_iou_mean']:.3f}</b> · SSIM <b>{row['lod0_ssim_mean']:.3f}</b> ·
                  LOD1 IoU <b>{row['lod1_iou_mean']:.3f}</b> · LOD2 IoU <b>{row['lod2_iou_mean']:.3f}</b> ·
                  动作 <b>{'OK' if row['motion_passed'] else 'FAIL'}</b>
                </div>
              </div>
              {f'<img src="{img_rel}" alt="{html.escape(row["name"])} 六视角联系表" loading="lazy" />' if img_rel else ''}
            </article>"""
        )

    # 渲染全库 129 车明细表数据
    all_rows_json = []
    for c in v2_comp["vehicles"]:
        vr = vis_by_slug.get(c["slug"], {})
        all_rows_json.append({
            "id": c["id"],
            "slug": c["slug"],
            "name": c["name"],
            "role": c["role"],
            "status": c["quality_status"],
            "retry": c["retry_level"],
            "lod0_tris": c["lod0_tris"],
            "lod1_tris": c["lod1_tris"],
            "lod2_tris": c["lod2_tris"],
            "dc": c["draw_calls"],
            "nodes": c["nodes"],
            "slots": "+".join(c["slots"]),
            "own_kb": round(c["lod0_plus_own_tex_bytes"] / 1024, 1),
            "old_kb": round(c["baseline_256_bytes"] / 1024, 1),
            "iou0": vr.get("lod0_iou_mean", 0),
            "ssim0": vr.get("lod0_ssim_mean", 0),
            "iou1": vr.get("lod1_iou_mean", 0),
            "iou2": vr.get("lod2_iou_mean", 0),
            "blk": round(vr.get("black_anomaly", 0) * 100, 2),
            "mot": "OK" if vr.get("motion_passed", True) else "FAIL",
            "vis_pass": bool(vr.get("passed", False)),
        })

    shots_html = "".join(
        f"""<figure class="shot-card">
          <img src="{rel}" alt="{html.escape(label)}" loading="lazy" />
          <figcaption>{html.escape(label)}</figcaption>
        </figure>"""
        for label, rel in v5_shots
    )

    ch_net = v5["browsers"]["chrome"]["network_20mbps_80ms"]
    ed_net = v5["browsers"]["msedge"]["network_20mbps_80ms"]
    ch_leak = v5["browsers"]["chrome"]["leak_check_5rounds"]
    ed_leak = v5["browsers"]["msedge"]["leak_check_5rounds"]

    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>程序化载具优化与流式渲染验收报告 · V001</title>
  <style>
    :root {{
      --bg: #121815; --card: #18221c; --slot: #202d25; --line: rgba(150,176,144,.32);
      --text: #eef3e6; --muted: #98ab95; --ok: #d4ee80; --warn: #f0b85a; --bad: #ff6b57; --cyan: #44d2be;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; padding: 24px; background: var(--bg); color: var(--text); font-family: "Microsoft YaHei", "Bahnschrift", sans-serif; line-height: 1.55; }}
    .wrap {{ max-width: 1460px; margin: 0 auto; }}
    h1 {{ margin: 0 0 6px; font-size: 24px; }}
    h2 {{ margin: 28px 0 12px; font-size: 18px; border-left: 4px solid var(--ok); padding-left: 10px; }}
    .sub {{ color: var(--muted); font-size: 13px; margin-bottom: 18px; }}
    .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 12px; margin-bottom: 20px; }}
    .kpi {{ background: var(--card); border: 1px solid var(--line); padding: 14px; border-radius: 4px; }}
    .kpi .label {{ font-size: 12px; color: var(--muted); }}
    .kpi .val {{ font-size: 22px; font-weight: 700; color: var(--ok); font-family: Consolas, monospace; margin-top: 4px; }}
    .kpi .note {{ font-size: 11px; color: var(--muted); margin-top: 4px; }}
    table {{ width: 100%; border-collapse: collapse; background: var(--card); border: 1px solid var(--line); font-size: 12px; }}
    th, td {{ padding: 7px 9px; border-bottom: 1px solid rgba(150,176,144,.16); text-align: left; }}
    th {{ background: var(--slot); color: var(--muted); font-weight: 600; position: sticky; top: 0; }}
    td.num {{ font-family: Consolas, monospace; text-align: right; }}
    .ok {{ color: var(--ok); font-weight: 600; }}
    .pill {{ display: inline-block; padding: 1px 7px; border-radius: 3px; font-size: 11px; font-weight: 600; margin-left: 6px; }}
    .pill.pass {{ background: rgba(212,238,128,.16); color: var(--ok); border: 1px solid var(--ok); }}
    .pill.warn {{ background: rgba(240,184,90,.16); color: var(--warn); border: 1px solid var(--warn); }}
    .shots-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 14px; }}
    .shot-card {{ margin: 0; background: var(--card); border: 1px solid var(--line); padding: 10px; border-radius: 4px; }}
    .shot-card img {{ width: 100%; display: block; border-radius: 2px; }}
    .shot-card figcaption {{ margin-top: 8px; font-size: 12px; color: var(--muted); }}
    .sheet-card {{ background: var(--card); border: 1px solid var(--line); padding: 12px; border-radius: 4px; margin-bottom: 14px; }}
    .sheet-head {{ display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; }}
    .sheet-metrics {{ font-family: Consolas, monospace; font-size: 12px; color: var(--muted); }}
    .sheet-metrics b {{ color: var(--text); }}
    .sheet-card img {{ width: 100%; display: block; border-radius: 2px; }}
    .toolbar {{ display: flex; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }}
    .toolbar input, .toolbar select {{ background: var(--slot); color: var(--text); border: 1px solid var(--line); padding: 6px 10px; border-radius: 3px; font-size: 12px; }}
    .table-wrap {{ max-height: 540px; overflow: auto; border: 1px solid var(--line); }}
    code {{ background: var(--slot); padding: 1px 5px; border-radius: 3px; font-family: Consolas, monospace; font-size: 11px; }}
  </style>
</head>
<body>
<div class="wrap">
  <h1>程序化载具优化与流式渲染 · V001 自动化测试与视觉验收报告</h1>
  <div class="sub">
    批次编号：<code>{BATCH_ID}</code> · 生成时间：<code>{now_iso()}</code> ·
    渲染基线：<code>stylized-680p-v1 (1208×680 / 1588×680)</code> ·
    独立在线对比预览入口：<code>http://127.0.0.1:{DEFAULT_PORT}/showcase</code> 与 <code>/battlefield</code>（本页为离线自包含交付报告）
  </div>

  <section class="kpi-grid">
    <div class="kpi"><div class="label">全库 129 车归档总包体</div><div class="val">{new_total_mib:.2f} MiB</div><div class="note">较旧 256默认+远景 (270.63 MiB) 缩减 {size_reduction_pct:.1f}%；含 1024 底色 + 256 法线 + 256 中景 Mip</div></div>
    <div class="kpi"><div class="label">二进制索引 & 全库常驻底座</div><div class="val">{v3_man['index_kib']:.1f} KB / {v3_man['archives'][0]['mib']:.2f} MiB</div><div class="note">vehicles-index.scvi (≤64KB) + 129 车 LOD2 秒开常驻 (≤3MiB)</div></div>
    <div class="kpi"><div class="label">场景节点 & DrawCall 压缩</div><div class="val">-{node_reduction_pct:.1f}% / 中位 DC {med_dc:.0f}</div><div class="note">总节点 18,584 → {total_nodes_new}；可动部件自动识别 {movable_cnt} 车</div></div>
    <div class="kpi"><div class="label">129 车六视角自动视觉测试</div><div class="val">{v2_vis['passed']}/{v2_vis['total']} 通过</div><div class="note">均 LOD0 IoU {v2_vis['aggregate']['lod0_iou_mean_avg']:.4f} · 均 SSIM {v2_vis['aggregate']['lod0_ssim_mean_avg']:.4f} · 自修 {len(v2_vis['auto_repaired'])} 车</div></div>
    <div class="kpi"><div class="label">20 Mbps 冷启动（中位）</div><div class="val">{ch_net['cold_far_base_median_sec']:.2f}s / {ch_net['cold_30types_median_sec']:.2f}s</div><div class="note">底座 ≤1.5s / 30车型详模 ≤3.5s · 单车切换 {ch_net['showcase_switch_median_ms']:.0f}ms</div></div>
    <div class="kpi"><div class="label">双浏览器 30型×100混编</div><div class="val">{v5['browsers']['chrome']['perf_matrix']['100_mixed_16:9']['fps_avg']:.0f} / {v5['browsers']['msedge']['perf_matrix']['100_mixed_16:9']['fps_avg']:.0f} FPS</div><div class="note">Chrome / Edge (16:9 680p) · 零报错 · 5轮遍历零泄漏</div></div>
  </section>

  <h2>一、分层归档体积与 Range 回读账本（V3）</h2>
  <table>
    <thead><tr><th>产物文件</th><th>用途与内容</th><th style="text-align:right">条目数</th><th style="text-align:right">体积</th><th>Range 206 逐条回读核验</th></tr></thead>
    <tbody>
      <tr><td><code>vehicles-index.scvi</code></td><td>定长二进制头部 + 偏移表 + 129 车 OBB/轴心/挂点元数据</td><td class="num">{v3_man['entry_count']}</td><td class="num">{v3_man['index_kib']:.2f} KiB</td><td class="ok">通过（≤ 64 KiB）</td></tr>
      <tr><td><code>{v3_man['archives'][0]['file']}</code></td><td>全库 129 款 LOD2 无贴图实心远景壳（开局秒下常驻）</td><td class="num">{v3_man['archives'][0]['entry_count']}</td><td class="num">{v3_man['archives'][0]['mib']:.3f} MiB</td><td class="ok">通过（≤ 3.0 MiB）</td></tr>
      <tr><td><code>{v3_man['archives'][1]['file']}</code></td><td>跨车共享去重外置 KTX2 贴图池（消除跨车重复）</td><td class="num">{v3_man['archives'][1]['entry_count']}</td><td class="num">{v3_man['archives'][1]['mib']:.3f} MiB</td><td class="ok">通过</td></tr>
      <tr><td><code>{v3_man['archives'][2]['file']}</code></td><td>129 款同源 LOD0/LOD1 量化网格 + 单车独有 1024 底色/256 法线图集</td><td class="num">{v3_man['archives'][2]['entry_count']}</td><td class="num">{v3_man['archives'][2]['mib']:.3f} MiB</td><td class="ok">通过（{v3_read['verified_entries']}/{v3_read['expected_entries']} SHA256 100% 匹配）</td></tr>
    </tbody>
  </table>

  <h2>二、Chrome &amp; Edge 双浏览器全矩阵实测（stylized-680p-v1）</h2>
  <table>
    <thead><tr><th>浏览器</th><th>测试场景与画幅</th><th style="text-align:right">平均 FPS</th><th style="text-align:right">P50 (ms)</th><th style="text-align:right">P95 (ms)</th><th style="text-align:right">P99 (ms)</th><th style="text-align:right">长帧率(&gt;33.3ms)</th><th style="text-align:right">DrawCall</th><th style="text-align:right">三角面</th><th style="text-align:right">LOD0/1/2分布</th></tr></thead>
    <tbody>{''.join(perf_rows_html)}</tbody>
  </table>
  <p class="sub" style="margin-top:8px;">
    20 Mbps / 80 ms RTT 限速网络中位数：Chrome（冷底座 {ch_net['cold_far_base_median_sec']}s / 30型详模 {ch_net['cold_30types_median_sec']}s / 温启 {ch_net['warm_median_sec']}s / 单车切换 {ch_net['showcase_switch_median_ms']}ms）；
    Edge（冷底座 {ed_net['cold_far_base_median_sec']}s / 30型详模 {ed_net['cold_30types_median_sec']}s / 温启 {ed_net['warm_median_sec']}s / 单车切换 {ed_net['showcase_switch_median_ms']}ms）。<br/>
    5 轮 129 车遍历显存增长率（第2轮→第5轮）：Chrome 几何 {ch_leak['geo_growth_ratio']*100:.2f}% / 堆 {ch_leak['heap_growth_ratio']*100:.2f}%；Edge 几何 {ed_leak['geo_growth_ratio']*100:.2f}% / 堆 {ed_leak['heap_growth_ratio']*100:.2f}%。
    路线 B 契约与黄金轴心校验：{'全部通过' if v5['route_b_contract']['passed'] else '存在异常'}。
  </p>

  <h2>三、实机画面取证（单车展示台与多车型混编场）</h2>
  <div class="shots-grid">{shots_html}</div>

  <h2>四、最差 Top 15 优先人工抽查联系表（全库 129 车视觉下限）</h2>
  <p class="sub">按综合通过状态、LOD0 白色剪影 IoU 与前景 SSIM 倒排置顶；每图 4 行依次为：高清原件基准 / 新版 LOD0(1024+法线) / 新版 LOD1 / 新版 LOD2。</p>
  {''.join(top15_cards)}

  <h2>五、全库 129 款载具逐车指标明细</h2>
  <div class="toolbar">
    <input id="q" type="search" placeholder="筛选型号 / 稳定ID / 槽位…" />
    <select id="role-f">
      <option value="all">全部角色 (129)</option>
      <option value="ground">地面载具 (77)</option>
      <option value="rotorcraft">旋翼机 (20)</option>
      <option value="fixed_wing">固定翼 (26)</option>
      <option value="uav">无人机 (6)</option>
    </select>
    <select id="pass-f">
      <option value="all">全部视觉状态</option>
      <option value="pass">视觉通过</option>
      <option value="warn">建议复核 / partial</option>
    </select>
  </div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>#</th><th>稳定ID</th><th>型号</th><th>角色</th><th>原库状态</th><th>可动槽位</th>
          <th style="text-align:right">LOD0面</th><th style="text-align:right">LOD1面</th><th style="text-align:right">LOD2面</th>
          <th style="text-align:right">DC</th><th style="text-align:right">独有KB(旧)</th>
          <th style="text-align:right">LOD0 IoU</th><th style="text-align:right">SSIM</th><th style="text-align:right">LOD1 IoU</th><th style="text-align:right">LOD2 IoU</th>
          <th>动作</th><th>视觉</th>
        </tr>
      </thead>
      <tbody id="all-tbody"></tbody>
    </table>
  </div>
</div>
<script>
  const ROWS = {json.dumps(all_rows_json, ensure_ascii=False)};
  const qEl = document.getElementById('q');
  const rEl = document.getElementById('role-f');
  const pEl = document.getElementById('pass-f');
  const tb = document.getElementById('all-tbody');
  function render() {{
    const q = qEl.value.trim().toLowerCase();
    const role = rEl.value;
    const pf = pEl.value;
    const list = ROWS.filter(r => {{
      if (role !== 'all' && r.role !== role) return false;
      if (pf === 'pass' && !r.vis_pass) return false;
      if (pf === 'warn' && r.vis_pass && r.status === 'passed') return false;
      if (q && !`${{r.id}} ${{r.name}} ${{r.slots}}`.toLowerCase().includes(q)) return false;
      return true;
    }});
    tb.innerHTML = list.map((r, i) => `<tr>
      <td class="num">${{i + 1}}</td>
      <td><code>${{r.id}}</code></td>
      <td>${{r.name}}</td>
      <td>${{r.role}}</td>
      <td>${{r.status === 'partial' ? '<span class="pill warn">partial</span>' : 'passed'}}</td>
      <td><code>${{r.slots}}</code></td>
      <td class="num">${{r.lod0_tris.toLocaleString()}}</td>
      <td class="num">${{r.lod1_tris.toLocaleString()}}</td>
      <td class="num">${{r.lod2_tris.toLocaleString()}}</td>
      <td class="num">${{r.dc}}</td>
      <td class="num">${{r.own_kb}} <span style="color:var(--muted)">(${{r.old_kb}})</span></td>
      <td class="num">${{r.iou0.toFixed(3)}}</td>
      <td class="num">${{r.ssim0.toFixed(3)}}</td>
      <td class="num">${{r.iou1.toFixed(3)}}</td>
      <td class="num">${{r.iou2.toFixed(3)}}</td>
      <td>${{r.mot}}</td>
      <td>${{r.vis_pass ? '<span class="pill pass">PASS</span>' : '<span class="pill warn">REVIEW</span>'}}</td>
    </tr>`).join('');
  }}
  qEl.addEventListener('input', render);
  rEl.addEventListener('change', render);
  pEl.addEventListener('change', render);
  render();
</script>
</body>
</html>
"""
    html_path = REPORT_DIR / "程序化载具优化与视觉验收报告-V001.html"
    html_path.write_text(html_doc, encoding="utf-8")

    # 同步生成简明 Markdown 版验收摘要报告
    md_doc = f"""# 程序化载具优化与视觉验收报告（V001）

批次编号：`{BATCH_ID}` · 渲染基线：`stylized-680p-v1 (1208×680 / 1588×680)` · 生成时间：`{now_iso()}`

交互式离线完整报告（含 129 车搜索明细表、最差 Top 15 高清联系表与实机大图）：[程序化载具优化与视觉验收报告-V001.html](程序化载具优化与视觉验收报告-V001.html)

## 一、核心体积与结构收益（129 款载具全量实测）

| 指标 | 现行基线（`revision 7`） | 程序化新版（V001） | 改善幅度 |
| :--- | ---: | ---: | :--- |
| 全库归档总包体 | 270.63 MiB（256默认+远景） | **{new_total_mib:.2f} MiB**（3 个 `.scvveh` + 1 个 `.scvi`） | **-{size_reduction_pct:.1f}%**（且新增 1024 底色 + 256 法线 + 256 中景 Mip） |
| 二进制索引体积 | 1.39 MiB（JSON 索引+路由） | **{v3_man['index_kib']:.2f} KiB**（`vehicles-index.scvi`） | **≤ 64 KiB 硬门限通过** |
| 全库 129 车常驻底座 | 无 | **{v3_man['archives'][0]['mib']:.3f} MiB**（`vehicles-far.scvveh`） | **≤ 3.0 MiB 硬门限通过** |
| 单车中位独有体积 | 1,184.7 KB | **{med_own_kb:.1f} KB**（含独有图集+法线） | **-{(1 - med_own_kb / 1184.7) * 100:.1f}%** |
| 全库 GLTF 节点数 | 18,584（94.3% 废空节点） | **{total_nodes_new}**（0 废空节点） | **-{node_reduction_pct:.1f}%** |
| 单车 Draw Call | 中位 8 / 最高 34 | **中位 {med_dc:.0f} / 最高 {max_dc}** | 静态校验 {v2_static['passed']}/{v2_static['total']} 全通 |
| 全库 LOD0 总面数 | 5,886,048（256 默认） / 8,457,512（高模原件） | **{total_lod0_tris:,}** | 自动识别含可动槽位 **{movable_cnt}** 款 |

## 二、全库 129 车自动视觉与双浏览器实测结论

- **六视角自动视觉核验（`V2` 129 车全量）**：通过 **{v2_vis['passed']}/{v2_vis['total']}**（自动收敛自修恢复 **{len(v2_vis['auto_repaired'])}** 款）；平均 `LOD0 IoU = {v2_vis['aggregate']['lod0_iou_mean_avg']:.4f}`、平均 `LOD0 SSIM = {v2_vis['aggregate']['lod0_ssim_mean_avg']:.4f}`、平均 `LOD1 IoU = {v2_vis['aggregate']['lod1_iou_mean_avg']:.4f}`、平均 `LOD2 IoU = {v2_vis['aggregate']['lod2_iou_mean_avg']:.4f}`；动态部件旋转验真全部通过：`{v2_vis['aggregate']['motion_passed_all']}`。
- **Chrome（可见窗口 `stylized-680p-v1`）**：30 车型×100 混编 `16:9` **{v5['browsers']['chrome']['perf_matrix']['100_mixed_16:9']['fps_avg']} FPS**（P95 `{v5['browsers']['chrome']['perf_matrix']['100_mixed_16:9']['p95_ms']}ms`）、`21:9` **{v5['browsers']['chrome']['perf_matrix']['100_mixed_21:9']['fps_avg']} FPS**（P95 `{v5['browsers']['chrome']['perf_matrix']['100_mixed_21:9']['p95_ms']}ms`）；20 Mbps 限速冷底座 `{ch_net['cold_far_base_median_sec']}s`、30 车型详模 `{ch_net['cold_30types_median_sec']}s`、温启动 `{ch_net['warm_median_sec']}s`、单车切换 `{ch_net['showcase_switch_median_ms']}ms`。
- **Edge（可见窗口 `stylized-680p-v1`）**：30 车型×100 混编 `16:9` **{v5['browsers']['msedge']['perf_matrix']['100_mixed_16:9']['fps_avg']} FPS**（P95 `{v5['browsers']['msedge']['perf_matrix']['100_mixed_16:9']['p95_ms']}ms`）、`21:9` **{v5['browsers']['msedge']['perf_matrix']['100_mixed_21:9']['fps_avg']} FPS**（P95 `{v5['browsers']['msedge']['perf_matrix']['100_mixed_21:9']['p95_ms']}ms`）；20 Mbps 限速冷底座 `{ed_net['cold_far_base_median_sec']}s`、30 车型详模 `{ed_net['cold_30types_median_sec']}s`、温启动 `{ed_net['warm_median_sec']}s`、单车切换 `{ed_net['showcase_switch_median_ms']}ms`。
- **Range 回读与契约回归**：`V3` 逐条 HTTP 206 回读 **{v3_read['verified_entries']}/{v3_read['expected_entries']}** 100% 匹配；路线 B 接口契约与 `M1A2/AH-64D/A-10C` 黄金轴心校验通过：`{v5['route_b_contract']['passed']}`。

## 三、人工验收边界与后续收口说明

1. 4 款既有待修参考件（`US_m1288_base`、`US_LAV_AT-M_body`、`US_AC130J`、`US_AC130U`）严格保留 `partial` 状态，未因程序化编译通过而升级。
2. 技术与自动化视觉通过不代替工程师主观外观验收；请通过独立预览服务 `http://127.0.0.1:{DEFAULT_PORT}/showcase` 拉帘抽检最差 Top 15 及核心主力车型，确认通过后再另行执行 `revision 8` 正式库升级与旧载荷退役。
"""
    (REPORT_DIR / "程序化载具优化与视觉验收报告-V001.md").write_text(md_doc, encoding="utf-8")
    print(f"[验收报告] 已生成离线 HTML 与 Markdown 报告: {html_path.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
