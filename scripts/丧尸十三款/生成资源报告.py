"""从冻结资源与真实回执生成离线HTML、脱敏JSON/CSV和精确待审清单。"""
import argparse
import base64
import csv
from datetime import datetime,timezone
import html
from io import BytesIO
from pathlib import Path
import re
import sys
from PIL import Image

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts/资源库'))
from 统一资源 import HOME,current,read,record_path,save,sha


def image_uri(path):
    with Image.open(path) as im:
        b=BytesIO();im.convert('RGB').resize((320,320)).save(b,format='JPEG',quality=88)
    return 'data:image/jpeg;base64,'+base64.b64encode(b.getvalue()).decode()


def deletion_candidates(batch,delivery):
    reference=batch/'待审清单/清理执行引用.json'
    if reference.exists():
        link=read(reference);receipt=(ROOT/link['receipt']).resolve()
        if not receipt.is_relative_to(ROOT/'local-data/维护清理') or sha(receipt)!=link['sha256']:
            raise ValueError('清理执行回执路径或摘要变化')
        executed=read(receipt);frozen=batch/'待审清单/历史候选.json';original=read(frozen)
        if executed['status']!='完成' or sha(frozen)!=executed['manifest_sha256']:
            raise ValueError('清理尚未完成或原冻结清单变化')
        removed={str((ROOT/r['path']).resolve()).casefold() for r in executed['files'] if r['state']=='purged'}
        expected={str(Path(r['完整路径']).resolve()).casefold() for r in original['files']}
        if removed!=expected or executed['deleted_files']!=original['count'] or executed['deleted_bytes']!=original['bytes']:
            raise ValueError('清理结果与原冻结范围不一致')
        if any(Path(r['完整路径']).exists() for r in original['files']):
            raise ValueError('已清除的旧路径重新出现，需要重新核对')
        return {'count':original['count'],'bytes':original['bytes'],'csv_sha256':original['csv_sha256'],
                'deleted':executed['deleted_files'],'quarantined':0,'status':'已永久清除',
                'receipt':link['receipt'],'receipt_sha256':link['sha256']}
    _,_,routes,index=current();active={str(record_path(v)).casefold() for v in routes.values()}
    masters=read(index/'主工程.json');active.update(str(record_path(v)).casefold() for v in masters.values())
    candidates=[]
    for previous in ['批次001','批次002']:
        for file in sorted((batch.parent/previous).rglob('*')):
            if not file.is_file() or file.suffix not in ('.blend','.glb'):continue
            match=re.search(r'角色(\d{2})',file.as_posix())
            if not match:continue
            number=int(match.group(1));role=delivery['models'][number]
            if file.suffix=='.blend':replacement=Path(role['blend']['path']);category='被校正版本替代的工程'
            elif file.name=='运行角色.glb':replacement=record_path(masters[role['id']+':source']);category='被校正版本替代的运行源'
            else:
                resolution=512 if '512' in file.name else 256;replacement=Path(next(v['path'] for v in role['variants'] if v['resolution']==resolution));category='未入库的旧规格'
            assert str(file.resolve()).casefold() not in active
            candidates.append({'完整路径':str(file.resolve()),'字节':file.stat().st_size,'SHA256':sha(file),'类别':category,'保留替代路径':str(replacement),'保留替代SHA256':sha(replacement),'保留关系':'原始归档和当前主工程继续保留；本项为失败或已修正专项批次','依赖复核':'当前库路由、主工程和冻结交付未引用；历史回执仍作为追溯证据保留','任务保护':'本专项与既有未知范围仍保护；未解除保护','处理状态':'候选，等待工程师对本精确清单的新确认；未删除未回收'})
    out=batch/'待审清单';out.mkdir(exist_ok=True)
    path=out/'历史候选.csv'
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(candidates[0]));writer.writeheader();writer.writerows(candidates)
    result={'schema':1,'approved':False,'executed':False,'count':len(candidates),'bytes':sum(r['字节'] for r in candidates),'csv_sha256':sha(path),'files':candidates,'scope':'仅本专项批次001和002的被替代工程及GLB；不涉及既有清理载荷','deleted':0,'quarantined':0}
    save(out/'历史候选.json',result)
    return {'count':result['count'],'bytes':result['bytes'],'csv_sha256':result['csv_sha256'],'deleted':0,'quarantined':0}


def performance_reference(delivery_sha):
    """只引用性能负责方已归档的同一资源交付，不启动采样。"""
    path=ROOT/'research/丧尸模型/十三款混合性能记录.json'
    report=ROOT/'reports/丧尸模型/十三款混合群体性能报告.html'
    if not path.is_file() or not report.is_file():
        return {'status':'待性能负责方归档','description':'由指定性能对话以同一冻结资源交付独立出具；本资源报告不继承旧单款700成绩'},'混合群体性能由指定对话独立出具，前台不可证明的样本不作硬件验收。'
    data=read(path);assert data['delivery']['sha256']==delivery_sha
    samples=[s for s in data['samples'] if s['config']['actorSet']=='mixed' and s['config']['count']==500 and s['duration']==180 and s['environment']['browserLabel']=='iab' and s['stable60Passed'] and s['nativeForegroundVerified'] and s['valid']]
    selected={s['config']['mode']:s for s in samples}
    assert all(mode in selected for mode in ['open','channel'])
    results=[{'mode':mode,'fps':selected[mode]['summary']['fps'],'p95_ms':selected[mode]['summary']['p95Ms'],'raw_sha256':selected[mode]['raw_sha256'],'code_sha256':selected[mode]['config']['code_sha256']} for mode in ['open','channel']]
    assert results[0]['code_sha256']==results[1]['code_sha256']
    result={'status':'本机内置浏览器500只双场景180秒通过','record':'research/丧尸模型/十三款混合性能记录.json','record_sha256':sha(path),'report':'reports/丧尸模型/十三款混合群体性能报告.html','results':results,'remaining':['Chrome／Edge独立前台性能'],'scope':'640×360、DPR1、RX 7900 XTX；500为本页引用的已通过档位，更多档位及容量边界以性能报告为准'}
    text=f'本机内置浏览器500只混合群体双场景180秒通过：开阔{results[0]["fps"]:.1f} FPS／P95 {results[0]["p95_ms"]:.1f}毫秒，通道{results[1]["fps"]:.1f} FPS／P95 {results[1]["p95_ms"]:.1f}毫秒。640×360、DPR1、RX 7900 XTX；Chrome／Edge独立前台性能仍待验证。500为本页引用的已通过档位，更多档位与容量边界见<a href="十三款混合群体性能报告.html">完整性能报告与数据</a>。'
    return result,text


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--batch',type=Path,required=True);a=parser.parse_args();batch=a.batch.resolve()
    delivery_path=ROOT/'local-data/丧尸模型/十三款优化/交接/当前交付.json';delivery=read(delivery_path)
    pointer,catalog,routes,index=current();assert sha(HOME/'当前版本.json')==delivery['library_pointer_sha256']
    browser=read(batch/'浏览器验证核验.json');blender=read(batch/'Blender回读.json');fixed=read(batch/'固定入口浏览器核验.json');http=read(batch/'固定入口HTTP核验.json');publication=read(batch/'发布保护核验.json')
    assert all(r['passed'] for r in [browser,blender,fixed,http,publication])
    output=ROOT/'reports/丧尸模型';output.mkdir(exist_ok=True);rows=[];cards=[]
    for role in delivery['models']:
        i=role['source_mesh_index'];job=read(batch/f'角色{i:02}/制作回执.json') if i else None
        checks=[x for b in browser['browsers'] for x in b['roles'] if x['id']==role['id']]
        row={'序号':i,'稳定ID':role['id'],'骨骼':role['bones'],'材质':role['materials'],'三角形':role['triangles'],'静置高度米':role['coordinate_system']['rest_height_m'],'256字节':next(v['bytes'] for v in role['variants'] if v['resolution']==256),'512字节':next(v['bytes'] for v in role['variants'] if v['resolution']==512),'原动作采样数':job['split_validation']['samples'] if job else '沿用编号0来源身份','原动作拆分最大误差米':job['split_validation']['max_error_m'] if job else None,'派生姿态最大误差米':max(x['pose_error_m'] for x in checks),'循环最大误差米':max(c['loop_max_error_m'] for x in checks for c in x['clips']),'双浏览器双档':'通过','人工验收':role['human_acceptance']}
        rows.append(row)
        views=''.join(f'<figure><img loading="lazy" width="320" height="320" alt="角色{i:02}{name}" src="{image_uri(batch/f"浏览器验证/chrome/角色{i:02}-256/{name}.png")}"><figcaption>{name}</figcaption></figure>' for name in ['正面','侧面','背面','待机'])
        cards.append(f'<article data-search="{html.escape(role["id"])} {i:02}"><header><b>{i:02}</b><span>{html.escape(role["id"])}</span><em>人工待验</em></header><div class="views">{views}</div><p>{role["bones"]}骨 · {role["triangles"]}三角形 · 高{row["静置高度米"]:.3f}米</p><p class="sub">256：{row["256字节"]:,}字节　／　512：{row["512字节"]:,}字节</p></article>')
    cleanup=deletion_candidates(batch,delivery)
    performance,performance_text=performance_reference(sha(delivery_path))
    guide=ROOT/'docs/项目开发经验/模型与音频优化复用指南.md'
    save(batch/'规范落实记录.json',{'read_at':datetime.now(timezone.utc).isoformat(),'guide_sha256':sha(guide),'applied':['按实际加权骨与必要祖先拆分，保留形体和原拓扑','原动作保真、自制循环和朝向分别核验','256/512均为简化材质；512不恢复法线','原件、当前可编辑工程和来源摘要保留','技术、人工和性能验收独立','共享锁、预期指针及原子发布，真实HTTP资源回归','与性能对话明确交接GPU窗口，采样期间冻结资源'],'remaining':['人工逐款外观与动作验收','Chrome／Edge独立前台性能及容量边界']+([] if cleanup.get('status')=='已永久清除' else ['删除精确清单待新确认']),'audio_scope':'仅回归原靶场13声音，不新增或重编码音频'})
    summary={'schema':1,'batch_id':delivery['batch_id'],'source_sha256':delivery['source_sha256'],'delivery_sha256':sha(delivery_path),'library_revision':pointer['revision'],'library_pointer_sha256':delivery['library_pointer_sha256'],'catalog_sha256':delivery['catalog_sha256'],'models_in_library':catalog['summary']['models'],'audio_events_unchanged':catalog['summary']['audio'],'technical_passed':True,'human_acceptance':'待工程师验收','blender':{'new_final_glb_readbacks':24,'new_master_readbacks':12,'existing_zero_master_readback':True,'zero_ktx2_variants':'保留既有资源，双浏览器直接回读，不称为Blender直接导入KTX2'},'browser_checks':52,'browser_versions':{b['channel']:b['version'] for b in browser['browsers']},'default_bytes':sum(r['256字节'] for r in rows),'fallback_bytes':sum(r['512字节'] for r in rows),'models':rows,'cleanup':cleanup,'performance':performance,'guide':'docs/项目开发经验/模型与音频优化复用指南.md','license':{'author':delivery['author'],'name':delivery['license'],'url':delivery['source_url']}}
    save(ROOT/'research/丧尸模型/十三款资源技术记录.json',summary)
    csv_file=output/'十三款资源技术记录.csv'
    with csv_file.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    table=''.join(f'<tr><td>{r["序号"]:02}</td><td>{r["稳定ID"]}</td><td>{r["骨骼"]}</td><td>{r["三角形"]}</td><td>{r["静置高度米"]:.3f}</td><td>{r["256字节"]:,}</td><td>{r["512字节"]:,}</td></tr>' for r in rows)
    document='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>十三款丧尸独立资源技术报告</title>
<style>:root{--ink:#17261f;--paper:#eeeade;--accent:#b2cb52;--line:#c6c5b8}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font-family:"Microsoft YaHei","Noto Sans CJK SC",sans-serif;line-height:1.7}main{max-width:1460px;margin:auto;padding:42px 38px}h1,h2{font-family:"STSong","Songti SC",serif;line-height:1.2}h1{font-size:clamp(38px,6vw,76px);max-width:1000px;margin:28px 0}h2{font-size:32px;margin-top:0}.eyebrow{letter-spacing:.12em;font-size:12px;border-bottom:2px solid var(--ink);padding-bottom:12px}.lead{max-width:790px;font-size:18px}.metrics{display:grid;grid-template-columns:repeat(4,1fr);margin:36px 0;border-block:1px solid var(--ink)}.metrics div{padding:22px 14px 22px 0}.metrics strong{display:block;font-size:42px;line-height:1.2}.metrics span{font-size:13px}.note{background:var(--ink);color:var(--paper);padding:24px 28px;border-left:8px solid var(--accent)}section{padding:40px 0;border-bottom:1px solid var(--line)}a{color:inherit;text-underline-offset:4px}input{padding:12px;border:1px solid var(--ink);font:inherit;background:transparent;min-width:280px}input:focus-visible{outline:3px solid #7a9634;outline-offset:3px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px;margin-top:24px}article{background:#182128;color:#eef0eb;padding:16px}article header{display:flex;align-items:center;gap:12px;border-bottom:1px solid #4a5359;padding-bottom:14px}article b{font-size:32px;color:var(--accent)}article span{font:13px monospace}em{font-size:11px;font-style:normal;margin-left:auto;color:#e4c886}.views{display:grid;grid-template-columns:repeat(4,1fr);gap:4px}figure{margin:0;text-align:center}img{display:block;width:100%;height:auto}figcaption{font-size:11px;color:#bcc6cb}article p{margin:12px 0 0}.sub{font-size:12px;color:#b9c5c8}table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:12px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}th{background:#dfdfd1}.scroll{overflow:auto}ol{padding-left:22px}code{overflow-wrap:anywhere;font-size:12px}.twocol{display:grid;grid-template-columns:1fr 1fr;gap:36px}.small{font-size:13px;color:#516156}footer{padding:24px 0;font-size:12px}@media(max-width:800px){main{padding:24px 16px}.grid,.twocol{grid-template-columns:1fr}.metrics{grid-template-columns:1fr 1fr}.views{grid-template-columns:repeat(2,1fr)}.metrics strong{font-size:34px}}@media print{input{display:none}article{break-inside:avoid}main{max-width:none;padding:0}}</style>
<main><div class="eyebrow">独立角色技术档案　／　ZOM-13-261103-003　／　统一库 revision __REV__</div><h1>十三款丧尸<br>独立资源技术报告</h1><p class="lead">保留13种原始形体与独立蒙皮，完成剩余12款拆分、动作整理、256／512派生及增量入库。编号0复用既有稳定资源。</p><div class="metrics"><div><strong>13</strong><span>不同源网格与稳定角色ID</span></div><div><strong>143</strong><span>当前模型入口，声音仍为253</span></div><div><strong>52</strong><span>双浏览器 × 13款 × 2规格</span></div><div><strong>__SIZE__ MB</strong><span>13款256档资源总字节，非运行内存</span></div></div><div class="note">资源技术核验已通过。人工外观与动作验收、混合群体性能分别记录；本页不承接旧单款700只成绩。</div>
<section class="twocol"><div><h2>验证的实际范围</h2><ol><li>新增12款各721个原动作采样点逐顶点对照，误差阈值0.0001米。</li><li>新增24份最终GLB及12份新工程在独立Blender进程重开；编号0现有主工程重新读取，既有KTX2双档由浏览器直接核验。</li><li>待机2秒、移动1.25秒均为项目自制。循环首尾、蒙皮变化、停走混合和资源释放通过。</li><li>Chrome／Edge实际加载8767新增卡片双档，8768三模型与13声音回归通过。</li></ol></div><div><h2>保留的边界</h2><p>11款新增角色为23骨，编号11为21骨；不补造头颈骨，不进行静态减面。共同来源尺度保留身高差异，编号11约0.967米。</p><p>新12款为Meshopt＋内嵌PNG；编号0继续KTX2。512只提高底色分辨率，两档均简化材质，不恢复已移除法线。</p><p>逐帧整体落地校正不是足部IK；没有真实运动捕捉或完整人群碰撞验收。</p></div></section>
<section><h2>逐款证据</h2><p>正面、侧面、背面为移动循环0.3125秒；待机为0.5秒。图像来自Chrome真实渲染，均为256档。原始512与Edge证据保存在本地冻结批次。</p><label>查找角色　<input id="search" type="search" placeholder="输入稳定ID或编号" aria-label="查找角色"></label><div class="grid">__CARDS__</div></section>
<section><h2>规格与身份</h2><div class="scroll"><table><thead><tr><th>编号</th><th>稳定ID</th><th>骨骼</th><th>三角形</th><th>高度／米</th><th>256／字节</th><th>512／字节</th></tr></thead><tbody>__TABLE__</tbody></table></div><p><a href="十三款资源技术记录.csv">下载技术CSV</a> · <a href="../../research/丧尸模型/十三款资源技术记录.json">技术JSON</a></p><p class="small">13款512档合计__FALLBACK__字节。整包原件36,182,136字节仍保留；整包原动作和独立自制循环范围不同，不据此计算无损压缩率。</p></section>
<section class="twocol"><div><h2>发布与协作</h2><p>发布器验证排他锁、版本竞争、输入摘要变化不切换及成功增量。新索引在独立目录完整写入后原子切换；旧索引和既有资源保留。当前143模型、253声音，原131模型卡片、声音和原件映射保持一致。</p><p>__PERFORMANCE__</p><p><a href="http://127.0.0.1:8767/">统一模型库</a> · <a href="http://127.0.0.1:8769/">群体实验</a> · <a href="../../docs/丧尸十三款拆分入库与混合群体-实施计划.md">专项计划</a></p></div><div><h2>__CLEANSTATUS__</h2><p>仅本专项前两批被校正的工程与GLB，__CLEANCOUNT__份、__CLEANBYTES__字节。精确CSV／JSON含完整路径、字节、SHA256、保留替代关系和依赖复核，保存在本地批次的待审清单。</p><p>原件、当前13款主工程、现有稳定资源、历史回执及图片继续保留。__CLEANRESULT__</p><code>清单SHA256：__CLEANSHA__</code></div></section>
<footer>来源：Jungle Jim，<a href="https://sketchfab.com/3d-models/super-low-poly-stylized-zombies-animated-6c112bed707b4444864b606299f8b85a">Super Low-poly Stylized Zombies</a>，CC BY 4.0。修改：独立拆分、骨架精简、材质和贴图规格、自制循环、统一朝向与整体落地。依据：<a href="../../docs/项目开发经验/模型与音频优化复用指南.md">模型与音频优化复用指南</a>。<br>资源交付SHA256：<code>__DELIVERY__</code><br>统一库指针SHA256：<code>__POINTER__</code></footer></main><script>document.querySelector('#search').addEventListener('input',e=>{const q=e.target.value.trim().toLowerCase();for(const c of document.querySelectorAll('article'))c.hidden=!c.dataset.search.includes(q);});</script></html>'''
    values={'__PERFORMANCE__':performance_text,'__REV__':str(pointer['revision']),'__SIZE__':f'{summary["default_bytes"]/1e6:.2f}','__CARDS__':''.join(cards),'__TABLE__':table,'__FALLBACK__':f'{summary["fallback_bytes"]:,}','__CLEANCOUNT__':str(cleanup['count']),'__CLEANBYTES__':f'{cleanup["bytes"]:,}','__CLEANSHA__':cleanup['csv_sha256'],'__DELIVERY__':summary['delivery_sha256'],'__POINTER__':delivery['library_pointer_sha256']}
    values['__CLEANSTATUS__']='旧版清理已完成' if cleanup.get('status')=='已永久清除' else '清理候选，尚未执行'
    values['__CLEANRESULT__']=(f'工程师明确要求立即清理后，已永久清除{cleanup["deleted"]}份、{cleanup["bytes"]:,}字节；回收载荷为0。原冻结CSV／JSON及逐文件清除回执保留，本次例外不改变后续七天规则。' if cleanup.get('status')=='已永久清除' else '删除与回收均为0；候选清单等待工程师新确认。')
    for key,value in values.items():document=document.replace(key,value)
    target=output/'十三款独立角色技术报告.html';target.write_text(document,'utf8')
    save(batch/'报告生成回执.json',{'html_sha256':sha(target),'technical_json_sha256':sha(ROOT/'research/丧尸模型/十三款资源技术记录.json'),'csv_sha256':sha(csv_file),'cleanup':cleanup,'guide_sha256':sha(guide)})
    print({'report':str(target),'default_bytes':summary['default_bytes'],'fallback_bytes':summary['fallback_bytes'],'cleanup':cleanup})


if __name__=='__main__':main()
