"""从真实回执生成独立离线报告，输出不含本机绝对路径。"""
import base64,html,importlib.util,json,pathlib,statistics
ROOT=pathlib.Path(__file__).resolve().parents[2];OUT=ROOT/'local-data/网页资源库/游戏版第一版';REPORT=ROOT/'reports/资源优化/游戏版整库优化验证.html'
def read(p):return json.loads(p.read_text('utf8'))
def mb(n):return f'{n/1e6:.2f}'
def run():
 import sys
 sys.path.insert(0,str(ROOT/'scripts/资源库'))
 from 兼容维护 import legacy_entry
 legacy_entry('report')
 spec=importlib.util.spec_from_file_location('game_report_service',ROOT/'scripts/资源优化/游戏版服务.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);catalog,_=m.catalog();names={r['base_sha256']:r['name'].split(' · ')[0] for r in catalog['models']};records=[read(p) for p in sorted((OUT/'验证').glob('*.json'))];assert len(records)==227 and all(r['passed'] for r in records)
 geometry=read(OUT/'完整几何核验.json');assert geometry['passed'] and geometry['count']==227
 browser=[read(OUT/'浏览器验证'/b/'汇总.json') for b in ['chrome','msedge']];assert all(r['count']==1354 and r['technical_passed']==1354 and r['visual_passed']==1354 and not r['errors'] for r in browser)
 visuals=[read(OUT/'靶场验证'/b/'画面验证.json') for b in ['chrome','msedge']];assert all(len(r['visual'])==336 and not r['errors'] for r in visuals);assert all(x.get('motion_max_error',0)==0 for r in visuals for x in r['visual'])
 loads=[read(OUT/'靶场验证'/b/'加载验证.json') for b in ['chrome','msedge']];assert all(len(r['network'])==18 and not r['errors'] for r in loads)
 load=read(OUT/'有声负载验证.json');assert len(load['samples'])==4
 rows=[];totals={'source':0,'previous':0,'256':0,'512':0,'all_derivatives':0}
 for r in records:
  old=read(ROOT/'local-data/网页资源库/全库第一版/验证'/(r['source_sha256']+'.model.json'));a=next(v for v in r['variants'] if v['resolution']==256 and v['level']==(0 if v['animated'] else 1));b=next(v for v in r['variants'] if v['resolution']==512 and v['level']==(0 if v['animated'] else 1));totals['source']+=r['source_bytes'];totals['previous']+=old['bytes'];totals['256']+=a['bytes'];totals['512']+=b['bytes'];totals['all_derivatives']+=sum(v['bytes'] for v in r['variants']);rows.append({'name':names[r['source_sha256']],'id':r['source_sha256'][:12],'source_mb':mb(r['source_bytes']),'previous_mb':mb(old['bytes']),'game_mb':mb(a['bytes']),'fallback_mb':mb(b['bytes']),'triangles':a['triangles'],'source_triangles':a['before_triangles'],'animated':a['animated']})
 network=[]
 for r in loads:
  for variant in ['baseline','256-lod1','512-lod1']:
   for speed in [10,50]:
    group=[x for x in r['network'] if x['variant']==variant and x['mbps']==speed];network.append({'browser':r['browser'],'variant':variant,'mbps':speed,'seconds':round(statistics.median(x['ready_seconds'] for x in group),2),'model_mb':mb(group[0]['model_bytes']),'transfer_mb':mb(statistics.median(x['transfer_bytes'] for x in group))})
 summary={'counts':{'models':227,'variants':1354,'browsers':2,'views':672,'network_samples':36},'totals':totals,'network':network,'assets':rows,'performance':[{'browser':x['browser'],'variant':x['variant'],'p95':x['sample']['p95'],'p99':x['sample']['p99'],'max':x['sample']['max'],'triangles':x['sample'].get('triangles'),'errors':x['errors']} for x in load['samples']]}
 (OUT/'交付摘要.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
 esc=html.escape;table=''.join(f"<tr><td>{esc(r['name'])}<small>{r['id']}</small></td><td>{r['previous_mb']}</td><td>{r['game_mb']}</td><td>{r['fallback_mb']}</td><td>{r['source_triangles']:,} → {r['triangles']:,}</td><td>{'保留骨骼拓扑' if r['animated'] else '静态细节层1'}</td></tr>" for r in rows)
 nets=''.join(f"<tr><td>{r['browser']}</td><td>{r['variant']}</td><td>{r['mbps']} Mbps</td><td><b>{r['seconds']:.2f} 秒</b></td><td>{r['model_mb']} MB</td><td>{r['transfer_mb']} MB</td></tr>" for r in network)
 perf=''.join(f"<tr><td>{esc(x['browser'])}</td><td>{esc(x['variant'])}</td><td>{x['sample']['p95']:.2f}</td><td>{x['sample']['p99']:.2f}</td><td>{x['sample']['max']:.2f}</td><td>{len(x['errors'])}</td></tr>" for x in load['samples'])
 images=''
 for kind,label in [('tank','M1A2'),('heli','AH-64D'),('plane','A-10C')]:
  images+=f'<h3>{label} · 640宽 · 最近允许视野</h3><div class="comparisons">'
  for variant,title in [('baseline','原运行压缩版'),('256-lod1','256 游戏版'),('512-lod1','512 回退档')]:
   p=OUT/'靶场验证/chrome'/f'{variant}-{kind}-640-58-0-0.3.jpg';data=base64.b64encode(p.read_bytes()).decode();images+=f'<figure><img src="data:image/jpeg;base64,{data}" alt="{label} {title}"><figcaption>{title}</figcaption></figure>'
  images+='</div>'
 reduction=(1-totals['256']/totals['previous'])*100
 doc=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>游戏版整库优化验证</title><style>
 :root{{color-scheme:dark;--bg:#111b1b;--card:#1b2928;--ink:#ecf1e8;--muted:#a6b6ac;--accent:#d7e8a2}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.75 system-ui}}main{{max-width:1280px;margin:auto;padding:40px 24px}}header{{border-top:3px solid var(--accent);padding:24px 0}}h1{{font-size:clamp(28px,5vw,48px);margin:5px 0}}h2{{margin-top:42px}}p,small{{color:var(--muted)}}small{{display:block}}.tag{{color:var(--accent);letter-spacing:2px}}.cards,.comparisons{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}}.card{{background:var(--card);padding:22px;border:1px solid #35443c}}.card strong{{display:block;font-size:32px;color:var(--accent)}}.note{{padding:18px;border-left:3px solid var(--accent);background:var(--card)}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;white-space:nowrap}}th,td{{padding:12px;text-align:left;border-bottom:1px solid #35443c}}th{{color:var(--accent)}}figure{{margin:0}}img{{width:100%;image-rendering:pixelated}}figcaption{{padding:8px;background:var(--card)}}input{{width:100%;padding:14px;background:var(--card);color:white;border:1px solid #65745e;margin:12px 0}}button{{padding:8px 14px;background:var(--card);color:var(--accent);border:1px solid #65745e;cursor:pointer}}footer{{margin-top:48px;border-top:1px solid #35443c;padding-top:18px}}@media(max-width:700px){{.cards,.comparisons{{grid-template-columns:1fr}}main{{padding:20px 14px}}}}@media print{{:root{{color-scheme:light;--bg:white;--card:#f4f6f0;--ink:#111;--muted:#333;--accent:#344522}}input,button{{display:none}}.scroll{{overflow:visible}}table{{white-space:normal;font-size:10px}}}}
 </style><main><header><div class="tag">资源工程 / 实际战术视角验证</div><h1>游戏版整库优化验证</h1><p>256主体贴图 · 简化材质 · 静态几何细节层 · 512回退档</p></header>
 <div class="cards"><div class="card"><strong>227 / 1,354</strong>唯一模型 / 两档全部几何版本</div><div class="card"><strong>{reduction:.1f}%</strong>游戏默认层相对上一轮模型总字节减少</div><div class="card"><strong>2 × 1,354</strong>Chrome与Edge逐版本加载和画面阈值通过</div></div>
 <h2>运行资源预算</h2><p>单位统一为十进制MB。默认静态模型使用细节层1，骨骼和动作模型保留完整拓扑。</p><div class="scroll"><table><tr><th>集合</th><th>总量</th><th>用途</th></tr><tr><td>原始唯一GLB</td><td>{mb(totals['source'])} MB</td><td>只读保留</td></tr><tr><td>上一轮保真压缩层</td><td>{mb(totals['previous'])} MB</td><td>质量对照与保底回退</td></tr><tr><td>256游戏默认层</td><td>{mb(totals['256'])} MB</td><td>按需加载，不整库预载</td></tr><tr><td>512回退默认层</td><td>{mb(totals['512'])} MB</td><td>需要时选择</td></tr><tr><td>本轮全部派生文件</td><td>{mb(totals['all_derivatives'])} MB</td><td>包含两档及各几何层；不代表首次下载量</td></tr></table></div>
 <h2>实际靶场画面</h2><p>共672张双浏览器对照：三类载具、640/480宽、视野58/96、两档朝向、两档动作相位；源码、光照和相机规则一致。截图保留真实像素，不以放大模型库细节替代游戏视角验收。</p>{images}
 <h2>首次可操作时间</h2><p>每组3次取中位数；新浏览器上下文禁用HTTP缓存，CDP模拟10/50 Mbps及40毫秒延迟。本地服务无生产CDN和HTTP压缩，不是公网测速。计时覆盖页面、引擎、解码器和三款模型，声音在用户主动开启后加载，因此不计入此表；同浏览器进程的编译缓存和系统文件缓存未作冷启动承诺。</p><div class="scroll"><table><thead><tr><th>浏览器</th><th>版本</th><th>限速</th><th>首次可操作</th><th>三款模型</th><th>资源传输统计</th></tr></thead><tbody>{nets}</tbody></table></div>
 <h2>100单位有声交战</h2><p>当前开发机高性能显卡，预热10秒后采样60秒，原运行版和游戏版串行；同机可能存在其他进程负载。音量100%及6dB增强保持不变。此表不代表普通电脑或500单位验收。</p><div class="scroll"><table><tr><th>浏览器</th><th>资源</th><th>P95 ms</th><th>P99 ms</th><th>最长帧 ms</th><th>页面错误</th></tr>{perf}</table></div>
 <h2>整库资源清单</h2><p>完整几何两档逐属性、索引循环排列、变形属性回读一致；所有版本节点、骨骼和动作保留。自动画面阈值为两档距离轮廓IoU≥0.94、前景MAE≤30/255，属于筛查指标，待工程师主观验收。原始待修状态不升级。</p><input id="search" placeholder="搜索载具名称或摘要" aria-label="搜索模型"><span id="count">227 项</span><div class="scroll"><table id="assets"><thead><tr><th>模型</th><th>上一轮 MB</th><th>256 MB</th><th>512 MB</th><th>三角形</th><th>处理类型</th></tr></thead><tbody>{table}</tbody></table></div>
 <h2>处理规则与回退</h2><ul><li>底色保留原颜色与透明通道，最长边256或512；遮蔽贴图最长边128，继续保留Mip链。</li><li>去掉细微法线；金属度和粗糙度由原贴图通道均值乘原系数，保持现有受光材质。未新增AO烘焙，未合并可动机械节点。</li><li>保留Float32属性，避免影响自定义机械读取；静态细节层沿用上一轮保守减面比例。骨骼和动作模型不套用静态减面。</li><li>运行接入逐条检查两浏览器回执与文件摘要，未通过的项保留旧派生；关闭运行配置的game_enabled即可回退。</li><li>原始GLB、Blender工程、声音和旧派生全部保留。本轮没有删除文件；既有删除候选仍需工程师另行确认。</li></ul>
 <div class="note">独立游戏预览固定本轮已验证的渐变接触阴影源码。另一个会话正在进行的实时阴影试验未纳入本轮性能结论，工作区源码仍保留对方改动。公开部署仍需单独处理按需加载、内容哈希缓存、服务器传输压缩和资源使用范围。当前预览仅在本机回环提供；整库体积不应作为一次下载包。512回退保留更高底色精度，材质仍为游戏简化方案；完整材质回退使用上一轮保真压缩层。</div>
 <footer>核验依据：完整几何核验、逐项浏览器回执、靶场画面对照、36次限速冷加载、四组100单位有声交战。<button onclick="window.print()">打印报告</button></footer></main><script>document.querySelector('#search').addEventListener('input',e=>{{let n=0;for(const row of document.querySelectorAll('#assets tbody tr')){{row.hidden=!row.textContent.toLowerCase().includes(e.target.value.toLowerCase());if(!row.hidden)n++;}}document.querySelector('#count').textContent=n+' 项';}});</script></html>'''
 REPORT.parent.mkdir(parents=True,exist_ok=True);REPORT.write_text(doc,encoding='utf8');print(REPORT.relative_to(ROOT),len(doc))
if __name__=='__main__':run()
