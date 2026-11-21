"""峰值越界的Opus改用无损FLAC，保留原始响度，不删除旧派生。"""
import hashlib,json,pathlib,subprocess
ROOT=pathlib.Path(__file__).resolve().parents[2];OUT=ROOT/'local-data/网页资源库/全库第一版';FF=ROOT/'local-data/tools/网页资源优化/1/node_modules/ffmpeg-static/ffmpeg.exe'
if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT/'scripts/资源库'))
    from 兼容维护 import legacy_entry
    legacy_entry()
    check=json.loads((OUT/'音频完整核验.json').read_text(encoding='utf8'));count=0
    for item in check['results']:
        if not item.get('peak_above_fullscale'):continue
        key=item['id'];p=OUT/'验证'/(key+'.audio.json');r=json.loads(p.read_text(encoding='utf8'));dst=OUT/'音效'/(key+'.flac')
        subprocess.run([str(FF),'-v','error','-y','-i',r['source'],'-map_metadata','-1','-c:a','flac','-compression_level','8',str(dst)],check=True)
        r['superseded_path']=r['path'];r.update(path=dst.relative_to(ROOT).as_posix(),bytes=dst.stat().st_size,sha256=hashlib.sha256(dst.read_bytes()).hexdigest(),codec='flac',reason='Opus解码峰值越过0dBFS，使用无损编码保留原始峰值与响度，旧候选待确认清理')
        p.write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf8');count+=1
    print('改用无损FLAC',count)
