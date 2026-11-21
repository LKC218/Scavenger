"""验证真实靶场、群体实验及正式资料库；可选180秒有声交战采样。"""
import argparse,hashlib,importlib.util,json,pathlib,sys,threading,time
from playwright.sync_api import sync_playwright
ROOT=pathlib.Path(__file__).resolve().parents[2];OUT=ROOT/'local-data/网页资源库/全库第一版'
sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'scripts/断箭'))
def load(file,name):
 spec=importlib.util.spec_from_file_location(name,ROOT/file);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
def run(performance=False):
 sys.path.insert(0,str(ROOT/'scripts/资源库'))
 from 统一资源 import available,HOME
 if available():
  if not performance:
   from 核验统一库 import run as verify
   return verify()
  global OUT
  OUT=HOME/'验收/运行性能';OUT.mkdir(parents=True,exist_ok=True)
 modules={k:load(f,'validate_'+k) for k,f in [('range','scripts/预览载具靶场.py'),('horde','scripts/预览丧尸群体测试.py'),('library','scripts/断箭/启动正式资料库.py')]};servers={k:m.create_server(0) for k,m in modules.items()}
 # 其他会话会改同一源码。固定本次HTTP实际提供的文件，两个浏览器读取同一份快照。
 frozen=[];snapshot=OUT/'运行源码快照'/('性能' if performance else '接入')
 for kind,server in servers.items():
  mapping=server.static if hasattr(server,'static') else server.routes
  for url,source in list(mapping.items()):
   if not isinstance(source,pathlib.Path) or url.startswith('/vendor/'):continue
   if source.suffix not in ['.js','.mjs','.html','.css','.json']:continue
   content=source.read_bytes();target=snapshot/kind/(hashlib.sha256(url.encode()).hexdigest()+source.suffix);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content);mapping[url]=target;frozen.append({'app':kind,'url':url,'source':source.relative_to(ROOT).as_posix(),'snapshot':target.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(content).hexdigest()})
 for s in servers.values():threading.Thread(target=s.serve_forever,daemon=True).start()
 result={'condition':'独立无窗口Chrome/Edge，本机高性能GPU；HTTP源码与运行清单冻结，同GPU可能有其他会话负载，不代表普通电脑验收','browsers':[],'frozen_static':frozen,'source_identity':[{ 'path':p.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for folder in ['src/载具靶场','src/资源加载','src/音效索引','src/丧尸群体测试','src/资料库'] for p in sorted((ROOT/folder).glob('*')) if p.suffix in ['.mjs','.html','.css']]}
 with sync_playwright() as pw:
  for browser in ['chrome','msedge']:
   b=pw.chromium.launch(channel=browser,headless=True);entry={'browser':browser,'version':b.version,'errors':[]};page=b.new_page(viewport={'width':1600,'height':1000});page.on('pageerror',lambda e:entry['errors'].append(str(e)));page.on('console',lambda m:entry['errors'].append(m.text) if m.type=='error' else None)
   page.goto(f'http://127.0.0.1:{servers["range"].server_port}/');page.wait_for_function('()=>!!window.range?.ready',timeout=60000)
   manifest=page.evaluate('range.manifest');assert len(manifest['runtime_optimization']['used'])==16,'靶场必须接入3模型和13声音'
   assert page.evaluate('range.audio.masterVolume===1 && range.mixer.boostDb===6')
   page.locator('#sound').click();page.wait_for_function('()=>range.mixer.active && range.audio.stats().cached===13',timeout=60000);page.wait_for_function('()=>range.mixer.engines.size===8',timeout=20000)
   page.locator('#unit-count').select_option('100');page.locator('#load-mode').select_option('combat');page.locator('#overview').click();page.wait_for_function('()=>range.sim.shots>10 && range.sim.hits>0',timeout=30000)
   entry['range']={'models':3,'audio':13,'runtime_assets':manifest['runtime_optimization']['used'],'perf':page.evaluate('range.perf()'),'gain_preserved':True};page.screenshot(path=str(OUT/f'{browser}优化靶场.png'))
   if performance:
    page.locator('#bench180').click();deadline=time.monotonic()+230
    while time.monotonic()<deadline:
     if page.evaluate('!!range.benchmark.last'):break
     page.wait_for_timeout(500)
    sample=page.evaluate('range.benchmark.last');assert sample and sample['duration']>=180
    assert sample['timeline'][-1]['shots']>sample['timeline'][0]['shots'] and sample['timeline'][-1]['hits']>sample['timeline'][0]['hits']
    entry['range']['benchmark']=sample
   page.close();page=b.new_page(viewport={'width':1600,'height':1000});page.on('pageerror',lambda e:entry['errors'].append(str(e)));page.goto(f'http://127.0.0.1:{servers["horde"].server_port}/');page.wait_for_function('()=>!!window.horde?.ready',timeout=60000);snapshot=page.evaluate('horde.snapshot()');assert snapshot['config']['asset'].get('runtime_variant');assert snapshot['animation']['bones']==23 and snapshot['animation']['independentSkeletons']==500
   before=snapshot['animation']['vertex'];page.wait_for_function('(v)=>horde.snapshot().animation.vertex.some((x,i)=>Math.abs(x-v[i])>0.001)',arg=before,timeout=20000);entry['horde']={'animated':500,'snapshot':page.evaluate('horde.snapshot()')};page.screenshot(path=str(OUT/f'{browser}优化群体.png'));page.close()
   page=b.new_page(viewport={'width':1440,'height':1000});page.on('pageerror',lambda e:entry['errors'].append(str(e)));page.goto(f'http://127.0.0.1:{servers["library"].server_port}/');page.wait_for_function('()=>document.querySelectorAll("#model-list button").length>0',timeout=30000);page.locator('#load-3d').click();page.wait_for_function('()=>document.querySelector("#viewer-state").textContent.includes("已就绪")',timeout=60000)
   data=page.evaluate('fetch("/api/search?type=model&limit=200").then(r=>r.json())');assert data['total']==129
   assert all(x.get('source_sha256') for x in data['results']), '全部正式模型应接入派生且保留原摘要'
   entry['library']={'total':data['total'],'ids':[x['id'] for x in data['results']],'quality_statuses':{s:sum(x['quality_status']==s for x in data['results']) for s in ['passed','partial']}};page.screenshot(path=str(OUT/f'{browser}优化正式库.png'));b.close();result['browsers'].append(entry);name='运行性能验证.json' if performance else '运行接入验证.json';(OUT/name).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');print(browser,'运行入口完成',len(entry['errors']),flush=True)
 for server in servers.values():server.shutdown()
 name='运行性能验证.json' if performance else '运行接入验证.json';(OUT/name).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');return result
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--performance',action='store_true');a=p.parse_args();run(a.performance)
