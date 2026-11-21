"""经过双浏览器逐资源验证后才接入派生；保持正式源索引不变。"""
import copy,hashlib,json,pathlib
from functools import lru_cache
ROOT=pathlib.Path(__file__).resolve().parents[2];OUT=ROOT/'local-data/网页资源库/全库第一版'
VENDOR=['examples/jsm/loaders/KTX2Loader.js','examples/jsm/libs/meshopt_decoder.module.js','examples/jsm/utils/WorkerPool.js','examples/jsm/libs/ktx-parse.module.js','examples/jsm/libs/zstddec.module.js','examples/jsm/math/ColorSpaces.js','examples/jsm/libs/basis/basis_transcoder.js','examples/jsm/libs/basis/basis_transcoder.wasm']
def read(p):return json.loads(pathlib.Path(p).read_text(encoding='utf8'))
def sha(p):return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()

def decoders(routes,folder,frozen):
    routes['/compressed-loader.mjs']=ROOT/'src/资源加载/压缩模型加载.mjs'
    for name in VENDOR:
        p=folder/name
        if sha(p)!=frozen[name]['sha256']:raise ValueError('压缩解码器摘要变化：'+name)
        routes['/vendor/'+name]=p

@lru_cache(maxsize=1)
def variants():
    import sys
    sys.path.insert(0,str(ROOT/'scripts/资源库'))
    from 统一资源 import available,replacements
    if available():return replacements()
    pointer=ROOT/'local-data/网页资源库/运行接入.json'
    if not pointer.exists() or not read(pointer).get('enabled'):return {}
    result={}
    for p in (OUT/'验证').glob('*.json'):
        if not p.name.endswith(('.model.json','.audio.json')):continue
        r=read(p)
        if not r.get('passed'):continue
        key=r['source_sha256']
        if r['kind']=='model':
            accepted=True
            for browser in ['chrome','msedge']:
                b=OUT/'浏览器验证'/browser/(key+'.json')
                if not b.exists():accepted=False;break
                test=read(b)
                if not test.get('technical_passed') or not test.get('visual_threshold_passed') or test.get('optimized_sha256')!=r['sha256']:accepted=False;break
            if not accepted:continue
        pth=(ROOT/r['path']).resolve()
        if not pth.is_relative_to(OUT.resolve()) or sha(pth)!=r['sha256']:raise ValueError('运行派生越界或摘要变化')
        result[key]={**r,'url':'/media/'+r['sha256']+pth.suffix,'route':{'path':str(pth),'bytes':r['bytes'],'sha256':r['sha256']}}
    # 游戏版逐项验证后覆盖；未通过项继续使用上面已验收的保真压缩版。
    settings=read(pointer)
    if settings.get('game_enabled'):
        game=ROOT/'local-data/网页资源库/游戏版第一版'
        for record in (game/'验证').glob('*.json'):
            r=read(record)
            if not r.get('passed'):continue
            key=r['source_sha256']
            if key not in result:continue
            resolution=settings.get('game_resolution',256);level=settings.get('game_level',1)
            choices=sorted(r['variants'],key=lambda v:(v['resolution']!=resolution,v['level']!=level,v['level']))
            for v in choices:
                identifier=f"{key}.{v['resolution']}.lod{v['level']}"
                accepted=True
                for browser in ['chrome','msedge']:
                    receipt=game/'浏览器验证'/browser/(identifier+'.json')
                    if not receipt.exists():accepted=False;break
                    test=read(receipt)
                    if test.get('sha256')!=v['sha256'] or not test.get('technical_passed') or not test.get('visual_threshold_passed'):accepted=False;break
                if not accepted:continue
                target=(ROOT/v['path']).resolve()
                if not target.is_relative_to(game.resolve()) or sha(target)!=v['sha256']:raise ValueError('游戏派生越界或摘要变化')
                result[key]={**result[key],**v,'kind':'model','source_sha256':key,'game_variant':identifier,'url':'/media/'+v['sha256']+'.glb','route':{'path':str(target),'bytes':v['bytes'],'sha256':v['sha256']}}
                break
    return result

def range_manifest(original,routes):
    import sys
    sys.path.insert(0,str(ROOT/'scripts/资源库'))
    from 统一资源 import available,range_manifest as unified_range
    if available():return unified_range(original,routes)
    result=copy.deepcopy(original);available=variants();used=[]
    for group in ['models','audio']:
        for key,row in result.get(group,{}).items():
            old=routes.get(row.get('url'));v=available.get(old['sha256']) if old else None
            if not v:continue
            row.update(url=v['url'],bytes=v['bytes'],runtime_source_sha256=v['source_sha256'],runtime_sha256=v['sha256']);routes[v['url']]=v['route'];used.append(key)
    result['runtime_optimization']={'used':used,'source_manifest_unchanged':True};return result

def library(server):
    if server.catalog.get('asset_library_revision'):return
    available=variants()
    for group,key in [('models','model_url'),('audio','url')]:
        for row in server.catalog[group]:
            v=available.get(row.get('sha256'))
            if not v:continue
            row['source_sha256']=row['sha256'];row.update({key:v['url'],'sha256':v['sha256'],'bytes':v['bytes']});row.setdefault('extensions',{})['runtime_optimization']='独立压缩派生；原始资料与质量状态保留';server.routes[v['url']]=v['route']

def horde(record,asset):
    v=variants().get(sha(asset))
    if not v:return record,asset
    result=copy.deepcopy(record);result['asset']['source_sha256']=result['asset']['sha256'];result['asset'].update(sha256=v['sha256'],bytes=v['bytes'],runtime_variant=v['path']);return result,ROOT/v['path']
