"""从真实回执生成离线报告、冻结删除候选和运行资产清单，不执行删除。"""
import base64,collections,csv,hashlib,html,io,json,os,pathlib,sys
from PIL import Image,ImageDraw,ImageFont
ROOT=pathlib.Path(__file__).resolve().parents[2];OUT=ROOT/'local-data/网页资源库/全库第一版';ARCHIVE=ROOT/'local-data/资源归档/全库第一版'
def read(p):return json.loads(pathlib.Path(p).read_text(encoding='utf8'))
def sha(p):
 h=hashlib.sha256()
 with pathlib.Path(p).open('rb') as f:
  for block in iter(lambda:f.read(8*1024*1024),b''):h.update(block)
 return h.hexdigest()
def save(p,data):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
def main():
 import sys
 sys.path.insert(0,str(ROOT/'scripts/资源库'))
 from 兼容维护 import legacy_entry
 legacy_entry('report')
 inventory=read(OUT/'盘点.json');files=inventory['files'];receipts=[read(p) for p in (OUT/'验证').glob('*.json') if p.name.endswith(('.model.json','.audio.json'))];models=[r for r in receipts if r['kind']=='model'];audio=[r for r in receipts if r['kind']=='audio'];archives=[read(p) for p in ARCHIVE.glob('*.验证.json')];archived=read(ARCHIVE/'路径映射.json');retained={r['path']:r['retained'] for r in archived['files']}
 zip_archives=archives
 solid_path=ARCHIVE/'固实归档验证.json';solid=read(solid_path) if solid_path.exists() else None
 use_solid=bool(solid and solid.get('passed') and solid['archive_bytes']<sum(r['archive_bytes'] for r in archives))
 if use_solid:
  archives=[solid];archived=read(ARCHIVE/'固实路径映射.json');retained={r['path']:r['retained'] for r in archived['files']};save(ARCHIVE/'当前归档.json',{'mapping':'固实路径映射.json','format':'7z','archive_sha256':solid['archive_sha256'],'verified_unique_files':solid['unique_files']})
 input_check=[]
 for r in files:
  p=ROOT/r['path'];input_check.append({'path':r['path'],'unchanged':p.is_file() and p.stat().st_size==r['bytes'] and sha(p)==r['sha256']})
 save(OUT/'原件保护核验.json',{'count':len(input_check),'unchanged':sum(r['unchanged'] for r in input_check),'files':input_check})
 # 正式、当前原型和丧尸工程均保护；只列其余同摘要重复件，不把唯一原始证据列为删除候选。
 active={str(pathlib.Path(r['path']).resolve()).casefold() for r in read(ROOT/'local-data/原型/载具靶场/私有路由.json').values()}
 protection_inputs=[]
 def protect_values(value,base):
  if isinstance(value,dict):
   for v in value.values():protect_values(v,base)
  elif isinstance(value,list):
   for v in value:protect_values(v,base)
  elif isinstance(value,str) and len(value)<2000 and '\n' not in value and pathlib.Path(value).suffix.lower() in {'.glb','.blend','.blend1','.wav','.ogg','.flac','.mp3'}:
   for parent in [ROOT,base]:
    try:active.add(str((parent/value).resolve()).casefold())
    except (ValueError,OSError):pass
 for folder,dirs,names in os.walk(ROOT/'local-data'):
  dirs[:]=[d for d in dirs if d not in ['tools','网页资源库','资源归档','node_modules','__pycache__']]
  base=pathlib.Path(folder)
  for name in names:
   if not name.endswith('.json'):continue
   if '路由' not in name and not (base.is_relative_to(ROOT/'local-data/原型') and '清单' in name):continue
   p=base/name
   try:protect_values(read(p),base);protection_inputs.append({'path':p.relative_to(ROOT).as_posix(),'sha256':sha(p)})
   except (ValueError,OSError) as error:raise RuntimeError('保护清单无法读取，停止生成删除候选：'+str(p)) from error
 grouped=collections.defaultdict(list)
 for r in files:grouped[r['sha256']].append(r)
 candidates=[];protected=[]
 for key,rows in grouped.items():
  keep=[r for r in rows if r.get('formal') or r['path'].startswith(('local-data/原型/','local-data/丧尸模型/')) or str((ROOT/r['path']).resolve()).casefold() in active]
  if not keep:keep=[rows[0]]
  for r in rows:
   cold=use_solid and r['path'].startswith('local-data/断箭/批次/') and str((ROOT/r['path']).resolve()).casefold() not in active
   if r in keep and not cold:protected.append(r['path']);continue
   keep_path=str(ROOT/retained[r['path']]['archive']) if cold and all(k['path'].startswith('local-data/断箭/批次/') for k in keep) else str(ROOT/keep[0]['path'])
   candidates.append({'path':str(ROOT/r['path']),'relative_path':r['path'],'bytes':r['bytes'],'sha256':key,'retained_path':keep_path,'archive':str(ROOT/retained[r['path']]['archive']),'archive_entry':retained[r['path']]['entry'],'category':'冷源归档' if cold else '同摘要重复件','reason':'已完整恢复核验的历史冷源；旧维护脚本使用原路径前需恢复' if cold else '同摘要重复件；原始路径可能被历史维护脚本引用，删除后可按路径映射恢复','status':'等待工程师确认，不执行删除'})
 for r in audio:
  if r.get('superseded_path'):
   p=ROOT/r['superseded_path'];candidates.append({'path':str(p),'relative_path':r['superseded_path'],'bytes':p.stat().st_size,'sha256':sha(p),'retained_path':str(ROOT/r['path']),'archive':'原始WAV已在冷归档','archive_entry':'按source_sha256查询','reason':'被无损FLAC替代的峰值越界Opus候选','status':'等待工程师确认，不执行删除'})
 if use_solid:
  for p in sorted(ARCHIVE.glob('原件-*.zip')):
   candidates.append({'path':str(p),'relative_path':p.relative_to(ROOT).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p),'retained_path':str(ROOT/solid['archive']),'retained_sha256':solid['archive_sha256'],'archive':str(ROOT/solid['archive']),'archive_entry':'全部1178份唯一原件，见固实路径映射','category':'已替代ZIP分包','reason':'固实归档已逐份恢复核对，可替代常规ZIP，保留审计回执','status':'等待工程师确认，不执行删除'})
  for r in solid['restored_checks']:
   kept=solid['entries'][r['sha256']]
   candidates.append({'path':str(ROOT/r['restored']),'relative_path':r['restored'],'bytes':r['bytes'],'sha256':r['sha256'],'retained_path':str(ROOT/solid['archive']),'retained_sha256':solid['archive_sha256'],'archive':str(ROOT/solid['archive']),'archive_entry':kept['entry'],'category':'归档恢复验证临时副本','reason':'完整解包核验产生的临时副本，已核对源摘要','status':'等待工程师确认，不执行删除'})
 manifest={'status':'等待确认','deleted_files':0,'files':candidates,'file_count':len(candidates),'bytes':sum(r['bytes'] for r in candidates),'protected_count':len(protected),'protected':protected,'protection_inputs':protection_inputs}
 save(OUT/'删除候选精确清单.json',manifest)
 with (OUT/'删除候选精确清单.csv').open('w',encoding='utf-8-sig',newline='') as f:
  writer=csv.DictWriter(f,fieldnames=list(dict.fromkeys(k for r in candidates for k in r)) if candidates else ['path']);writer.writeheader();writer.writerows(candidates)
 manifest_hash=sha(OUT/'删除候选精确清单.json')
 (OUT/'删除审批说明.md').write_text(f'# 删除候选待确认\n\n尚未执行任何删除。\n\n- 文件数：{len(candidates)}\n- 总字节：{manifest["bytes"]:,}\n- JSON清单SHA256：`{manifest_hash}`\n- 精确路径、摘要、保留文件和归档条目见同目录CSV及JSON。\n\n清单按类别区分已固实归档的历史冷源、同内容副本、被替代的ZIP和恢复验证临时副本。正式资源、当前原型和丧尸工程保留；历史冷源含唯一文件，原内容保存在已完整解包核验的固实归档中。历史维护脚本若读取被批准移除的旧路径，应先用恢复脚本恢复，不可直接把归档成功当作所有脚本迁移完成。归档和派生目前增加磁盘占用，审批字节不等于已释放空间。\n',encoding='utf8')
 browser=[]
 for name in ['chrome','msedge']:
  p=OUT/'浏览器验证'/name/'汇总.json'
  if p.exists():browser.append(read(p))
 lods=[read(p) for p in (OUT/'验证').glob('*.lod.json')]
 summary={'inventory_files':len(files),'inventory_bytes':inventory['bytes'],'unique_contents':inventory['unique_hashes'],'model_unique':len(models),'model_passed':sum(r['passed'] for r in models),'audio_unique':len(audio),'audio_passed':sum(r['passed'] for r in audio),'source_runtime_bytes':sum(r['source_bytes'] for r in receipts),'optimized_runtime_bytes':sum(r.get('bytes',0) for r in receipts if r['passed']),'archives':len(archives),'archive_bytes':sum(r['archive_bytes'] for r in archives),'archive_verified':all(r['passed'] for r in archives),'originals_unchanged':sum(r['unchanged'] for r in input_check),'deletion_candidate_count':len(candidates),'deletion_candidate_bytes':manifest['bytes'],'deletion_manifest_sha256':manifest_hash,'archive_format':'7z' if use_solid else 'zip','deletion_categories':{category:{'files':sum(r.get('category','音频旧候选')==category for r in candidates),'bytes':sum(r['bytes'] for r in candidates if r.get('category','音频旧候选')==category)} for category in sorted({r.get('category','音频旧候选') for r in candidates})},'deleted_files':0,'flac_fallbacks':sum(r.get('codec')=='flac' for r in audio),'lod_models':len(lods),'browser':[{'browser':r['browser'],'version':r['version'],'models':r['model_count'],'technical_passed':r['technical_passed'],'visual_threshold_passed':r['visual_threshold_passed'],'audio':len(r['audio']),'audio_passed':sum(a['passed'] for a in r['audio'])} for r in browser]}
 save(OUT/'汇总.json',summary)
 entries=[]
 for r in receipts:
  aliases=[a for a in files if a['sha256']==r['source_sha256']];ids=sorted({x['id'] for a in aliases for x in a.get('formal',[])})
  entries.append({'name':'、'.join(ids) or (pathlib.Path(aliases[0]['path']).parent.name if pathlib.Path(aliases[0]['path']).stem.lower() in ['model','模型'] else pathlib.Path(aliases[0]['path']).stem),'kind':r['kind'],'sha':r['source_sha256'],'before':r['source_bytes'],'after':r.get('bytes',0),'passed':r['passed'],'aliases':len(aliases),'quality':sorted({str(x.get('quality_status')) for a in aliases for x in a.get('formal',[])})})
  if r['kind']=='model':
   levels=[]
   for suffix in ['', '.lod1','.lod2']:
    p=OUT/'浏览器验证/chrome'/(r['source_sha256']+suffix+'.json')
    levels.append(str(int(read(p)['optimized']['triangles'])) if p.exists() else '—')
   entries[-1]['triangles']=' → '.join(levels)
 report=ROOT/'reports/资源优化/整库资源优化与归档报告.html';report.parent.mkdir(parents=True,exist_ok=True)
 payload=json.dumps({'summary':summary,'entries':entries},ensure_ascii=False).replace('</','<\\/')
 template='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>整库资源优化与归档报告</title><style>:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#0d171c;color:#e1ece8;font:16px/1.7 system-ui}main{max-width:1240px;margin:auto;padding:40px 26px}small,.muted{color:#98b1ac}h1{font-size:clamp(30px,5vw,54px);line-height:1.2;margin:12px 0 24px}h2{margin:38px 0 14px;font-size:23px}header{border-bottom:1px solid #3a504f;padding-bottom:24px}.tag{color:#b8e88b;letter-spacing:3px}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.card{padding:20px;background:#1a2b30;border:1px solid #314849;border-radius:12px}.number{font-size:30px;color:#b8e88b;font-variant-numeric:tabular-nums}.bar{height:20px;background:#30484b;margin:10px 0}.bar span{display:block;height:100%;background:#b8e88b}a{color:#afdbcf}input,select{padding:12px;background:#21343a;color:white;border:1px solid #526e6b;border-radius:6px;max-width:100%;margin:0 10px 12px 0}table{border-collapse:collapse;width:100%;font-size:14px}td,th{text-align:left;padding:10px;border-bottom:1px solid #304246}th{color:#b2c9c1}.scroll{overflow:auto}details{padding:14px;border:1px solid #304849;margin:12px 0}code{overflow-wrap:anywhere}li{margin:7px 0}.notice{border-left:4px solid #dfb67a;padding:12px 20px;background:#302b24}.footer{margin-top:36px;color:#98b1ac}@media(max-width:700px){.grid{grid-template-columns:1fr 1fr}main{padding:22px 14px}.number{font-size:24px}td,th{padding:8px}}@media print{body{background:white;color:#111}.card,details,.notice{background:white;border-color:#aaa}.number,a,th{color:#164638}input,select{display:none}}</style><main><header><div class="tag">资源工程 / 全量审计 / 项目内归档</div><h1>让资源库更轻，<br>让原件始终可追溯。</h1><p>桌面网页端 · Three.js · 模型、骨骼动画、音效全量覆盖</p><p class="muted">本报告由真实盘点和验证回执生成；运行资产压缩、原件冷归档与磁盘清理分别计数。</p></header><h2>01 / 全库覆盖</h2><div class="grid" id="cards"></div><h2>02 / 运行资源压缩</h2><div class="card"><div id="sizes"></div><div class="bar"><span id="ratio"></span></div><small>按不同源内容统计；Blender工程与历史副本不计入网页加载体积。贴图编码有损，几何基础层不减面。</small></div><h2>03 / 验证证据</h2><div class="scroll"><table><thead><tr><th>浏览器</th><th>模型加载与结构</th><th>视觉阈值</th><th>音频完整解码</th></tr></thead><tbody id="browsers"></tbody></table></div><p>图像比较条件：640×360，同镜头原版与优化版；轮廓交并比≥0.97、前景平均像素误差≤18。骨骼在5个时间点比较全部蒙皮顶点，误差阈值0.01模型单位。基础层另核对全部几何、动画采样器、蒙皮和变形数据；节点变换允许1e-5浮点写回差异，三角形索引允许保持绕序的循环排列。</p><div class="notice">程序阈值通过不等于人工逐件外观与听感签收。普通桌面硬件尚无明确最低配置，本机浏览器加载成功不能证明普通电脑100单位稳定60 FPS；压缩也不能解决所有模拟、绘制或动画CPU负载。</div><h2>04 / 处理规则与保留范围</h2><details open><summary>模型与骨骼动画</summary><p>基础层保留节点名称、层级、机械部件、浮点几何与骨骼动作；贴图最长边1024，颜色ETC1S，线性贴图UASTC，生成Mip链，几何使用Meshopt。静态模型从55%与25%目标细节层开始，带误差限制与边界锁定；轮廓不达标的18组自动改用75%与55%等更保守参数，实际面数可能达不到目标；细节层仅候选，不自动接入。骨骼和变形模型保持基础拓扑。</p><p>全部Blender工程纳入只读盘点、去重冷归档和原件摘要保护；它们是可编辑源文件，不冒充网页运行资源。已存在4项模型待修与3项静音声音待复核状态保留，历史未入库候选不自动升级正式资源。</p></details><details open><summary>声音与峰值</summary><p>默认Opus 96kbps，保留声道和时长，不改变混音增益、不裁掉尾音。发现编码后峰值越界的素材改用无损FLAC，保留瞬态和响度。全部素材完整解码并比较RMS及时间；解码成AudioBuffer后的内存仍由采样率、声道和时长决定。旧峰值异常候选保留在删除清单，尚未移除。</p></details><details open><summary>冷归档与删除审批</summary><p id="archive"></p><p>归档按摘要只存一份原件，路径映射覆盖全部源位置。先验证ZIP，再以固实7z压缩相似数据，完整解包1178份唯一文件并核对SHA256；旧ZIP和验证临时副本均待审批清理。恢复脚本可按原相对路径恢复至临时目录，显式选项可恢复原位置，拒绝覆盖不同内容。正式资源、当前原型和丧尸工程保护；启用固实归档后，历史冷源原文件可列入待确认清单，唯一内容仍由已完整恢复核验的归档保留。历史维护脚本仍可能需要原路径，清理后须按映射恢复。</p><p><a href="../../local-data/网页资源库/全库第一版/删除候选精确清单.csv">精确CSV</a>、<a href="../../local-data/网页资源库/全库第一版/删除审批说明.md">审批说明</a>及JSON位于项目内<code>local-data/网页资源库/全库第一版/</code>。<strong>本次删除0文件，磁盘释放0字节。</strong>归档保留在项目中，当前磁盘占用会增加，审批候选字节不能计为已释放。</p><p>冻结删除清单SHA256：<code id="manifest"></code></p></details><h2>05 / 每份资源的结果</h2><input id="query" placeholder="搜索型号、名称或摘要"><select id="type"><option value="all">全部类型</option><option value="model">模型</option><option value="audio">音效</option></select><span id="count"></span><div class="scroll"><table><thead><tr><th>资源</th><th>类型</th><th>源体积</th><th>派生体积</th><th>降低</th><th>状态</th></tr></thead><tbody id="rows"></tbody></table></div><h2>06 / 查阅入口</h2><p><a href="http://127.0.0.1:8770/">本机整库资源验证台</a> · <a href="../../docs/整库资源优化与归档-实施计划.md">实施计划</a> · <a href="../../scripts/资源优化/README.md">流水线与恢复说明</a></p><p>模型来源与许可沿用正式库；丧尸原件为Jungle Jim的Super Low-poly Stylized Zombies animated（CC BY 4.0），改动包括贴图编码和压缩派生，原件归档保留。</p><p>技术依据：<a href="https://threejs.org/docs/pages/GLTFLoader.html">Three.js GLTFLoader</a>、<a href="https://gltf-transform.dev/modules/functions/functions/simplify">glTF Transform simplify</a>、<a href="https://developer.mozilla.org/en-US/docs/Web/API/AudioBuffer">MDN AudioBuffer</a>。浏览器资源预算是本项目目标，不存在适用于全部网页游戏的统一文件大小标准。</p><div class="footer">离线报告：数据、样式与交互全部内嵌。外部来源和本机验证台需要相应网络或服务。</div></main><script>const DATA=__DATA__;const s=DATA.summary;const mb=n=>(n/1048576).toFixed(2)+' MiB';const gb=n=>(n/1e9).toFixed(2)+' GB';document.querySelector('#cards').innerHTML=[['源文件',s.inventory_files],['不同内容',s.unique_contents],['模型回读通过',s.model_passed+'/'+s.model_unique],['音效通过',s.audio_passed+'/'+s.audio_unique]].map(([k,v])=>`<div class="card"><small>${k}</small><div class="number">${v}</div></div>`).join('');document.querySelector('#sizes').textContent=gb(s.source_runtime_bytes)+' → '+gb(s.optimized_runtime_bytes)+'，减少 '+(100*(1-s.optimized_runtime_bytes/s.source_runtime_bytes)).toFixed(1)+'%';document.querySelector('#ratio').style.width=100*s.optimized_runtime_bytes/s.source_runtime_bytes+'%';document.querySelector('#browsers').innerHTML=s.browser.map(b=>`<tr><td>${b.browser}<br><small>${b.version}</small></td><td>${b.technical_passed}/${b.models}</td><td>${b.visual_threshold_passed}/${b.models}</td><td>${b.audio_passed}/${b.audio}</td></tr>`).join('');document.querySelector('#archive').textContent=`${s.archives}个归档分包，共${gb(s.archive_bytes)}；源文件保护核验${s.originals_unchanged}/${s.inventory_files}。删除候选${s.deletion_candidate_count}份，${s.deletion_candidate_bytes.toLocaleString()}字节，待工程师确认。`;document.querySelector('#manifest').textContent=s.deletion_manifest_sha256;const esc=x=>String(x).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));function render(){const q=document.querySelector('#query').value.toLowerCase(),type=document.querySelector('#type').value;const rows=DATA.entries.filter(r=>(type==='all'||r.kind===type)&&(r.name.toLowerCase().includes(q)||r.sha.includes(q)));document.querySelector('#count').textContent=rows.length+'项';document.querySelector('#rows').innerHTML=rows.map(r=>`<tr><td>${esc(r.name)}<br><small>${r.sha.slice(0,12)} · ${r.aliases}个源路径</small></td><td>${r.kind==='model'?'模型':'音效'}</td><td>${mb(r.before)}</td><td>${mb(r.after)}</td><td>${(100*(1-r.after/r.before)).toFixed(1)}%</td><td>${r.passed?'技术通过':'待修'}<br><small>${esc(r.quality.join('、'))}</small></td></tr>`).join('');}document.querySelector('#query').oninput=render;document.querySelector('#type').onchange=render;render();</script></html>'''
 gallery=[]
 for identifier in ['model:US_M1A2','model:US_AH64D','model:US_A10']:
  row=next((r for r in files if any(f['id']==identifier and f['role']=='model_url' for f in r.get('formal',[]))),None)
  if row:
   p=OUT/'浏览器验证/chrome'/(row['sha256']+'.jpg')
   if p.exists():gallery.append((identifier,p))
 zombie=next((r for r in files if r['path'].endswith('super_low-poly_stylized_zombies_animated.glb')),None)
 if zombie:
  p=OUT/'浏览器验证/chrome'/(zombie['sha256']+'.jpg')
  if p.exists():gallery.append(('丧尸原件完整角色组',p))
 images='<h2>原版与优化版对照</h2><p class="muted">每组左侧为原版、右侧为基础压缩版；固定同镜头和动作时间，不是细节层截图。</p>'
 for label,p in gallery:images+=f'<figure style="margin:24px 0"><figcaption>{html.escape(label)}</figcaption><img style="width:100%;height:auto" src="data:image/jpeg;base64,{base64.b64encode(p.read_bytes()).decode()}" alt="{html.escape(label)}原版与优化版对照"></figure>'
 runtime=OUT/'运行接入验证.json'
 if runtime.exists():
  actual=read(runtime);images+='<h2>实际运行入口</h2><div class="scroll"><table><tr><th>浏览器</th><th>靶场</th><th>群体角色</th><th>正式库</th><th>异常</th></tr>'
  for r in actual.get('browsers',[]):images+=f'<tr><td>{html.escape(r["browser"])}</td><td>{r.get("range",{}).get("models",0)}模型／{r.get("range",{}).get("audio",0)}音效</td><td>{r.get("horde",{}).get("animated",0)}只动画</td><td>{r.get("library",{}).get("total",0)}条稳定ID</td><td>{len(r.get("errors",[]))}</td></tr>'
  images+='</table></div>'
 performance=OUT/'运行性能验证.json'
 if performance.exists():
  measured=read(performance)
  if len(measured.get('browsers',[]))==2 and measured.get('frozen_static'):
   images+='<h2>100单位持续有声交战</h2><p>RX 7900 XTX，640宽像素画面，100%音量与6dB增强。HTTP源码及运行清单冻结，Chrome／Edge串行各预热10秒、采样180秒。共享GPU上可能存在其他会话负载，不作为与旧版本的严格A/B速度比较。</p><div class="scroll"><table><tr><th>浏览器</th><th>实际秒数</th><th>P95毫秒</th><th>P99毫秒</th><th>超过33.34毫秒</th><th>入画单位最少</th></tr>'
   for r in measured['browsers']:
    b=r['range']['benchmark'];images+=f'<tr><td>{r["browser"]}</td><td>{b["duration"]:.2f}</td><td>{b["p95"]:.2f}</td><td>{b["p99"]:.2f}</td><td>{b["over33ms"]}</td><td>{min(x["visibleUnits"] for x in b["timeline"])}</td></tr>'
   images+='</table></div><p class="muted">发射与命中持续增长；实际入画数量见上表。并行阴影任务的后续源码不覆盖本轮快照；普通电脑的最低配置与实机60帧验收仍待确定。</p>'
 categories='<div class="scroll"><table><tr><th>待确认类别</th><th>文件数</th><th>字节数</th></tr>'
 for category,values in summary['deletion_categories'].items():categories+=f'<tr><td>{html.escape(category)}</td><td>{values["files"]}</td><td>{values["bytes"]:,}</td></tr>'
 categories+='</table></div>'
 template=template.replace('<p>冻结删除清单SHA256：',categories+'<p>冻结删除清单SHA256：')
 template=template.replace('<th>降低</th><th>状态</th>','<th>降低</th><th>基础→细节层三角形</th><th>状态</th>')
 template=template.replace("toFixed(1)}%</td><td>${r.passed?", "toFixed(1)}%</td><td>${r.triangles||'—'}</td><td>${r.passed?")
 template=template.replace('<h2>06 / 查阅入口</h2>',images+'<h2>06 / 查阅入口</h2>')
 report.write_text(template.replace('__DATA__',payload),encoding='utf8');print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
