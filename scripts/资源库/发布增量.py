"""通过冻结技术清单增量发布（12款模型或36条载具循环音效）；预期指针、排他锁和原子切换，不删除旧版。"""
import argparse
from collections import Counter
import copy
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
import uuid

from 统一资源 import ROOT,HOME,sha,read,save
sys.path.insert(0,str(ROOT/'scripts/维护清理'))
from 清理工具 import lock

AUDIO_ID_PATTERN=re.compile(r'^audio:[A-Za-z0-9_.:-]+$')


def _load_locked_snapshot(home,expected_revision,expected_pointer_sha):
    pointer_file=home/'当前版本.json'
    pointer=read(pointer_file)
    if pointer['revision']!=expected_revision or sha(pointer_file)!=expected_pointer_sha:
        raise ValueError('当前指针已变化，拒绝发布')
    base=(home/pointer['directory']).resolve()
    if not base.is_relative_to(home):raise ValueError('索引路径越界')
    data={}
    for name,digest in pointer['metadata'].items():
        if Path(name).name!=name or sha(base/name)!=digest:raise ValueError('当前元数据变化')
        data[name]=read(base/name)
    return pointer_file,pointer,base,data


def _commit_snapshot(home,pointer_file,pointer,base,data,changed_names,expected_pointer_sha,revision,suffix=''):
    directory='索引版本/'+f'{revision:03}-'+(suffix+'-' if suffix else '')+uuid.uuid4().hex[:12]
    target=home/directory;target.mkdir(parents=True,exist_ok=False)
    for name in pointer['metadata']:
        if name in changed_names:save(target/name,data[name])
        else:shutil.copyfile(base/name,target/name)
    new_pointer={**pointer,'revision':revision,'directory':directory,'metadata':{name:sha(target/name) for name in pointer['metadata']}}
    # 重新复核期望身份，防止不遵守锁的外部写入方被覆盖。
    if sha(pointer_file)!=expected_pointer_sha:raise ValueError('提交前指针变化')
    staged=home/('当前版本.'+uuid.uuid4().hex+'.writing.json');save(staged,new_pointer)
    with staged.open('r+b') as f:os.fsync(f.fileno())
    os.replace(staged,pointer_file)
    return new_pointer


def _retain_file(root,home,routes,src_path,digest,target_rel_path):
    p=Path(src_path);target=home/target_rel_path;target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if sha(target)!=digest:raise ValueError('内容地址冲突')
    else:
        # 独占创建，发布之间不覆盖文件。
        with p.open('rb') as source,target.open('xb') as dest:shutil.copyfileobj(source,dest)
    if sha(target)!=digest:raise ValueError('复制摘要不符')
    r={'path':target.relative_to(root).as_posix(),'bytes':target.stat().st_size,'sha256':digest}
    url='/media/'+digest+target.suffix.lower()
    routes[url]=r
    return r,url


def _refresh_catalog_summary(catalog):
    models=catalog['models'];audio=catalog['audio'];scenes=catalog.get('scenes',[])
    catalog['summary']={
        'models':len(models),
        'scenes':len(scenes),
        'audio':len(audio),
        'models_with_audio':sum(bool(m.get('audio_ids')) for m in models),
        'associated_audio':sum(bool(a.get('model_ids')) for a in audio),
        'unassigned_audio':sum(not a.get('model_ids') for a in audio),
        'model_statuses':dict(Counter(m['quality_status'] for m in models)),
        'audio_statuses':dict(Counter(a['quality_status'] for a in audio)),
    }


def publish(manifest,expected_revision,expected_pointer_sha,root=ROOT,home=HOME,guard=lock):
    """guard可注入隔离锁用于事务测试；正式入口固定项目维护锁。"""
    root,home=Path(root).resolve(),Path(home).resolve()
    with guard():
        pointer_file,pointer,base,data=_load_locked_snapshot(home,expected_revision,expected_pointer_sha)
        if not manifest['technical_passed'] or len(manifest['models'])!=12:raise ValueError('需要12款通过技术核验的冻结清单')
        existing={m['id'] for m in data['资源索引.json']['models']}
        incoming={m['id'] for m in manifest['models']}
        if len(incoming)!=12 or existing&incoming:raise ValueError('稳定ID重复，拒绝新增')
        # 先核对每份输入和证据；复制阶段失败亦不会触碰旧指针。
        for role in manifest['models']:
            if role['human_acceptance']!='待工程师验收' or role['card']['quality_status']!='pending' or role['card']['id']!=role['id']:raise ValueError('人工验收或稳定ID不符合冻结契约')
            if {v['resolution'] for v in role['variants']}!={256,512}:raise ValueError('双档不完整')
            for row in [*role['variants'],role['blend'],role['source'],role['preview'],*role['evidence']]:
                p=Path(row['path']).resolve()
                if not p.is_relative_to(root/'local-data') or p.is_symlink():raise ValueError('输入路径越界或链接')
                if not p.is_file() or p.stat().st_size!=row['bytes'] or sha(p)!=row['sha256']:raise ValueError('冻结输入摘要不符：'+str(p))
        catalog=data['资源索引.json'];routes=data['资源路由.json'];registry=data['模型登记.json'];masters=data['主工程.json'];replacements=data['运行替换.json']
        new_ids=[]
        for role in manifest['models']:
            variants=[]
            for v in role['variants']:
                r,url=_retain_file(root,home,routes,v['path'],v['sha256'],Path('当前资源/模型')/(v['sha256']+Path(v['path']).suffix.lower()))
                variants.append({**v,**r,'url':url})
            blend,blend_url=_retain_file(root,home,routes,role['blend']['path'],role['blend']['sha256'],Path('当前资源/主工程')/(role['blend']['sha256']+Path(role['blend']['path']).suffix.lower()))
            preview,preview_url=_retain_file(root,home,routes,role['preview']['path'],role['preview']['sha256'],Path('当前资源/预览')/(role['preview']['sha256']+Path(role['preview']['path']).suffix.lower()))
            source,_=_retain_file(root,home,routes,role['source']['path'],role['source']['sha256'],Path('当前资源/装配源')/(role['source']['sha256']+Path(role['source']['path']).suffix.lower()))
            chosen=next(v for v in variants if v['resolution']==256)
            card=copy.deepcopy(role['card']);card.update(model_url=chosen['url'],sha256=chosen['sha256'],bytes=chosen['bytes'],blend_url=blend_url,preview_url=preview_url,source_sha256=source['sha256'],runtime_variants=[{k:v[k] for k in ['resolution','level','url','sha256','bytes','triangles','before_triangles']} for v in variants])
            catalog['models'].append(card);masters[role['id']]=blend;masters[role['id']+':source']=source
            registry[source['sha256']]={'id':role['id'],'name':card['name'],'source_sha256':source['sha256'],'kind':'model','variants':variants,'default':chosen['sha256'],'provenance':role['provenance'],'technical_evidence':role['evidence']}
            replacements[source['sha256']]={**chosen,'kind':'model','source_sha256':source['sha256']}
            data['来源别名.json'][str(Path(role['source']['path'])).replace('\\','/')]=source['sha256']
            data['维护输入.json']['models'].append({'source_sha256':source['sha256'],'id':role['id'],'source':source})
            new_ids.append(role['id'])
        revision=expected_revision+1
        catalog.update(revision=revision,updated_at=time.time())
        catalog['summary'].update(models=len(catalog['models']),model_statuses=dict(Counter(m['quality_status'] for m in catalog['models'])))
        # 除模型相关索引外的JSON仍按原文件字节复制，包括音频与靶场清单。
        changed={'资源索引.json','资源路由.json','模型登记.json','主工程.json','运行替换.json','来源别名.json','维护输入.json','保留策略.json'}
        data['保留策略.json']['masters']=len(masters)
        new_pointer=_commit_snapshot(home,pointer_file,pointer,base,data,changed,expected_pointer_sha,revision)
        return {'passed':True,'previous_pointer':pointer,'previous_pointer_sha256':expected_pointer_sha,'pointer':new_pointer,'pointer_sha256':sha(pointer_file),'models_added':new_ids,'summary':catalog['summary'],'manifest_sha256':manifest.get('manifest_sha256'),'deleted':0}


def publish_audio_loops(report,expected_revision,expected_pointer_sha,report_dir=None,root=ROOT,home=HOME,guard=lock):
    """增量发布36条全检通过的derived_loop V2载具引擎循环音并绑定129辆载具（不覆盖既有ID与原音）。"""
    root,home=Path(root).resolve(),Path(home).resolve()
    with guard():
        pointer_file,pointer,base,data=_load_locked_snapshot(home,expected_revision,expected_pointer_sha)
        summary_in=report.get('summary') or {}
        loops=report.get('loops') or []
        vmap=report.get('vehicle_loop_map') or {}
        if not summary_in.get('all_checks_passed') or not loops or not vmap:
            raise ValueError('音频循环质检报告未全部通过或缺少映射数据')

        catalog=data['资源索引.json'];routes=data['资源路由.json']
        models_by_id={m['id']:m for m in catalog['models']}
        existing_audio_ids={a['id'] for a in catalog['audio']}

        # 先核对全部待入库文件及模型绑定存在性
        resolved_files={}
        incoming_ids=set()
        for rec in loops:
            qa=rec.get('qa') or {}
            if not qa.get('passed') or qa.get('clip_samples',-1)!=0 or qa.get('peak',99)>=0.85:
                raise ValueError('循环音频质检指标未达标：'+str(rec.get('source_name')))
            raw_path=rec.get('loop_abs_path') or (str(Path(report_dir)/rec['loop_file']) if report_dir else rec.get('loop_file'))
            p=Path(raw_path).resolve()
            if not p.is_relative_to(root/'local-data') or p.is_symlink():
                raise ValueError('音频路径越界或软链接：'+str(p))
            if not p.is_file() or p.stat().st_size!=rec['bytes'] or sha(p)!=rec['sha256']:
                raise ValueError('音频输入摘要不符：'+str(p))
            src_id=rec['source_audio_id']
            short_hash=src_id.split(':')[1].split('-')[0]
            clean_stem=re.sub(r'[^A-Za-z0-9._-]+','_',str(rec.get('source_name','engine'))).strip('._-') or 'engine'
            new_id=f'audio:loop:v2:{clean_stem}_{short_hash}'
            if not AUDIO_ID_PATTERN.fullmatch(new_id) or new_id in existing_audio_ids or new_id in incoming_ids:
                raise ValueError('循环音频ID无效或已存在：'+new_id)
            incoming_ids.add(new_id)
            resolved_files[src_id]={'path':p,'new_id':new_id,'clean_stem':clean_stem,'short_hash':short_hash,'rec':rec}

        vehicles_by_source={}
        for slug,row in sorted(vmap.items()):
            mid=row['model_id']
            if mid not in models_by_id:
                raise ValueError(f'统一库缺少载具模型：{slug} ({mid})')
            src_id=row['source_audio_id']
            if src_id not in resolved_files:
                raise ValueError(f'载具引用了未入库的循环源：{slug} -> {src_id}')
            vehicles_by_source.setdefault(src_id,[]).append(row)

        added_ids=[]
        for src_id,meta in resolved_files.items():
            rec=meta['rec'];new_id=meta['new_id']
            rel_target=Path('当前资源/音效/引擎循环')/f"循环V2-{meta['clean_stem']}-{meta['short_hash']}-{rec['sha256'][:10]}.ogg"
            retained,url=_retain_file(root,home,routes,meta['path'],rec['sha256'],rel_target)
            bound_rows=vehicles_by_source.get(src_id,[])
            model_ids=[];bindings=[]
            for brow in bound_rows:
                mid=brow['model_id']
                if mid not in model_ids:model_ids.append(mid)
                ev=(f"同系映射（{brow['fallback_reason']}）" if brow.get('mapping_mode')=='sibling_fallback'
                    else f"由正式库确证 MoveEvent ({src_id}) 离线派生 120ms 等功率交叉淡化循环")
                bindings.append({'model_id':mid,'field':'MoveEvent','evidence':ev})
                m_card=models_by_id[mid]
                if new_id not in m_card.setdefault('audio_ids',[]):m_card['audio_ids'].append(new_id)

            catalog['audio'].append({
                'id':new_id,
                'type':'audio',
                'name':f"{rec['source_name']}（无缝循环 V2）",
                'aliases':[rec['source_name'],src_id,*([rec['legacy_loop_alias_id']] if rec.get('legacy_loop_alias_id') else [])],
                'category':'引擎循环',
                'tags':['循环','derived_loop_v2',rec.get('source_category','引擎')],
                'quality_status':'ready',
                'issue':'项目等功率交叉淡化派生循环（V2），自动化解码全检通过，实机听感待确认',
                'event_guid':'',
                'event_path':'',
                'banks':[],
                'url':url,
                'sha256':retained['sha256'],
                'bytes':retained['bytes'],
                'source_sha256':rec.get('source_catalog_sha256') or retained['sha256'],
                'source_audio_id':src_id,
                'format':'OGG',
                'kind':'derived_loop',
                'check':{
                    'duration_seconds':rec['duration'],
                    'channels':1,
                    'sample_rate':rec['sample_rate'],
                    'rms':rec['rms'],
                    'peak':rec['peak'],
                    'clip_samples':rec['qa']['clip_samples'],
                    'seam_100ms_rms_delta_db':rec['qa']['seam_100ms_rms_delta_db'],
                },
                'render_profile':{},
                'model_ids':model_ids,
                'bindings':bindings,
                'source':{
                    'derivation':'120ms 等功率交叉淡化，48kHz 单声道 OGG Opus 96k，RMS 约 0.12，峰值 < 0.85',
                    'source_audio_id':src_id,
                    'source_wav_sha256':rec.get('source_wav_sha256',''),
                },
                'extensions':{'asset_library':'统一资源库','loop_version':'v2'},
            })
            added_ids.append(new_id)

        revision=expected_revision+1
        catalog.update(revision=revision,updated_at=time.time())
        _refresh_catalog_summary(catalog)
        changed={'资源索引.json','资源路由.json'}
        new_pointer=_commit_snapshot(home,pointer_file,pointer,base,data,changed,expected_pointer_sha,revision,suffix='audio-loops')
        return {
            'passed':True,
            'previous_pointer':pointer,
            'previous_pointer_sha256':expected_pointer_sha,
            'pointer':new_pointer,
            'pointer_sha256':sha(pointer_file),
            'audio_added':added_ids,
            'audio_added_count':len(added_ids),
            'vehicles_mapped':len(vmap),
            'summary':catalog['summary'],
            'report_sha256':report.get('report_sha256'),
            'deleted':0,
        }


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path)
    p.add_argument('--audio-loops-report',type=Path,help='129车循环映射与质检报告.json路径')
    p.add_argument('--expected-revision',type=int,required=True)
    p.add_argument('--expected-pointer-sha',required=True)
    a=p.parse_args()
    if bool(a.manifest)==bool(a.audio_loops_report):
        p.error('必须且仅指定 --manifest 或 --audio-loops-report 之一')
    if a.manifest:
        manifest=read(a.manifest);manifest['manifest_sha256']=sha(a.manifest)
        result=publish(manifest,a.expected_revision,a.expected_pointer_sha)
    else:
        report=read(a.audio_loops_report);report['report_sha256']=sha(a.audio_loops_report)
        result=publish_audio_loops(report,a.expected_revision,a.expected_pointer_sha,report_dir=a.audio_loops_report.parent)
    save(HOME/'验收'/f"增量发布-{result['pointer']['revision']:03}.json",result);print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
