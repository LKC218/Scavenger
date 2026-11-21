"""归档内置浏览器下载样本，核对页面状态与只读宿主前台记录。"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'local-data/丧尸模型/性能测试/ZOM-PERF-261103-001/浏览器验证'
MATRIX=[(n,m,640,60) for n in (100,300,500) for m in ('static','animated','open','channel')]+[(500,'open',480,60),(500,'open',1600,60),(500,'open',640,180),(500,'channel',640,180)]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True,type=Path);parser.add_argument('--index',type=int,choices=range(1,17))
    parser.add_argument('--allow-host-background',action='store_true',help='记录宿主前后台混合条件，不能称为原生前台样本');args=parser.parse_args()
    raw=json.loads(args.input.read_text('utf-8'));c=raw['config'];a=raw['summary']
    identity=(c['count'],c['mode'],c['view']['internal'][0],raw['requestedDurationS'])
    if args.index:assert identity==MATRIX[args.index-1],f'采样组合不匹配：{identity}'
    else:assert 100<=c['count']<=5000 and raw['requestedDurationS'] in (20,60,180),'容量探索配置不支持'
    assert raw['valid'] and not raw['interruptions'],'页面中断样本不能归为有效样本'
    assert a['elapsedS']>=identity[3]-.1 and a['minVisible']==c['count'],'采样时长或全群可见条件不满足'
    assert a['minAnimated']==(0 if c['mode']=='static' else c['count']),'真实动画更新数量不满足'
    end=datetime.fromisoformat(raw['createdAt'].replace('Z','+00:00')).timestamp();start=end-a['elapsedS']-raw['warmupS']
    log=[json.loads(x) for x in (OUT/'IAB前台状态.jsonl').read_text('utf-8').splitlines() if x.strip()]
    checks=[r for r in log if start-.6<=r['epoch']<=end+.6]
    assert checks and checks[0]['epoch']<=start+.6 and checks[-1]['epoch']>=end-.6,'宿主观察记录未覆盖整组'
    assert max(b['epoch']-a['epoch'] for a,b in zip(checks,checks[1:]))<1.5,'宿主观察记录存在缺口'
    front=all(r['hostForeground'] for r in checks)
    assert front or args.allow_host_background,'采样期间宿主曾切出前台'
    raw.update(browser='iab',headless=False,windowMode='Codex内置可见面板'+('（宿主全程前台）' if front else '（宿主前后台混合）'),browser_version='Chromium 156（UA Chrome/156.0.0.0）',nativeForegroundVerified=front,nativeForegroundChecks=len(checks),foregroundObservation={'intervalS':.5,'scope':'预热及正式采样，Windows宿主前台只读观察','startEpoch':start,'endEpoch':end,'hostForegroundPct':100*sum(r['hostForeground'] for r in checks)/len(checks)},download_sha256=hashlib.sha256(args.input.read_bytes()).hexdigest())
    n,m,w,d=identity
    name=f'iab-{args.index:02}-{n}-{m}-{w}-{d}.json' if args.index else f'iab-cap-{n}-{m}-{w}-{d}-{c["code_sha256"][:8]}.json'
    dest=OUT/name
    assert not dest.exists(),'已归档同组，拒绝覆盖'
    dest.write_text(json.dumps(raw,ensure_ascii=False,separators=(',',':')),'utf-8')
    summary_path=OUT/('iab-性能汇总.json' if args.index else 'iab-容量汇总.json')
    summary=json.loads(summary_path.read_text('utf-8')) if summary_path.exists() else {'browser':'iab','browser_version':raw['browser_version'],'windowMode':raw['windowMode'],'samples':[]}
    summary['samples'].append({'index':args.index,'count':n,'mode':m,'width':w,'duration':d,'valid':True,'summary':a,'file':name})
    summary['samples'].sort(key=lambda s:(s['count'],s['duration'],s['mode']))
    summary_path.write_text(json.dumps(summary,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps({'file':name,'checks':len(checks),'fps':round(a['fps'],2),'p95':round(a['p95Ms'],2),'completed':len(summary['samples'])},ensure_ascii=False))


if __name__=='__main__':main()
