"""专项回收已获批准的统一库清单；无永久删除接口，不关闭整组任务保护。"""
import argparse
import concurrent.futures
import ctypes
from ctypes import wintypes
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.request import urlopen

from 统一资源 import ROOT, HOME, current, read, record_path, sha

APPROVED = 'c0f912431a18239308a5f22e13cef846efa4f7232bdc21550ec22160d91d2559'
MANIFEST = 'local-data/资源库/待删除/删除候选精确清单.json'
BASE = 'local-data/维护清理/统一库专项回收'
RUN = 'local-data/维护清理/回收/统一库清单-261103-001'
OTHER_RECEIPT = 'local-data/维护清理/回收/恢复副本1178-261103-001/回执.json'
OTHER_PREFIX = 'tmp/整库固实归档恢复核验/'
INITIAL = {
    '既有断箭任务待核对': ['local-data/断箭'],
    '既有资源优化任务待核对': ['local-data/网页资源库', 'local-data/资源归档'],
    '既有统一资源库任务待核对': ['local-data/资源库'],
    '既有原型任务待核对': ['local-data/原型'],
    '既有丧尸任务待核对': ['local-data/丧尸模型'],
    '既有临时任务待核对': ['tmp', 'temp'],
}
spec = importlib.util.spec_from_file_location('shared_cleanup', ROOT/'scripts/维护清理/清理工具.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def approved_rows(identity):
    require(identity == APPROVED and sha(c.checked(MANIFEST)) == APPROVED, '批准清单摘要不匹配')
    manifest = c.read(MANIFEST)
    rows = manifest['files']
    require(len(rows) == 10911 and sum(r['bytes'] for r in rows) == 77789867436, '批准数量或字节变化')
    require(len({r['relative_path'].casefold() for r in rows}) == len(rows), '存在重复路径')
    for row in rows:
        path = c.checked(row['relative_path'])
        require(path == Path(row['path']), '绝对路径与相对路径不一致')
        require(row['relative_path'].startswith(('local-data/', 'tmp/资源恢复/', OTHER_PREFIX)), '超出已审核资料目录')
        require(path.suffix.lower() in {'.glb','.blend','.blend1','.wav','.opus','.flac','.ktx2','.png','.jpg','.jpeg','.zip','.7z'}, '超出批准媒体类型')
    return rows


def task_conflicts(registry, path):
    """仅为核验通过的获批文件细化六个初始未知保护；新增、活动保护一律生效。"""
    conflicts = c.blockers(registry, path)
    result = []
    for key in conflicts:
        task = registry['tasks'][key]
        scoped_initial = key in INITIAL and task['status'] == 'unknown' and sorted(task['paths']) == sorted(INITIAL[key])
        if not scoped_initial:
            result.append(key)
    return result


def approved_subset(rows):
    known = {r['relative_path']:r for r in approved_rows(APPROVED)}
    require(len({r['path'].casefold() for r in rows})==len(rows), '执行子集包含重复项')
    for row in rows:
        original = known.get(row['path'])
        require(original and row['bytes']==original['bytes'] and row['sha256']==original['sha256'], '执行子集超出获批清单')
        c.checked(row['path'])


def occupancy(paths):
    """Windows Restart Manager仅查询，不结束任何进程。"""
    class Unique(ctypes.Structure):
        _fields_ = [('pid',wintypes.DWORD),('started',wintypes.FILETIME)]
    class Info(ctypes.Structure):
        _fields_ = [('process',Unique),('app',wintypes.WCHAR*256),('service',wintypes.WCHAR*64),
                    ('kind',ctypes.c_int),('status',wintypes.ULONG),('session',wintypes.DWORD),('restartable',wintypes.BOOL)]
    rm = ctypes.WinDLL('Rstrtmgr')
    rm.RmStartSession.argtypes = [ctypes.POINTER(wintypes.DWORD),wintypes.DWORD,wintypes.LPWSTR]
    rm.RmRegisterResources.argtypes = [wintypes.DWORD,wintypes.UINT,ctypes.POINTER(wintypes.LPCWSTR),wintypes.UINT,ctypes.c_void_p,wintypes.UINT,ctypes.c_void_p]
    rm.RmGetList.argtypes = [wintypes.DWORD,ctypes.POINTER(wintypes.UINT),ctypes.POINTER(wintypes.UINT),ctypes.POINTER(Info),ctypes.POINTER(wintypes.DWORD)]
    rm.RmEndSession.argtypes = [wintypes.DWORD]
    session = wintypes.DWORD()
    key = ctypes.create_unicode_buffer(33)
    require(rm.RmStartSession(ctypes.byref(session),0,key)==0, '无法启动文件占用查询')
    try:
        for start in range(0,len(paths),256):
            chunk = [str(p) for p in paths[start:start+256]]
            names = (wintypes.LPCWSTR*len(chunk))(*chunk)
            require(rm.RmRegisterResources(session,len(chunk),names,0,None,0,None)==0, '无法登记文件占用查询')
        needed,count,reasons = wintypes.UINT(),wintypes.UINT(),wintypes.DWORD()
        code = rm.RmGetList(session,ctypes.byref(needed),ctypes.byref(count),None,ctypes.byref(reasons))
        if code == 0:
            return {'files':len(paths),'processes':[],'passed':True}
        for _ in range(4):
            require(code==234, '文件占用查询失败：'+str(code))
            count.value = needed.value
            data = (Info*count.value)()
            code = rm.RmGetList(session,ctypes.byref(needed),ctypes.byref(count),data,ctypes.byref(reasons))
            if code == 0:
                processes = [{'pid':p.process.pid,'app':p.app,'service':p.service} for p in data[:count.value]]
                return {'files':len(paths),'processes':processes,'passed':True}
        raise ValueError('文件占用持续变化')
    finally:
        rm.RmEndSession(session)


def require_unoccupied(rows):
    result = occupancy([c.checked(r['path']) for r in rows])
    require(not result['processes'], '文件仍被进程使用：'+str(result['processes']))
    return result


def project_processes():
    command = "[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(python|pythonw|blender|7za|ffmpeg)\\.exe$' } | Select-Object ProcessId,Name,CommandLine) | ConvertTo-Json -Depth 3"
    output = subprocess.run(['powershell','-NoProfile','-Command',command],capture_output=True,check=True)
    rows = json.loads(output.stdout.decode('utf-8-sig')) if output.stdout.strip() else []
    suspicious = []
    for row in rows:
        command = row.get('CommandLine') or ''
        if any(name in command for name in ['准备删除清单.py','整理统一库.py','固实归档.py','生成游戏版.mjs','模型批处理.mjs','批量验证.py']):
            suspicious.append(row)
    require(not suspicious, '存在读取旧媒体的处理进程：'+str(suspicious))
    return rows


def retained_files():
    pointer,catalog,routes,index = current()
    files = {row['path']:row for row in routes.values()}
    for row in read(index/'主工程.json').values():
        files[row['path']] = row
    for row in read(index/'原件映射.json').values():
        files[row['archive']['path']] = row['archive']
    require(len(files)==923, '当前保留集合变化，先重做审计')
    guarded = []
    for row in files.values():
        actual = c.fingerprint(row['path'])
        require(actual['sha256']==row['sha256'] and actual['bytes']==row['bytes'], '保留件变化：'+row['path'])
        guarded.append(actual)
    guarded.append(c.fingerprint('local-data/资源库/当前版本.json'))
    for name in pointer['metadata']:
        guarded.append(c.fingerprint((index/name).relative_to(ROOT).as_posix()))
    # 并行任务要求保留的旧固实包与凭证不进入本轮回收。
    for name in ['原始资源固实归档.7z','固实归档验证.json','固实路径映射.json','当前归档.json']:
        guarded.append(c.fingerprint('local-data/资源归档/全库第一版/'+name))
    return guarded, catalog, routes


def check_runtime(routes, approved):
    targets = {r['relative_path'].casefold() for r in approved}
    require(not any(r['path'].casefold() in targets for r in routes.values()), '当前资源仍引用候选')
    result = []
    for port,endpoint in [(8767,'/api/catalog'),(8768,'/manifest.json'),(8769,'/manifest.json')]:
        response = json.load(urlopen(f'http://127.0.0.1:{port}'+endpoint,timeout=10))
        if port == 8767:
            require(response.get('asset_library_revision')==1 and len(response['models'])==131 and len(response['audio'])==253, '资料库版本不符')
            urls = [r['model_url'] for r in response['models']]+[r['url'] for r in response['audio']]
        elif port == 8768:
            values = [*response['models'].values(),*response['audio'].values()]
            require(len(values)==16 and all(r['asset_library']=='统一资源库' for r in values), '8768未使用统一库')
            urls = [r['url'] for r in values]
        else:
            asset = response['asset']
            require(Path(asset['file']).resolve().is_relative_to((HOME/'当前资源').resolve()), '群体实验仍使用旧资源')
            urls = []
        require(all(url in routes for url in urls), '服务存在未登记的资源URL')
        result.append({'port':port,'current_library':True,'media_urls':len(urls)})
    return result


def prepare(identity):
    approved = approved_rows(identity)
    registry = c.read(c.STATE+'/登记.json')
    require(registry['tasks']['统一模型音频库迁移']['status']=='closed', '迁移任务未结束')
    review = c.read(BASE+'/所属任务核对.json')
    require(len(review['threads'])>=7, '缺少所属聊天依赖复核')
    other = c.read(OTHER_RECEIPT)
    require(other['status']=='完成' and other['moved_files']==1178 and other['deleted_files']==0, '并行回收尚未完成')
    other_rows = {r['path']:r for r in other['files'] if r['state']=='stored'}
    guarded,catalog,routes = retained_files()
    preserved = {r['path'].casefold() for r in guarded}
    selected,skipped,already = [],[],[]
    for row in approved:
        relative = row['relative_path']
        if relative.startswith(OTHER_PREFIX):
            entry = other_rows.get(relative)
            require(entry and entry['sha256']==row['sha256'] and entry['bytes']==row['bytes'], '重叠回收身份不符')
            c.same(entry,entry['stored'])
            require(not c.checked(relative).exists(), '并行回收原路径重新出现，保持保护')
            already.append({'path':relative,'bytes':row['bytes'],'sha256':row['sha256'],'receipt':OTHER_RECEIPT})
            continue
        conflicts = task_conflicts(registry,relative)
        if conflicts or relative.casefold() in preserved:
            skipped.append({'path':relative,'bytes':row['bytes'],'sha256':row['sha256'],'reason':'当前保留或其他任务保护','tasks':conflicts})
            continue
        require(c.checked(relative).is_file(), '获批文件缺失且无回执：'+relative)
        selected.append(row)
    require(len(already)==1178, '并行范围不匹配')
    print('复核本轮文件摘要',len(selected),'项',flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        actual = list(pool.map(lambda r:c.fingerprint(r['relative_path']),selected))
    for actual_row,old in zip(actual,selected):
        require(actual_row['sha256']==old['sha256'] and actual_row['bytes']==old['bytes'], '获批文件已变化：'+old['relative_path'])
    require(not c.checked(RUN).exists(), '本批已存在回执，禁止重复执行')
    runtime = check_runtime(routes,approved)
    occupied = require_unoccupied(actual)
    processes = project_processes()
    result = {'created':time.time(),'approved_manifest':MANIFEST,'approved_sha256':APPROVED,'files':actual,
              'count':len(actual),'bytes':sum(r['bytes'] for r in actual),'skipped':skipped,'already_recycled':already,
              'preserved':guarded,'runtime':runtime,'occupancy':occupied,'processes':processes,
              'registry_sha256':sha(c.checked(c.STATE+'/登记.json')),'task_review_sha256':sha(c.checked(BASE+'/所属任务核对.json')),
              'scope_release':'只在获批文件指纹内确认旧六组预置未知保护已无活动依赖；整组保护继续保留，新增或明确活动保护不豁免',
              'approval':'工程师本轮已确认精确清单，要求复核引用及其他任务占用后进入七天回收；不启用自动永久清除'}
    c.save(BASE+'/执行预检.json',result,exclusive=True)
    c.save(BASE+'/预检身份.json',{'sha256':sha(c.checked(BASE+'/执行预检.json')),'approved_sha256':APPROVED},exclusive=True)
    print('执行预检通过',result['count'],result['bytes'],'字节；重叠已回收',len(already),'保护',len(skipped),flush=True)
    return result


def move_one(row, destination):
    c.same(row)
    target = c.checked(destination)
    require(not target.exists(), '回收目标已经存在')
    target.parent.mkdir(parents=True,exist_ok=True)
    os.rename(c.checked(row['path']),target)


def execute(identity):
    approved_rows(identity)
    prepared = c.read(BASE+'/执行预检.json')
    require(sha(c.checked(BASE+'/执行预检.json'))==c.read(BASE+'/预检身份.json')['sha256'], '执行预检摘要变化')
    require(prepared['approved_sha256']==APPROVED, '执行预检关联错误')
    approved_subset(prepared['files'])
    registry = c.read(c.STATE+'/登记.json')
    require(sha(c.checked(c.STATE+'/登记.json'))==prepared['registry_sha256'], '任务登记变化，先重新复核')
    require(sha(c.checked(BASE+'/所属任务核对.json'))==prepared['task_review_sha256'], '任务复核证据变化')
    require(not c.checked(RUN).exists(), '回收已开始或完成，禁止重复执行')
    for row in prepared['files']:
        require(not task_conflicts(registry,row['path']), '出现新的活动保护')
    for row in prepared['preserved']:
        c.same(row)
    c.save(BASE+'/执行前占用.json',require_unoccupied(prepared['files']),exclusive=True)
    project_processes()
    receipt = {'created':time.time(),'status':'进行中','approved_manifest':MANIFEST,'approved_sha256':APPROVED,
               'preflight_sha256':sha(c.checked(BASE+'/执行预检.json')),'approval':prepared['approval'],
               'scope_release':prepared['scope_release'],'files':[],'skipped':prepared['skipped'],
               'already_recycled_count':len(prepared['already_recycled']),'other_receipt':OTHER_RECEIPT,
               'preserved':prepared['preserved'],'deleted_files':0,'automatic_purge':False}
    c.save(RUN+'/回执.json',receipt,exclusive=True)
    try:
        with c.checked(RUN+'/逐文件日志.jsonl').open('x',encoding='utf8') as journal:
            for number,row in enumerate(prepared['files']):
                require(c.read(c.STATE+'/登记.json')==registry, '任务登记变化，立即停止')
                require(not task_conflicts(registry,row['path']), '出现任务占用')
                if number%250==0:
                    require_unoccupied(prepared['files'][number:number+250])
                stored = RUN+'/文件/'+row['path']
                entry = {**row,'stored':stored,'moved_at':time.time(),'state':'pending',
                         'item':{'path':row['path'],'kind':'history','task':'统一库清单回收复核'}}
                receipt['files'].append(entry)
                journal.write(json.dumps(entry,ensure_ascii=False)+'\n');journal.flush();os.fsync(journal.fileno())
                move_one(row,stored)
                entry['state']='stored'
                journal.write(json.dumps({'path':row['path'],'state':'stored'},ensure_ascii=False)+'\n');journal.flush();os.fsync(journal.fileno())
                if (number+1)%250==0:
                    c.save(RUN+'/回执.json',receipt)
                    print('已进入七天回收',number+1,'/',prepared['count'],flush=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda row:c.same(row,row['stored']),receipt['files']))
        for row in prepared['preserved']:
            c.same(row)
        _,_,routes,_ = current()
        check_runtime(routes,approved_rows(identity))
        receipt.update(status='完成',completed_at=time.time(),moved_files=len(receipt['files']),
                       moved_bytes=sum(r['bytes'] for r in receipt['files']),
                       purge_not_before=max(r['moved_at'] for r in receipt['files'])+7*86400,preserved_verified=True)
    except Exception:
        receipt['status']='中断待核对'
        raise
    finally:
        c.save(RUN+'/回执.json',receipt)
    print('回收完成',receipt['moved_files'],receipt['moved_bytes'],'字节；永久删除0份',flush=True)
    return receipt


def restore_batch():
    receipt = c.read(RUN+'/回执.json')
    require(receipt['status']=='完成', '中断回执需先人工核对')
    require(receipt['approved_sha256']==APPROVED, '回执关联错误')
    approved_subset(receipt['files'])
    registry = c.read(c.STATE+'/登记.json')
    for row in receipt['files']:
        require(row['state']=='stored' and row['stored']==RUN+'/文件/'+row['path'], '回收条目身份不符')
        require(not task_conflicts(registry,row['path']), '原路径出现新的任务保护，先复核恢复范围')
        require(not c.checked(row['path']).exists(), '原路径已有文件，拒绝覆盖')
        c.same(row,row['stored'])
    for row in receipt['files']:
        require(c.read(c.STATE+'/登记.json')==registry, '任务登记变化，停止恢复')
        require(not c.checked(row['path']).exists(), '原路径出现文件，拒绝覆盖')
        c.checked(row['path']).parent.mkdir(parents=True,exist_ok=True)
        os.rename(c.checked(row['stored']),c.checked(row['path']))
        row['state']='restored'
        c.save(RUN+'/回执.json',receipt)
    receipt.update(status='已恢复',restored_at=time.time())
    c.save(RUN+'/回执.json',receipt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','execute','restore'])
    parser.add_argument('--approved-sha',required=True)
    args = parser.parse_args()
    require(args.approved_sha==APPROVED,'批准摘要不匹配')
    with c.lock():
        if args.action=='prepare':prepare(args.approved_sha)
        elif args.action=='execute':execute(args.approved_sha)
        else:restore_batch()


if __name__=='__main__':
    main()
