"""验证真实轮廓、建筑遮光、可动部件、远景退化及局部有声性能。"""
import argparse
import hashlib
import html
import json
from pathlib import Path
import re
from urllib.parse import unquote

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'local-data/原型/载具靶场/验证/实时阴影1'
PICTURES = ROOT/'reports/载具靶场/实时阴影对照'


def sha(data): return hashlib.sha256(data).hexdigest()


def verify_report():
    directory=ROOT/'reports/载具靶场';path=directory/'实时阴影验证.html'
    result={'errors':[],'image_checks':[],'links':[]}
    for name in ['实时阴影验证.md','实时阴影验证.html']:
        content=(directory/name).read_text('utf8')
        assert not re.search(r'\b[A-Za-z]:[\\/](?:Users|SteamLibrary)',content,re.I)
        links=re.findall(r'\[[^\]\n]*\]\(([^)\n]+)\)',content) if name.endswith('.md') else re.findall(r'href="([^"]+)"',content)
        for link in links:
            if link.startswith(('http:','https:','#')):continue
            assert (directory/unquote(link.split('#')[0])).exists(),link;result['links'].append(link)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(viewport={'width':1400,'height':1000})
        page.on('pageerror',lambda e:result['errors'].append(str(e)))
        page.goto(path.as_uri());assert page.locator('tbody tr').count()==6
        for scene in ['三型','建筑','画外']:
            page.locator(f'[data-scene="{scene}"]').click()
            for mode in (['simple','realtime'] if scene!='画外' else ['realtime']):
                if scene!='画外':page.locator(f'[data-mode="{mode}"]').click()
                page.wait_for_function('()=>document.querySelector("#comparison").complete&&document.querySelector("#comparison").naturalWidth>0')
                result['image_checks'].append({'scene':scene,'mode':mode,'source':page.locator('#comparison').get_attribute('src')})
        page.set_viewport_size({'width':390,'height':844})
        result['mobile']=page.evaluate('()=>({width:innerWidth,document:document.documentElement.scrollWidth})')
        assert result['mobile']['document']<=390
        page.screenshot(path=str(OUT/'报告窄屏.png'),full_page=True)
        page.emulate_media(media='print');assert page.evaluate('()=>getComputedStyle(document.documentElement).backgroundColor')=='rgb(255, 255, 255)'
        assert not result['errors'];result['passed']=True;browser.close()
    (OUT/'报告页面验证.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print('报告离线图片、交互、链接、窄屏及打印检查通过',flush=True)


def report():
    """从固定回执生成可离线阅读的对照，不公开本机资源路径。"""
    files=['chrome-视觉.json','msedge-视觉.json','chrome-性能-100.json','msedge-性能-100.json','chrome-性能-500.json']
    receipts=[json.loads((OUT/name).read_text('utf8')) for name in files]
    identity=receipts[0]['source_identity'];runtime=receipts[0]['runtime_identity']
    for result in receipts:
        assert result.get('passed') and not result['errors']
        assert result['source_identity']==identity and result['runtime_identity']==runtime
    for item in identity:assert sha((ROOT/item['path']).read_bytes())==item['sha256']
    rows=[]
    for result in receipts[2:]:
        for case in result['cases']:
            sample=case['sample'];rows.append({'browser':result['channel'],'version':result['version'],'mode':case['mode'],
                'count':sample['totalUnits'],'p95':sample['p95'],'p99':sample['p99'],'max':sample['max'],'over33ms':sample['over33ms'],
                'gpu95':case['timing']['gpu']['p95'],'gpu50':case['timing']['gpu']['p50'],'renderCpu95':case['timing']['cpuSubmission']['p95'],
                'drawCalls':sample['drawCalls'],'triangles':sample['triangles'],'casters':case['timing']['casters'],
                'shadow':sample['shadow'],'renderer':sample['renderer'],'internal':sample['internal'],'visibleUnitsStart':sample['visibleUnits'],'visibleUnitsEnd':case['end']['visibleUnits'],'shotsEnd':case['end']['shots'],
                'within60fps':case['within60fps'],'disjoint':case['timing']['disjoint'],'dropped':case['timing']['dropped']})
    summary={'schema':1,'source_identity':identity,'runtime_identity':runtime,'viewport':[1600,1000],'camera':{'x':-180,'z':165,'span':120,'angle':.35},
             'conditions':'独立无窗口；三型现有运行资源；局部前线持续交战；音量100%及6dB；640宽；预热10秒再采样60秒；浏览器串行','cases':rows}
    main=[r for r in rows if r['count']==100 and r['mode']=='realtime']
    deltas={channel:next(r['gpu95'] for r in rows if r['count']==100 and r['mode']=='realtime' and r['browser']==channel)-next(r['gpu95'] for r in rows if r['count']==100 and r['mode']=='simple' and r['browser']==channel) for channel in ['chrome','msedge']}
    summary['mainPassed']=all(r['within60fps'] for r in main);summary['gpu95Delta100']=deltas
    over=[k for k,v in deltas.items() if v>2]
    conclusion='100单位实时阴影两浏览器整帧P95'+('均通过' if summary['mainPassed'] else '未全部通过')+'16.67ms目标。独立采样的GPU P95差值：'+ '，'.join(f'{k}约{v:+.2f}ms' for k,v in deltas.items())+'。'+('、'.join(over)+'超出' if over else '均未超出')+'最初约2ms的增量参考；两组独立采样的分位差不是逐帧因果测量，不能承诺统一或零GPU开销。'
    directory=ROOT/'reports/载具靶场'
    (directory/'实时阴影验证摘要.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
    table=['| 浏览器 | 单位 | 模式 | 帧P95 | 最长帧 | GPU P95 | 投影单位中位数 |','| --- | --- | --- | --- | --- | --- | --- |']
    for r in rows:table.append(f'| {r["browser"]} | {r["count"]} | {"实时" if r["mode"]=="realtime" else "简化"} | {r["p95"]:.2f}ms | {r["max"]:.2f}ms | {r["gpu95"]:.3f}ms | {r["casters"]["p50"]} |')
    markdown='''# 靶场实时阴影验证

固定太阳的局部实时投影已接入，提供真实部件轮廓、建筑遮光和自阴影；右侧阴影控件可切简化模式。近景2048阴影图，跨度220至420米平滑切回简化底影，总览停止阴影图更新。

## 视觉与交互

Chrome／Edge的实际截图、独立阴影层、共享几何、画外投影者、炮塔／旋翼矩阵同步、远景互补权重、真实键鼠选择／移动／缩放和连续重置通过。直接设置模型姿态仅用于渲染夹具，不冒充战斗输入验收。未发现明显条纹、悬浮投影；快速镜头长时间试玩仍需工程师验收。

[离线视觉对照](实时阴影验证.html)可切换简化／实时，比较三型轮廓与建筑遮光。

## 性能

CONDITIONS

TABLE

CONCLUSION

100单位主验以整帧P95不超过16.67ms判断；GPU查询包含主绘制和阴影，异步读取，不用相同FPS推断零开销。500仅探索，投影单位为局部灯光视锥内实例；显示模型仍全量提交，不能把本表当成500单位全部近景同屏容量。单组60秒不代表长时间无尖峰或其他设备达标。

## 实现与边界

显示与投影实例共享当前资源几何，可动部件使用同一矩阵。主相机第0层、阴影相机第1层，依据本地固定Three.js 0.187.0支持；投影批按灯光视锥压紧，建筑仍共享100个盒体批。光照方向固定，影区按纹素对齐；不增加级联阴影、额外实时灯或后期。

远景建筑投影随实时侧淡出；简化模式保留载具底影。首轮不制作远景建筑烘焙图。原模型及原声音只读，未做新的投影减面。当前结果使用冻结运行清单，未覆盖并行会话的独立游戏版资源入口。

源码与清单SHA及完整浏览器回执保存在本地`验证/实时阴影1/`；公开摘要仅保留身份和测量，不包含原模型、原音频及私人路径。旧假阴影、城市和声音报告保留当时条件。

官方依据：[阴影原理](https://threejs.org/manual/pages/shadows.html)、[阴影参数](https://threejs.org/docs/pages/LightShadow.html)。
'''.replace('CONDITIONS',summary['conditions']+'。内部尺寸'+str(rows[0]['internal'])+'；实际GPU：'+rows[0]['renderer']+'。').replace('TABLE','\n'.join(table)).replace('CONCLUSION',conclusion)
    (directory/'实时阴影验证.md').write_text(markdown,encoding='utf8')
    bodyrows=''.join('<tr>'+''.join(f'<td>{html.escape(str(x))}</td>' for x in [r['browser'],r['count'],'实时' if r['mode']=='realtime' else '简化',f'{r["p95"]:.2f}',f'{r["max"]:.2f}',f'{r["gpu95"]:.3f}',r['casters']['p50']])+'</tr>' for r in rows)
    document='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>载具靶场 · 实时阴影验证</title>
<style>:root{color-scheme:dark;font:16px/1.7 system-ui;background:#18221e;color:#e5eadc}body{max-width:1120px;margin:auto;padding:32px 20px}h1{font-size:clamp(28px,5vw,48px);line-height:1.2}h2{margin-top:36px}small,p{color:#bec8b3}.tag{color:#d6ed92;font-size:13px;letter-spacing:2px}button,a{color:#d6ed92}button{background:#29352c;border:1px solid #59694c;border-radius:6px;padding:9px 18px;cursor:pointer;margin:4px}button[aria-pressed=true]{background:#d6ed92;color:#18221e}.frame{background:#29352c;padding:10px;border-radius:10px}.frame img{width:100%;display:block;image-rendering:pixelated}.table{overflow:auto}table{border-collapse:collapse;white-space:nowrap;width:100%;font-size:14px}td,th{text-align:left;padding:12px;border-bottom:1px solid #3e4a3c}.note{border-left:3px solid #99b86a;padding:8px 18px;background:#243025}a{overflow-wrap:anywhere}@media print{:root{color-scheme:light;background:white;color:black}button{display:none}small,p{color:#333}.note{background:#eee}body{padding:0}}</style>
<span class="tag">THREE.JS / 载具靶场</span><h1>让阴影跟随实体轮廓</h1><p>固定太阳 · 2048局部实时投影 · 建筑遮光 · 自阴影 · 远景平滑降级</p>
<div class="note">100单位以60 FPS为主验目标。表中为本机独立无窗口实测；500单位属于探索，人工试玩与其他设备另行验收。</div>
<h2>实际画面对照</h2><div id="scenes"><button data-scene="三型" aria-pressed="true">三型载具轮廓</button><button data-scene="建筑" aria-pressed="false">建筑遮光</button><button data-scene="画外" aria-pressed="false">画外投影</button></div>
<div id="modes"><button data-mode="simple" aria-pressed="false">简化</button><button data-mode="realtime" aria-pressed="true">实时</button></div><p id="caption"></p><div class="frame"><img id="comparison" alt="载具阴影实测截图"></div>
<h2>局部交战性能</h2><p>CONDITIONS。内部640宽，视口1600×1000，跨度120米；GPU计时包含主绘制和阴影。</p><div class="table"><table><thead><tr><th>浏览器</th><th>单位</th><th>阴影</th><th>帧P95毫秒</th><th>最长帧毫秒</th><th>GPU P95毫秒</th><th>投影单位中位数</th></tr></thead><tbody>BODYROWS</tbody></table></div>
<p class="note">CONCLUSION</p><h2>如何实现</h2><p>显示与投影实例共享真实模型几何，独立阴影层只提交局部投影单位。炮塔、炮管、旋翼与机体侧倾同步；地图、道路、建筑与模型接收阴影。模型原件和音效没有改写。</p><p>跨度220至420米平滑切回简化底影，总览停止更新阴影图。远景建筑投影同步淡出；本轮未增加烘焙建筑图、级联阴影或额外实时灯。</p>
<p>相同帧率不代表GPU成本相同。刷新率限制、局部投影数量、现用资源版本和采样时间均限制结论；不以本表承诺500单位全部同屏或长期无尖峰。</p><p><a href="实时阴影验证.md">完整说明</a> · <a href="实时阴影验证摘要.json">脱敏数据</a> · <a href="../../src/载具靶场/README.md">模块说明</a></p>
<script>let scene='三型',mode='realtime';const img=document.querySelector('#comparison');function update(){let name,caption;if(scene==='三型'){name='chrome-'+mode+'-三型';caption='飞机机翼、尾翼和直升机旋翼的真实轮廓，近景关闭叠加底影。'}else if(scene==='建筑'){name=mode==='realtime'?'chrome-建筑遮光':'chrome-建筑无遮光对照';caption='相同载具位置：建筑阴影覆盖地面及车身。'}else{name='chrome-画外投影';caption='直升机已离开主相机画面，仍向画内投影；此图仅提供实时模式。'}img.src='实时阴影对照/'+name+'.png';document.querySelector('#caption').textContent=caption;document.querySelectorAll('[data-scene]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.scene===scene)));document.querySelectorAll('[data-mode]').forEach(b=>{b.disabled=scene==='画外';b.setAttribute('aria-pressed',String(b.dataset.mode===mode))});}document.querySelectorAll('[data-scene]').forEach(b=>b.onclick=()=>{scene=b.dataset.scene;update()});document.querySelectorAll('[data-mode]').forEach(b=>b.onclick=()=>{mode=b.dataset.mode;update()});update();</script></html>'''.replace('CONDITIONS',html.escape(summary['conditions'])).replace('BODYROWS',bodyrows).replace('CONCLUSION',html.escape(conclusion))
    (directory/'实时阴影验证.html').write_text(document,encoding='utf8')
    print('实时阴影报告及脱敏摘要已生成',flush=True)


def run(channel, performance=False, count=100):
    port = json.loads((OUT/'服务身份.json').read_text('utf-8-sig'))['port']
    url = f'http://127.0.0.1:{port}/'
    result = {'channel': channel, 'headless': True, 'viewport': [1600, 1000],
              'kind': '性能' if performance else '视觉交互', 'cases': [], 'errors': []}
    sources = sorted(p for p in (ROOT/'src/载具靶场').iterdir() if p.suffix in ['.mjs', '.html', '.css'])
    result['source_identity'] = [{'path': p.relative_to(ROOT).as_posix(), 'sha256': sha(p.read_bytes())} for p in sources]
    OUT.mkdir(parents=True, exist_ok=True); PICTURES.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=channel, headless=True)
        result['version'] = browser.version
        try:
            for mode in ['simple', 'realtime']:
                context = browser.new_context(viewport={'width': 1600, 'height': 1000})
                page = context.new_page()
                page.on('pageerror', lambda e: result['errors'].append(str(e)))
                page.on('console', lambda m: result['errors'].append(m.text) if m.type == 'error' else None)
                page.on('response', lambda r: result['errors'].append(str(r.status)+' '+r.url) if r.status >= 400 else None)
                page.goto(url, wait_until='networkidle')
                def wait(expr, timeout=30000): page.wait_for_function('()=>'+expr, timeout=timeout)
                def value(expr): return page.evaluate('()=>'+expr)
                wait('window.range?.ready', 60000)
                manifest = page.request.get(url+'manifest.json').body()
                assert sha(manifest) == sha((OUT/'改前源码/运行清单.json').read_bytes()), '清单发生变化'
                fingerprint = {'manifest': sha(manifest), 'loader': sha(page.request.get(url+'compressed-loader.mjs').body())}
                if 'runtime_identity' in result:assert result['runtime_identity']==fingerprint
                result['runtime_identity'] = fingerprint
                assert page.request.get(url+'场景.mjs').body() == (ROOT/'src/载具靶场/场景.mjs').read_bytes()
                page.locator('#shadow-quality').select_option(mode)
                row = {'mode': mode}; result['cases'].append(row)
                if performance:
                    page.locator('#unit-count').select_option(str(count))
                    page.locator('#load-mode').select_option('combat')
                    # 局部镜头看持续交战前线，不能用已自动降级的全场总览测实时阴影。
                    page.evaluate('()=>{const v=range.view;v.stopCamera();v.center.set(-180,0,165);v.span=v.targetSpan=120;v.angle=v.targetAngle=.35;v.updateCamera();}')
                    page.locator('#sound').click(); wait('range.mixer.active&&range.mixer.engines.size===8')
                    page.evaluate('''()=>{
                      const v=range.view,gl=v.renderer.getContext(),ext=gl.getExtension('EXT_disjoint_timer_query_webgl2'),draw=v.render.bind(v);
                      window.timing={cpu:[],gpu:[],pending:[],frames:0,disjoint:0,dropped:0,casters:[]};
                      v.render=()=>{const s=timing,b=range.benchmark.run,active=b&&performance.now()-b.start>=b.warmup*1000;
                        if(ext&&s.pending.length){const q=s.pending[0];if(gl.getQueryParameter(q,gl.QUERY_RESULT_AVAILABLE)){s.pending.shift();if(gl.getParameter(ext.GPU_DISJOINT_EXT))s.disjoint++;else s.gpu.push(gl.getQueryParameter(q,gl.QUERY_RESULT)/1e6);gl.deleteQuery(q);}}
                        let q=null;if(active&&ext&&s.frames++%8===0){if(s.pending.length<8){q=gl.createQuery();gl.beginQuery(ext.TIME_ELAPSED_EXT,q);}else s.dropped++;}
                        const t=performance.now();draw();if(q){gl.endQuery(ext.TIME_ELAPSED_EXT);s.pending.push(q);}if(active){s.cpu.push(performance.now()-t);s.casters.push(v.shadowStats.casters);}
                      };
                    }''')
                    print(f'{channel} {count}单位 {mode}：预热10秒＋近景交战60秒', flush=True)
                    page.locator('#bench60').click();wait('!!range.benchmark.run');wait('!range.benchmark.run', 95000)
                    row['sample']=value('range.benchmark.last');row['end']=value('range.perf()')
                    row['timing']=page.evaluate('''()=>{const s=timing,summary=a=>{const b=a.slice().sort((x,y)=>x-y);return{samples:b.length,p50:b[Math.floor(b.length*.5)]??null,p95:b[Math.floor(b.length*.95)]??null,p99:b[Math.floor(b.length*.99)]??null};};return{gpu:summary(s.gpu),cpuSubmission:summary(s.cpu),casters:summary(s.casters),disjoint:s.disjoint,dropped:s.dropped};}''')
                    sample=row['sample'];assert sample['audio'] and sample['volume']==1 and sample['enhancementDb']==6
                    assert sample['totalUnits']==count and sample['duration']>=60 and sample['frames']>1000
                    assert sample['timeline'][-1]['shots']>sample['timeline'][0]['shots']+20
                    assert sample['shadow']['weight']==(1 if mode=='realtime' else 0)
                    if mode=='realtime':assert row['timing']['casters']['p50']>0
                    row['within60fps']=sample['p95']<=1000/60
                    print(f'完成 P95={sample["p95"]:.2f}ms GPU95={row["timing"]["gpu"]["p95"]}',flush=True)
                else:
                    page.locator('#load-mode').select_option('static');page.locator('#pause').click();wait('range.sim.paused')
                    # 固定姿态仅用于图像夹具，交互另以真实键鼠回归。
                    row['fixture']=page.evaluate('''()=>{const v=range.view,s=range.sim;
                      for(const [i,u] of s.units.entries()){u.x=480+i*2;u.z=480;}
                      const tank=s.units.find(u=>u.kind==='tank'),heli=s.units.find(u=>u.kind==='heli'),plane=s.units.find(u=>u.kind==='plane');
                      Object.assign(tank,{x:-270,z:-220,y:0,yaw:.5,turretYaw:-.5});Object.assign(heli,{x:-243,z:-200,y:15,yaw:-.4});Object.assign(plane,{x:-280,z:-247,y:18,yaw:.7,bank:.15});
                      v.center.set(-263,0,-225);v.span=v.targetSpan=96;v.angle=v.targetAngle=.35;v.updateCamera();v.sync(s,new Set(),null,0);v.render();return{perf:range.perf(),ids:[tank.id,heli.id,plane.id]};}''')
                    page.add_style_tag(content='#stats,#toast{visibility:hidden!important}')
                    page.locator('#canvas-host').screenshot(path=str(PICTURES/f'{channel}-{mode}-三型.png'))
                    if mode=='realtime':
                        row['structure']=page.evaluate('''()=>{const v=range.view;return{stats:{...v.shadowStats},shadowLayers:v.sun.shadow.camera.layers.mask,cameraLayers:v.camera.layers.mask,
                          parts:[...v.units.values()].flatMap(u=>u.parts).map(p=>({shared:p.batch.geometry===p.caster.geometry,receive:p.batch.receiveShadow,cast:p.caster.castShadow,layers:p.caster.layers.mask})),
                          buildings:v.terrainRoot.children.filter(o=>o.castShadow).map(o=>({receive:o.receiveShadow,layers:o.layers.mask}))};}''')
                        assert row['structure']['shadowLayers']==2 and row['structure']['cameraLayers']==1
                        assert all(p['shared'] and p['receive'] and p['cast'] and p['layers']==2 for p in row['structure']['parts'])
                        assert row['structure']['stats']['casters']>=3
                        row['parts']=page.evaluate('''async()=>{const THREE=await import('three'),v=range.view,s=range.sim,rows=[];
                          for(const yaw of [-1,.4,1.5]){const u=s.units.find(u=>u.kind==='tank');u.turretYaw=yaw;s.time+=.2;v.sync(s,new Set(),null,0);
                            let delta=0;for(const kind of ['tank','heli','plane']){const u=s.units.find(u=>u.kind===kind),unit=v.units.get(u.id);for(const p of unit.parts){const a=new THREE.Matrix4(),b=new THREE.Matrix4();p.batch.getMatrixAt(unit.index,a);p.caster.getMatrixAt(unit.shadowIndex,b);delta=Math.max(delta,...a.elements.map((x,i)=>Math.abs(x-b.elements[i])));}}rows.push({yaw,delta});}
                          return rows;}''')
                        assert all(r['delta']<1e-6 for r in row['parts'])
                        row['offscreen']=page.evaluate('''()=>{const v=range.view,s=range.sim,u=s.units.find(u=>u.kind==='heli');
                          Object.assign(u,{x:v.center.x-25,z:v.center.z+15,y:130});v.sync(s,new Set(),null,0);v.render();
                          return{air:v.project(u),ground:v.project({x:u.x+70/110*u.y,y:0,z:u.z-40/110*u.y}),shadowIndex:v.units.get(u.id).shadowIndex};}''')
                        assert not row['offscreen']['air']['visible'] and row['offscreen']['ground']['visible'] and row['offscreen']['shadowIndex']>=0
                        page.locator('#canvas-host').screenshot(path=str(PICTURES/f'{channel}-画外投影.png'))
                        # 建筑背阴和受光对照：同一辆坦克沿固定太阳方向前后移动。
                        row['building']=page.evaluate('''()=>{const v=range.view,s=range.sim,o=s.map.obstacles.filter(o=>o.building&&o.x===157).sort((a,b)=>b.h-a.h)[0],u=s.units.find(u=>u.kind==='tank');
                          Object.assign(u,{x:o.x+o.w/2+5,z:o.z-4,y:0,yaw:0,turretYaw:0});v.center.set(u.x-4,0,u.z);v.span=v.targetSpan=62;v.updateCamera();v.sync(s,new Set(),null,0);v.render();return{obstacle:o,unit:{x:u.x,z:u.z}};}''')
                        page.locator('#canvas-host').screenshot(path=str(PICTURES/f'{channel}-建筑遮光.png'))
                        page.locator('#shadow-quality').select_option('simple')
                        page.locator('#canvas-host').screenshot(path=str(PICTURES/f'{channel}-建筑无遮光对照.png'))
                        page.locator('#shadow-quality').select_option('realtime')
                        row['fade']=page.evaluate('''()=>{const v=range.view,rows=[];for(const span of [220,270,320,370,420,800]){v.span=v.targetSpan=span;v.updateCamera();v.sync(range.sim,new Set(),null,0);v.render();rows.push({...v.shadowStats,simpleOpacity:v.shadowMat.opacity,span});}return rows;}''')
                        assert row['fade'][0]['weight']==1 and row['fade'][-1]['weight']==0 and not row['fade'][-1]['updating']
                        assert all(abs(r['weight']+r['simpleOpacity']-1)<1e-6 for r in row['fade'])
                        page.locator('#canvas-host').screenshot(path=str(PICTURES/f'{channel}-远景.png'))
                        # 恢复真实初始场景，校验选取和移动没有被阴影代理影响。
                        page.locator('#load-mode').select_option('manual');page.keyboard.press('1');page.keyboard.press('c')
                        wait('!range.view.centerTarget')
                        if value('range.sim.paused'):page.locator('#pause').click()
                        wait('!range.sim.paused')
                        before=value('range.sim.units[0].x');host=page.locator('#canvas-host').bounding_box()
                        page.mouse.click(host['x']+host['width']*.35,host['y']+host['height']*.6,button='right')
                        wait('range.sim.units.some(u=>u.kind==="tank"&&u.moving)')
                        page.keyboard.down('d');page.wait_for_timeout(300);page.keyboard.up('d')
                        page.mouse.move(host['x']+host['width']/2,host['y']+host['height']/2);page.mouse.wheel(0,-180)
                        wait('range.view.span<96')
                        row['input']=value('({selected:range.commander.selected.size,moving:range.sim.units.filter(u=>u.moving).length,span:range.view.span})')
                        # 重置后实例数据应释放，共享几何保持不变。
                        page.locator('#unit-count').select_option('100');page.locator('#unit-count').select_option('30')
                        row['reset']=value('({units:range.sim.units.length,geometries:range.view.renderer.info.memory.geometries,mode:range.view.shadowMode})')
                        assert row['reset']['units']==30 and row['reset']['mode']=='realtime'
                        row['reset_geometries']=[]
                        for _ in range(3):
                            page.locator('#reset').click();wait('range.view.renderer.info.memory.geometries>0')
                            row['reset_geometries'].append(value('range.view.renderer.info.memory.geometries'))
                        assert len(set(row['reset_geometries']))==1
                for item in result['source_identity']:assert sha((ROOT/item['path']).read_bytes())==item['sha256'], '采样中源码被改变'
                context.close()
            assert not result['errors'], result['errors'];result['passed']=True
        finally:
            name=f'{channel}-性能-{count}' if performance else f'{channel}-视觉'
            (OUT/(name+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
            browser.close()
    print(channel+'：通过',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--channel',choices=['chrome','msedge'],default='chrome')
    parser.add_argument('--performance',action='store_true')
    parser.add_argument('--report',action='store_true')
    parser.add_argument('--verify-report',action='store_true')
    parser.add_argument('--count',type=int,choices=[100,500],default=100)
    args=parser.parse_args()
    if args.verify_report:verify_report()
    elif args.report:report()
    else:run(args.channel,args.performance,args.count)
