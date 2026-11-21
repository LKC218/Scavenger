"""只读盘点旧卡通运行文件、保护登记及当前HTTP消费者，不删除或改动资产。"""
import csv,hashlib,json,time
from pathlib import Path
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'local-data/原作群尸/V001-01a1aa48/退役审查'
def read(p):return json.loads(p.read_text('utf-8'))
def sha(p):
    with p.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def main():
    OUT.mkdir(parents=True,exist_ok=True);target=OUT/str(time.time_ns());target.mkdir()
    delivery_path=ROOT/'local-data/丧尸模型/十三款优化/交接/当前交付.json';delivery=read(delivery_path);rows=[];masters=[]
    for model in delivery['models']:
        masters.append({'id':model['id'],**model['blend'],'policy':'保留原件及最新可编辑主工程，不纳入普通载荷清理'})
        for variant in model['variants']:
            path=Path(variant['path']);rows.append({'id':model['id'],'resolution':variant['resolution'],'path':str(path),'bytes':variant['bytes'],'sha256':variant['sha256'],'identity_stat_matches':path.exists() and path.stat().st_size==variant['bytes'],'retirement_status':'暂缓：当前正式库及既有已通过服务仍可能引用，需专项退役流程'})
    consumers=[]
    for port in [8767,8769,8794,8797,8798]:
        try:
            with urlopen(f'http://127.0.0.1:{port}/api/status',timeout=2) as response:status=json.load(response)
            item={'port':port,'status':status}
            if port in [8794,8798]:
                with urlopen(f'http://127.0.0.1:{port}/manifest.json',timeout=2) as response:manifest=json.load(response)
                item['actor_ids']=[m['id'] for m in manifest['actors']['models']]
            consumers.append(item)
        except Exception as error:consumers.append({'port':port,'read_status':str(error),'note':'无法读取不代表消费者结束，保持保护'})
    registry=read(ROOT/'local-data/维护清理/登记.json');tasks=[]
    for name,task in registry['tasks'].items():
        paths=[p for p in task.get('paths',[]) if '资源库' in p or '丧尸模型' in p]
        if task['status']!='closed' and paths:tasks.append({'task':name,'status':task['status'],'paths':paths,'note':'这是登记范围，不推断仍在实际读取每个文件'})
    result={'schema':1,'phase':'先替换当前默认运行，旧载荷逐步退役','source_delivery_sha256':sha(delivery_path),'runtime_files':rows,'files':len(rows),'bytes':sum(r['bytes'] for r in rows),'masters_to_keep':masters,'http_consumers':consumers,'registered_protections':tasks,
            'baseline_copy':'8797旧卡通对照已复制至本轮独立冻结目录，不再依赖统一库旧GLB路径','remaining':'8794已通过版和8769旧实验保留，正式资产库仍有旧卡通条目；既有分享包有独立副本，不能当成当前库路径的直接消费者','deletion_performed':False,'automatic_retirement_registered':False,
            'next_gate':'新原作候选经工程师具体验收后，再由相关服务负责人迁移或结束旧消费者；正式库载荷走专项审查，不用通用工具绕过'}
    (target/'审查.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    with (target/'旧卡通运行文件.csv').open('x',encoding='utf-8-sig',newline='') as stream:
        fields=['id','resolution','path','bytes','sha256','retirement_status'];writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
    text='# 旧卡通群尸逐步退役审查\n\n本轮先迁移默认运行入口。下列26份媒体为待交接审查，尚未登记自动删除；原件和13份最新可编辑工程继续保留。\n\n|稳定ID|档位|路径|字节|SHA-256|\n|---|---:|---|---:|---|\n'
    text+='\n'.join(f"|{r['id']}|{r['resolution']}|`{r['path']}`|{r['bytes']}|`{r['sha256']}`|" for r in rows)
    text+='\n\n8794通过版、旧8769实验、正式资料库及原有保护仍需分别交接。旧分享包是独立交付，不默认修改或删除。8797对照已冻结自有副本。精确消费者与保护登记见同目录审查JSON。\n'
    (target/'审查清单.md').write_text(text,encoding='utf-8');(OUT/'当前审查.json').write_text(json.dumps({'directory':str(target),'sha256':sha(target/'审查.json')},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'files':len(rows),'bytes':result['bytes'],'active_protections':len(tasks),'directory':str(target),'deleted':False},ensure_ascii=False))
if __name__=='__main__':main()
