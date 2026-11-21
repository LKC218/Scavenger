"""对照冻结的改前场景，验证阴影视觉、资源释放与同条件有声性能。"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'local-data/原型/载具靶场/验证/阴影优化1'
PICTURES = ROOT/'reports/载具靶场/阴影对照'


def sha(data): return hashlib.sha256(data).hexdigest()


def isolate(channel, port=None):
    """同上下文冻结场景交错切换阴影，区分整段GPU抖动与阴影本身的成本。"""
    if port is None: port=json.loads((OUT/'服务身份.json').read_text('utf-8-sig'))['port']
    identity=json.loads((OUT/(channel+'-视觉.json')).read_text('utf8'))['source_identity']
    for item in identity: assert sha((ROOT/item['path']).read_bytes())==item['sha256']
    result={'channel':channel,'kind':'冻结场景渲染隔离','source_identity':identity,'headless':True,'viewport':[1600,1000],'cases':[],'errors':[]}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel=channel,headless=True)
        result['version']=browser.version
        try:
            for count in [100,500]:
                context=browser.new_context(viewport={'width':1600,'height':1000})
                page=context.new_page();page.on('pageerror',lambda e:result['errors'].append(str(e)))
                page.on('console',lambda m:result['errors'].append(m.text) if m.type=='error' else None)
                page.goto(f'http://127.0.0.1:{port}/',wait_until='networkidle');page.wait_for_function('()=>window.range?.ready')
                page.locator('#unit-count').select_option(str(count));page.locator('#load-mode').select_option('combat')
                page.locator('#overview').click();page.wait_for_function('()=>range.view.span===range.sim.map.maxSpan&&!range.view.centerTarget&&range.sim.time>=10')
                row=page.evaluate('''async()=>{
                  const THREE=await import('three'),v=range.view,s=range.sim,gl=v.renderer.getContext(),ext=gl.getExtension('EXT_disjoint_timer_query_webgl2');
                  if(!ext)return{available:false};s.paused=true;const draw=v.render.bind(v);v.render=()=>{};v.sync=()=>{};
                  const snapshots=[...new Set([...v.units.values()].map(x=>x.shadows))].map(b=>({b,g:b.geometry,m:b.material,array:b.instanceMatrix.array.slice()}));
                  const oldGeo=new THREE.CircleGeometry(1,24),oldMat=new THREE.MeshBasicMaterial({color:0x111b10,side:THREE.DoubleSide,transparent:true,opacity:.25,depthWrite:false}),d=new THREE.Object3D();
                  const hemi=v.scene.children.find(o=>o.isHemisphereLight),sun=v.scene.children.find(o=>o.isDirectionalLight),rows=[];
                  const apply=mode=>{for(const p of snapshots){p.b.geometry=mode==='before'?oldGeo:p.g;p.b.material=mode==='before'?oldMat:p.m;if(mode==='after')p.b.instanceMatrix.array.set(p.array);}
                    if(mode==='before')for(const u of s.units){d.position.set(u.x,.045,u.z);d.rotation.set(-Math.PI/2,0,0);d.scale.set(u.kind==='tank'?4.2:7,u.kind==='tank'?6.3:4,1);d.updateMatrix();const a=v.units.get(u.id);a.shadows.setMatrixAt(a.index,d.matrix);}
                    for(const p of snapshots)p.b.instanceMatrix.needsUpdate=true;
                    hemi.intensity=mode==='before'?2.4:2.15;hemi.groundColor.setHex(mode==='before'?0x65754a:0x74816c);sun.intensity=mode==='before'?3.2:3.4;
                  };
                  async function timed(){const q=gl.createQuery();gl.beginQuery(ext.TIME_ELAPSED_EXT,q);for(let i=0;i<4;i++)draw();gl.endQuery(ext.TIME_ELAPSED_EXT);gl.flush();const deadline=performance.now()+5000;
                    while(!gl.getQueryParameter(q,gl.QUERY_RESULT_AVAILABLE)){if(performance.now()>deadline)throw Error('GPU隔离查询超时');await new Promise(requestAnimationFrame);}
                    const bad=gl.getParameter(ext.GPU_DISJOINT_EXT),ms=gl.getQueryParameter(q,gl.QUERY_RESULT)/1e6/4;gl.deleteQuery(q);if(bad)throw Error('GPU计时不连续');return ms;
                  }
                  // 两侧均预热后执行ABBA，前后各32份独立查询；每份四次渲染取平均。
                  for(const mode of ['before','after']){apply(mode);for(let i=0;i<10;i++)draw();}
                  for(let i=0;i<16;i++)for(const mode of ['before','after','after','before']){apply(mode);draw();rows.push({mode,ms:await timed()});}
                  apply('after');oldGeo.dispose();oldMat.dispose();const summary=mode=>{const a=rows.filter(r=>r.mode===mode).map(r=>r.ms).sort((x,y)=>x-y);return{samples:a.length,p50:a[Math.floor(a.length*.5)],p95:a[Math.floor(a.length*.95)]};};
                  return{available:true,units:s.units.length,internal:[v.renderer.domElement.width,v.renderer.domElement.height],before:summary('before'),after:summary('after'),rows};
                }''')
                result['cases'].append(row);context.close()
                print(channel+'：同场景GPU隔离 '+str(count)+'单位 '+json.dumps({k:row.get(k) for k in ['before','after']},ensure_ascii=False),flush=True)
            assert not result['errors'];result['passed']=True
        finally:
            (OUT/(channel+'-渲染隔离.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');browser.close()


def report():
    """仅从通过且匹配当前源码的回执整理报告，不把异常复测覆盖为首测。"""
    visual=[]; cases=[]; isolated=[]; isolation_rows=[]
    for browser in ['chrome','msedge']:
        for suffix in ['视觉','性能']:
            p=OUT/(browser+'-'+suffix+'.json');r=json.loads(p.read_text('utf8'))
            assert r.get('passed'), str(p)
            for item in r['source_identity']: assert sha((ROOT/item['path']).read_bytes()) == item['sha256'], '源码已变化'
            if suffix=='视觉': visual.append(r)
            else:
                for item in r['cases']: cases.append({'browser':browser,'round':'首测',**item})
        repeat=OUT/(browser+'-性能复测.json')
        if repeat.exists():
            r=json.loads(repeat.read_text('utf8'));assert r.get('passed')
            for item in r['source_identity']: assert sha((ROOT/item['path']).read_bytes()) == item['sha256']
            for item in r['cases']: cases.append({'browser':browser,'round':'反序复测',**item})
        proof=json.loads((OUT/(browser+'-渲染隔离.json')).read_text('utf8'));assert proof.get('passed')
        for item in proof['source_identity']: assert sha((ROOT/item['path']).read_bytes())==item['sha256']
        for item in proof['cases']:
            assert item['available']
            isolated.append({'browser':browser,**item})
            a,b=item['before'],item['after']
            isolation_rows.append(f'| {browser} | {item["units"]} | {a["p50"]:.3f} | {b["p50"]:.3f} | {a["p95"]:.3f} | {b["p95"]:.3f} |')
    rows=[]; exported=[]
    for c in cases:
        s=c['sample'];t=c['render_timing'];g=t['gpu']['p95'];cpu=t['cpuSubmission']['p95']
        gpu_text=f'{g:.3f}' if g is not None else '不可用'
        rows.append(f'| {c["browser"]} | {c["count"]} | {c["round"]} | {"改前" if c["mode"]=="before" else "改后"} | {s["p95"]:.2f} | {s["p99"]:.2f} | {s["max"]:.2f} | {gpu_text} | {cpu:.2f} | {s["over33ms"]} |')
        exported.append({'browser':c['browser'],'round':c['round'],'units':c['count'],'mode':c['mode'],'duration':s['duration'],
                         'p95':s['p95'],'p99':s['p99'],'max':s['max'],'over33ms':s['over33ms'],'render_timing':t,
                         'source_sha256':next(x['sha256'] for x in visual[0]['source_identity'] if x['path'].endswith('/场景.mjs'))})
    before,after=[c['structure']['perf'] for c in visual[0]['cases']]
    folder=ROOT/'reports/载具靶场'
    (folder/'阴影优化验证摘要.json').write_text(json.dumps({'schema':1,'viewport':[1600,1000],'internal':[640,395],
        'audio':{'volume':1,'enhancementDb':6,'voices':48,'engines':8},'cases':exported,'render_isolation':isolated},ensure_ascii=False,indent=2),encoding='utf8')
    text='''# 载具阴影优化验证

## 范围与实现

固定白天，保留像素风，改善车底压地感与轻微方向感；首轮允许建筑遮光简化。每单位仍为一份假阴影、每型号一个实例批；顶点透明度提供中央深、边缘淡的轮廓，朝向跟随车身。固定太阳产生高度投影，空中载具随各自高度淡化。主要模型材质已有AO与法线信息，本轮不重复烘焙，只校准原有两盏灯。

单份阴影由26顶点改为17顶点，保持24三角形，改用单面透明材质。新增每实例4字节透明度，仅高度改变时上传；按型号独立几何使资源对象数量略增，连续重置会释放旧几何。没有增加贴图、实时阴影贴图或后期通道。

## 视觉对照

同样的30单位静态布局、跨度120与镜头角度；数字截图不替代工程师观感验收。

### 改前

![改前实心椭圆阴影](阴影对照/chrome-before-局部.png)

### 改后

![改后车底渐变与短方向影](阴影对照/chrome-after-局部.png)

### 空中高度与道路

![空地载具分离展示](阴影对照/chrome-after-空地.png)

![道路上的坦克接触阴影](阴影对照/chrome-after-道路.png)

空地图使用测试姿态便于观察不同车型，实际巡航高度沿用游戏配置。阴影平面0.065米，高于道路0.045米。选择、旋转、缩放、真实右键移动后阴影跟随与连续四次重置均通过两浏览器检查；0、90度及斜向的车身轴对照通过。

## 性能

本机RX 7900 XTX，独立无窗口Chrome／Edge，视口1600×1000，三维640×395，全场总览。总音量100%、增强6dB，有声交战；每组预热10秒、正式60秒，浏览器测试串行。改前只拦截冻结的场景源码，其他源码及同一份运行资源清单一致；本阶段使用当前压缩运行资源，不能直接与旧资产阶段容量比较。

GPU耗时来自EXT_disjoint_timer_query_webgl2，每8帧异步抽样；CPU列仅包裹渲染提交，不代表模拟或页面全部CPU。显示帧时间可能受约144Hz刷新节奏限制。

| 浏览器 | 单位 | 轮次 | 版本 | 帧P95毫秒 | 帧P99毫秒 | 最长帧毫秒 | GPU P95毫秒 | 提交CPU P95毫秒 | 超33.34毫秒帧数 |
| --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
'''+ '\n'.join(rows)+f'''

同一固定静态画面：绘制{before['drawCalls']}→{after['drawCalls']}，提交三角形{before['triangles']:,}→{after['triangles']:,}，纹理{before['textures']}→{after['textures']}，几何资源对象{before['geometries']}→{after['geometries']}。交战中靶标恢复和弹丸使瞬时绘制数变化，性能表不拿单个时刻的绘制数冒充完整段平均值。

整段GPU分位在浏览器与轮次间存在明显波动，首测Chrome500单位和Edge部分样本的改后值较高，因此保留首测并追加反序复测及同上下文隔离。判断应同时查看各轮、完整帧时间与GPU结果，不以相同FPS宣称GPU成本为零。摘要见[验证数据](阴影优化验证摘要.json)。500单位仍为探索，不承诺长期最大容量或其他设备性能。

### 同一冻结场景的GPU隔离

在同一浏览器、同一WebGL上下文和同一冻结交战姿态下，仅交错替换三批阴影的几何、材质、矩阵及原有两盏灯参数。先预热两侧，再执行16轮改前／改后／改后／改前，每侧32份查询，每份包含4次渲染，表内是平均单次渲染毫秒。此项用于定位渲染成本，不代表实际交战帧率，计时波动仍需保留。

| 浏览器 | 单位 | 改前中位毫秒 | 改后中位毫秒 | 改前P95毫秒 | 改后P95毫秒 |
| --- | ---: | ---: | ---: | ---: | ---: |
'''+'\n'.join(isolation_rows)+'''

100单位主验与500单位探索的正式样本均达到当前本机60FPS帧预算。阴影绘制与提交量下降，同场景典型GPU时间作为辅助对照；不将尾部分位波动解释成已经证明所有时刻零成本。

## 服务与证据

验证中发现旧服务读取共享导出清单，其他端口覆盖或清理该文件会令媒体路由与清单不一致。现改为每服务内存冻结清单。独立HTTP故障注入将清单文件映射到不存在的路径，GET／HEAD仍200、长度一致，三型模型HEAD均200；没有删除实际共享文件。当前验证使用系统分配端口，未终止旧8768服务；Python修复对新进程生效。

原始回执、冻结源码、运行清单摘要、故障注入与排障记录在忽略的`local-data/原型/载具靶场/验证/阴影优化1/`。错误加载和中止样本不计入上表。模型与声音原件未改写，正式研究资料不作为运行依赖。

## 限制与复用

当前是假阴影：平地接收、车型近似轮廓，未实现建筑挡光、墙面／屋顶接收、斜坡贴合或真实自阴影。坦克炮管与飞机侧倾不参与真实轮廓投射。视觉最终由工程师试玩确认。

- [靶场模块](../../src/载具靶场/README.md)
- [实施计划](../../docs/靶场阴影优化-实施计划.md)
- [Three.js官方阴影说明](https://threejs.org/manual/pages/shadows.html)
'''
    (folder/'阴影优化验证.md').write_text(text,encoding='utf8')
    print(f'阴影报告：{len(cases)}组性能样本，两浏览器视觉通过',flush=True)


def run(channel, performance=False, port=None, repeat=False):
    OUT.mkdir(parents=True, exist_ok=True)
    PICTURES.mkdir(parents=True, exist_ok=True)
    original = (OUT/'改前源码/场景.mjs').read_bytes()
    baseline = json.loads((OUT/'改前源码/身份.json').read_text('utf8'))
    assert sha(original) == next(x['sha256'] for x in baseline if x['path'].endswith('/场景.mjs'))
    current = [{'path': p.relative_to(ROOT).as_posix(), 'sha256': sha(p.read_bytes())}
               for p in sorted((ROOT/'src/载具靶场').iterdir()) if p.suffix in ('.mjs', '.html', '.css')]
    assert [x['path'] for x, y in zip(baseline, current) if x != y] == ['src/载具靶场/场景.mjs']
    if port is None: port=json.loads((OUT/'服务身份.json').read_text('utf-8-sig'))['port']
    url = f'http://127.0.0.1:{port}/'
    result = {'channel': channel, 'port': port, 'headless': True, 'viewport': [1600, 1000], 'source_identity': current,
              'baseline_identity': baseline, 'checks': [], 'errors': [], 'cases': []}
    target = OUT/(channel+('-性能复测.json' if repeat else '-性能.json' if performance else '-视觉.json'))
    def save(): target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    def passed(name): result['checks'].append(name); save(); print(channel+'：'+name, flush=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=channel, headless=True)
        result['version'] = browser.version
        try:
            # 顺序交错改前／改后；每次独立上下文，避免旧程序或纹理缓存串用。
            for count in ([500] if repeat else [100, 500] if performance else [30]):
                for mode in (['after', 'before'] if repeat else ['before', 'after']):
                    context = browser.new_context(viewport={'width': 1600, 'height': 1000}, device_scale_factor=1)
                    page = context.new_page()
                    if mode == 'before':
                        page.route(re.compile(r'(?:场景|%E5%9C%BA%E6%99%AF)\.mjs(?:\?.*)?$', re.I),
                                   lambda route: route.fulfill(status=200, content_type='text/javascript', body=original))
                    page.on('pageerror', lambda e: result['errors'].append(str(e)))
                    page.on('console', lambda m: result['errors'].append(m.text) if m.type == 'error' else None)
                    page.on('response', lambda r: result['errors'].append(str(r.status)+' '+r.url) if r.status >= 400 else None)
                    def wait(expression, timeout=20000): page.wait_for_function('() => '+expression, timeout=timeout)
                    def value(expression): return page.evaluate('() => '+expression)
                    page.goto(url, wait_until='networkidle')
                    assert page.locator('#canvas-host').count() == 1, '该端口不是载具靶场'
                    wait('window.range?.ready', 50000)
                    manifest = page.request.get(url+'manifest.json').body()
                    assert sha(manifest) == sha((OUT/'改前源码/运行清单.json').read_bytes()), '运行资源版本变化'
                    loader = page.request.get(url+'compressed-loader.mjs').body()
                    fingerprint = {'manifest': sha(manifest), 'loader': sha(loader)}
                    if 'runtime_identity' not in result: result['runtime_identity'] = fingerprint
                    assert result['runtime_identity'] == fingerprint
                    row = {'mode': mode, 'count': count}
                    result['cases'].append(row)
                    row['gpu_extension'] = value('!!range.view.renderer.getContext().getExtension("EXT_disjoint_timer_query_webgl2")')
                    if performance:
                        print(f'{channel}：开始 {mode} {count}单位，预热10秒＋正式60秒', flush=True)
                        page.locator('#unit-count').select_option(str(count))
                        page.locator('#load-mode').select_option('combat')
                        page.locator('#sound').click()
                        wait('range.mixer.active && range.mixer.engines.size===8')
                        page.locator('#overview').click()
                        wait('range.view.span===range.sim.map.maxSpan && !range.view.centerTarget')
                        # 仅验证页包裹渲染调用；可用时异步回收GPU查询，不用gl.finish阻塞。
                        page.evaluate('''() => {
                          const v=range.view,gl=v.renderer.getContext(),ext=gl.getExtension('EXT_disjoint_timer_query_webgl2'),render=v.render.bind(v);
                          window.shadowTiming={cpu:[],gpu:[],pending:[],dropped:0,disjoint:0,frames:0};
                          v.render=()=>{const s=shadowTiming,active=range.benchmark.run,r=active&&performance.now()-active.start>=active.warmup*1000;
                            if(ext&&s.pending.length){const p=s.pending[0];if(gl.getQueryParameter(p,gl.QUERY_RESULT_AVAILABLE)){s.pending.shift();const disjoint=gl.getParameter(ext.GPU_DISJOINT_EXT);if(disjoint)s.disjoint++;else s.gpu.push(gl.getQueryParameter(p,gl.QUERY_RESULT)/1e6);gl.deleteQuery(p);}}
                            let q=null;if(r&&ext&&s.frames++%8===0){if(s.pending.length<8){q=gl.createQuery();gl.beginQuery(ext.TIME_ELAPSED_EXT,q);}else s.dropped++;}
                            const start=performance.now();render();if(q){gl.endQuery(ext.TIME_ELAPSED_EXT);s.pending.push(q);}if(r)s.cpu.push(performance.now()-start);
                          };
                        }''')
                        page.locator('#bench60').click(); wait('!!range.benchmark.run')
                        wait('!range.benchmark.run', 95000)
                        row['sample'] = value('range.benchmark.last')
                        row['end'] = value('range.perf()')
                        row['render_timing'] = page.evaluate('''() => {
                          const s=shadowTiming,summary=a=>{const b=a.slice().sort((x,y)=>x-y);return{samples:b.length,p50:b[Math.floor(b.length*.5)]??null,p95:b[Math.floor(b.length*.95)]??null,p99:b[Math.floor(b.length*.99)]??null};};
                          return{cpuSubmission:summary(s.cpu),gpu:summary(s.gpu),disjoint:s.disjoint,dropped:s.dropped};
                        }''')
                        sample = row['sample']
                        assert sample['totalUnits'] == count and sample['audio'] and sample['volume'] == 1 and sample['enhancementDb'] == 6
                        assert sample['duration'] >= 60 and sample['frames'] > 1000
                        assert sample['timeline'][-1]['shots'] > sample['timeline'][0]['shots']+20
                        assert all(p['voices'] <= 48 and p['engines'] <= 8 for p in sample['timeline'])
                        passed(f'{mode} {count}单位有声60秒：P95 {sample["p95"]:.2f}ms，绘制 {sample["drawCalls"]}')
                    else:
                        page.locator('#load-mode').select_option('static')
                        page.locator('#pause').click(); wait('range.sim.paused')
                        # 固定图像对照姿态；输入验收另用真实键鼠，不以这些测试夹具冒充输入。
                        row['structure'] = page.evaluate('''() => {
                          const v=range.view,tanks=range.sim.units.filter(u=>u.kind==='tank');v.home(true);range.commander.selected.clear();
                          v.center.set(tanks.reduce((n,u)=>n+u.x,0)/tanks.length,0,tanks.reduce((n,u)=>n+u.z,0)/tanks.length);v.span=v.targetSpan=120;v.angle=v.targetAngle=.45;v.updateCamera();v.sync(range.sim,new Set(),null,0);v.render();
                          return{perf:range.perf(),shadowEnabled:v.renderer.shadowMap.enabled,batches:[...new Set([...v.units.values()].map(x=>x.shadows))].map(b=>({instances:b.count,vertices:b.geometry.getAttribute('position').count,triangles:b.geometry.index.count/3,colorSize:b.geometry.getAttribute('color')?.itemSize??0,side:b.material.side})),
                            materials:Object.fromEntries(Object.entries(v.rigs).map(([k,r])=>[k,r.parts.map(p=>({type:p.material.type,map:!!p.material.map,ao:!!p.material.aoMap,normal:!!p.material.normalMap}))]))};
                        }''')
                        page.add_style_tag(content='#stats,#toast{visibility:hidden!important}')
                        page.locator('#canvas-host').screenshot(path=str(PICTURES/(channel+'-'+mode+'-局部.png')))
                        if mode == 'after':
                            assert not row['structure']['shadowEnabled']
                            assert all(b['triangles'] == 24 and b['vertices'] == 17 and b['side'] == 0 for b in row['structure']['batches'])
                            row['projection'] = page.evaluate('''async() => {
                              const THREE=await import('three'),v=range.view,s=range.sim,rows=[];
                              for(const kind of ['tank','heli','plane']){
                                const u=s.units.find(u=>u.kind===kind),old={x:u.x,y:u.y,z:u.z,yaw:u.yaw};
                                for(const height of kind==='tank'?[0]:[15,75])for(const yaw of [0,Math.PI/2,.7]){
                                  u.x=-270;u.z=-220;u.y=height;u.yaw=yaw;v.sync(s,new Set(),null,0);
                                  const b=v.units.get(u.id),m=new THREE.Matrix4();b.shadows.getMatrixAt(b.index,m);const a=m.elements;
                                  rows.push({kind,height,yaw,offset:[a[12]-u.x,a[14]-u.z],ground:a[13],axis:[a[0],a[2]],opacity:b.shadows.geometry.getAttribute('shadowOpacity').getX(b.index)});
                                }Object.assign(u,old);
                              }v.sync(s,new Set(),null,0);return rows;
                            }''')
                            for kind in ['tank', 'heli', 'plane']:
                                rows = [x for x in row['projection'] if x['kind'] == kind]
                                for r in rows:
                                    assert r['offset'][0] > 0 and r['offset'][1] < 0 and .06 < r['ground'] < .07
                                    length = math.hypot(*r['axis'])
                                    assert abs(r['axis'][0]/length-math.cos(r['yaw'])) < 1e-5
                                    assert abs(r['axis'][1]/length+math.sin(r['yaw'])) < 1e-5
                                assert abs(rows[0]['axis'][0]) > 1 and abs(rows[1]['axis'][0]) < 1e-5
                                assert rows[0]['offset'] == rows[1]['offset']
                                if kind != 'tank': assert rows[0]['opacity'] > rows[-1]['opacity']
                            # 单独排列三型，全部影子落在城外平地和道路上；检查旋转、透视层次。
                            page.evaluate('''() => {
                              const s=range.sim,v=range.view;let n=0;for(const u of s.units){if(['tank','heli','plane'].some(k=>s.units.find(x=>x.kind===k)===u)){u.x=-220+n*40;u.z=-390;u.y=u.kind==='tank'?0:u.kind==='heli'?15:35;u.yaw=.7;n++;}else{u.x=500;u.z=500;}}
                              v.center.set(-160,0,-390);v.span=v.targetSpan=190;v.updateCamera();v.sync(s,new Set(),null,0);v.render();
                            }''')
                            page.locator('#canvas-host').screenshot(path=str(PICTURES/(channel+'-after-空地.png')))
                            page.evaluate('''() => {
                              const s=range.sim,v=range.view,u=s.units.find(u=>u.kind==='tank');u.x=-90;u.z=-190;u.yaw=.7;
                              for(const other of s.units)if(other!==u){other.x=500;other.z=500;}
                              v.center.set(-90,0,-190);v.span=v.targetSpan=64;v.updateCamera();v.sync(s,new Set(),null,0);v.render();
                            }''')
                            page.locator('#canvas-host').screenshot(path=str(PICTURES/(channel+'-after-道路.png')))
                            page.locator('#reset').click(); page.locator('#load-mode').select_option('manual')
                            page.keyboard.press('1'); page.keyboard.press('c')
                            wait('range.commander.selected.size===24 && !range.view.centerTarget')
                            before_pose = value('({angle:range.view.angle,span:range.view.span})')
                            box = page.locator('#canvas-host').bounding_box()
                            x, y = box['x']+box['width']*.5, box['y']+box['height']*.6
                            page.mouse.move(x, y); page.mouse.down(button='middle'); page.mouse.move(x+80,y+10,steps=8); page.mouse.up(button='middle')
                            page.mouse.wheel(0, -150); wait('Math.abs(range.view.span-range.view.targetSpan)<.01')
                            assert abs(value('range.view.angle')-before_pose['angle']) > .1
                            assert value('range.view.span') < before_pose['span']
                            page.locator('#canvas-host').screenshot(path=str(PICTURES/(channel+'-after-旋转.png')))
                            page.locator('#reset').click()
                            unit = value('range.sim.units.find(u=>u.kind==="tank").id')
                            page.locator('[data-unit="'+unit+'"]').click(); page.keyboard.press('c')
                            wait('!range.view.centerTarget')
                            move = value('(()=>{const u=range.sim.units.find(u=>u.id==="'+unit+'"),p=range.view.project({x:u.x+18,y:0,z:u.z+12});return{id:u.id,x:u.x,z:u.z,screen:p};})()')
                            box = page.locator('#canvas-host').bounding_box()
                            page.mouse.click(box['x']+move['screen']['x'],box['y']+move['screen']['y'],button='right')
                            wait('Math.hypot(range.sim.units.find(u=>u.id==="'+unit+'").x-('+str(move['x'])+'),range.sim.units.find(u=>u.id==="'+unit+'").z-('+str(move['z'])+'))>4')
                            row['moving_shadow'] = page.evaluate('''async id=>{const THREE=await import('three'),u=range.sim.units.find(u=>u.id===id),v=range.view.units.get(id),m=new THREE.Matrix4();v.shadows.getMatrixAt(v.index,m);return{offset:[m.elements[12]-u.x,m.elements[14]-u.z],yaw:u.yaw,axis:[m.elements[0],m.elements[2]]};}''', unit)
                            assert abs(row['moving_shadow']['offset'][0]-70/110*1.65) < 1e-4
                            assert abs(row['moving_shadow']['offset'][1]+40/110*1.65) < 1e-4
                            row['reset_geometries'] = []
                            for _ in range(4):
                                page.locator('#reset').click()
                                page.wait_for_function('() => range.view.renderer.info.memory.geometries>0')
                                row['reset_geometries'].append(value('range.view.renderer.info.memory.geometries'))
                            assert len(set(row['reset_geometries'])) == 1, row['reset_geometries']
                            passed('三型朝向与高度、道路上方层次、真实选择／旋转／缩放，连续重置无几何增长')
                        else:
                            assert all(b['vertices'] == 26 and b['triangles'] == 24 for b in row['structure']['batches']), '未实际加载改前场景'
                        passed(mode+' 固定局部截图与材质登记')
                    save(); context.close()
            if not performance:
                a, b = [x['structure']['perf'] for x in result['cases']]
                assert b['textures'] == a['textures'] and b['triangles'] <= a['triangles'] and b['drawCalls'] < a['drawCalls']
                passed('相同固定场景纹理数不增、三角形与绘制调用减少')
            assert not result['errors'], result['errors']
            result['passed'] = True
        except Exception as error:
            result['failure'] = str(error); save(); raise
        finally:
            save(); browser.close()
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', choices=['chrome', 'msedge'], default='chrome')
    parser.add_argument('--performance', action='store_true')
    parser.add_argument('--repeat', action='store_true', help='500单位按改后／改前反序复测，单独保存异常复核证据')
    parser.add_argument('--report', action='store_true', help='根据匹配当前源码的通过回执生成Markdown对照和脱敏摘要')
    parser.add_argument('--isolate', action='store_true', help='同一冻结场景交错切换改前与改后阴影，定位GPU时序差异')
    parser.add_argument('--port', type=int, help='默认读取本轮本地服务身份；使用日常靶场可传8768')
    args = parser.parse_args()
    if args.isolate: isolate(args.browser,args.port)
    elif args.report: report()
    else: run(args.browser, args.performance or args.repeat, args.port, args.repeat)
