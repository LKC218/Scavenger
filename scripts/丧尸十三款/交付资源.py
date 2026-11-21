"""生成冻结入库清单或原子写入资源交接；只由资源执行对话调用。"""
import argparse
import copy
from datetime import datetime,timezone
from pathlib import Path
import os
import sys
import uuid

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts/资源库'))
from 统一资源 import HOME,current,read,record_path,sha,save


def file_record(path):
    path=Path(path).resolve();return {'path':str(path),'bytes':path.stat().st_size,'sha256':sha(path)}


def prepare(batch):
    browser=read(batch/'浏览器验证核验.json');blender=read(batch/'Blender回读.json')
    assert browser['passed'] and blender['passed'] and blender['count']==12
    assert len(browser['browsers'])==2 and all(len(b['roles'])==26 and all(r['technical_passed'] for r in b['roles']) for b in browser['browsers'])
    pointer,catalog,routes,index=current();base=next(m for m in catalog['models'] if m['id']=='model:zombie-runtime');models=[]
    for i in range(1,13):
        folder=batch/f'角色{i:02}';j=read(folder/'制作回执.json');d=read(folder/'派生回执.json');rb=read(folder/'回读回执.json')
        assert rb['passed'] and j['source_unchanged'] and j['split_validation']['passed']
        assert all(next(x for x in b['roles'] if x['id']==j['id'] and x['resolution']==v['resolution'])['sha256']==v['sha256'] for b in browser['browsers'] for v in d['variants'])
        card=copy.deepcopy(base)
        card.update(id=j['id'],source_id=j['id'].split(':')[1],name=f'丧尸角色{i:02}',aliases=['丧尸','zombie',f'角色{i:02}'],variant='独立骨架与项目自制循环',geometry={'triangles':j['triangles'],'texture_max':256,'profile':'十三款独立运行'},limitations=['技术核验通过，人工外观与动作待工程师验收','项目自制循环，无足部IK或真实运动捕捉'],extensions={'asset_library':'统一资源库','source_mesh_index':i,'bones':j['bones'],'animation_source':'项目自制'})
        evidence=[file_record(folder/name) for name in ['制作回执.json','派生回执.json','回读回执.json']]+[file_record(batch/'浏览器验证核验.json')]
        models.append({'id':j['id'],'human_acceptance':'待工程师验收','card':card,'variants':d['variants'],'blend':j['blend'],'source':j['asset'],'preview':file_record(batch/f'浏览器验证/chrome/角色{i:02}-256/正面.png'),'evidence':evidence,'provenance':{'original_sha256':j['source_sha256'],'source_mesh_index':i,'source_mesh_name':j['source_mesh_name'],'source_node':j['source_node'],'source_materials':j['source_materials'],'actions_source':j['actions_source']}})
    result={'schema':1,'batch_id':'ZOM-13-261103-003','technical_passed':True,'expected_revision':pointer['revision'],'expected_pointer_sha256':sha(HOME/'当前版本.json'),'models':models}
    target=batch/'入库冻结清单.json'
    if target.exists():raise ValueError('冻结清单已存在，拒绝覆盖')
    save(target,result);print({'manifest':str(target),'sha256':sha(target),'models':len(models),'revision':pointer['revision']})


def freeze(batch):
    p,c,r,index=current();registry=read(index/'模型登记.json');masters=read(index/'主工程.json');browser=read(batch/'浏览器验证核验.json');models=[]
    assert read(batch/'编号零主工程回读.json')['passed']
    for i in range(13):
        identity='model:zombie-runtime' if i==0 else f'model:zombie-{i:02}'
        card=next(m for m in c['models'] if m['id']==identity)
        if i:
            folder=batch/f'角色{i:02}';j=read(folder/'制作回执.json');evidence=[file_record(folder/name) for name in ['制作回执.json','派生回执.json','回读回执.json']]
            scale=j['scale'];bones=j['bones'];triangles=j['triangles'];materials=j['materials'];source_materials=j['source_materials']
        else:
            scale={'unit':'米','up':'+Y','forward':'+Z','rest_height_m':1.75,'origin':'静置脚底中心'};bones=23;triangles=800;materials=1;source_materials=['Cartoon_zombie3_Bake'];evidence=[file_record(batch/'编号零主工程回读.json')]
        evidence.append(file_record(batch/'浏览器验证核验.json'))
        variants=[]
        for v in card['runtime_variants']:
            file=record_path(r[v['url']]);assert sha(file)==v['sha256'];variants.append({'resolution':v['resolution'],**file_record(file)})
        checks=[x for b in browser['browsers'] for x in b['roles'] if x['id']==identity]
        assert len(checks)==4 and all(x['technical_passed'] for x in checks)
        models.append({'id':identity,'source_mesh_index':i,'source_mesh_name':str(i),'source_node':f'Object_{7+2*i}','source_materials':source_materials,'source_sha256':'19523156c1b229d2720e72eb6a9400488f6a793eb14a62fd49a09dec38ace7ea','runtime_source_sha256':card['source_sha256'],'bones':bones,'materials':materials,'triangles':triangles,'clips':[{'name':'待机','duration_s':2.0},{'name':'移动','duration_s':1.25}],'animation_source':'本项目自制循环，非游戏原生行走动作','coordinate_system':scale,'nominal_move_speed_m_s':1.2,'variants':variants,'blend':{**masters[identity],'path':str(record_path(masters[identity]))},'technical_passed':True,'technical_receipts':evidence,'human_acceptance':'待工程师验收'})
    result={'schema':1,'batch_id':'ZOM-13-261103-003','frozen_at':datetime.now(timezone.utc).isoformat(),'source_sha256':'19523156c1b229d2720e72eb6a9400488f6a793eb14a62fd49a09dec38ace7ea','library_revision':p['revision'],'library_pointer_sha256':sha(HOME/'当前版本.json'),'catalog_sha256':sha(index/'资源索引.json'),'technical_passed':True,'models':models,'limits':['人工外观和动作待工程师验收','无足部IK、真实步态或设备性能验收','编号0保持既有KTX2双档；新增12款最终双档均已Blender回读'],'author':'Jungle Jim','license':'CC BY 4.0','source_url':'https://sketchfab.com/3d-models/super-low-poly-stylized-zombies-animated-6c112bed707b4444864b606299f8b85a'}
    target=ROOT/'local-data/丧尸模型/十三款优化/交接/当前交付.json'
    if target.exists():raise ValueError('有效交付已存在，更新前须协调样本身份')
    temp=target.with_name('交付-'+uuid.uuid4().hex+'.writing.json');save(temp,result);os.replace(temp,target)
    save(batch/'交付身份.json',file_record(target));print({'delivery':str(target),'sha256':sha(target),'models':13,'library_revision':p['revision']})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','freeze']);p.add_argument('--batch',type=Path,required=True);a=p.parse_args();batch=a.batch.resolve()
    if not batch.is_relative_to(ROOT/'local-data/丧尸模型/十三款优化'):raise ValueError('批次越界')
    (prepare if a.action=='prepare' else freeze)(batch)
