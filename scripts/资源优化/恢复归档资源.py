"""按原相对路径恢复单份归档资产；默认写临时恢复目录，拒绝覆盖不同内容。"""
import argparse,hashlib,json,pathlib,shutil,subprocess,zipfile
ROOT=pathlib.Path(__file__).resolve().parents[2];HOME=ROOT/'local-data/资源归档/全库第一版'
def restore(source,original=False):
    import sys
    sys.path.insert(0,str(ROOT/'scripts/资源库'))
    from 统一资源 import available,resolve_source
    if available():
        if original:raise ValueError('统一库不回填旧目录，请使用统一恢复路径或显式复制到新工作目录')
        return resolve_source(source)
    pointer=HOME/'当前归档.json'
    mapping=json.loads(pointer.read_text(encoding='utf8'))['mapping'] if pointer.exists() else '路径映射.json'
    rows=json.loads((HOME/mapping).read_text(encoding='utf8'))['files'];row=next((r for r in rows if r['path']==source.replace('\\','/')),None)
    if not row:raise ValueError('原路径不在冻结归档清单中')
    retained=row['retained'];archive=(ROOT/retained['archive']).resolve()
    if not archive.is_relative_to(HOME.resolve()):raise ValueError('归档位置越界')
    dst=(ROOT/row['path']).resolve() if original else ROOT/'tmp/资源恢复'/(row['sha256']+row['format'])
    if not dst.is_relative_to(ROOT.resolve()):raise ValueError('恢复位置越界')
    if dst.exists():
        if hashlib.sha256(dst.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('恢复目标存在不同内容，拒绝覆盖')
        return dst
    dst.parent.mkdir(parents=True,exist_ok=True)
    if retained.get('format')=='7z':
        executable=ROOT/'local-data/tools/网页资源优化/1/node_modules/7zip-bin/win/x64/7za.exe'
        with dst.open('xb') as out:subprocess.run([str(executable),'x',str(archive),retained['entry'],'-so','-bsp0','-bso0'],stdout=out,stderr=subprocess.PIPE,check=True)
    else:
        with zipfile.ZipFile(archive) as z, z.open(retained['entry']) as src,dst.open('xb') as out:shutil.copyfileobj(src,out,8*1024*1024)
    if hashlib.sha256(dst.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('恢复摘要不一致，保留异常文件供检查')
    return dst
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',required=True);p.add_argument('--original',action='store_true',help='显式恢复原路径，不覆盖不同内容');a=p.parse_args();print(restore(a.source,a.original))
