"""把本轮已核对原件派生为固定网页白名单，不修改输入和统一库。"""
import argparse, array, hashlib, json, shutil, wave
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def record(p,name,origin,**extra):return {'path':p.relative_to(ROOT).as_posix(),'sha256':sha(p),'bytes':p.stat().st_size,'name':name,'origin':origin,'status':'技术可读待人工视听',**extra}
def write_wav(source,target,check,loop=False):
    with wave.open(str(source),'rb') as w:
        if w.getsampwidth()!=2:raise ValueError('只支持已核验PCM16')
        channels=w.getnchannels();rate=w.getframerate();samples=array.array('h',w.readframes(w.getnframes()))
    start=max(0,check['first_nonzero_frame']-int(.01*rate));end=min(len(samples)//channels,check['last_nonzero_frame']+int(.1*rate));samples=samples[start*channels:end*channels]
    if loop:
        # 派生循环不是原生循环点；固定稳定区间和100毫秒交叉淡化。
        samples=samples[:min(len(samples),int(6.5*rate)*channels)];overlap=min(int(.1*rate),len(samples)//channels//8);frames=len(samples)//channels
        for i in range(overlap):
            t=i/max(1,overlap-1)
            for c in range(channels):samples[(frames-overlap+i)*channels+c]=round(samples[(frames-overlap+i)*channels+c]*(1-t)+samples[i*channels+c]*t)
        samples=samples[overlap*channels:]
    target.parent.mkdir(parents=True,exist_ok=True)
    with wave.open(str(target),'wb') as w:w.setnchannels(channels);w.setsampwidth(2);w.setframerate(rate);w.writeframes(samples.tobytes())
    return {'durationSeconds':len(samples)/channels/rate,'channels':channels,'sampleRate':rate,'sourceStartFrame':start,'loopAdaptation':'固定段100毫秒交叉淡化，非原生循环点' if loop else '只去除首尾静音，PCM有效段不变'}
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',default='local-data/断箭/批次/肃清视听-V001-01a1ac6a');parser.add_argument('--output',default='local-data/大地图肃清/首批/V001-01a1ac6a/视听');args=parser.parse_args();source=(ROOT/args.source).resolve();out=(ROOT/args.output).resolve()
    if not source.is_relative_to((ROOT/'local-data/断箭/批次').resolve()) or not out.is_relative_to((ROOT/'local-data/大地图肃清/首批/V001-01a1ac6a/视听').resolve()):raise ValueError('输出范围不符')
    if (out/'素材清单.json').exists():raise FileExistsError('不覆盖冻结网页清单')
    out.mkdir(parents=True,exist_ok=True);audit=read(source/'来源核对.json');audio={};textures={};provenance=[]
    for render in read(source/'丧尸音频渲染.json'):
        if render['render_status']!='passed' or render['check']['peak']==0 or render['check']['clip_samples']:raise ValueError('原生事件未通过幅度检查')
        name=render['event']['path'].split('/')[-1];death='Death' in name;target=out/'声音'/(name+'.wav');details=write_wav(source/render['output'],target,render['check'],not death);row=record(target,name,'原作FMOD2.01.11离线事件网页派生',event=render['event'],sourceSha256=render['check']['sha256'],**details)
        audio.setdefault('zombieDeath' if death else 'zombieGroup',[]).append(row);provenance.append({'role':'zombieDeath' if death else 'zombieGroup','source':str((source/render['output']).relative_to(ROOT)),'sha256':render['check']['sha256'],'basis':'已枚举真实事件与GUID；非原游戏实时混音/群体聚合语义'})
    library=ROOT/'local-data/资源库';version=library/read(library/'当前版本.json')['directory'];index=read(version/'资源索引.json');routes=read(version/'资源路由.json');wanted={'mainFire':'SFX_VEH_MAINGUN_120mm_Generic_US','m2Fire':'SFX_VEH_MACHINE_GUN_High_Caliber_12.7_M2_Browning','mainImpact':'SFX_Expl_ROUND_120-125','m2Impact':'SFX_BULLET_HCal_MG_Impact_Burst','vehicleDeath':'Death_Exp_Veh_AmmoDetonation','tankMove':'US_AbramsM1A2_Engine（当前无缝循环）'}
    for role,name in wanted.items():
        row=next(r for r in index['audio'] if r['name']==name);route=routes[row['url']];p=ROOT/route['path']
        if sha(p)!=route['sha256']:raise ValueError('正式库媒体摘要变化')
        target=out/'声音'/(role+p.suffix);target.parent.mkdir(exist_ok=True);shutil.copyfile(p,target);check=row.get('check',{});rate=check.get('sample_rate',48000);offset=max(0,check.get('first_nonzero_frame',0)/rate-.01);end=(check.get('last_nonzero_frame',0)/rate+.1) if check.get('last_nonzero_frame') else check.get('duration_seconds',0)
        audio[role]=record(target,row['name'],'已核对原作字段关联的正式音频副本',eventPath=row.get('event_path'),sourceId=row['id'],offsetSeconds=offset,durationSeconds=max(.05,end-offset));provenance.append({'role':role,'source':route['path'],'sha256':route['sha256'],'bindings':[b for b in row.get('bindings',[]) if b.get('model_id')=='model:US_M1A2']})
    selects=[('muzzle',source,'Texture_Fire_Muzzle_BaseMap',1,1),('heatExplosion',source,'Explosion8_12x7',12,7),('vehicleExplosion',source/'毁伤补充','GroundExplosion4_8x8_BaseMap',8,8)]
    from PIL import Image
    for role,folder,name,cols,rows in selects:
        rows_source=read(folder/'VFX引用核对.json')['textures'];r=next(x for x in rows_source if x['name']==name);p=Path(r['path']);target=out/'贴图'/(role+'.png');target.parent.mkdir(exist_ok=True);image=Image.open(p);image.thumbnail((1024,1024));image.save(target);textures[role]=record(target,name,'原作定向VFX引用图可用贴图网页缩放派生',sourceObjectId=r['id'],sourceSha256=r['sha256'],columns=cols,rows=rows,playbackStatus='按贴图命名与画面布局适配；原HDRP图、精确时序和光照未复现',blend='additive');provenance.append({'role':role,'source':p.relative_to(ROOT).as_posix(),'sha256':r['sha256'],'packageSha256':audit['package']['sha256']})
    missing=[{'role':'blood','scope':'本次VFX包名称和3种弹药InfantryImpact实际引用链','status':'未找到独立血液贴图；保留网页深红碎粒临时反馈，不代表全游戏不存在'}, {'role':'nativeVFX','status':'HDRP/VFX Graph不直接移植；13个弹药外部引用和3个毁伤外部引用未解析'}, {'role':'mantletPitch','status':'5个完整炮管分量已按真实轴局部运行派生；110三角形防盾及其他机械联动保持，未猜测绑定'}, {'role':'wreck','status':'车辆原模型深色残骸网页适配；未确证独立原作毁伤网格'}, {'role':'audioMix','status':'已取真实Walker/Runner事件，网页粗声像和循环派生待实际听审'}]
    result={'version':'V001','audio':audio,'textures':textures,'provenance':provenance,'missing':missing,'sourceIdentity':{'build':audit['build'],'vfxPackageSha256':audit['package']['sha256'],'gameReadOnly':True},'acceptance':{'technical':'PCM幅度与摘要通过，贴图可解码','visual':'待浏览器与工程师确认','listening':'待工程师听审'}}
    (out/'素材清单.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'audioRoles':len(audio),'textureRoles':len(textures),'bytes':sum(p.stat().st_size for p in out.rglob('*') if p.is_file()),'manifest':str((out/'素材清单.json').relative_to(ROOT))},ensure_ascii=False))
if __name__=='__main__':main()
