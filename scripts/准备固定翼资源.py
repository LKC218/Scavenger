"""向既有原型追加A-10C及其已登记声音；原模型与正式库只读。"""
import argparse
import io
import json
from pathlib import Path

from PIL import Image
from 检查载具原型资源 import OUT, query, read_glb, write_glb, sha, check

SOUNDS={'planeShot':'SFX_AIR_PLANEGUN_30mm_GAU8_US','planeImpact':'SFX_Explosion_20-30mm_SupFAST_HE',
        'planeMove':'US_A10_Turbine_New','bombShot':'BA_DropBomb','bombImpact':'SFX_Expl_MISSILE_AA-A2A-A2G_Big'}


def prepare():
    import sys
    sys.path.insert(0,str(ROOT/'scripts/资源库'))
    from 统一资源 import available
    if available():
        from 兼容维护 import check_runtime
        check_runtime(sync_legacy=True)
        return
    manifest=json.loads((OUT/'资源清单.json').read_text('utf-8'))
    routes=json.loads((OUT/'私有路由.json').read_text('utf-8'))
    inputs=json.loads((OUT/'输入核验.json').read_text('utf-8'))
    revision,row=query('model:US_A10');source=Path(row['local_paths']['model_url']);digest=sha(source)
    if digest!=row['sha256'] or row['quality_status']!='passed':raise ValueError('A-10C输入身份或静态状态不符')
    doc,binary=read_glb(source)
    bombs=[n['name'] for n in doc['nodes'] if 'Mk84' in n.get('name','')]
    if not bombs:raise ValueError('当前装配没有Mk84挂载节点，不能按既定方案派生')
    triangles=lambda d:sum(d['accessors'][p['indices']]['count']//3 for m in d['meshes'] for p in m['primitives'])
    before=triangles(doc);images={};sizes=[]
    for im in doc.get('images',[]):
        v=doc['bufferViews'][im['bufferView']];data=bytes(binary[v.get('byteOffset',0):v.get('byteOffset',0)+v['byteLength']])
        with Image.open(io.BytesIO(data)) as picture:
            old=list(picture.size);picture.thumbnail((1024,1024),Image.Resampling.LANCZOS)
            buffer=io.BytesIO();picture.save(buffer,format='PNG',optimize=True);images[im['bufferView']]=buffer.getvalue()
            im['mimeType']='image/png';sizes.append({'before':old,'after':list(picture.size)})
    compact=bytearray()
    for i,v in enumerate(doc['bufferViews']):
        data=images.get(i,bytes(binary[v.get('byteOffset',0):v.get('byteOffset',0)+v['byteLength']]))
        compact.extend(b'\0'*(-len(compact)%4));v['byteOffset']=len(compact);v['byteLength']=len(data);compact.extend(data)
    doc['asset']['generator']='城市靶场A-10C：仅1024纹理派生，程序飞行，原件只读'
    output=OUT/'plane.glb';write_glb(output,doc,compact);after,_=read_glb(output)
    if triangles(after)!=before or sha(source)!=digest:raise ValueError('原件或三角形守恒失败')
    def register(path):
        path=Path(path).resolve()
        if not path.is_relative_to(OUT.parents[1].resolve()):raise ValueError('媒体必须位于项目local-data')
        fingerprint=sha(path);url='/media/'+fingerprint+path.suffix
        routes[url]={'path':str(path),'sha256':fingerprint,'bytes':path.stat().st_size};return url
    manifest['models']['plane']={'id':'model:US_A10','url':register(output),'source_sha256':digest,'bytes':output.stat().st_size,
        'detail':{'triangles_before':before,'triangles_after':before,'textures':sizes,'bomb_nodes':bombs,'source_animations':len(doc.get('animations',[])),'front':'-Z','up':'+Y'}}
    inputs=[r for r in inputs if r['id']!='model:US_A10']
    inputs.append({'id':'model:US_A10','revision':revision,'path':str(source),'sha256':digest,'bytes':source.stat().st_size})
    for key,name in SOUNDS.items():
        matches=[]
        for identity in row['audio_ids']:
            rev,a=query(identity)
            if a['name']==name:matches.append((rev,a))
        if len(matches)!=1:raise ValueError('声音匹配不唯一：'+name)
        rev,a=matches[0]
        if 'model:US_A10' not in a['model_ids']:raise ValueError('声音未关联A-10C')
        url=register(a['local_paths']['url'])
        if routes[url]['sha256']!=a['sha256']:raise ValueError('声音摘要变化')
        c=a['check'];manifest['audio'][key]={'audio_id':a['id'],'url':url,'name':name,'sha256':a['sha256'],
            'duration_seconds':c['duration_seconds'],'sample_rate':c['sample_rate'],'channels':c['channels'],
            'bindings':[b for b in a['bindings'] if b['model_id']=='model:US_A10']}
        if not any(r['id']==a['id'] for r in inputs):inputs.append({'id':a['id'],'revision':rev,**routes[url]})
    # 旧派生媒体路由只移除不再被当前清单引用的登记，不删除实体文件。
    used={a['url'] for a in [*manifest['models'].values(),*manifest['audio'].values()]};routes={k:v for k,v in routes.items() if k in used}
    for name,value in [('资源清单.json',manifest),('私有路由.json',routes),('输入核验.json',inputs)]:
        (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    check();print(json.dumps({'model':'model:US_A10','triangles':before,'source_bytes':source.stat().st_size,'derived_bytes':output.stat().st_size,'bomb_nodes':bombs,'audio_added':list(SOUNDS)},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--prepare',action='store_true');args=parser.parse_args()
    if args.prepare:prepare()
    else:check()
