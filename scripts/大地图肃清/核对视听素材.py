"""首批视听只读定向核对与提取；不启动游戏、不改正式索引。"""
import argparse, hashlib, importlib.metadata, json, re, shutil, struct, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
DEFAULT='local-data/断箭/批次/肃清视听-V001-01a1ac6a'

def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(p,data):
    p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def identity(p):return {'path':str(p.resolve()),'bytes':p.stat().st_size,'sha256':sha(p)}
def checked_output(value):
    p=(ROOT/value).resolve();boundary=(ROOT/'local-data/断箭/批次').resolve()
    if not p.is_relative_to(boundary) or p==boundary:raise ValueError('输出必须在独立本地断箭批次内')
    return p
def references(tree):
    if isinstance(tree,dict):
        if 'm_PathID' in tree and tree['m_PathID']:yield tree
        for v in tree.values():yield from references(v)
    elif isinstance(tree,list):
        for v in tree:yield from references(v)
def glb_nodes(output):
    r=ROOT/'local-data/资源库';d=r/read(r/'当前版本.json')['directory'];index=read(d/'资源索引.json');routes=read(d/'资源路由.json');row=next(x for x in index['models'] if x['id']=='model:US_M1A2');p=ROOT/routes[row['model_url']]['path'];b=p.read_bytes();g=json.loads(b[20:20+struct.unpack_from('<I',b,12)[0]]);parents={c:i for i,n in enumerate(g['nodes']) for c in n.get('children',[])}
    def transform(i):
        point=[0,0,0];chain=[]
        while True:
            node=g['nodes'][i];chain.append({'index':i,**node});rotation=node.get('rotation',[0,0,0,1])
            if rotation!=[0,0,0,1]:raise ValueError('炮口链含旋转，需完整矩阵复核')
            point=[point[k]*node.get('scale',[1,1,1])[k]+node.get('translation',[0,0,0])[k] for k in range(3)]
            if i not in parents:break
            i=parents[i]
        return {'position':dict(zip('xyz',point)),'chain':chain}
    result={'model':identity(p),'coordinates':{'forward':'-Z','up':'+Y','yaw':'+Y','pitch':'+X'},'nodes':{g['nodes'][i]['name']:transform(i) for i in [47,48,49,57,59,62,63]},'visual_gap':'turret静态网格含炮管，空骨架不能直接驱动独立主炮俯仰；网页偏航使用实际轴重挂原网格'}
    save(output/'M1A2炮口与轴核对.json',result)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',default=DEFAULT);parser.add_argument('--extract',action='store_true');parser.add_argument('--vfx-focus',choices=['ammunition','vehicle-death'],default='ammunition');parser.add_argument('--skip-audio',action='store_true');args=parser.parse_args();out=checked_output(args.output)
    if (out/'来源核对.json').exists():raise FileExistsError('本轮已执行，不覆盖来源凭证')
    cfg=read(ROOT/'local-data/断箭/本地配置.json');game=Path(cfg['game_directory']);manifest=Path(cfg['steam_manifest']);match=re.search(r'"buildid"\s+"(\d+)"',manifest.read_text(encoding='utf-8'));build=match.group(1) if match else None
    if build!='26273833':raise ValueError('游戏构建与既有索引不同，停止套用旧元数据')
    pc=game/'BrokenArrow_Data/StreamingAssets/aa/PC';old=read(ROOT/'local-data/原型/丧尸防线/V003-01a1a2af/交互资源/vfx-索引.json');bundle=pc/old['source'];bundle_identity=identity(bundle)
    if bundle_identity['sha256']!=old['sha256']:raise ValueError('VFX包与已核验索引身份变化')
    with bundle.open('rb') as f:header=f.read(96)
    if not header.startswith(b'UnityFS\0'):raise ValueError('不是UnityFS输入')
    inputs=[bundle_identity,identity(manifest)];bankdir=game/'BrokenArrow_Data/StreamingAssets'
    for n in ['Master.strings.bank','Master.bank','SFX_Unit.bank']:
        p=bankdir/n;item=identity(p)
        with p.open('rb') as f:item['header_hex']=f.read(16).hex()
        inputs.append(item)
    db=ROOT/'local-data/断箭/批次/BA-26273833-001/证据/装配/源配置核验.json';tables=read(db)['database']['tables'];ammo=[x for x in tables['Ammunitions'] if x['Id'] in [256,253,19]];weapons=[x for x in tables['Weapons'] if x['Id'] in [342,18]]
    out.mkdir(parents=True,exist_ok=True);glb_nodes(out);save(out/'原字段.json',{'weapons':weapons,'ammunition':ammo,'database':identity(db)})
    event_file=ROOT/'local-data/断箭/批次/BA-26273833-002/证据/兼容性与事件索引.json';names=['Zomb_Walker','Zomb_Runner','Zomb_Waalker-Runner_Death'];events=[e for e in read(event_file)['events'] if e['path'].split('/')[-1] in names];save(out/'丧尸事件候选.json',{'source':identity(event_file),'events':events,'scope':'只核对Walker、Runner与其死亡事件；不证明其他事件不存在'})
    tools=[identity(Path(__file__)),identity(ROOT/'scripts/断箭/提取音效.py'),identity(ROOT/'local-data/tools/FMOD/2.01.11/fmodstudio.dll')]
    report={'version':'V001','build':build,'source_read_only':True,'game_started':False,'package':bundle_identity,'header_hex':header.hex(),'unityfs_header_version':struct.unpack_from('>I',header,8)[0],'inputs':inputs,'tools':tools,'extract':args.extract,'missing':[]}
    if args.extract:
        import UnityPy
        report['UnityPy']=importlib.metadata.version('UnityPy');env=UnityPy.load(str(bundle));objects={x.path_id:x for x in env.objects};presets=[];seeds=set();wanted={x['FXPreset'] for x in ammo}
        for path,ptr in env.container.items():
            if args.vfx_focus=='ammunition' and Path(path).stem in wanted:
                tree=ptr.deref().read_typetree();presets.append({'path':path,'id':ptr.path_id,'fields':tree});seeds.add(ptr.path_id)
        # 血液和毁伤仅把明确名称命中作为候选，再追逐实际引用，不按名称宣称原语义。
        extra=[x for x in old['objects'] if x['type']=='GameObject' and (x['name'] in ['Ground_Unit_Death','Death_Unit_AMMO_Detonation_Fire_Vertical_Y'] if args.vfx_focus=='vehicle-death' else 'blood' in x['name'].lower())]
        seeds.update(x['id'] for x in extra);queue=[(x,0) for x in seeds];seen=set();records=[];textures=[];external=[]
        while queue:
            oid,depth=queue.pop(0)
            if oid in seen or depth>10:continue
            if len(seen)>=3000:report['missing'].append('引用图超过3000对象安全边界，未继续展开');break
            seen.add(oid);obj=objects.get(oid)
            if not obj:continue
            row={'id':oid,'type':obj.type.name,'name':obj.peek_name() or '', 'depth':depth}
            if obj.type.name=='Texture2D':
                if len(textures)>=32:report['missing'].append('贴图超过32张定向预算');continue
                try:
                    data=obj.read();p=out/'贴图'/f'{oid}.png';p.parent.mkdir(parents=True,exist_ok=True);image=data.image;image.save(p);row.update(identity(p),width=image.width,height=image.height);textures.append(row)
                except Exception as error:row['error']=str(error)
            else:
                try:
                    tree=obj.read_typetree();refs=list(references(tree));row['references']=refs
                    if obj.type.name in ['MonoBehaviour','Material','ParticleSystem','VisualEffect','VisualEffectAsset']:save(out/'参数'/f'{oid}.json',tree)
                    for ref in refs:
                        if ref.get('m_FileID',0)==0:queue.append((ref['m_PathID'],depth+1))
                        else:external.append({'from':oid,**ref})
                except Exception as error:row['error']=str(error)
            records.append(row)
        save(out/'VFX引用核对.json',{'presets':presets,'extra_name_candidates':extra,'objects':records,'textures':textures,'external_unresolved':external,'adaptation':'Unity HDRP/VFX Graph参数和贴图不是可直接运行的Three.js粒子；候选贴图须单独选用与调色验证'})
        sys.path.insert(0,str(ROOT/'scripts/断箭'));from 提取音效 import render
        from 检查音效 import inspect
        settings=ROOT/'local-data/断箭/批次/BA-26273833-002/证据/音频设置原始对象.json';target=out/'证据/音频设置原始对象.json';target.parent.mkdir(exist_ok=True);shutil.copyfile(settings,target)
        renders=[]
        for event in ([] if args.skip_audio else events):
            receipt=render(out,event,event['guid'],8);save(out/'尝试'/event['guid']/'渲染记录.json',receipt)
            if receipt['render_status']=='passed':receipt['check']=inspect(out/receipt['output'])
            renders.append(receipt);save(out/'丧尸音频渲染.json',renders)
        report['vfx_counts']={'presets':len(presets),'reachable_objects':len(records),'textures':len(textures),'unresolved_external':len(external)};report['zombie_audio']=[{'event':r['event']['path'],'render_status':r['render_status'],'peak':r.get('check',{}).get('peak')} for r in renders]
    save(out/'来源核对.json',report);print(json.dumps({'build':build,'output':str(out.relative_to(ROOT)),'vfx':report.get('vfx_counts'),'audio':report.get('zombie_audio')},ensure_ascii=False))
if __name__=='__main__':main()
