"""汇总本轮响度、生命周期与负载证据，生成离线报告或检查报告页面。"""
import argparse
import hashlib
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'local-data/原型/载具靶场/验证/响度增强1'
REPORT = ROOT/'reports/载具靶场/靶场响度增强验证.html'


def generate():
    summary = {'schema': 1, 'defaultVolume': 1, 'defaultEnhancementDb': 6, 'waveforms': [], 'performance': [], 'lifecycle': []}
    identity = None
    for channel in ['chrome', 'msedge']:
        for kind, path in [('waveforms', OUT/(channel+'-响度.json')), ('performance', OUT/(channel+'-负载.json')),
                           ('lifecycle', OUT/'生命周期'/(channel+'-音效交互.json'))]:
            record = json.loads(path.read_text('utf-8'))
            if not record.get('passed') or record['errors']: raise ValueError('回执未完整通过：'+path.name)
            if identity is None: identity = record['source_identity']
            if record['source_identity'] != identity: raise ValueError('回执运行源码身份不一致')
            for row in identity:
                if hashlib.sha256((ROOT/row['path']).read_bytes()).hexdigest() != row['sha256']:
                    raise ValueError('当前源码与测量不一致：'+row['path'])
            if kind == 'waveforms':
                for dense in record['waveforms']['dense']:
                    if dense['nearCeilingFraction'] >= .05 or dense['peak'] >= .951: raise ValueError('密集混音标定超界')
                summary[kind].append({k: record[k] for k in ['channel', 'version', 'checks', 'waveforms', 'zero_output_peak', 'before_mix_sha256']})
            elif kind == 'lifecycle':
                summary[kind].append({k: record[k] for k in ['channel', 'version', 'checks', 'real_asset_output', 'reset_anchors']})
            else:
                for sample in record['samples']:
                    row = {k: sample[k] for k in ['date', 'duration', 'frames', 'p50', 'p95', 'p99', 'max', 'over33ms', 'cpuP95', 'enhancementDb', 'volume', 'totalUnits', 'audio', 'anchors']}
                    row.update(channel=channel, version=record['version'])
                    row['timeline'] = [{k: p[k] for k in ['seconds', 'p95', 'voices', 'engines', 'audioRmsDb', 'audioPeak', 'shots', 'hits']} for p in sample['timeline']]
                    row['newShots'] = sample['timeline'][-1]['shots']-sample['timeline'][0]['shots']
                    summary[kind].append(row)
    if len(summary['performance']) != 4: raise ValueError('需要两浏览器双档四组负载')
    summary['source_identity'] = identity
    source_snapshot = OUT/'改前源码/声音.mjs'
    digest = hashlib.sha256(source_snapshot.read_bytes()).hexdigest()
    if any(r['before_mix_sha256'] != digest for r in summary['waveforms']): raise ValueError('改前混音函数身份不符')
    summary['gainRanges'] = {str(db): [min(r['gain'+str(db)+'Db'] for w in summary['waveforms'] for r in w['waveforms']['rows']),
                                     max(r['gain'+str(db)+'Db'] for w in summary['waveforms'] for r in w['waveforms']['rows'])] for db in [6, 12]}
    (REPORT.parent/'靶场响度验证摘要.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    performance = ''.join(f'<tr><th>{r["channel"]} · +{r["enhancementDb"]}dB</th><td>{r["duration"]:.0f}秒</td><td>{r["p95"]:.2f} / {r["p99"]:.2f}</td><td>{r["max"]:.2f}</td><td>{r["over33ms"]}</td><td>{r["audio"]["peak"]:.3f}</td><td>{r["newShots"]:,}</td></tr>' for r in summary['performance'])
    gain6 = summary['gainRanges']['6']; gain12 = summary['gainRanges']['12']
    checks = ''.join('<li>'+html.escape(s)+'</li>' for s in summary['waveforms'][0]['checks'])
    data = json.dumps(summary, ensure_ascii=False).replace('</', '<\\/')
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>靶场响度增强验证</title><style>
:root{color-scheme:dark;--bg:#111a19;--panel:#1c2926;--text:#e7eee4;--muted:#a3b5a9;--line:#3a4a41;--accent:#d7e98c;--strong:#edbd77}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:16px/1.75 system-ui,'Microsoft YaHei',sans-serif}main{max-width:1160px;padding:40px 24px 70px;margin:auto}header{border-top:4px solid var(--accent);padding:28px 0}h1{font-size:clamp(32px,4vw,50px);line-height:1.25;margin:10px 0 20px}h2{font-size:25px;margin:0 0 16px}h3{margin:0 0 8px}p{margin:0 0 14px}a{color:var(--accent);text-underline-offset:4px}.muted,small{color:var(--muted)}.eyebrow{font-size:12px;color:var(--accent);letter-spacing:.14em}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.card,section{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:24px}.card strong{display:block;font-size:29px;color:var(--accent)}section{margin-top:26px}.notice{padding-left:15px;border-left:3px solid var(--strong);color:var(--muted)}label{display:inline-flex;gap:8px;align-items:center;flex-wrap:wrap;margin:0 15px 16px 0}select{background:#111a19;color:var(--text);font:inherit;border:1px solid var(--line);border-radius:4px;padding:8px;max-width:100%}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}.scroll{overflow:auto}.comparison{display:grid;gap:16px}.comparison article{border-top:1px solid var(--line);padding-top:12px}.comparison h3{font-size:14px;overflow-wrap:anywhere}.bar{display:grid;grid-template-columns:65px 1fr 75px;gap:12px;align-items:center;font-size:13px}.track{background:#111a19;height:12px;border-radius:2px}.track i{display:block;height:100%;background:var(--accent)}.bar.strong .track i{background:var(--strong)}svg{width:100%;height:auto;background:#111a19;border-radius:6px}details{margin-top:18px}summary{cursor:pointer;color:var(--accent)}li{margin:8px 0}.chain{padding:14px;background:#111a19;border-radius:6px;overflow-wrap:anywhere;color:var(--accent)}footer{margin-top:24px;color:var(--muted);font-size:13px}
@media(max-width:700px){main{padding:20px 14px}.cards{grid-template-columns:1fr}section{padding:20px 16px}.bar{grid-template-columns:48px 1fr 65px}}
@media print{:root{color-scheme:light;--bg:white;--panel:white;--text:#19231b;--muted:#45554b;--line:#aaa;--accent:#455e22;--strong:#8e5e20}main{padding:0}section,.card{break-inside:avoid}select{background:white;color:black}.chain,svg,.track{background:#eee}}
</style><main><header><span class="eyebrow">载具靶场 / 平均输出标定</span><h1>近处也偏小：<br>本轮提高实际输出。</h1><p>默认100%总音量与6dB增强，提供12dB强增强和原始0dB对比。三型机械声连续播放，切档保持当前声源。</p><p><a href="http://127.0.0.1:8768/?v=loudness1">打开新版靶场 ↗</a>　<a href="靶场响度验证摘要.json">验证摘要</a>　<a href="载具音效补全验证.html">上一阶段记录</a></p><p class="notice">对比统一使用100%音量、相同真实素材和空间位置。这里测的是数字波形平均输出，RMS不等同LUFS或人耳感知倍数；耳机、音箱与主观听感仍待工程师试玩。</p></header>
<div class="cards"><article class="card"><span class="eyebrow">默认增强档</span><strong>__GAIN6__ dB</strong><p>13项真实素材近／中距离的实际平均提升范围，按两浏览器测量。</p></article><article class="card"><span class="eyebrow">12dB强增强档</span><strong>__GAIN12__ dB</strong><p>机械底声基本达到完整12dB，强事件受压缩和限幅控制。</p></article><article class="card"><span class="eyebrow">回退与对比</span><strong>原始 0dB</strong><p>与改前混音函数的平均输出等价；当前总音量仍可独立调到0至100%。</p></article></div>
<section><h2>增益如何分配</h2><p class="chain">主音量 → 驱动增益 → 压缩 → 输出增益 → 软限幅 → 设备输出</p><div class="scroll"><table><thead><tr><th>档位</th><th>压缩前</th><th>压缩后</th><th>压缩阈值</th></tr></thead><tbody><tr><th>原始0dB</th><td>0dB</td><td>0dB</td><td>−8dB</td></tr><tr><th>增强6dB · 默认</th><td>3dB</td><td>3dB</td><td>−8dB</td></tr><tr><th>强增强12dB</th><td>9dB</td><td>3dB</td><td>−8dB</td></tr></tbody></table></div><p>两处增益按35毫秒时间常数平滑变化。把部分增益放到压缩前，较强声音能先受控，压缩后的提升最多3dB；保留原来的末端软限幅。</p><p class="muted">本轮纠正了此前的诊断：原生压缩器本身有随阈值、比例和拐点变化的自动补偿。依据见<a href="https://www.w3.org/TR/webaudio-1.0/#computing-the-makeup-gain">W3C Web Audio规范</a>。直接调阈值并叠加后置增益的排障版本未交付，原回执独立保留。</p></section>
<section><h2>同素材平均输出对比</h2><p>保存改前混音函数，逐项读取当前13个解码缓存。水平距离20／160米、保留原型声源高度，每次双声道离线渲染4秒；原始0dB与改前等价，6／12dB使用当前生产混音函数。</p><label>浏览器<select id="browser"><option value="chrome">Chrome</option><option value="msedge">Edge</option></select></label><label>水平距离<select id="distance"><option value="20">近处 · 20米</option><option value="160">中距离 · 160米</option></select></label><div id="comparison" class="comparison"></div><p class="muted">柱条范围0至12dB。该图是相同位置的前后比值，不能直接当作设备绝对声压。</p><details><summary>查看已通过的本轮检查</summary><ul>__CHECKS__</ul></details></section>
<section><h2>48路密集真实素材混音</h2><p>模拟八路机械声与四十路事件的集中重叠，使用真实缓存素材及固定错时。下表保留接近输出上沿的样本比例，用于观察限幅介入；它不是失真率，也不能代替听审。</p><div class="scroll"><table><thead><tr><th>浏览器</th><th>档位</th><th>4秒RMS dBFS</th><th>峰值</th><th>绝对值≥0.94样本</th></tr></thead><tbody id="dense"></tbody></table></div><p class="notice">所有离线输出峰值低于0.951，非有限值为0。强增强档会更明显压缩密集齐射的动态，默认使用6dB；零音量在12dB档仍输出静音。</p></section>
<section><h2>当前代码的100单位有声交战</h2><p>Chrome／Edge各测6与12dB两档，统一100%音量；每档预热10秒，正式采样60秒，恢复靶持续交战。条件：独立无窗口浏览器、1600×1000视口、内部宽640、RX7900XTX。结果仅代表本轮短样本。</p><div class="scroll"><table><thead><tr><th>样本</th><th>时长</th><th>P95 / P99 毫秒</th><th>最长帧 毫秒</th><th>超过33.34毫秒</th><th>抽样峰值</th><th>新增发射</th></tr></thead><tbody>__PERFORMANCE__</tbody></table></div><label>样本<select id="sample"></select></label><label>指标<select id="metric"><option value="audioRmsDb">输出窗RMS dBFS</option><option value="p95">滚动P95 毫秒</option><option value="voices">总声部</option><option value="audioPeak">抽样峰值</option></select></label><svg id="chart" viewBox="0 0 960 250" role="img" aria-label="有声交战采样时间线"></svg><p id="caption" class="muted"></p><p class="muted">约每秒一项；RMS与峰值来自末端1024样本窗，P95为滚动帧窗。全段分位见表格。总声部48、机械声8的预算与节点守恒另行核验，不把数据外推为其他设备或长时容量。</p></section>
<section><h2>两分钟试玩</h2><ol><li>刷新靶场，点击“声音 关”开启，确认总音量100%、响度增强6dB。</li><li>按1／2／4选择型号，C聚焦，比较待机、移动、悬停与飞行声。</li><li>保持同一位置，切原始0dB、增强6dB和强增强12dB，选择合适档位。</li><li>切恢复靶持续交战，听开火与爆炸是否清楚、有层次；检查关闭或暂停没有残留。</li></ol></section><footer>本页由两浏览器的响度、生命周期与负载六份通过回执生成，核对当前运行源码身份。改前快照、排障回执、实际WAV与完整原始测量保存在本地忽略目录。音效补全与城市阶段历史报告保持原状。正式库、镜头、武器规则未改。</footer>
<script type="application/json" id="data">__DATA__</script><script>
const data=JSON.parse(document.getElementById('data').textContent),el=id=>document.getElementById(id),labels={tankMove:'M1A2 引擎',heliMove:'AH-64D 旋翼',planeMove:'A-10C 涡轮'};
function comparison(){const record=data.waveforms.find(r=>r.channel===el('browser').value),rows=record.waveforms.rows.filter(r=>r.distance===Number(el('distance').value));el('comparison').replaceChildren();for(const r of rows){const article=document.createElement('article'),title=document.createElement('h3');title.textContent=labels[r.key]??r.key;article.append(title);for(const [name,value,strong] of [['6dB',r.gain6Db,false],['12dB',r.gain12Db,true]]){const div=document.createElement('div');div.className='bar'+(strong?' strong':'');const a=document.createElement('span');a.textContent=name;const track=document.createElement('div');track.className='track';const fill=document.createElement('i');fill.style.width=Math.min(100,value/12*100)+'%';track.append(fill);const number=document.createElement('span');number.textContent='+'+value.toFixed(2)+'dB';div.append(a,track,number);article.append(div);}el('comparison').append(article);}}
for(const w of data.waveforms)for(const r of w.waveforms.dense){const tr=document.createElement('tr');for(const value of [w.channel,r.db+'dB',r.dbfs.toFixed(2),r.peak.toFixed(4),(r.nearCeilingFraction*100).toFixed(3)+'%']){const td=document.createElement('td');td.textContent=value;tr.append(td);}el('dense').append(tr);}
data.performance.forEach((r,i)=>{const o=document.createElement('option');o.value=i;o.textContent=r.channel+' · +'+r.enhancementDb+'dB';el('sample').append(o);});
function chart(){const r=data.performance[Number(el('sample').value)],key=el('metric').value,points=r.timeline,values=points.map(p=>p[key]);let lower=key==='audioRmsDb'?Math.floor(Math.min(...values)/10)*10:0,upper=key==='audioRmsDb'?0:Math.max(.01,...values)*1.15;const last=points.at(-1).seconds,path=points.map((p,i)=>(i?'L':'M')+(58+p.seconds/last*875).toFixed(2)+' '+(210-(p[key]-lower)/(upper-lower)*180).toFixed(2)).join(' ');el('chart').innerHTML='<path d="M58 24V210H935" stroke="#718575" fill="none"/><path d="'+path+'" stroke="#d7e98c" stroke-width="2.5" fill="none"/><text x="8" y="34" fill="#a3b5a9" font-size="13">'+upper.toFixed(1)+'</text><text x="8" y="214" fill="#a3b5a9" font-size="13">'+lower.toFixed(1)+'</text><text x="58" y="238" fill="#a3b5a9" font-size="13">0秒</text><text x="890" y="238" fill="#a3b5a9" font-size="13">'+Math.round(last)+'秒</text>';el('caption').textContent=points.length+'项时间线 · '+r.frames.toLocaleString()+'帧 · 节点登记／场景 '+r.anchors.registered+'／'+r.anchors.inScene;}
for(const id of ['browser','distance'])el(id).addEventListener('change',comparison);for(const id of ['sample','metric'])el(id).addEventListener('change',chart);comparison();chart();
</script></main></html>'''
    for key, value in [('__GAIN6__', f'{gain6[0]:.1f}～{gain6[1]:.1f}'), ('__GAIN12__', f'{gain12[0]:.1f}～{gain12[1]:.1f}'),
                       ('__CHECKS__', checks), ('__PERFORMANCE__', performance), ('__DATA__', data)]: page = page.replace(key, value)
    REPORT.write_text(page, encoding='utf-8'); print('已生成：'+str(REPORT))


def verify_page():
    from playwright.sync_api import sync_playwright
    result = {'checks': [], 'errors': []}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.on('pageerror', lambda e: result['errors'].append(str(e)))
        page.goto(REPORT.as_uri(), wait_until='load')
        for channel in ['chrome', 'msedge']:
            for distance in ['20', '160']:
                page.locator('#browser').select_option(channel); page.locator('#distance').select_option(distance)
                assert page.locator('#comparison article').count() == 13
        result['checks'].append('两浏览器双距离13素材平均增益离线切换')
        for sample in range(4):
            for metric in ['audioRmsDb', 'p95', 'voices', 'audioPeak']:
                page.locator('#sample').select_option(str(sample)); page.locator('#metric').select_option(metric)
                assert 'NaN' not in page.locator('#chart').inner_html() and page.locator('#chart path').count() == 2
        assert page.locator('#dense tr').count() == 6
        result['checks'].append('四样本四指标时间线与六项密集波形数据')
        for href in page.locator('a').evaluate_all('(a)=>a.map(e=>e.getAttribute("href"))'):
            if not href.startswith(('http:', 'https:', '#')): assert (REPORT.parent/href).is_file(), href
        assert 'E:\\' not in REPORT.read_text('utf-8') and 'C:\\Users' not in REPORT.read_text('utf-8')
        result['checks'].append('本地链接存在且无本机绝对路径')
        page.locator('#sample').select_option('0'); page.locator('#metric').select_option('audioRmsDb')
        page.screenshot(path=str(OUT/'响度报告-桌面.png'), full_page=True)
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
        page.screenshot(path=str(OUT/'响度报告-窄屏.png'), full_page=True)
        page.emulate_media(media='print'); assert page.evaluate('getComputedStyle(document.body).backgroundColor') == 'rgb(255, 255, 255)'
        result['checks'].append('390窄屏不横向溢出，打印白底')
        assert not result['errors']; result['passed'] = True; browser.close()
    (OUT/'报告页面验证.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--verify-page', action='store_true')
    if parser.parse_args().verify_page: verify_page()
    else: generate()
