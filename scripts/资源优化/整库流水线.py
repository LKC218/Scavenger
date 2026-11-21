"""整库只读盘点、独立派生和可验证冷归档；不包含删除操作。"""
from __future__ import annotations
import argparse, concurrent.futures, datetime, hashlib, json, os, pathlib, struct, subprocess, sys, time, wave, zipfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / 'local-data/网页资源库/全库第一版'
TOOLS = ROOT / 'local-data/tools/网页资源优化/1'
EXTS = {'.glb', '.gltf', '.blend', '.blend1', '.fbx', '.obj', '.wav', '.ogg', '.mp3', '.flac', '.m4a'}

def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(8*1024*1024), b''): h.update(block)
    return h.hexdigest()

def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix+'.writing')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf8')
    temp.replace(path)

def inventory():
    paths=[]
    for root, dirs, files in os.walk(ROOT/'local-data'):
        dirs[:] = sorted(d for d in dirs if d not in {'tools','网页资源库','资源归档','node_modules','__pycache__','.git'})
        for name in sorted(files):
            p=pathlib.Path(root)/name
            if p.suffix.lower() in EXTS: paths.append(p)
    def inspect(p):
        st=p.stat(); item={'path':p.relative_to(ROOT).as_posix(),'bytes':st.st_size,'mtime_ns':st.st_mtime_ns,'sha256':digest(p),'format':p.suffix.lower()}
        if p.suffix.lower()=='.glb':
            with p.open('rb') as f:
                header=f.read(20); length,kind=struct.unpack('<II',header[12:20]); g=json.loads(f.read(length))
            item['structure']={k:len(g.get(k,[])) for k in ['nodes','meshes','materials','textures','animations','skins','accessors']}
            item['extensions']=g.get('extensionsUsed',[])
        elif p.suffix.lower()=='.wav':
            try:
                with wave.open(str(p)) as w: item['audio']={'channels':w.getnchannels(),'rate':w.getframerate(),'frames':w.getnframes(),'seconds':w.getnframes()/w.getframerate(),'sample_bytes':w.getsampwidth()}
            except Exception as e: item['inspection_error']=str(e)
        return item
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool: records=list(pool.map(inspect,paths))
    indexed=[]
    for typ in ['model','audio']:
        offset=0
        while True:
            r=subprocess.run([sys.executable,'-X','utf8','-B',str(ROOT/'scripts/断箭/查询资料库.py'),'--type',typ,'--limit','200','--offset',str(offset),'--json'],cwd=ROOT,capture_output=True,text=True,encoding='utf8',check=True)
            data=json.loads(r.stdout);indexed.extend(data['results']);offset+=len(data['results'])
            if offset>=data['total']:break
    # 私有清单只写入已忽略的本地目录。
    by_path={str((ROOT/r['path']).resolve()).casefold():r for r in records}
    for item in indexed:
        for role, path in item.get('local_paths',{}).items():
            target=by_path.get(str(pathlib.Path(path).resolve()).casefold())
            if target: target.setdefault('formal',[]).append({'id':item['id'],'role':role,'quality_status':item.get('quality_status',item.get('status'))})
    result={'created':datetime.datetime.now(datetime.timezone.utc).isoformat(),'files':records,'formal_records':indexed,'file_count':len(records),'bytes':sum(r['bytes'] for r in records),'unique_hashes':len({r['sha256'] for r in records})}
    save(OUT/'盘点.json',result);print(json.dumps({k:v for k,v in result.items() if k not in ['files','formal_records']},ensure_ascii=False),flush=True)

def process(skip_models=False):
    data=json.loads((OUT/'盘点.json').read_text(encoding='utf8'));unique={}
    for r in data['files']:
        if r['format'] in ['.glb','.wav','.ogg','.mp3','.flac','.m4a']: unique.setdefault((r['sha256'],r['format']),r)
    jobs=[{'source':str(ROOT/r['path']),'source_sha256':r['sha256'],'kind':'model' if r['format']=='.glb' else 'audio','source_bytes':r['bytes']} for r in unique.values()]
    save(OUT/'任务.json',jobs)
    if not skip_models:
        with (OUT/'模型编码.log').open('ab') as log:
            subprocess.run(['node',str(ROOT/'scripts/资源优化/模型批处理.mjs'),str(OUT/'任务.json'),str(OUT)],cwd=ROOT,stdout=log,stderr=log,check=True)
    ffmpeg=TOOLS/'node_modules/ffmpeg-static/ffmpeg.exe'
    audio_jobs=[j for j in jobs if j['kind']=='audio']
    def encode(j):
        key=j['source_sha256'];receipt=OUT/'验证'/f'{key}.audio.json';dst=OUT/'音效'/f'{key}.ogg'
        if receipt.exists():
            old=json.loads(receipt.read_text(encoding='utf8'))
            retained=ROOT/old.get('path','不存在')
            if old.get('passed') and retained.is_file() and digest(retained)==old['sha256']:return old
        dst.parent.mkdir(parents=True,exist_ok=True)
        try:
            # 不裁尾、不强制单声道、不改混音增益。循环点和空间用途沿用原始定义。
            subprocess.run([str(ffmpeg),'-hide_banner','-loglevel','error','-y','-i',j['source'],'-map_metadata','-1','-c:a','libopus','-b:a','96k','-vbr','on',str(dst)],capture_output=True,check=True)
            check=subprocess.run([str(ffmpeg),'-hide_banner','-i',str(dst),'-af','astats=metadata=0:reset=0','-f','null','-'],capture_output=True,text=True,encoding='utf8',errors='replace',check=True)
            (OUT/'验证'/f'{key}.audio.log').write_text(check.stderr,encoding='utf8')
            result={**j,'path':dst.relative_to(ROOT).as_posix(),'bytes':dst.stat().st_size,'sha256':digest(dst),'passed':True,'verification':'完整解码及幅度统计；听感与循环语义仍需按用途验收'}
        except Exception as e: result={**j,'passed':False,'error':str(e)}
        save(receipt,result);return result
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for i,r in enumerate(pool.map(encode,audio_jobs)):
            if i%25==0: print('音效',i+1,'/',len(audio_jobs),flush=True)

def archive():
    data=json.loads((OUT/'盘点.json').read_text(encoding='utf8'));unique={}
    for r in data['files']:unique.setdefault(r['sha256'],r)
    home=ROOT/'local-data/资源归档/全库第一版';home.mkdir(parents=True,exist_ok=True)
    mapping={};groups=[];group=[];size=0
    for sha,r in sorted(unique.items()):
        if size+r['bytes']>1024**3 and group:groups.append(group);group=[];size=0
        group.append(r);size+=r['bytes']
    if group:groups.append(group)
    for n,group in enumerate(groups,1):
        p=home/f'原件-{n:03d}.zip';receipt=p.with_suffix('.验证.json')
        if receipt.exists() and p.exists():
            old=json.loads(receipt.read_text(encoding='utf8'))
            if digest(p)==old.get('archive_sha256') and old.get('passed'):
                mapping.update(old['entries']);print('归档复用',n,flush=True);continue
        entries={}
        with zipfile.ZipFile(p,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as z:
            for r in group:
                src=ROOT/r['path']
                if digest(src)!=r['sha256']:raise RuntimeError('输入已变化：'+r['path'])
                name='objects/'+r['sha256']+r['format'];z.write(src,name)
                entries[r['sha256']]={'archive':p.relative_to(ROOT).as_posix(),'entry':name,'bytes':r['bytes']}
        with zipfile.ZipFile(p) as z:
            for sha,item in entries.items():
                h=hashlib.sha256()
                with z.open(item['entry']) as f:
                    for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
                if h.hexdigest()!=sha:raise RuntimeError('解压摘要不一致：'+sha)
        save(receipt,{'passed':True,'archive_sha256':digest(p),'archive_bytes':p.stat().st_size,'entries':entries});mapping.update(entries);print('归档完整核验',n,'/',len(groups),flush=True)
    save(home/'路径映射.json',{'files':[{**r,'retained':mapping[r['sha256']]} for r in data['files']]})

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('stage',choices=['inventory','process','audio','archive']);args=parser.parse_args()
    sys.path.insert(0,str(ROOT/'scripts/资源库'))
    from 统一资源 import available
    if available():
        raise SystemExit('旧整库流水线已退役。请运行 scripts/资源库/维护资源库.py audit 或 verify；模型重建使用 rebuild。原件从统一分类归档恢复。')
    OUT.mkdir(parents=True,exist_ok=True);{'inventory':inventory,'process':process,'audio':lambda:process(True),'archive':archive}[args.stage]()
if __name__=='__main__':main()
