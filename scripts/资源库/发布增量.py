"""通过冻结技术清单增量发布；预期指针、排他锁和原子切换，不删除旧版。"""
import argparse
from collections import Counter
import copy
import json
import os
from pathlib import Path
import shutil
import sys
import time
import uuid

from 统一资源 import ROOT,HOME,sha,read,save
sys.path.insert(0,str(ROOT/'scripts/维护清理'))
from 清理工具 import lock


def publish(manifest,expected_revision,expected_pointer_sha,root=ROOT,home=HOME,guard=lock):
    """guard可注入隔离锁用于事务测试；正式入口固定项目维护锁。"""
    root,home=Path(root).resolve(),Path(home).resolve()
    with guard():
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
        def retain(row,kind):
            p=Path(row['path']);target=home/'当前资源'/kind/(row['sha256']+p.suffix.lower());target.parent.mkdir(parents=True,exist_ok=True)
            if target.exists():
                if sha(target)!=row['sha256']:raise ValueError('内容地址冲突')
            else:
                # 独占创建，发布之间不覆盖文件。
                with p.open('rb') as source,target.open('xb') as dest:shutil.copyfileobj(source,dest)
            if sha(target)!=row['sha256']:raise ValueError('复制摘要不符')
            r={'path':target.relative_to(root).as_posix(),'sha256':row['sha256'],'bytes':target.stat().st_size}
            url='/media/'+row['sha256']+target.suffix;routes[url]=r
            return r,url
        for role in manifest['models']:
            variants=[]
            for v in role['variants']:
                r,url=retain(v,'模型');variants.append({**v,**r,'url':url})
            blend,blend_url=retain(role['blend'],'主工程');preview,preview_url=retain(role['preview'],'预览');source,_=retain(role['source'],'装配源')
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
        directory='索引版本/'+f'{revision:03}-'+uuid.uuid4().hex[:12];target=home/directory;target.mkdir(parents=True,exist_ok=False)
        for name in pointer['metadata']:
            if name in changed:save(target/name,data[name])
            else:shutil.copyfile(base/name,target/name)
        new_pointer={**pointer,'revision':revision,'directory':directory,'metadata':{name:sha(target/name) for name in pointer['metadata']}}
        # 重新复核期望身份，防止不遵守锁的外部写入方被覆盖。
        if sha(pointer_file)!=expected_pointer_sha:raise ValueError('提交前指针变化')
        staged=home/('当前版本.'+uuid.uuid4().hex+'.writing.json');save(staged,new_pointer)
        with staged.open('r+b') as f:os.fsync(f.fileno())
        os.replace(staged,pointer_file)
        return {'passed':True,'previous_pointer':pointer,'previous_pointer_sha256':expected_pointer_sha,'pointer':new_pointer,'pointer_sha256':sha(pointer_file),'models_added':new_ids,'summary':catalog['summary'],'manifest_sha256':manifest.get('manifest_sha256'),'deleted':0}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--manifest',type=Path,required=True);p.add_argument('--expected-revision',type=int,required=True);p.add_argument('--expected-pointer-sha',required=True);a=p.parse_args()
    manifest=read(a.manifest);manifest['manifest_sha256']=sha(a.manifest)
    result=publish(manifest,a.expected_revision,a.expected_pointer_sha)
    save(HOME/'验收'/f"增量发布-{result['pointer']['revision']:03}.json",result);print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
