"""在共享维护锁内发布已封存场景并重绑未变的十三款交付；失败恢复两份指针。"""
import argparse
from contextlib import nullcontext
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

from 地图执行记录 import ROOT, atomic_json
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'scripts/资源库')]
from 群体资源交接 import load_delivery
from 发布场景 import publish, rollback, current_data, sha_manifest, lock
from 统一资源 import read, sha

DELIVERY='local-data/丧尸模型/十三款优化/交接/当前交付.json'


def bytes_sha(raw):
    return hashlib.sha256(raw).hexdigest()


def atomic_bytes(path,raw):
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.writing')
    with temporary.open('xb') as stream:
        stream.write(raw);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)


def find_scene_receipt(state,home):
    """凭完整摘要定位一次已切换事务，覆盖进程在返回发布结果前被终止的恢复窗口。"""
    current_sha=sha(home/'当前版本.json')
    matches=[]
    for path in (home/'验收').glob('场景发布-*/发布回执.json'):
        row=read(path)
        if (row.get('manifest_sha256')==state['manifest_sha256'] and
            row.get('previous_pointer_sha256')==state['previous_pointer_sha256'] and
            row.get('pointer_sha256')==current_sha and row.get('scene_id')==state['scene_id']):
            matches.append(path.parent)
    if len(matches)!=1:raise ValueError('不能唯一匹配本次确切场景发布，保留现场')
    return matches[0]


def restore(output,root=ROOT,home=None,guard=lock):
    root=Path(root).resolve();home=Path(home).resolve() if home else root/'local-data/资源库'
    output=Path(output).resolve();output.relative_to(root/'local-data')
    with guard():
        state=read(output/'交接回执.json');delivery=root/DELIVERY
        old_pointer=output/'原指针.json';old_delivery=output/'原交付.json'
        if sha(old_pointer)!=state['previous_pointer_sha256'] or sha(old_delivery)!=state['previous_delivery_sha256']:
            raise ValueError('原指针或交付恢复副本变化')
        pointer_sha=sha(home/'当前版本.json');delivery_sha=sha(delivery)
        allowed_delivery={state['previous_delivery_sha256'],state.get('planned_delivery_sha256')}
        if delivery_sha not in allowed_delivery:raise ValueError('交付已有其他改动，拒绝覆盖')
        if pointer_sha!=state['previous_pointer_sha256']:
            receipt=find_scene_receipt(state,home)
            rollback(receipt,pointer_sha,home=home,guard=nullcontext)
        if delivery_sha!=state['previous_delivery_sha256']:
            atomic_bytes(delivery,old_delivery.read_bytes())
        state.update(status='已恢复原指针及原交付',rolled_back=True)
        atomic_json(output/'交接回执.json',state)
        return state


def publish_delivered(manifest,expected_revision,expected_sha,output,root=ROOT,home=None,guard=lock,loader=load_delivery):
    root=Path(root).resolve();home=Path(home).resolve() if home else root/'local-data/资源库'
    output=Path(output).resolve();output.relative_to(root/'local-data')
    state=None
    with guard():
        previous,base,data=current_data(home,expected_revision,expected_sha)
        delivery=root/DELIVERY;old_delivery=delivery.read_bytes()
        for resolution in [256,512]:loader(delivery,resolution,root)
        old=read(delivery)
        if old['library_pointer_sha256']!=expected_sha:raise ValueError('原交付不是当前库绑定')
        output.mkdir(parents=True,exist_ok=False)
        (output/'原指针.json').write_bytes((home/'当前版本.json').read_bytes())
        (output/'原交付.json').write_bytes(old_delivery)
        state={'status':'准备发布及消费者交接','scene_id':manifest['scene']['id'],
            'manifest_sha256':sha_manifest(manifest),'previous_pointer_sha256':expected_sha,
            'previous_delivery_sha256':bytes_sha(old_delivery),'previous_revision':expected_revision,
            'human_acceptance':'待人工验收','deleted':0,'services_stopped':0}
        atomic_json(output/'交接回执.json',state)
        try:
            result=publish(manifest,expected_revision,expected_sha,root,home,guard=nullcontext)
            state.update(status='场景已切换待交付重绑',scene_publish=result)
            atomic_json(output/'交接回执.json',state)
            pointer=read(home/'当前版本.json');index=home/pointer['directory']
            new_catalog=read(index/'资源索引.json');new_routes=read(index/'资源路由.json')
            # 新场景只能追加，原媒体、原件、声音及运行映射必须保持身份。
            for group in ['models','audio']:
                actual={row['id']:row for row in new_catalog[group]}
                if any(actual.get(row['id'])!=row for row in data['资源索引.json'][group]):
                    raise ValueError('发布改动了既有模型或声音登记')
            if any(new_routes.get(key)!=row for key,row in data['资源路由.json'].items()):raise ValueError('原媒体路由变化')
            changed={'资源索引.json','资源路由.json','模型登记.json','主工程.json','场景登记.json','保留策略.json'}
            if any(pointer['metadata'].get(name)!=value for name,value in previous['metadata'].items() if name not in changed):
                raise ValueError('既有原件或运行映射变化')
            frozen=copy.deepcopy(old)
            frozen.update(library_revision=pointer['revision'],library_pointer_sha256=result['pointer_sha256'],
                catalog_sha256=sha(index/'资源索引.json'),frozen_at=datetime.now(timezone.utc).isoformat(),
                index_handoff={'kind':'场景增量','previous_delivery_sha256':bytes_sha(old_delivery),
                    'models_unchanged':True,'scene_manifest_sha256':state['manifest_sha256']})
            if frozen['models']!=old['models']:raise ValueError('消费者模型或动作集合变化')
            raw=json.dumps(frozen,ensure_ascii=False,indent=2).encode()
            state.update(planned_delivery_sha256=bytes_sha(raw),status='准备切换交付')
            (output/'计划新交付.json').write_bytes(raw);atomic_json(output/'交接回执.json',state)
            if delivery.read_bytes()!=old_delivery:raise ValueError('交付并发变化')
            atomic_bytes(delivery,raw)
            for resolution in [256,512]:loader(delivery,resolution,root)
            state.update(status='场景发布与双档消费者身份交接通过',passed=True,
                pointer_sha256=result['pointer_sha256'],revision=pointer['revision'],delivery_sha256=sha(delivery),
                source_program_sha256=sha(Path(__file__)))
            atomic_json(output/'交接回执.json',state)
            return state
        except Exception as error:
            state.update(status='发布或交接失败待恢复',error=repr(error))
            atomic_json(output/'交接回执.json',state)
            try:restore(output,root,home,guard=nullcontext)
            except Exception as recovery:
                state['recovery_error']=repr(recovery);atomic_json(output/'交接回执.json',state)
            raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['publish','rollback']);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--manifest',type=Path);parser.add_argument('--expected-revision',type=int);parser.add_argument('--expected-pointer-sha')
    args=parser.parse_args()
    result=restore(args.output) if args.operation=='rollback' else publish_delivered(read(args.manifest),args.expected_revision,args.expected_pointer_sha,args.output)
    print(json.dumps({k:v for k,v in result.items() if k not in ['scene_publish']},ensure_ascii=False,indent=2))
