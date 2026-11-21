"""从十三款冻结资源、真实样本和功能证据生成离线HTML及研究摘要。"""
import base64
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

from 群体资源交接 import load_delivery
from 群体采样归档 import qualification

ROOT = Path(__file__).resolve().parents[1]
BATCH = ROOT / 'local-data/丧尸模型/性能测试/十三款混合/ZOM-MIX-261103-001'
REPORT = ROOT / 'reports/丧尸模型/十三款混合群体性能报告.html'
RESEARCH = ROOT / 'research/丧尸模型'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    return json.loads(path.read_text('utf-8-sig'))


def timeline(rows, length=360):
    width = max(1, (len(rows)+length-1)//length)
    result, elapsed = [], 0
    for i in range(0, len(rows), width):
        window = rows[i:i+width]
        result.append({'s': elapsed/1000, 'maxMs': max(r['frame'] for r in window)})
        elapsed += sum(r['frame'] for r in window)
    return result


def build():
    manifest, _ = load_delivery()
    samples = []
    for path in sorted((BATCH/'样本').glob('*.json')):
        raw = read(path)
        if raw['config']['delivery']['sha256'] != manifest['delivery']['sha256']:
            raise ValueError('样本包含其他资源交付，必须另行报告')
        rows, config = raw['rows'], raw['config']
        total = sum(r['frame'] for r in rows)
        if abs(len(rows)*1000/total-raw['summary']['fps']) > 1e-6:
            raise ValueError('逐帧重算FPS与摘要不匹配')
        times = sorted(r['frame'] for r in rows)
        recalculated = {'frames': len(rows), 'elapsedS': total/1000,
                        'p95Ms': times[math.ceil(len(rows)*.95)-1],
                        'p99Ms': times[math.ceil(len(rows)*.99)-1], 'maxMs': times[-1],
                        'over33Pct': 100*sum(t > 33.3 for t in times)/len(rows),
                        'simulationDroppedS': sum(r['dropped'] for r in rows),
                        'minVisible': min(r['visible'] for r in rows),
                        'minAnimated': min(r['animated'] for r in rows)}
        if any(abs(value-raw['summary'][key]) > 1e-6 for key, value in recalculated.items()):
            raise ValueError('逐帧重算分位数、长帧或完整性与摘要不匹配')
        if raw['stable60Passed'] != qualification(raw):
            raise ValueError('稳定60帧判定与完整口径不匹配')
        with path.with_suffix('.csv').open(encoding='utf-8-sig', newline='') as stream:
            actual = list(csv.DictReader(line for line in stream if not line.startswith('#')))
        if len(actual) != len(rows):
            raise ValueError('JSON与CSV逐帧数量不同')
        for row, csv_row in zip(rows, actual):
            if abs(float(csv_row['frame'])-row['frame']) > 1e-8:
                raise ValueError('JSON与CSV帧间隔不同')
            for model in row['models']:
                if any(int(csv_row[model['id']+':'+key]) != model[key] for key in ('count', 'visible', 'animated', 'moving')):
                    raise ValueError('JSON与CSV逐款数据不同')
        samples.append({'file': path.name, 'raw_sha256': sha(path), 'csv_sha256': sha(path.with_suffix('.csv')),
                        'createdAt': raw['createdAt'], 'config': config, 'environment': raw['environment'],
                        'duration': raw['requestedDurationS'], 'warmup': raw['warmupS'],
                        'valid': raw['valid'], 'interruptions': raw['interruptions'],
                        'nativeForegroundVerified': raw['nativeForegroundVerified'],
                        'foregroundObservation': raw['foregroundObservation'], 'stable60Passed': raw['stable60Passed'],
                        'models': raw['models'], 'summary': raw['summary'], 'timeline': timeline(rows)})
    functional_path = BATCH/'功能验证/内置浏览器功能.json'
    functional = read(functional_path) if functional_path.is_file() else None
    hardware_path = BATCH/'测试硬件.json'
    hardware = read(hardware_path) if hardware_path.is_file() else None
    capacities = []
    def condition(sample):
        c, e = sample['config'], sample['environment']
        return {'browser': e['browserLabel'], 'code_sha256': c['code_sha256'], 'seed': c['seed'], 'patrol': c['patrol'],
                'internal': c['view']['internal'], 'display': c['view']['display'], 'dpr': c['view']['dpr'],
                'span': c['view']['span'], 'angle': c['view']['angle'], 'center': c['view']['center'],
                'viewport': e['viewport'], 'gpu': e['gpu'], 'userAgent': e['userAgent']}
    identities = {json.dumps(condition(s), sort_keys=True): condition(s) for s in samples}
    for key, identity in identities.items():
        valid = [s for s in samples if json.dumps(condition(s), sort_keys=True) == key and s['config']['actorSet'] == 'mixed' and s['stable60Passed']]
        open_counts = {s['config']['count'] for s in valid if s['config']['mode'] == 'open'}
        channel_counts = {s['config']['count'] for s in valid if s['config']['mode'] == 'channel'}
        capacities.append({**identity, 'highestBothScenesPassed': max(open_counts & channel_counts, default=None)})
    comparisons = []
    for single in samples:
        c = single['config']
        if c['actorSet'] != 'single' or c['count'] != 500 or c['mode'] != 'open' or single['duration'] != 60 or not(single['valid'] and single['nativeForegroundVerified']):
            continue
        mixed = next((s for s in samples if s['config']['actorSet'] == 'mixed' and s['config']['count'] == 500 and s['config']['mode'] == 'open' and s['duration'] == 60 and s['valid'] and s['nativeForegroundVerified'] and condition(s) == condition(single)), None)
        if mixed:
            comparisons.append({'browser': single['environment']['browserLabel'], 'code_sha256': c['code_sha256'],
                                'single': single['summary'], 'mixed': mixed['summary'],
                                'fpsDeltaPct': 100*(mixed['summary']['fps']/single['summary']['fps']-1)})
    data = {'schema': 1, 'generatedAt': datetime.now(timezone.utc).isoformat(), 'delivery': manifest['delivery'],
            'models': manifest['models'], 'hardware': hardware, 'capacities': capacities, 'comparisons': comparisons, 'samples': samples,
            'functional': {'available': functional is not None, 'file_sha256': sha(functional_path) if functional else None,
                           'all13StationaryDeforming': bool(functional and len(functional['perModelMotion']) == 13
                               and {m['id'] for m in functional['perModelMotion']} == {m['id'] for m in manifest['models']}
                               and all(m['vertexDelta'] > 1e-6 and m['quaternionDelta'] > 1e-6 for m in functional['perModelMotion'])),
                           'independentSkeletons': functional['animatedStationaryA']['animation']['independentSkeletons'] if functional else None,
                           'sharedGeometries': functional['animatedStationaryA']['animation']['sharedGeometry'] if functional else None},
            'criteria': {'durationS': 180, 'minAverageFps': 60, 'maxP95Ms': 16.7, 'longFrameMs': 33.3,
                         'over33PctLessThan': .1, 'allInFrustum': True, 'allAnimated': True, 'simulationDroppedS': 0},
            'limitations': ['只覆盖本机和指定浏览器窗口，不代表普通电脑或移动端验收',
                            '视锥内计数不是无遮挡像素可见数量，拥挤时仍可能身体交叠',
                            '普通SkinnedMesh逐只绘制，同款共享资源不等于蒙皮已合批',
                            '程序自制待机与移动循环；人工外观、动作观感仍待工程师验收',
                            '无战斗、攻击死亡、完整地图及声音负载，资源数量不是显存字节',
                            'Chrome和Edge的资源技术验证不能替代其独立前台性能矩阵',
                            'GPU查询异步返回，正式报告采用自动归档时快照；手动晚下载末帧可能补值，不能与旧摘要混算',
                            '旧单款700只成绩不继承；未测相邻失败档时只能报告最高已通过数量'],
            'references': ['docs/丧尸十三款拆分入库与混合群体-实施计划.md',
                           'docs/项目开发经验/模型与音频优化复用指南.md', 'src/丧尸群体测试/README.md']}
    RESEARCH.mkdir(parents=True, exist_ok=True)
    (RESEARCH/'十三款混合性能记录.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    fields = ['browser', 'actorSet', 'count', 'mode', 'duration', 'valid', 'nativeForegroundVerified', 'fps', 'p95Ms', 'p99Ms', 'maxMs', 'over33Pct', 'simulationDroppedS', 'stable60Passed', 'code_sha256', 'raw_sha256']
    with (RESEARCH/'十三款混合性能摘要.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields);writer.writeheader()
        for s in samples:
            c, a = s['config'], s['summary']
            writer.writerow({'browser': s['environment']['browserLabel'], 'actorSet': c['actorSet'], 'count': c['count'], 'mode': c['mode'],
                             'duration': s['duration'], 'valid': s['valid'], 'nativeForegroundVerified': s['nativeForegroundVerified'],
                             **{k: a[k] for k in ['fps', 'p95Ms', 'p99Ms', 'maxMs', 'over33Pct', 'simulationDroppedS']},
                             'stable60Passed': s['stable60Passed'], 'code_sha256': c['code_sha256'], 'raw_sha256': s['raw_sha256']})
    shot = BATCH/'功能验证/十三款混合试玩.png'
    picture = '<img class="preview" alt="十三款混合群体实际运行画面" src="data:image/png;base64,'+base64.b64encode(shot.read_bytes()).decode()+'">' if shot.is_file() else '<p class="muted">实际截图在功能验证后补入；数据不使用示意渲染图。</p>'
    safe_data = json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')
    text = TEMPLATE.replace('__DATA__', safe_data).replace('__PICTURE__', picture)
    REPORT.parent.mkdir(parents=True, exist_ok=True);REPORT.write_text(text, encoding='utf-8')
    print(json.dumps({'report': str(REPORT.relative_to(ROOT)), 'samples': len(samples), 'capacities': capacities, 'all13Deforming': data['functional']['all13StationaryDeforming']}, ensure_ascii=False))


TEMPLATE = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>十三款混合群体 · 性能研究</title>
<style>:root{--bg:#101914;--card:#1a271f;--line:#3a4d3b;--text:#e5eadb;--muted:#a2b295;--lime:#d1ec81;--orange:#f2ad78}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.7 "Microsoft YaHei",sans-serif}main{max-width:1240px;margin:auto;padding:44px 30px 70px}header{border-bottom:1px solid var(--line);padding-bottom:40px}.eyebrow{font-size:11px;letter-spacing:3px;color:var(--lime)}h1{font-size:clamp(30px,4.5vw,60px);font-weight:400;line-height:1.2;margin:24px 0 18px;letter-spacing:1px}h2{font-size:24px;font-weight:400;margin:0 0 18px}.muted,small{color:var(--muted)}.lead{font-size:17px;max-width:850px;color:var(--muted)}.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--line);margin:28px 0}.kpi{background:var(--card);padding:22px}.kpi strong{display:block;font:38px/1.4 Consolas;color:var(--lime)}section{padding:32px 0;border-bottom:1px solid var(--line)}.grid{display:grid;grid-template-columns:1fr 1fr;gap:25px}.box{padding:22px;background:var(--card);border:1px solid var(--line)}.preview{width:100%;display:block;border:1px solid var(--line);margin:20px 0}table{width:100%;border-collapse:collapse;font-size:12px}td,th{text-align:left;padding:10px 8px;border-bottom:1px solid var(--line);white-space:nowrap}th{font-weight:400;color:var(--muted)}.table-wrap{overflow-x:auto}.pass{color:var(--lime)}.pending{color:var(--orange)}button,select{background:var(--card);border:1px solid #5a7053;color:var(--text);padding:8px 12px;cursor:pointer;font:inherit}button:hover{border-color:var(--lime)}.filters{display:flex;gap:10px;flex-wrap:wrap;margin:16px 0}.hash{font:11px/1.7 Consolas;word-break:break-all}.tag{border:1px solid var(--line);padding:3px 8px;font-size:11px;display:inline-block;margin:4px 4px 4px 0}canvas{width:100%;height:230px}details{margin:14px 0}summary{cursor:pointer;color:var(--lime)}.spaced li{margin:8px 0}a{color:var(--lime)}pre{white-space:pre-wrap;font-size:11px}footer{font-size:12px;color:var(--muted);padding-top:25px}@media(max-width:720px){main{padding:25px 16px}.grid{grid-template-columns:1fr}.kpis{grid-template-columns:repeat(2,1fr)}.kpi{padding:15px}.kpi strong{font-size:29px}}@media print{body{background:white;color:#222}main{padding:0}.box,.kpi{background:white;border-color:#bbb}.muted,small{color:#555}h1{font-size:32px}button,select{display:none}section{break-inside:avoid}.preview{max-height:450px;object-fit:contain}.pass{color:#305c00}.pending{color:#823c00}}</style>
<main><header><div class="eyebrow">SCAVENGER / 群体实验 02 / 冻结资源与真实采样</div><h1>十三款，一起动起来。</h1><p class="lead">从独立角色到真实混合群体。保留不同体型、骨架和动作相位，用同一版本的逐帧数据回答当前机器可以承担多少只。</p><span class="tag">640 × 360 / DPR 1</span><span class="tag">普通蒙皮 / 群体30Hz</span><span class="tag">自制待机与移动循环</span><div id="identity" class="hash"></div></header>
<div class="kpis"><div class="kpi"><small>独立角色</small><strong>13</strong><small>不同源网格与稳定ID</small></div><div class="kpi"><small>默认规格总下载</small><strong id="download">—</strong><small>256档GLB，不含引擎</small></div><div class="kpi"><small>真实归档样本</small><strong id="samples">—</strong><small>含失败与短测，分类展示</small></div><div class="kpi"><small>双场景最高通过档</small><strong id="capacity">待测</strong><small>仅当前设备／环境</small></div></div>
<section><h2>先看结论与验收边界</h2><div class="grid"><div class="box"><div id="conclusion"></div><p class="muted">180秒平均≥60 FPS，P95≤16.7毫秒，超过33.3毫秒帧占比&lt;0.1%，全群在视锥内、持续动画、模拟欠时为0，并通过页面与Windows前台核验。</p></div><div class="box"><b>功能与性能分开</b><div id="functional"></div><p class="muted">同款共享几何、贴图、材质和片段；每只仍有独立骨架、Mixer与绘制调用。这里没有将500只蒙皮合为一次绘制，也未引入战斗与声音负载。</p></div></div>__PICTURE__</section>
<section><h2>同版本实测矩阵</h2><p class="muted">15组矩阵包含500单款开阔对照、100／300／500四模式60秒及500开阔／通道180秒。短测用于探索，不能标记稳定60帧。点击样本行查看真实长帧及逐款证据。</p><div class="filters"><select id="browser"><option value="all">全部浏览器</option><option value="iab">内置浏览器</option><option value="chrome">Chrome</option><option value="edge">Edge</option></select><select id="kind"><option value="all">全部角色组合</option><option value="mixed">十三款混合</option><option value="single">编号0单款</option></select><button id="reset">显示全部</button></div><div class="table-wrap"><table><thead><tr><th>浏览器 / 组合</th><th>数量 / 场景</th><th>秒数</th><th>平均FPS</th><th>P95</th><th>P99</th><th>&gt;33.3ms</th><th>前台</th><th>判定</th></tr></thead><tbody id="results"></tbody></table></div><p id="missing" class="muted"></p><div id="detail" class="box" hidden><h3 id="detail-title"></h3><canvas id="timeline" width="1100" height="230"></canvas><p class="muted">按时间窗口保留最长帧；虚线16.7毫秒。完整逐帧数据保存在原始JSON／CSV，曲线压缩不丢弃统计峰值。</p><div id="costs"></div><div class="table-wrap"><table><thead><tr><th>款式</th><th>实际数量</th><th>最低视锥</th><th>最低动画</th><th>平均移动</th><th>证据完整</th></tr></thead><tbody id="model-evidence"></tbody></table></div><p id="sample-identity" class="hash"></p></div></section>
<section><h2>资源与真实体型</h2><p class="muted">编号11为21骨、约0.967米，其余多为23骨。保持共同来源尺度，未强制放大矮小角色；待机2秒、移动1.25秒均为本项目自制循环，人工外观与动作观感待验。</p><div class="table-wrap"><table><thead><tr><th>稳定ID</th><th>源网格</th><th>骨骼 / 材质</th><th>三角形</th><th>身高米</th><th>256字节</th><th>SHA256</th></tr></thead><tbody id="assets"></tbody></table></div></section>
<section><h2>环境、瓶颈与后续优化</h2><div class="grid"><div class="box"><h3>当前实测环境</h3><div id="environment"></div><p class="muted">GPU异步查询与CPU提交时间分别记录，不能相加当作整帧时间；纹理／几何数量不是显存字节。测试未宣称整机完全空载。</p></div><div class="box"><h3>按实际成本决定下一步</h3><ul class="spaced"><li>动画与矩阵占比高：先检查每角色更新、骨架层级和必要动作轨道，再评估分级更新或GPU动画。</li><li>提交与绘制调用高：当前仍逐只绘制，可在保留动作与颜色差异前提下评估实例化蒙皮方案。</li><li>群体模拟高：复核空间网格、邻居预算和流场更新；不直接升级为每只独立寻路。</li><li>每次优化重新固定源码与资源，在相同负载下对照；继续保留P99、最长帧与模拟欠时。</li></ul></div></div><ul id="limits" class="spaced muted"></ul></section>
<section><h2>可追溯依据</h2><p>模型：Jungle Jim，<a href="https://sketchfab.com/3d-models/super-low-poly-stylized-zombies-animated-6c112bed707b4444864b606299f8b85a">Super Low-poly Stylized Zombies</a>，<a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>。拆分、骨架精简、材质与自制循环修改在项目交付记录中保留。</p><details><summary>已采用的项目经验与维护入口</summary><ul class="spaced"><li>读取《模型与音频优化复用指南》，当前以revision4冻结交付为准。</li><li>使用实际镜头配置确定256默认档；保留真实体型和逐款骨数，不套用静态减面。</li><li>同款共享资源、每只独立骨架与动作相位；共享不等于蒙皮合批。</li><li>固定源码、资源、镜头与画质；CPU、GPU、长帧和模拟欠时分别记录。</li><li>声音、战斗与完整地图未加载，其预算需要后续独立验证。</li></ul><p class="muted">项目文档：docs/丧尸十三款拆分入库与混合群体-实施计划.md；docs/项目开发经验/模型与音频优化复用指南.md；src/丧尸群体测试/README.md。历史单款报告：reports/丧尸模型/五百只同屏性能测试报告.html。本页展示所需数据与画面均已嵌入，无需访问这些文件即可阅读。</p></details><details><summary>交付与样本身份摘要</summary><pre id="provenance"></pre></details></section><footer id="generated"></footer></main>
<script id="data" type="application/json">__DATA__</script><script>
const D=JSON.parse(document.getElementById('data').textContent),$=id=>document.getElementById(id),fmt=(n,d=2)=>n==null?'—':Number(n).toFixed(d),names={static:'静态',animated:'原地动画',open:'开阔移动',channel:'窄通道'},bn={iab:'内置',chrome:'Chrome',edge:'Edge'};
$('download').textContent=fmt(D.models.reduce((s,m)=>s+m.asset.bytes,0)/1e6)+' MB';$('samples').textContent=D.samples.length;$('identity').textContent='资源 '+D.delivery.batch_id+' / 统一库revision '+D.delivery.library_revision+' / 交付SHA256 '+D.delivery.sha256;
const passed=D.capacities.filter(c=>c.highestBothScenesPassed!=null);$('capacity').textContent=passed.length?Math.max(...passed.map(c=>c.highestBothScenesPassed))+'只':'待测';$('conclusion').textContent=passed.length?passed.map(c=>bn[c.browser]+'最高双场景已通过 '+c.highestBothScenesPassed+'只。').join(' ')+' 仅适用于下列源码和配置；未验证相邻失败档时不称绝对容量上限。':'尚无双场景180秒完整通过档。请查看实测表中的短测、失败及待补项。';
$('functional').textContent=D.functional.all13StationaryDeforming?'十三款原地逐款顶点变形通过；'+D.functional.independentSkeletons+'份独立骨架、'+D.functional.sharedGeometries+'份共享几何。右键、暂停、镜头与单款对照有独立功能证据。':'功能证据待补，不能从页面打开或计数推断实际动画通过。';
$('assets').innerHTML=D.models.map(m=>'<tr><td>'+m.id+'</td><td>'+m.source_mesh_index+'</td><td>'+m.bones+' / '+m.materials+'</td><td>'+m.triangles+'</td><td>'+fmt(m.coordinate_system.rest_height_m,3)+'</td><td>'+m.asset.bytes.toLocaleString()+'</td><td class="hash">'+m.asset.sha256+'</td></tr>').join('');
$('environment').textContent=JSON.stringify(D.hardware??{},null,2);$('environment').className='hash';$('limits').innerHTML=D.limitations.map(x=>'<li>'+x+'</li>').join('');$('provenance').textContent=JSON.stringify({delivery:D.delivery,identities:D.capacities,functional:D.functional},null,2);$('generated').textContent='生成于 '+D.generatedAt+' · 单文件离线可读；原始证据按批次保留。';
function draw(s){const c=$('timeline'),x=c.getContext('2d'),w=c.width,h=c.height,values=s.timeline,max=Math.max(40,...values.map(r=>r.maxMs));x.clearRect(0,0,w,h);x.strokeStyle='#71825c';x.setLineDash([6,5]);x.beginPath();x.moveTo(0,h-16.7/max*(h-24));x.lineTo(w,h-16.7/max*(h-24));x.stroke();x.setLineDash([]);x.strokeStyle='#d1ec81';x.beginPath();values.forEach((r,i)=>{const a=i/(values.length-1)*w,b=h-r.maxMs/max*(h-24);i?x.lineTo(a,b):x.moveTo(a,b)});x.stroke();x.fillStyle='#a2b295';x.font='12px monospace';x.fillText('峰值 '+fmt(s.summary.maxMs)+' ms',10,15);}
function detail(s){$('detail').hidden=false;$('detail-title').textContent=bn[s.environment.browserLabel]+' / '+s.config.actorSet+' / '+s.config.count+'只 / '+names[s.config.mode]+' / '+s.duration+'秒';draw(s);const a=s.summary;$('costs').textContent='CPU平均：模拟 '+fmt(a.cpuMs.simulation)+' / 动画 '+fmt(a.cpuMs.animation)+' / 矩阵 '+fmt(a.cpuMs.sync)+' / 提交 '+fmt(a.cpuMs.submit)+' ms；GPU '+fmt(a.gpuMs)+' ms（有效 '+a.gpuSamples+' 帧）；绘制 '+fmt(a.meanCalls,0)+' 次；欠时 '+fmt(a.simulationDroppedS,4)+' s';$('model-evidence').innerHTML=s.models.filter(m=>m.count).map(m=>'<tr><td>'+m.id+'</td><td>'+m.count+'</td><td>'+m.minVisible+'</td><td>'+m.minAnimated+'</td><td>'+fmt(m.meanMoving,1)+'</td><td>'+m.complete+'</td></tr>').join('');$('sample-identity').textContent='源码 '+s.config.code_sha256+' / 原始JSON '+s.raw_sha256+' / 前台覆盖 '+fmt(s.foregroundObservation.foregroundPct)+'% / '+s.file;}
function render(){const list=D.samples.filter(s=>($('browser').value==='all'||s.environment.browserLabel===$('browser').value)&&($('kind').value==='all'||s.config.actorSet===$('kind').value));$('results').innerHTML=list.map((s,i)=>{const a=s.summary;return '<tr data-row="'+i+'" tabindex="0"><td>'+bn[s.environment.browserLabel]+' / '+(s.config.actorSet==='mixed'?'十三款':'单款')+'</td><td>'+s.config.count+' / '+names[s.config.mode]+'</td><td>'+s.duration+'</td><td>'+fmt(a.fps,1)+'</td><td>'+fmt(a.p95Ms)+'</td><td>'+fmt(a.p99Ms)+'</td><td>'+fmt(a.over33Pct,3)+'%</td><td>'+(s.valid&&s.nativeForegroundVerified?'合格':'不合格')+'</td><td class="'+(s.stable60Passed?'pass':'pending')+'">'+(s.stable60Passed?'180秒通过':s.duration<180?'短测／对照':'未通过')+'</td></tr>'}).join('');for(const row of $('results').querySelectorAll('tr')){row.onclick=()=>detail(list[+row.dataset.row]);row.onkeydown=e=>{if(e.key==='Enter')row.click()}}const done=D.samples.filter(s=>s.duration>=60&&s.valid&&s.nativeForegroundVerified);$('missing').textContent=['iab','chrome','edge'].map(b=>bn[b]+'已归档合格窗口≥60秒样本 '+done.filter(s=>s.environment.browserLabel===b).length+'组').join('；')+'。其他环境未测不表示通过。';}
$('browser').onchange=$('kind').onchange=render;$('reset').onclick=()=>{$('browser').value=$('kind').value='all';render()};render();
$('generated').textContent='生成于 '+new Intl.DateTimeFormat('zh-CN',{timeZone:'America/Los_Angeles',dateStyle:'long',timeStyle:'long'}).format(new Date(D.generatedAt))+' · 单文件离线可读；原始证据按批次保留。';
if(D.comparisons.length){const p=document.createElement('p');p.className='muted';p.textContent=D.comparisons.map(c=>bn[c.browser]+'同版本500只开阔60秒：单款 '+fmt(c.single.fps,1)+' FPS / P95 '+fmt(c.single.p95Ms)+' ms；混合 '+fmt(c.mixed.fps,1)+' FPS / P95 '+fmt(c.mixed.p95Ms)+' ms；平均FPS变化 '+fmt(c.fpsDeltaPct,1)+'%。').join(' ')+' 此为相同配置的顺序对照，未隔离温度与后台调度。';document.getElementById('conclusion').appendChild(p);}
</script></html>'''


if __name__ == '__main__':
    build()
