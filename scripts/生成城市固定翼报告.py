"""从完整靶场回执生成无私有路径的离线HTML及可追溯摘要。"""
import hashlib
import json
from datetime import datetime,timezone
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]
LOCAL=ROOT/'local-data/原型/载具靶场/验证/城市修订1'
OUT=ROOT/'reports/载具靶场'


def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    inputs=['chrome-浏览器验证.json','msedge-浏览器验证.json','镜头/chrome-镜头验证.json','镜头/msedge-镜头验证.json','chrome-固定翼.json','msedge-固定翼.json','chrome-性能.json','msedge-性能.json']
    data={'generated':datetime.now(timezone.utc).isoformat(),'environment':{'cpu':'AMD Ryzen 7 9800X3D','gpu':'AMD Radeon RX 7900 XTX','system':'Windows 11','viewport':[1600,1000],'headless':True},'checks':[],'samples':[],'evidence':[]}
    identity=read(LOCAL/'采样源码身份.json')
    for row in identity['files']:
        if sha(ROOT/row['path'])!=row['sha256']:raise ValueError('采样后源码已改变：'+row['path'])
    data['source_identity']={'recorded_at':identity['recorded_at'],'files':[{k:r[k] for k in ['path','sha256']} for r in identity['files']]}
    timeline_keys=['seconds','time','p50','p95','p99','cpuP95','totalUnits','visibleUnits','simulatedUnits','movingUnits','projectiles','shots','hits','voices','engines','audioPeak','audioDropped','drawCalls','triangles','waitingPaths','visualAmmoDropped']
    sample_keys=['date','label','duration','frames','p50','p95','p99','max','over20ms','over33ms','cpuP95','map','load','audio','volume','recover','totalUnits','buildings','internal','viewport','renderer','browser','anchors']
    for name in inputs:
        path=LOCAL/name;r=read(path)
        if r.get('status')!='passed' and r.get('passed') is not True:raise ValueError('验收未完成：'+name)
        if r.get('errors'):raise ValueError('回执存在运行错误：'+name)
        if 'source_identity' in r and r['source_identity']!=data['source_identity']['files']:raise ValueError('回执源码版本不一致：'+name)
        data['evidence'].append({'name':name,'sha256':sha(path)})
        data['checks'].append({'name':name,'count':len(r['checks']),'checks':r['checks'],'version':r.get('version',r.get('browser_version'))})
        if 'audio_proof' in r:data.setdefault('audio_proof',{})[r['channel']]=r['audio_proof']
        for sample in r.get('samples',[]):
            item={k:sample[k] for k in sample_keys if k in sample};item['channel']=r['channel'];item['timeline']=[{k:t[k] for k in timeline_keys if k in t} for t in sample['timeline']];item['final']={k:v for k,v in sample['final'].items() if k in timeline_keys or k in ['time','textures','geometries']};data['samples'].append(item)
    data['rules']=read(LOCAL/'规则验证.json')
    manifest=read(ROOT/'local-data/原型/载具靶场/资源清单.json')
    data['models']=[{'kind':kind,'id':row['id'],'source_sha256':row['source_sha256'],'derived_sha256':Path(row['url']).stem,'bytes':row['bytes'],'triangles':row['detail'].get('triangles_after')} for kind,row in manifest['models'].items()]
    for name,destination in [('chrome-50-static-640-声音True.png','城市全景.png'),('chrome-A10近景.png','固定翼近景.png')]:
        path=LOCAL/name;target=OUT/'assets'/destination;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
    OUT.mkdir(parents=True,exist_ok=True)
    summary=json.dumps(data,ensure_ascii=False,indent=2)
    (OUT/'城市固定翼性能摘要.json').write_text(summary,encoding='utf-8')
    template=(ROOT/'scripts/城市固定翼报告模板.html').read_text(encoding='utf-8')
    html=template.replace('__REPORT_DATA__',json.dumps(data,ensure_ascii=False).replace('<','\\u003c'))
    (OUT/'城市固定翼与性能验证.html').write_text(html,encoding='utf-8')
    print(json.dumps({'samples':len(data['samples']),'checks':sum(r['count'] for r in data['checks']),'report':'reports/载具靶场/城市固定翼与性能验证.html'},ensure_ascii=False))


if __name__=='__main__':main()
