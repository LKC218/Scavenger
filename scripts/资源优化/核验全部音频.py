"""完整解码全部音频派生，比较时长、声道、RMS与峰值，不修改增益。"""
import concurrent.futures,json,pathlib,subprocess
import numpy as np
ROOT=pathlib.Path(__file__).resolve().parents[2];OUT=ROOT/'local-data/网页资源库/全库第一版';FF=ROOT/'local-data/tools/网页资源优化/1/node_modules/ffmpeg-static/ffmpeg.exe'

def decode(path):
    r=subprocess.run([str(FF),'-v','error','-i',str(path),'-f','f32le','-ar','48000','-ac','2','-'],capture_output=True,check=True)
    a=np.frombuffer(r.stdout,dtype='<f4').reshape(-1,2)
    return {'frames':len(a),'seconds':len(a)/48000,'rms':float(np.sqrt(np.mean(a.astype(np.float64)**2))),'peak':float(np.max(np.abs(a))),'finite':bool(np.isfinite(a).all())}

def check(p):
    r=json.loads(p.read_text(encoding='utf8'))
    if not r.get('passed'):return {'id':r['source_sha256'],'passed':False,'error':'编码未通过'}
    try:
        a=decode(r['source']);b=decode(ROOT/r['path']);delta=20*np.log10(max(b['rms'],1e-12)/max(a['rms'],1e-12))
        duration=abs(a['seconds']-b['seconds']);passed=a['finite'] and b['finite'] and duration<=.025 and (abs(delta)<=1.5 or a['rms']<1e-8)
        return {'id':r['source_sha256'],'passed':bool(passed),'source':a,'optimized':b,'duration_delta':duration,'rms_delta_db':float(delta),'silent_source':a['rms']<1e-8,'peak_above_fullscale':b['peak']>1,'note':'统一48k双声道比较；源声道保留由编码参数及浏览器回读另核对。静音素材不升级质量状态。'}
    except Exception as e:return {'id':r['source_sha256'],'passed':False,'error':str(e)}

if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT/'scripts/资源库'))
    from 兼容维护 import legacy_entry
    legacy_entry('verify')
    paths=sorted((OUT/'验证').glob('*.audio.json'))
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(check,paths))
    summary={'count':len(results),'passed':sum(r['passed'] for r in results),'results':results};(OUT/'音频完整核验.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8');print(summary['count'],summary['passed'])
