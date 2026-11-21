"""核对真实来源、产物和通过凭证，绑定不可变技术交付；不发布、不升级人工状态。"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import time

from 地图执行记录 import ROOT, atomic_json, fingerprint
sys.path.insert(0,str(ROOT/'scripts/资源库'))
from 发布场景 import validate,acceptance_subject,sha_manifest,glb_dependencies


def require(condition,message):
    if not condition:raise ValueError(message)


def run(args):
    directory=args.directory.resolve();directory.relative_to(ROOT/'local-data/断箭/批次')
    manifest_path=args.manifest.resolve();manifest_path.relative_to(directory)
    manifest=copy.deepcopy(json.loads(manifest_path.read_text('utf-8')))
    validate(manifest)
    files=manifest['files'];evidence=list(manifest['acceptance']['evidence']);checks=[]
    def add(path,proof=False):
        path=path.resolve();key=path.relative_to(directory).as_posix();fp=fingerprint(path)
        if key in files:require(all(files[key][k]==fp[k] for k in ['bytes','sha256']),'冻结文件变化：'+key)
        else:files[key]={'bytes':fp['bytes'],'sha256':fp['sha256'],'dependencies':sorted(glb_dependencies(path,key)) if path.suffix=='.glb' else []}
        if proof and key not in evidence:evidence.append(key)
        return key
    def read(rel,label=None,test=None):
        path=directory/rel;data=json.loads(path.read_text('utf-8'))
        if test is not None:require(test(data),'凭证未通过：'+(label or rel))
        add(path,True)
        if label:checks.append({'name':label,'passed':True,'file':rel})
        return data
    def add_folder(path):
        for file in sorted(path.rglob('*')):
            if file.is_file() and file.suffix in ['.json','.png','.webm'] and not any(word in file.name for word in ['进行中','失败','.writing']):add(file)

    scope=read('范围冻结/V001-r02/范围与条目.json','来源范围',lambda d:d['source_bounds_xz']==[0,9000,0,9000])
    require(scope['counts']=={'bank_source':112437,'bank_required':93008,'trees_source':467331,'trees_required':343234,'direct_meshes_source':149,'direct_meshes_required':127,'prototypes':133},'冻结来源数量变化')
    require(len(manifest['assets'])==133 and len(set(manifest['scene']['asset_ids']))==133,'原型集合不完整')
    asset_ai=read('独立原型/full-r06/AI视觉复核-r01.json','全部原型四向AI',lambda d:d['ai_visual_status']=='passed' and len(d['assets'])==133)
    ai_by_id={row['asset_id']:row for row in asset_ai['assets']}
    for asset in manifest['assets']:
        row=ai_by_id[asset['id']]
        require(row['ai_visual_status']=='passed' and len(row['views'])==4,'原型四向未通过')
        for role,label in [('runtime','glb'),('blend','blend')]:
            require(files[asset['files'][role]]['sha256']==row[label]['sha256'],'原型与实际审图对象不同：'+asset['id'])
    readback=read('独立原型/full-r06/readback-汇总.json','全部原型独立回读',lambda d:len(d)==133 and all(x['status']=='工程及GLB独立回读通过' and x['glb_bounds_error_m']<=.001 for x in d))
    require(sum(x['source_triangles'] for x in readback)==2357969 and sum(x['triangles'] for x in readback)==2276422,'原型面数或派生政策变化')
    read('阶段验收/A1-r02.json','A1阶段',lambda d:d['technical_status']=='passed' and d['ai_visual_status']=='passed' and d['assets']==133)
    read('实例分页/r01/独立核验/r02/核验凭证.json','完整实例分页',lambda d:d['passed'] and d['instances']==436242 and not d['duplicate_sources'] and not d['missing_cross_references'])
    read('碰撞分页/r01/独立核验/r02/核验凭证.json','全部碰撞分页',lambda d:d['passed'] and d['unique_shapes']==199649)
    read('模拟分区/r01/独立核验/r01/核验凭证.json','模拟分区闭合',lambda d:d['passed'] and d['cells']==5184 and d['packages']==1296)
    height=read('全图分块/r02/支撑核验/r02/独立BVH凭证.json','独立原网格高度',lambda d:d['passed'] and d['samples']==60237 and d['max_error_m']<=.02 and not d['failures'])
    read('全图分块/r02/射线核验/r02/核验凭证.json','真实三角形射线',lambda d:d['passed'] and d['real_samples']==46656 and d['max_error_m']<=.001)
    encoded=read('全图运行/r01/压缩核验.json','全部运行几何与依赖回读',lambda d:len(d['files'])==5313 and all(x['geometry_exact'] and x['external_roundtrip'] for x in d['files']))
    master=read('总装配/r03/独立核验/r01/核验凭证.json','完整母版矩阵及部件',lambda d:d['passed'] and d['max_matrix_error']<=.001 and d['surface_parts']==127 and d['actual_prototype_mesh_parts']==156)
    require(files[manifest['scene']['files']['blend']]['sha256']==master['blend_identity']['sha256'],'母版不是已重开对象')
    projects=read('依赖收集/full-blend-r01/独立回读/r01/核验凭证.json','134份工程闭包独立重开',lambda d:d['passed'] and d['files']==134 and len(d['projects'])==134)
    collection=read('依赖收集/full-blend-r01/依赖收集凭证.json')
    for key,row in collection['files'].items():require(key in files and all(files[key][k]==row[k] for k in ['bytes','sha256']),'工程闭包不再是已回读文件')
    projection=read('运行接入/r11/投影正式验证/r01/chrome/核验凭证.json','119种中远景四向轮廓',lambda d:d['passed'] and len(d['assets'])==119 and not d['errors'] and all(len(x['rows'])==8 and all(y['passed'] and y['error_pixels']<=2 for y in x['rows']) for x in d['assets']))

    queue_path=args.queue.resolve();queue_path.relative_to(directory)
    queue=read(queue_path.relative_to(directory).as_posix(),'当前冻结版本技术队列',lambda d:d['passed'] and len(d['completed'])==13)
    runtime=ROOT/queue['identity']['directory'];runtime.resolve().relative_to(directory)
    runtime_file=runtime/'地图运行.json';runtime_hash=fingerprint(runtime_file)['sha256']
    require(runtime_hash==queue['identity']['map_sha256'],'运行清单与回归队列不同')
    data=json.loads(runtime_file.read_text('utf-8'));add(runtime_file,True)
    compact=json.loads((directory/manifest['scene']['files']['manifest']).read_text('utf-8'))
    require(compact['source_runtime']['sha256']==runtime_hash,'候选不是当前运行清单派生')
    require(data['bounds']==scope['runtime_bounds'] and len(data['chunks'])==5184 and not data['grass_enabled'],'范围、区块或草丛政策不同')
    require(data.get('water') in data['files'] and len(data.get('water_layer',{}).get('source_keys',[]))==2,'两处独立水面缺失')
    projection_data=read('运行接入/r11/地图运行.json')
    old_prototypes={row['id']:row for row in projection_data['prototypes']}
    for row in data['prototypes']:
        if row['category'] not in ['building','decor']:continue
        for role in ['file','middle_file','far_file']:
            require(data['files'][row[role]]['sha256']==projection_data['files'][old_prototypes[row['id']][role]]['sha256'],'投影核验模型已变化')
    water_path=Path(data['water']).parent/'水面核验.json'
    water=read(water_path.as_posix(),'两处水面严格回读',lambda d:d['passed'] and d['triangles']==10417 and len(set(d['source_keys']))==2)
    require(files[data['water']]['sha256']==water['assets']['水面.glb']['sha256'],'水面与回读对象不同')
    service_file=runtime/('浏览器源码-'+queue['identity']['revision'])/'服务身份.json'
    service=json.loads(service_file.read_text('utf-8'));add_folder(service_file.parent)
    for key,row in files.items():
        if '/运行源码/地图验证/' in key:
            name=Path(key).name
            require(row['sha256']==service['sources'][name]['source_sha256'],'候选与主验地图代码不同：'+name)
    required_names={'功能与故障-'+b for b in ['chrome','msedge']}|{'平移缩放稳定-'+b for b in ['chrome','msedge']}|{'跨层资源-'+b for b in ['chrome','msedge']}|{'六项正式负载-'+b for b in ['chrome','msedge']}|{'五圈180秒回访-'+b for b in ['chrome','msedge']}|{'三冷三温限速-'+b for b in ['chrome','msedge']}|{'81格双视角采集'}
    require({x['name'] for x in queue['completed']}==required_names,'回归条目缺失或重复')
    summaries={}
    for item in queue['completed']:
        path=directory/item['proof'];require(fingerprint(path)['sha256']==item['sha256'],'队列通过凭证变化')
        proof=read(item['proof'],item['name'],lambda d:d.get('passed',d.get('technical_passed')) is True)
        add_folder(path.parent)
        if 'identity' in proof:require(proof['identity']['map_sha256']==runtime_hash,'凭证来自其他运行清单')
        if item['name'].startswith('六项正式负载'):
            require(proof['matrix_complete'] and len(proof['samples'])==6 and not proof['errors'],'六负载不完整')
            for sample in proof['samples']:
                raw=json.loads((path.parent/(sample['name']+'.json')).read_text('utf-8'))
                require(raw['formal'] and raw['performance_passed'] and raw['p95']<=16.67 and raw['p99']<=25 and raw['max']<=100 and raw['average_fps']>=60 and raw['long_frame_ratio']<.001,'正式帧时间未达标')
                require(raw['final']['masterVolume']==.25 and raw['final']['enhancementDb']==6,'声音规格不同')
                if sample['name']=='百单位全程同屏':require(raw['minimum_visible_units']==100 and raw['duration']>=180,'百单位没有完整同屏180秒')
            summaries[item['name']]=proof['samples']
        elif item['name'].startswith('五圈180秒回访'):
            require(proof['formal'] and len(proof['loops'])==5 and all(proof['growth_checks'].values()) and not proof['errors'],'长回访未通过')
            for n in range(1,6):
                raw=json.loads((path.parent/f'第{n}圈.json').read_text('utf-8'));require(all(x['misses']==0 and x['checked']>=9 for x in raw['timeline']),'回访存在非源空洞')
            summaries[item['name']]={'growth_checks':proof['growth_checks'],'loops':[{'loop':x['loop'],'geometries':x['final']['geometries'],'textures':x['final']['textures'],'jsHeap':x['final']['jsHeap']} for x in proof['loops']]}
        elif item['name'].startswith('三冷三温'):
            cold=[x for x in proof['samples'] if x['name'].startswith('冷启动-')];warm=[x for x in proof['samples'] if x['name'].startswith('温启动-')]
            require(len(cold)==len(warm)==3 and all(x['passed'] and x['operable_seconds']<=15 and x['actual_transfer_bytes']<=25*1048576 and x['map_actual_transfer_bytes']<=20*1048576 for x in cold) and all(x['passed'] and x['operable_seconds']<=3 for x in warm),'限速三冷三温未达标')
            summaries[item['name']]={'cold_s':[x['operable_seconds'] for x in cold],'warm_s':[x['operable_seconds'] for x in warm]}
        elif item['name'].startswith('功能与故障'):
            require(len(proof['cases'])==8 and not proof['unexpected_errors'] and all(x['passed'] for x in proof['cases']),'功能或独立故障未通过')
        elif item['name'].startswith('跨层资源'):require(len(proof['cycles'])==5 and all(proof['growth_checks'].values()) and not proof['errors'],'跨层释放未通过')
    macro_dir=runtime/'分区网页审图/final-r01'
    macro_ai=read((macro_dir/'AI审图凭证.json').relative_to(directory).as_posix(),'18页宏观AI审图',lambda d:d['passed_limited_scope'] and d['reviewed_contacts']==18)
    require(macro_ai['input_sha256']==fingerprint(macro_dir/'审图输入.json')['sha256'],'宏观观察输入变化')
    for row in macro_ai['contacts']:require(fingerprint(macro_dir/row['file'])['sha256']==row['sha256'],'宏观联系图变化')
    for row in json.loads((macro_dir/'审图输入.json').read_text('utf-8'))['views']:require(fingerprint(macro_dir/row['file'])['sha256']==row['sha256'],'宏观原图变化')
    near_dir=runtime/'近景网页审图/r01';near_ai=read((near_dir/'AI审图凭证.json').relative_to(directory).as_posix(),'四处正常近景AI审图',lambda d:d['passed_limited_scope'] and len(d['reviewed_images'])==4)
    require(near_ai['input_sha256']==fingerprint(near_dir/'审图输入.json')['sha256'],'近景输入变化');add_folder(near_dir)
    for row in near_ai['reviewed_images']:require(fingerprint(near_dir/row['file'])['sha256']==row['sha256'],'近景观察图片变化')
    library=read(args.library_proof.resolve().relative_to(directory).as_posix(),'最新候选双浏览器',lambda d:d['passed'] and len(d['browsers'])==2 and all(x['passed'] and not x['errors'] for x in d['browsers']))
    require(library['manifest']['sha256']==fingerprint(manifest_path)['sha256'],'库验证对象不是当前候选');add_folder(args.library_proof.parent)
    standalone=read(args.standalone_proof.resolve().relative_to(directory).as_posix(),'真实独立运行包',lambda d:d['passed'] and len(d['browsers'])==2 and all(x['passed'] and x.get('visible_water_meshes')==2 and not x['errors'] for x in d['browsers']))
    coll_path=args.runtime_collection/'依赖收集凭证.json';coll=read(coll_path.resolve().relative_to(directory).as_posix())
    require(standalone['receipt_sha256']==fingerprint(coll_path)['sha256'] and coll['package_sha256']==sha_manifest(json.loads(manifest_path.read_text('utf-8'))),'独立包不是当前候选闭包');add_folder(args.standalone_proof.parent)
    for key,row in coll['files'].items():require(key in files and all(files[key][k]==row[k] for k in ['bytes','sha256']),'收集包文件与候选不同')
    consumers=read(args.consumer_proof.resolve().relative_to(directory).as_posix(),'原有消费者实际回归',lambda d:d['passed'] and len(d['browsers'])==2 and all(x['passed'] and not x['errors'] and x['model_specs']==3 and x['playback'] and x['range']['hits']>0 and x['horde']['roles']==13 and x['horde']['animation_changed'] for x in d['browsers']))
    require(consumers['candidate_manifest']['sha256']==fingerprint(manifest_path)['sha256'],'消费者不是当前候选验证')
    require(consumers['source_program']['sha256']==fingerprint(ROOT/'scripts/断箭/验证场景消费者.py')['sha256'],'消费者验证器变化')
    add_folder(args.consumer_proof.parent)
    handoff_proof=read(args.handoff_proof.resolve().relative_to(directory).as_posix(),'发布和交付六项联动事务',lambda d:d['passed'] and len(d['cases'])==6 and all(x['passed'] for x in d['cases']))
    for name,row in handoff_proof['programs'].items():require(row['sha256']==fingerprint(ROOT/'scripts/断箭'/name)['sha256'],'交接验证源码变化')
    read('候选事务验证/binding-r02/验证结果.json','18项隔离事务',lambda d:d['passed'] and len(d['cases'])==18 and all(x['passed'] for x in d['cases']))
    output=directory/'候选入库'/args.revision;output.mkdir(parents=True,exist_ok=False)
    source_archive=output/'验收源码';source_archive.mkdir()
    for source in [Path(__file__),ROOT/'scripts/资源库/发布场景.py',ROOT/'scripts/资源库/验证场景发布.py',*[ROOT/'scripts/断箭'/name for name in ['验证场景消费者.py','发布全图交付.py','验证全图交接.py',*queue['programs']]]]:
        if source.name in queue['programs']:require(fingerprint(source)['sha256']==queue['programs'][source.name],'通过验证器与封存源码不同')
        target=source_archive/source.name;shutil.copy2(source,target);add(target,True)
    manifest['acceptance'].update({key:True for key in ['complete','modular','web','library','ai_visual']});manifest['acceptance']['human']='待人工验收'
    manifest['acceptance']['evidence']=evidence
    receipt_key=(output/'技术验收凭证.json').relative_to(directory).as_posix()
    subject=acceptance_subject(manifest,receipt_key)
    certificate={'schema':'scene-technical-acceptance-v1','passed':True,'subject_sha256':subject,'human_acceptance':'待人工验收',
        'gates':{key:True for key in ['complete','modular','web','library','ai_visual']},'created_epoch':time.time(),'checks':checks,'summaries':summaries,
        'scope':'当前冻结场景及133资产的本机技术和已声明范围AI视觉；不等于工程师验收、普通电脑或移动设备通过',
        'source_stage_status_policy':'来源运行清单的制作时标记保留；当前状态由本凭证和正式登记单列，不回写来源记录',
        'evidence':[{'file':key,'bytes':files[key]['bytes'],'sha256':files[key]['sha256']} for key in evidence]}
    atomic_json(directory/receipt_key,certificate);add(directory/receipt_key,True)
    manifest['acceptance']['binding']={'schema':1,'receipt':receipt_key,'subject_sha256':subject}
    validate(manifest,accepted=True)
    atomic_json(output/'场景入库清单.json',manifest)
    print(json.dumps({'passed':True,'files':len(files),'assets':len(manifest['assets']),'manifest':str(output/'场景入库清单.json'),'subject_sha256':subject},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['directory','manifest','queue','library-proof','standalone-proof','runtime-collection','consumer-proof','handoff-proof']:parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--revision',required=True);args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+',args.revision):raise ValueError('交付修订无效')
    run(args)
