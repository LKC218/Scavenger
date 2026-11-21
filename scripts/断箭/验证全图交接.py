"""隔离验证场景发布与交付指针联动、失败恢复和中断恢复；仅为事务夹具。"""
import argparse
from contextlib import nullcontext
from pathlib import Path
import sys

import 发布全图交付 as handoff
from 地图执行记录 import atomic_json, fingerprint
sys.path.insert(0,str(handoff.ROOT/'scripts/资源库'))
from 验证场景发布 import fixture
from 统一资源 import read, save, sha


def run(output):
    output.mkdir(parents=True,exist_ok=False);cases=[]
    for case in ['成功及回滚','原交付身份不符','重绑后验证失败','交付切换中断','场景切换返回前中断','后续指针阻止回滚']:
        root=output/case;home,base,manifest=fixture(root)
        delivery=root/handoff.DELIVERY
        save(delivery,{'library_revision':3,'library_pointer_sha256':sha(home/'当前版本.json'),
            'catalog_sha256':sha(base/'资源索引.json'),'models':[{'id':'fixture-only','bytes_unchanged':True}]})
        if case=='原交付身份不符':
            row=read(delivery);row['library_pointer_sha256']='0'*64;save(delivery,row)
        previous_pointer=(home/'当前版本.json').read_bytes();previous_delivery=delivery.read_bytes()
        def loader(path,resolution,root):
            row=read(path);pointer=read(home/'当前版本.json')
            assert row['library_pointer_sha256']==sha(home/'当前版本.json')
            assert row['library_revision']==pointer['revision']
            assert row['catalog_sha256']==sha(home/pointer['directory']/'资源索引.json')
            if case=='重绑后验证失败' and pointer['revision']==4 and resolution==512:raise ValueError('故意注入双档验证失败')
        transaction=root/'local-data/交接';original_publish=handoff.publish;original_write=handoff.atomic_bytes
        def interrupted_publish(*args,**kwargs):
            result=original_publish(*args,**kwargs)
            if case=='场景切换返回前中断':raise SystemExit('故意模拟进程终止')
            return result
        def interrupted_write(path,raw):
            original_write(path,raw)
            if case=='交付切换中断' and path==delivery and read(path)['library_revision']==4:raise SystemExit('故意模拟进程终止')
        handoff.publish=interrupted_publish;handoff.atomic_bytes=interrupted_write
        try:
            failure=None
            try:
                result=handoff.publish_delivered(manifest,3,sha(home/'当前版本.json'),transaction,root,home,guard=nullcontext,loader=loader)
            except (ValueError,AssertionError,SystemExit) as error:failure=repr(error)
            if case in ['原交付身份不符','重绑后验证失败','交付切换中断','场景切换返回前中断']:
                assert failure,case
                if '中断' in case:handoff.restore(transaction,root,home,guard=nullcontext)
            elif case=='成功及回滚':
                assert failure is None and result['passed']
                assert read(delivery)['models']==read(transaction/'原交付.json')['models']
                handoff.restore(transaction,root,home,guard=nullcontext)
            else:
                assert failure is None and result['passed']
                later=read(home/'当前版本.json');later['revision']+=1;save(home/'当前版本.json',later)
                before_pointer=sha(home/'当前版本.json');before_delivery=sha(delivery)
                try:handoff.restore(transaction,root,home,guard=nullcontext)
                except ValueError:pass
                else:raise AssertionError('过期回滚未拒绝')
                assert sha(home/'当前版本.json')==before_pointer and sha(delivery)==before_delivery
            if case!='后续指针阻止回滚':
                assert (home/'当前版本.json').read_bytes()==previous_pointer and delivery.read_bytes()==previous_delivery
            cases.append({'name':case,'passed':True,'injected_failure':failure})
        finally:
            handoff.publish=original_publish;handoff.atomic_bytes=original_write
    result={'passed':True,'scope':'纯事务夹具，不代表13款真实模型或浏览器通过','cases':cases,
        'programs':{p.name:fingerprint(p) for p in [Path(__file__),Path(handoff.__file__)]}}
    atomic_json(output/'验证结果.json',result);print('六项场景消费者交接事务通过',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output=args.output.resolve();args.output.relative_to(handoff.ROOT/'local-data')
    run(args.output)
