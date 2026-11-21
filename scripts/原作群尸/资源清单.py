"""核对本轮原作运行资源和旧卡通对照，不修改统一资料库。"""
import hashlib,json,struct,sys,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from 群体资源交接 import load_delivery

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def manifests():
    baseline=ROOT/'local-data/原作群尸/V001-01a1aa48/旧卡通对照';marker=baseline/'冻结清单.json'
    if not marker.exists():
        old,old_routes=load_delivery();baseline.mkdir(parents=True,exist_ok=False);copies={}
        for url,source in old_routes.items():
            source=Path(source);target=baseline/(sha(source)+source.suffix);shutil.copyfile(source,target)
            if sha(target)!=sha(source):raise ValueError('旧对照冻结副本不一致')
            copies[url]={'path':str(target),'sha256':sha(target),'source':str(source)}
        marker.write_text(json.dumps({'manifest':old,'routes':copies},ensure_ascii=False,indent=2),encoding='utf-8')
    frozen=json.loads(marker.read_text('utf-8'));cartoon=[]
    for row in frozen['manifest']['models']:
        row=dict(row);row.update(family='cartoon',states={'idle':'待机','move':'移动','attack':'待机','death':'待机'});cartoon.append(row)
    native=[];routes={}
    for url,record in frozen['routes'].items():
        p=Path(record['path'])
        if not p.is_relative_to(baseline) or sha(p)!=record['sha256']:raise ValueError('旧对照身份变化')
        routes[url]=p
    for i,role in enumerate(['Walker','Runner']):
        directory=ROOT/'local-data/断箭/批次/BA-26273833-014'/role
        receipt=json.loads((directory/'压缩回执.json').read_text('utf-8'));path=Path(receipt['path']).resolve()
        if not receipt['passed'] or sha(path)!=receipt['sha256'] or not path.is_relative_to(directory.resolve()):raise ValueError('原作运行文件身份不符')
        raw=json.loads((directory/'派生记录.json').read_text('utf-8'));clips=raw['animations'];url=f'/native-assets/{receipt["sha256"]}.glb';routes[url]=path
        native.append({'id':'model:ba-'+role.lower()+'-lod2','name':role,'family':'native','source_mesh_index':i,'bones':46,'materials':1,'triangles':raw['triangles'],'url':url,
                       'asset':{'bytes':receipt['bytes'],'sha256':receipt['sha256']},'clips':clips,'states':{'idle':'ZombieIdle','move':'ZombieRunning' if role=='Walker' else 'ZombieSprint','attack':'ZombieAttack','death':'ZombieDying'},
                       'coordinate_system':{'unit':'米','up':'+Y','forward':'+Z','rest_height_m':1.86}})
    return {'native':{'family':'native','models':native,'label':'原作 Walker／Runner · LOD2'},'cartoon':{'family':'cartoon','models':cartoon,'label':'旧卡通十三款 · 冻结对照'}},routes
