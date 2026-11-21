"""按冻结服务串行运行全图最终技术回归；任一失败即停止，不自动重试或放宽预算。"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[2]


def run(args):
    if any(not re.fullmatch(r'[A-Za-z0-9_-]+',value) for value in [args.runtime_revision,args.run]):
        raise ValueError('运行修订或回归名无效')
    directory=args.directory.resolve();directory.relative_to(ROOT/'local-data/断箭/批次')
    runtime=directory/'运行接入'/args.runtime_revision
    output=runtime/'最终技术回归队列'/args.run;output.mkdir(parents=True,exist_ok=False)
    with urlopen(f'http://127.0.0.1:{args.port}/api/status') as response:identity=json.load(response)
    expected=hashlib.sha256((runtime/'地图运行.json').read_bytes()).hexdigest()
    assert identity['map_sha256']==expected and identity['app']=='scavenger-airbase-map'
    jobs=[]
    def add(name,script,extra,proof,flag='passed'):
        jobs.append({'name':name,'script':script,'extra':extra,'proof':proof,'flag':flag})
    for browser in ['chrome','msedge']:
        add('功能与故障-'+browser,'验证全图故障.py',['--browser',browser,'--run',args.run],f'故障与驻留验证/{args.run}/{browser}/核验凭证.json')
        add('平移缩放稳定-'+browser,'验证全图显示稳定.py',['--browser',browser,'--run',args.run],f'显示稳定验证/{args.run}/{browser}/核验凭证.json')
        add('跨层资源-'+browser,'验证全图切层.py',['--browser',browser,'--run',args.run],f'跨层往返验证/{args.run}/{browser}/核验凭证.json')
    add('81格双视角采集','审图全图分区.py',['--browser','chrome','--run',args.run],f'分区网页审图/{args.run}/审图输入.json','technical_passed')
    for browser in ['chrome','msedge']:
        add('六项正式负载-'+browser,'验证全图负载.py',['--browser',browser,'--run',args.run],f'负载正式验证/{args.run}/{browser}/核验凭证.json')
    for browser in ['chrome','msedge']:
        add('五圈180秒回访-'+browser,'验证全图回访.py',['--browser',browser,'--run',args.run],f'全图回访正式验证/{args.run}/{browser}/核验凭证.json')
    for browser in ['chrome','msedge']:
        add('三冷三温限速-'+browser,'验证全图网络.py',['--browser',browser,'--run',args.run],f'网络正式验证/{args.run}/{browser}/核验凭证.json')
    scripts={job['script']:hashlib.sha256((ROOT/'scripts/断箭'/job['script']).read_bytes()).hexdigest() for job in jobs}
    state={'status':'进行中','pid':os.getpid(),'started_epoch':time.time(),'identity':identity,'programs':scripts,'completed':[],
        'scope':'串行技术回归，宏观图仍须实际AI审阅；不自动发布或升级人工状态'}
    if args.resume_state:
        previous_path=args.resume_state.resolve();previous_path.relative_to(runtime.resolve())
        previous=json.loads(previous_path.read_text('utf-8'));assert previous['identity']==identity,'续跑服务身份不一致'
        known={job['name']:job for job in jobs}
        for row in previous['completed']:
            job=known[row['name']];proof=directory/row['proof'];proof.resolve().relative_to(runtime.resolve())
            assert hashlib.sha256(proof.read_bytes()).hexdigest()==row['sha256'],'已完成凭证摘要变化'
            assert scripts[job['script']]==previous['programs'][job['script']],'已完成项验证器变化，不能复用'
            assert json.loads(proof.read_text('utf-8')).get(job['flag']) is True
        state['completed']=previous['completed'];state['resumed_from']={'file':previous_path.relative_to(directory).as_posix(),'sha256':hashlib.sha256(previous_path.read_bytes()).hexdigest()}
    lock=threading.Lock();stop=threading.Event()
    def save():
        with lock:
            state['updated_epoch']=time.time();file=output/'执行状态.json';temp=file.with_suffix('.json.writing')
            temp.write_text(json.dumps(state,ensure_ascii=False,indent=2),'utf-8');os.replace(temp,file)
    def heartbeat():
        next_time=time.time()+15
        while not stop.wait(.25):
            if time.time()>=next_time:save();next_time=time.time()+15
    save();thread=threading.Thread(target=heartbeat,daemon=True);thread.start()
    try:
        for index,job in enumerate(jobs,1):
            if any(row['name']==job['name'] for row in state['completed']):
                print('复用已核对凭证：'+job['name'],flush=True);continue
            with urlopen(f'http://127.0.0.1:{args.port}/api/status') as response:current=json.load(response)
            assert current==identity,'回归期间冻结服务身份变化'
            source=ROOT/'scripts/断箭'/job['script']
            assert hashlib.sha256(source.read_bytes()).hexdigest()==scripts[job['script']],'回归期间验证器源码变化'
            state['active']={'index':index,'total':len(jobs),'name':job['name'],'started_epoch':time.time()};save()
            print('开始：'+job['name'],flush=True)
            subprocess.run([sys.executable,'-X','utf8','-B',str(source),'--directory',str(runtime),'--port',str(args.port),*job['extra']],cwd=ROOT,check=True)
            proof=runtime/job['proof'];record=json.loads(proof.read_text('utf-8'));assert record.get(job['flag']) is True,job['name']
            state['completed'].append({'name':job['name'],'proof':proof.relative_to(directory).as_posix(),'sha256':hashlib.sha256(proof.read_bytes()).hexdigest()});save()
        state.update(status='串行技术回归完成，待AI审图及交付封存',passed=True);state.pop('active',None)
    except BaseException as error:
        state.update(status='回归停止，失败保留且未重试',passed=False,exception=repr(error));raise
    finally:
        stop.set();thread.join();save()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True);parser.add_argument('--runtime-revision',required=True)
    parser.add_argument('--run',required=True);parser.add_argument('--port',type=int,default=8784)
    parser.add_argument('--resume-state',type=Path,help='核对同一冻结服务已完成条目后，仅续跑未通过项目；使用新回归名')
    run(parser.parse_args())
