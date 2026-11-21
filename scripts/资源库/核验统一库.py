"""统一库文件、原件恢复、页面、全部模型和声音的双浏览器核验。"""
import importlib.util,json,pathlib,sys,threading,time
from playwright.sync_api import sync_playwright
from 统一资源 import ROOT,HOME,current,read,record_path,sha,restore,save
sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'scripts/断箭'))
def load(file,name):
 s=importlib.util.spec_from_file_location(name,ROOT/file);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def master_evidence(index,registry):
 """按当前工程身份复用已通过回读；新增工程须有摘要绑定的专项证据。"""
 historical=read(HOME/'验收/主工程回读.json');assert historical['passed']
 accepted={r['sha256']:r for r in historical['models'] if r['passed']}
 for asset in registry.values():
  for receipt in asset.get('technical_evidence',[]):
   if pathlib.Path(receipt['path']).name!='回读回执.json':continue
   p=pathlib.Path(receipt['path']);p=p if p.is_absolute() else ROOT/p
   assert p.resolve().is_relative_to((ROOT/'local-data').resolve()) and sha(p)==receipt['sha256']
   proof=read(p);assert proof['passed'] and proof['blend']['passed']
   accepted[proof['blend']['sha256']]=proof['blend']
 expected=[{'id':k,'sha256':v['sha256']} for k,v in read(index/'主工程.json').items() if v['path'].endswith('.blend')]
 expected.extend({'id':'original:'+k,'sha256':k} for k,v in read(index/'原件映射.json').items() if v['category']=='源工程')
 missing=[r for r in expected if r['sha256'] not in accepted]
 assert not missing,('当前工程缺少回读证据',missing)
 return {'passed':True,'count':len(expected),'evidence_reused_by_sha256':True,'models':expected}
def run():
 _,catalog,routes,index=current();registry=read(index/'模型登记.json');checks=[]
 for url,row in routes.items():
  p=record_path(row);assert p.stat().st_size==row['bytes'] and sha(p)==row['sha256'];checks.append({'url':url,**row,'passed':True})
 masters=master_evidence(index,registry)
 original=read(index/'原件映射.json');restored=[]
 for category in ['模型','音效','源工程']:
  key=next(k for k,v in original.items() if v['category']==category);p=restore(key);assert sha(p)==key;restored.append({'category':category,'sha256':key,'path':str(p)})
 services={'library':load('scripts/断箭/启动正式资料库.py','unified_test_library').create_server(0),'range':load('scripts/预览载具靶场.py','unified_test_range').create_server(0),'horde':load('scripts/预览丧尸群体测试.py','unified_test_horde').create_server(0)}
 # 系统端口若被浏览器禁止，改用已确认高位端口。
 safe=load('scripts/资源优化/游戏版服务.py','unified_test_ports')
 for key,s in list(services.items()):
  if s.server_port<20000:
   s.server_close();services[key]=load({'library':'scripts/断箭/启动正式资料库.py','range':'scripts/预览载具靶场.py','horde':'scripts/预览丧尸群体测试.py'}[key],'new_'+key).create_server(safe.safe_port(0))
 for s in services.values():threading.Thread(target=s.serve_forever,daemon=True).start()
 models=[v for r in registry.values() if r['kind']=='model' for v in r['variants']];audio=list({r['url']:r for r in catalog['audio']}.values());result={'counts':{'models':len(catalog['models']),'audio_events':len(catalog['audio']),'model_variants':len(models),'audio_files':len(audio),'current_media':len(checks)},'master_evidence':masters,'file_checks':checks,'restored_samples':restored,'browsers':[]}
 with sync_playwright() as pw:
  for browser in ['chrome','msedge']:
   errors=[];b=pw.chromium.launch(channel=browser,headless=True);page=b.new_page(viewport={'width':1440,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)));page.goto(f'http://127.0.0.1:{services["library"].server_port}/');page.wait_for_function('document.querySelectorAll("#model-list button").length>0');page.locator('#global-search').fill('M1A2');page.locator('#model-list button').first.click();page.locator('#load-3d').click();page.wait_for_function('document.querySelector("#viewer-state").textContent.includes("已就绪")',timeout=60000)
   profiles=page.locator('#spec-row [data-spec]').count();assert profiles==3;page.locator('#spec-row [data-spec="2"]').click();page.locator('#load-3d').click();page.wait_for_function('document.querySelector("#viewer-state").textContent.includes("已就绪")',timeout=60000);page.screenshot(path=str(HOME/f'验收/{browser}统一库.png'))
   # 直接在同一真实Three.js环境逐个解析全部当前规格，避免只验证缩略图。
   model_results=page.evaluate('''async(items)=>{const T=await import('three'),{createModelLoader,disposeModelLoader}=await import('/compressed-loader.mjs');const renderer=new T.WebGLRenderer(),loader=createModelLoader(renderer),rows=[];for(const item of items){try{const g=await loader.loadAsync(item.url);let meshes=0,bones=0;g.scene.traverse(o=>{if(o.isMesh)meshes++;if(o.isSkinnedMesh)bones+=o.skeleton.bones.length;});if(!meshes)throw Error('没有网格');if(g.animations.length){const mixer=new T.AnimationMixer(g.scene);for(const clip of g.animations)mixer.clipAction(clip).play();for(const t of [0,.25,.5,.75,1.25]){mixer.setTime(t);g.scene.updateMatrixWorld(true);g.scene.traverse(o=>{if(!o.isSkinnedMesh)return;o.skeleton.update();const a=o.geometry.attributes.position;for(let i=0;i<a.count;i++){const v=new T.Vector3().fromBufferAttribute(a,i);o.applyBoneTransform(i,v);if(!Number.isFinite(v.x+v.y+v.z))throw Error('骨骼顶点非有限');}});}}g.scene.traverse(o=>{o.geometry?.dispose();for(const m of Array.isArray(o.material)?o.material:[o.material]){if(!m)continue;for(const v of Object.values(m))if(v?.isTexture)v.dispose();m.dispose();}});rows.push({sha256:item.sha256,passed:true,meshes,bones,animations:g.animations.length});}catch(e){rows.push({sha256:item.sha256,passed:false,error:String(e)});}}disposeModelLoader(renderer);renderer.dispose();return rows;}''',models);assert all(r['passed'] for r in model_results),[r for r in model_results if not r['passed']]
   audio_results=page.evaluate('''async(items)=>{const c=new AudioContext(),rows=[];for(const item of items){try{const b=await c.decodeAudioData(await(await fetch(item.url)).arrayBuffer());rows.push({sha256:item.sha256,passed:b.duration>0,duration:b.duration,channels:b.numberOfChannels});}catch(e){rows.push({sha256:item.sha256,passed:false,error:String(e)});}}await c.close();return rows;}''',audio);assert all(r['passed'] for r in audio_results)
   page.locator('#audio-tab').click();page.locator('[data-play]:visible').first.click();page.wait_for_function('document.querySelector("#player").currentTime>0',timeout=15000);page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth');page.close()
   page=b.new_page(viewport={'width':1600,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)));page.goto(f'http://127.0.0.1:{services["range"].server_port}/');page.wait_for_function('window.range?.ready',timeout=60000);assert page.evaluate('Object.values(range.manifest.models).every(r=>r.asset_library==="统一资源库")');page.locator('#sound').click();page.wait_for_function('range.audio.stats().cached===13',timeout=60000);assert page.evaluate('range.audio.masterVolume===1 && range.mixer.boostDb===6');page.locator('#unit-count').select_option('100');page.locator('#load-mode').select_option('combat');page.locator('#overview').click();page.wait_for_function('range.sim.shots>10 && range.sim.hits>0',timeout=40000);page.screenshot(path=str(HOME/f'验收/{browser}优化靶场.png'));range_result=page.evaluate('({models:range.manifest.models,audio:range.audio.stats(),perf:range.perf()})');page.close()
   page=b.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.goto(f'http://127.0.0.1:{services["horde"].server_port}/');page.wait_for_function('window.horde?.ready',timeout=60000);snapshot=page.evaluate('horde.snapshot()');assert snapshot['animation']['bones']==23;before=snapshot['animation']['vertex'];page.wait_for_function('(v)=>horde.snapshot().animation.vertex.some((x,i)=>Math.abs(x-v[i])>.001)',arg=before);page.close();b.close();assert not errors,errors
   result['browsers'].append({'browser':browser,'models':model_results,'audio':audio_results,'profiles':profiles,'library_playback':True,'narrow':True,'range':range_result,'horde':{'bones':23,'animated':True},'errors':errors});save(HOME/'验收/统一库核验.json',result);print(browser,'全部模型及音频、靶场、骨骼角色通过',flush=True)
 for s in services.values():s.shutdown()
 return result
if __name__=='__main__':run()
