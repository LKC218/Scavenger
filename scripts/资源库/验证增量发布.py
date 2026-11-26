"""在保留的隔离沙盒验证锁、竞争、失败不切换及成功增量；不接触活动库。"""
from contextlib import contextmanager
import copy
from pathlib import Path
import sys
import uuid
from 发布增量 import publish, publish_audio_loops
import 发布增量 as publisher
from 统一资源 import ROOT,save,read,sha


def test_audio_loops_sandbox(sandbox):
    """在隔离沙盒内验证36/多条音频循环增量入库的锁、摘要、ID冲突及双向关联完整性。"""
    results=[]
    for case in ['音频锁占用','音频摘要变化','音频ID冲突','音频成功发布']:
        root=sandbox/case;home=root/'local-data/资源库';home.mkdir(parents=True)
        data={
            '资源索引.json':{
                'models':[
                    {'id':'model:US_M1A2','name':'M1A2','quality_status':'passed','audio_ids':[]},
                    {'id':'model:US_AC130J','name':'AC130J','quality_status':'passed','audio_ids':[]},
                ],
                'audio':[{'id':'audio-keep','quality_status':'ready','model_ids':['model:US_M1A2']}],
                'summary':{'models':2,'audio':1},
            },
            '资源路由.json':{},
            '模型登记.json':{},'主工程.json':{},'运行替换.json':{},'来源别名.json':{},
            '维护输入.json':{'models':[]},'保留策略.json':{'masters':0},
            '靶场运行清单.json':{'unchanged':'原载具与13声音'},'原件映射.json':{'unchanged':'归档'},
        }
        base=home/'索引';base.mkdir()
        for name,value in data.items():save(base/name,value)
        save(home/'当前版本.json',{'revision':7,'directory':'索引','metadata':{name:sha(base/name) for name in data}})
        identity=sha(home/'当前版本.json')
        ogg=root/'local-data/engine-loop.ogg';ogg.write_bytes(b'OggS-loop-fixture')
        ogg_sha=sha(ogg);ogg_bytes=ogg.stat().st_size
        src_id='audio:11111111-2222-3333-4444-555555555555:abcdef0123456789'
        if case=='音频ID冲突':
            data['资源索引.json']['audio'].append({'id':'audio:loop:v2:US_Test_Engine_11111111','quality_status':'ready','model_ids':[]})
            save(base/'资源索引.json',data['资源索引.json'])
            save(home/'当前版本.json',{'revision':7,'directory':'索引','metadata':{name:sha(base/name) for name in data}})
            identity=sha(home/'当前版本.json')
        report={
            'summary':{'all_checks_passed':True},
            'loops':[{
                'source_audio_id':src_id,'source_name':'US_Test_Engine','source_category':'载具发动机',
                'loop_file':'loops/test.ogg','loop_abs_path':str(ogg),'sha256':ogg_sha,'bytes':ogg_bytes,
                'sample_rate':48000,'duration':6.38,'rms':0.12,'peak':0.52,
                'qa':{'passed':True,'peak':0.52,'clip_samples':0,'seam_100ms_rms_delta_db':0.15},
            }],
            'vehicle_loop_map':{
                'US_M1A2':{'slug':'US_M1A2','model_id':'model:US_M1A2','mapping_mode':'direct','source_audio_id':src_id,'fallback_reason':''},
                'US_AC130J':{'slug':'US_AC130J','model_id':'model:US_AC130J','mapping_mode':'sibling_fallback','source_audio_id':src_id,'fallback_reason':'同系映射'},
            },
        }
        lockfile=home/'测试锁'
        @contextmanager
        def guard():
            with lockfile.open('x') as f:f.write('test')
            try:yield
            finally:lockfile.unlink()
        if case=='音频锁占用':lockfile.write_text('other')
        if case=='音频摘要变化':report['loops'][0]['sha256']='0'*64
        try:
            result=publish_audio_loops(report,7,identity,root=root,home=home,guard=guard)
            assert case=='音频成功发布'
            p=read(home/'当前版本.json');new=home/p['directory']
            idx=read(new/'资源索引.json')
            assert p['revision']==8 and len(idx['audio'])==2 and result['audio_added_count']==1
            added=next(a for a in idx['audio'] if a['id']=='audio:loop:v2:US_Test_Engine_11111111')
            assert set(added['model_ids'])=={'model:US_M1A2','model:US_AC130J'}
            assert all(added['id'] in m['audio_ids'] for m in idx['models'])
            assert all(sha(new/name)==sha(base/name) for name in ['靶场运行清单.json','原件映射.json'])
            results.append({'case':case,'passed':True,'revision':8})
        except (ValueError,OSError) as error:
            assert case!='音频成功发布'
            assert sha(home/'当前版本.json')==identity
            results.append({'case':case,'passed':True,'pointer_unchanged':True,'error':str(error)})
    return results


def main():
    sandbox=ROOT/'local-data/丧尸模型/十三款优化/发布器验证'/uuid.uuid4().hex
    sandbox.mkdir(parents=True)
    results=[]
    for case in ['锁占用','版本竞争','摘要变化','指针切换异常','成功发布']:
        root=sandbox/case;home=root/'local-data/资源库';home.mkdir(parents=True)
        data={'资源索引.json':{'models':[{'id':'old','quality_status':'pending'}],'audio':[{'id':'audio-keep'}],'summary':{'models':1,'audio':1}},'资源路由.json':{},'模型登记.json':{},'主工程.json':{},'运行替换.json':{},'来源别名.json':{},'维护输入.json':{'models':[]},'保留策略.json':{'masters':0},'靶场运行清单.json':{'unchanged':'原载具与13声音'},'原件映射.json':{'unchanged':'归档'}}
        base=home/'索引';base.mkdir()
        for name,value in data.items():save(base/name,value)
        save(home/'当前版本.json',{'revision':3,'directory':'索引','metadata':{name:sha(base/name) for name in data}})
        identity=sha(home/'当前版本.json')
        file=root/'local-data/input.glb';file.write_bytes(b'publication-transaction-fixture')
        r={'path':str(file),'bytes':file.stat().st_size,'sha256':sha(file)}
        manifest={'technical_passed':True,'models':[{'id':f'model:{i}','human_acceptance':'待工程师验收','variants':[{**r,'resolution':res,'level':0,'triangles':800,'before_triangles':800} for res in [256,512]],'blend':r,'source':r,'preview':r,'evidence':[r],'card':{'id':f'model:{i}','name':'事务夹具','quality_status':'pending'},'provenance':{'fixture':True}} for i in range(12)]}
        lockfile=home/'测试锁'
        @contextmanager
        def guard():
            with lockfile.open('x') as f:f.write('test')
            try:yield
            finally:lockfile.unlink()
        if case=='锁占用':lockfile.write_text('other')
        if case=='摘要变化':manifest['models'][0]['variants'][0]['sha256']='0'*64
        original_replace=publisher.os.replace
        if case=='指针切换异常':
            def fail_replace(source,target):raise OSError('注入原子切换前故障')
            publisher.os.replace=fail_replace
        try:
            result=publish(manifest,2 if case=='版本竞争' else 3,identity,root,home,guard)
            assert case=='成功发布'
            p=read(home/'当前版本.json');new=home/p['directory']
            assert p['revision']==4 and len(read(new/'资源索引.json')['models'])==13
            assert read(new/'资源索引.json')['audio']==data['资源索引.json']['audio']
            assert all(sha(new/name)==sha(base/name) for name in ['靶场运行清单.json','原件映射.json'])
            results.append({'case':case,'passed':True,'revision':4})
        except (ValueError,OSError) as error:
            assert case!='成功发布'
            assert sha(home/'当前版本.json')==identity
            results.append({'case':case,'passed':True,'pointer_unchanged':True,'error':str(error)})
        finally:
            publisher.os.replace=original_replace
    results.extend(test_audio_loops_sandbox(sandbox))
    save(sandbox/'验证结果.json',{'passed':True,'cases':results,'scope':'事务控制夹具，不代表模型技术验证'})
    print({'passed':True,'path':str(sandbox),'cases':results})


if __name__=='__main__':main()
