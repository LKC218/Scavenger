"""从本轮完整回执生成脱敏的离线音效覆盖与负载报告，保留城市历史报告。"""
import hashlib
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT/'local-data/原型/载具靶场'
OUT = DATA/'验证/音效补全1'
REPORT = ROOT/'reports/载具靶场'


def generate():
    receipts = []
    for browser in ['chrome', 'msedge']:
        for suffix in ['音效交互', '持续负载']:
            record = json.loads((OUT/(browser+'-'+suffix+'.json')).read_text('utf-8'))
            if not record.get('passed') or record['errors']: raise ValueError('回执未通过：'+browser+suffix)
            for row in record['source_identity']:
                if hashlib.sha256((ROOT/row['path']).read_bytes()).hexdigest() != row['sha256']:
                    raise ValueError('源码已变更，不能沿用回执：'+row['path'])
            receipts.append(record)
    loops = json.loads((DATA/'循环音效核验.json').read_text('utf-8'))
    manifest = json.loads((DATA/'资源清单.json').read_text('utf-8'))
    for row in loops:
        asset = manifest['audio'][row['key']]
        if not row['source_unchanged'] or asset['sha256'] != row['derived_sha256'] or not asset.get('loop'):
            raise ValueError('当前运行循环与核验不符')
        if hashlib.sha256((DATA/'循环音效'/(row['key']+'-循环.wav')).read_bytes()).hexdigest() != row['derived_sha256']:
            raise ValueError('派生文件摘要变化')
    summary = {'schema': 1, 'scope': '三型载具机械运行声补全', 'conditions': '独立无窗口浏览器，1600×1000，640宽三维；本机RX7900XTX；非人工听审',
               'loops': loops, 'interactions': [], 'samples': [], 'source_identity': receipts[0]['source_identity']}
    for record in receipts:
        if 'real_asset_output' in record:
            summary['interactions'].append({k: record[k] for k in ['channel', 'version', 'checks', 'real_asset_output', 'tank_transition', 'muted_peak', 'reset_anchors']})
        for sample in record['samples']:
            if any(p['voices'] > 48 or p['engines'] > 8 for p in sample['timeline']):
                raise ValueError('时间线声音预算超限')
            # 不发布设备私有字段；时间线只保留本报告所需计数和指标。
            row = {k: sample[k] for k in ['date', 'duration', 'frames', 'p50', 'p95', 'p99', 'max', 'over33ms', 'cpuP95', 'totalUnits', 'shots', 'hits', 'anchors']}
            row['channel'] = record['channel']; row['version'] = record['version']; row['audio'] = sample['audio']
            row['timeline'] = [{k: point[k] for k in ['seconds', 'p95', 'voices', 'engines', 'audioPeak', 'shots', 'hits']} for point in sample['timeline']]
            row['newShots'] = sample['timeline'][-1]['shots']-sample['timeline'][0]['shots']
            row['newHits'] = sample['timeline'][-1]['hits']-sample['timeline'][0]['hits']
            row['sampledMaxVoices'] = max(p['voices'] for p in sample['timeline'])
            row['sampledMaxEngines'] = max(p['engines'] for p in sample['timeline'])
            summary['samples'].append(row)
    if len(summary['samples']) != 4: raise ValueError('要求四组完整采样')
    REPORT.mkdir(exist_ok=True)
    (REPORT/'载具音效验证摘要.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    performance_rows = ''.join(f'<tr><th>{r["channel"]} · {r["totalUnits"]}单位</th><td>{r["duration"]:.0f}秒</td><td>{r["p95"]:.2f} / {r["p99"]:.2f}</td><td>{r["max"]:.2f}</td><td>{r["over33ms"]}</td><td>{r["audio"]["voices"]} / {r["audio"]["engines"]}</td><td>{r["newShots"]:,}</td></tr>' for r in summary['samples'])
    loop_rows = ''.join(f'<tr><th>{html.escape(manifest["audio"][r["key"]]["name"])}</th><td>{r["seconds"]:.2f}秒</td><td>{r["rms"]:.3f}</td><td>{r["minimum_100ms_rms"]:.3f}</td><td>{r["boundary_step"]:.4f} / {r["local_difference_p999"]:.4f}</td></tr>' for r in loops)
    checks = ''.join('<li>'+html.escape(check)+'</li>' for check in summary['interactions'][0]['checks'])
    data = json.dumps(summary, ensure_ascii=False).replace('</', '<\\/')
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>载具音效补全与验证</title>
<style>
:root{color-scheme:dark;--bg:#10191e;--panel:#1a272e;--line:#35464c;--text:#e5ede9;--muted:#adc1bd;--accent:#e9b963;--green:#9cd0af}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:16px/1.7 system-ui,'Microsoft YaHei',sans-serif}main{max-width:1180px;margin:auto;padding:48px 28px 70px}header{border-top:4px solid var(--accent);padding:28px 0}h1{font-size:clamp(30px,4vw,52px);line-height:1.2;margin:12px 0 20px}h2{font-size:26px;margin:0 0 18px}h3{margin:0 0 10px}.eyebrow{color:var(--accent);letter-spacing:.14em;font-size:13px}p{margin:0 0 16px}.muted,small{color:var(--muted)}a{color:var(--accent);text-underline-offset:4px}section{margin:30px 0;padding:28px;background:var(--panel);border:1px solid var(--line);border-radius:12px}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.card{padding:22px;border:1px solid var(--line);border-radius:9px}.tag{color:var(--green);font-size:13px}.flow{padding:12px;background:#10191e;border-radius:6px;color:var(--accent)}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:12px 10px;border-bottom:1px solid var(--line);white-space:nowrap}th{font-weight:600}.scroll{overflow:auto}select{max-width:100%;background:#10191e;color:var(--text);padding:10px;border:1px solid var(--line);border-radius:6px;font:inherit}label{display:inline-flex;gap:10px;align-items:center;flex-wrap:wrap;margin:0 16px 16px 0}svg{width:100%;height:auto;background:#10191e;border-radius:8px}#metric-caption{margin-top:10px}.notice{border-left:3px solid var(--accent);padding-left:16px;color:var(--muted)}li{margin:8px 0}.bars{display:grid;gap:12px}.bar{display:grid;grid-template-columns:140px 1fr 80px;align-items:center;gap:10px}.bar i{height:14px;background:var(--green);display:block;min-width:3px;border-radius:2px}details{margin-top:20px}summary{cursor:pointer;color:var(--accent)}code{overflow-wrap:anywhere}footer{color:var(--muted);font-size:14px}
@media(max-width:760px){main{padding:22px 14px}.grid{grid-template-columns:1fr}section{padding:20px 16px}.bar{grid-template-columns:80px 1fr 65px}h2{font-size:23px}}
@media print{:root{color-scheme:light;--bg:white;--panel:white;--text:#142018;--muted:#41534c;--line:#aaa;--accent:#715018;--green:#306346}main{padding:0}section{break-inside:avoid}svg{background:#eee}select{background:white;color:black}.flow{background:#eee;color:#333}}
</style><main>
<header><span class="eyebrow">载具靶场 / 音效增量验证</span><h1>载具停下来，<br>机械声继续运行。</h1><p>补齐M1A2、AH-64D与A-10C的持续运行声音，消除有限素材尾部静音与移动停止即断声的问题。</p><p><a href="http://127.0.0.1:8768/">打开本机靶场 ↗</a>　<a href="载具音效验证摘要.json">查看验证摘要</a>　<a href="城市固定翼与性能验证.html">城市阶段历史报告</a></p><p class="notice">本页可离线阅读。试玩需要本机8768服务与本地资源。测试为独立无窗口Chrome／Edge；技术输出通过不代替耳机、扬声器的人工听感验收。</p></header>
<div class="grid"><article class="card"><span class="tag">运行覆盖</span><h3>M1A2 坦克</h3><p class="flow">待机 ⇄ 行驶</p><p>停车保留引擎底声；行驶增大响度、提高播放速率。原型使用同一真实引擎素材，不冒充独立履带录音。</p></article><article class="card"><span class="tag">运行覆盖</span><h3>AH-64D 直升机</h3><p class="flow">悬停 ⇄ 旋翼飞行</p><p>悬停仍有连续旋翼声，移动时平滑变化。原有机炮和导弹发射、命中事件继续保留。</p></article><article class="card"><span class="tag">运行覆盖</span><h3>A-10C 固定翼</h3><p class="flow">巡航盘旋 ⇄ 攻击任务</p><p>涡轮持续工作，取消任务后继续盘旋和发声；机炮、投弹与爆炸仍按原事件触发。</p></article></div>
<section><h2>声音为何曾经不全</h2><p>旧逻辑只选取正在移动且240米内的前八个单位，停车与悬停直接停声。三份10.24秒有限渲染中，最后约两秒没有有效声音，按整段时长重播就反复出现空档。</p><p>现在以三份确证机械声的1至7.5秒稳定段，生成单声道、120毫秒平滑交叉淡化的6.38秒循环。按音频时钟连续播放，状态切换只改变增益和速率，不重启整个音源。</p><div class="scroll"><table><thead><tr><th>真实来源事件</th><th>派生周期</th><th>循环RMS</th><th>跨四周期最小窗RMS</th><th>接缝跳变 / 素材局部差分P99.9</th></tr></thead><tbody>__LOOPS__</tbody></table></div><p class="muted">接缝指标衡量数值连续性；不代表已完成主观无缝听审。正式库原WAV和原模型保持只读，源ID与SHA-256保存在摘要中。</p></section>
<section><h2>真实素材输出，而非仅检查播放标志</h2><p>双浏览器分别从运行中三型空间声源的增益输出抽样14秒，跨过至少两个循环。下图展示各型最小分析窗RMS；所有窗均有有效信号。</p><label>浏览器<select id="browser"><option value="chrome">Chrome</option><option value="msedge">Edge</option></select></label><div id="asset-bars" class="bars"></div><p class="muted">此为真实素材节点的短窗技术分析，未录制扬声器声音。零音量另外检查混音末端，输出接近0。</p><details><summary>查看已通过的真实输入检查</summary><ul>__CHECKS__</ul></details></section>
<section><h2>有声交战负载</h2><p>每组预热10秒后正式采样。100单位为主验档，500单位只探索；所有单位仍参与模拟，声音按预算代表播放。总物理声音不超过48：八路机械声与四十路事件。</p><div class="scroll"><table><thead><tr><th>样本</th><th>正式时长</th><th>P95 / P99 毫秒</th><th>最长帧 毫秒</th><th>超过33.34毫秒</th><th>末端总声部 / 机械声</th><th>段内新增发射</th></tr></thead><tbody>__PERFORMANCE__</tbody></table></div><p class="muted">条件：本机RX7900XTX、1600×1000视口、内部宽640的像素画面，独立无窗口浏览器，串行执行。不能据此承诺其他设备或可见窗口长时性能。</p><label>样本<select id="sample"></select></label><label>指标<select id="metric"><option value="p95">滚动P95 毫秒</option><option value="voices">总声部</option><option value="engines">机械声</option><option value="audioPeak">抽样峰值</option></select></label><svg id="chart" viewBox="0 0 960 250" role="img" aria-label="有声交战时间线"></svg><p id="metric-caption" class="muted"></p><p class="muted">时间线约每秒一项，P95为最近最多3600帧的滚动值；表格P95／P99取整个正式采样段。峰值是分析窗抽样，不能当作全程逐样本最大值。</p></section>
<section><h2>预算与距离</h2><p>700米内先保留各型号一个代表，余量按选中、邻近和已发声优先分配；560至700米淡出。坦克／旋翼／固定翼的参考距离分别45／90／140米，按同型数量降低叠加增益、分散循环相位，避免一群同型号完全同相放大。退场淡出仍占八路预算，释放后才补位。</p><p class="notice">没有找到独立发电机事件，当前用真实引擎待机覆盖机械运行感；没有新增未实现的武器、损毁、起降声音。状态播放速率属于原型混音，尚未重建原游戏FMOD的RPM多层参数。</p></section>
<section><h2>回来后这样验收</h2><ol><li>打开靶场并刷新，点击“声音 关”开启声音，保持70%音量。</li><li>按1选坦克，C聚焦；右键移动，再按E停车，对比行驶和待机。</li><li>按2选旋翼，C聚焦；移动后按E，检查悬停仍有旋翼声。</li><li>按4选固定翼，C聚焦；全场总览后B拖线，按E取消，确认盘旋仍有涡轮声。</li><li>按空格暂停或关闭声音，检查没有残留；音色、响度与战斗遮盖感由工程师听审。</li></ol></section>
<footer>本报告由本轮四份通过回执生成，校验运行源码身份；完整原始证据保存在本地忽略目录“原型／载具靶场／验证／音效补全1”。历史城市报告及测量未覆盖。源音频与循环派生分别记录，资源未公开发布。</footer>
<script type="application/json" id="data">__DATA__</script><script>
const data=JSON.parse(document.getElementById('data').textContent),el=id=>document.getElementById(id),names={tank:'坦克',heli:'旋翼',plane:'固定翼'};
function assets(){const row=data.interactions.find(r=>r.channel===el('browser').value);el('asset-bars').replaceChildren();for(const r of row.real_asset_output){const div=document.createElement('div');div.className='bar';const label=document.createElement('span');label.textContent=names[r.kind];const bar=document.createElement('i');bar.style.width=Math.min(100,r.minRms/.012*100)+'%';const number=document.createElement('small');number.textContent=r.minRms.toFixed(4);div.append(label,bar,number);el('asset-bars').append(div);}}
data.samples.forEach((r,i)=>{const o=document.createElement('option');o.value=i;o.textContent=r.channel+' · '+r.totalUnits+'单位 · '+Math.round(r.duration)+'秒';el('sample').append(o);});
function chart(){const r=data.samples[Number(el('sample').value)],key=el('metric').value,points=r.timeline,max=Math.max(.01,...points.map(p=>p[key]))*1.15,last=points.at(-1).seconds;const path=points.map((p,i)=>(i?'L':'M')+(52+p.seconds/last*884).toFixed(2)+' '+(212-p[key]/max*184).toFixed(2)).join(' ');el('chart').innerHTML='<path d="M52 24V212H936" fill="none" stroke="#718880"/><path d="'+path+'" fill="none" stroke="#e9b963" stroke-width="2.5"/><text x="10" y="32" fill="#adc1bd" font-size="13">'+max.toFixed(2)+'</text><text x="25" y="215" fill="#adc1bd" font-size="13">0</text><text x="52" y="238" fill="#adc1bd" font-size="13">0秒</text><text x="878" y="238" fill="#adc1bd" font-size="13">'+Math.round(last)+'秒</text>';el('metric-caption').textContent=points.length+'项时间线 · 正式段'+r.frames.toLocaleString()+'帧 · 新增命中'+r.newHits.toLocaleString()+' · 节点登记／场景 '+r.anchors.registered+'／'+r.anchors.inScene;}
el('browser').addEventListener('change',assets);el('sample').addEventListener('change',chart);el('metric').addEventListener('change',chart);assets();chart();
</script></main></html>'''
    for key, value in [('__LOOPS__', loop_rows), ('__CHECKS__', checks), ('__PERFORMANCE__', performance_rows), ('__DATA__', data)]: page = page.replace(key, value)
    path = REPORT/'载具音效补全验证.html'; path.write_text(page, encoding='utf-8')
    print('已生成：'+str(path))


if __name__ == '__main__': generate()
