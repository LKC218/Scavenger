"""全部长测结束后独立重算逐帧数据，制作离线画面与性能报告。"""
import csv,hashlib,html,json,math,statistics,shutil,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2];DATA=ROOT/'local-data/原作群尸/V001-01a1aa48';OUT=ROOT/'reports/原作群尸';RUN=DATA/'长测/无窗口/高精度九六'
def read(p):return json.loads(p.read_text('utf-8'))
def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def percentile(v,p):v=sorted(v);return v[min(len(v)-1,math.floor((len(v)-1)*p))]
def main():
    target=OUT/'原作群尸运行与性能对比-V001.html'
    if target.exists():raise FileExistsError('报告已存在，修订须另存')
    cases=[];sources=set();audit=DATA/'统计复核';audit.mkdir(exist_ok=True)
    for count in [500,650,800]:
        for family in ['cartoon','native']:
            marker=read(RUN/f'{family}-{count}-180s-完成.json');path=DATA/'样本'/(marker['archive']['file']+'.json');raw=read(path);rows=raw['rows'];values=[r['frame'] for r in rows];total=sum(values);fps=len(rows)*1000/total;p95=percentile(values,.95);dropped=sum(r['dropped'] for r in rows)
            assert raw['environment']['headless'] and raw['requestedDurationS']==180
            assert abs(raw['summary']['fps']-fps)<1e-6 and abs(raw['summary']['p95Ms']-p95)<.11
            assert abs(raw['summary']['simulationDroppedS']-dropped)<1e-6
            assert min(r['visible'] for r in rows)==count and min(r['animated'] for r in rows)==count
            ids={m['id'] for m in raw['config']['models']}
            for row in rows:
                assert {m['id'] for m in row['models']}==ids and sum(m['count'] for m in row['models'])==count
                assert all(m['visible']==m['count'] and m['animated']==m['count'] for m in row['models'])
            sources.add(raw['config']['code_sha256']);bins={};origin=rows[0]['t']
            for row in rows:
                k=int((row['t']-origin)/1000);bins[k]=max(bins.get(k,0),row['frame'])
            fields=['t','frame','simulation','animation','sync','submit','gpu','calls','triangles','visible','animated','moving','neighbors','dropped'];model_fields=[m['id']+':'+k for m in raw['config']['models'] for k in ['count','visible','animated','moving']]
            with (audit/f'{family}-{count}-完整逐帧.csv').open('x',encoding='utf-8-sig',newline='') as f:
                writer=csv.writer(f);writer.writerow(fields+model_fields);writer.writerows([*[r.get(k) for k in fields],*[m[k] for m in r['models'] for k in ['count','visible','animated','moving']]] for r in rows)
            summary=raw['summary'];cases.append({'family':family,'count':count,'fps':fps,'p95_ms':p95,'p99_ms':percentile(values,.99),'max_ms':max(values),'gpu_ms':summary['gpuMs'],'gpu_samples':summary['gpuSamples'],'submit_cpu_ms':summary['cpuMs']['submit'],'sync_cpu_ms':summary['cpuMs']['sync'],'simulation_cpu_ms':summary['cpuMs']['simulation'],'calls':summary['meanCalls'],'triangles':summary['meanTriangles'],'dropped_s':dropped,'long33_pct':100*sum(v>33.3 for v in values)/len(values),'min_visible':summary['minVisible'],'min_animated':summary['minAnimated'],'elapsed_s':total/1000,'frames':len(rows),'sample':path.name,'sample_sha256':sha(path),'environment':raw['environment'],'budget_pass':fps>=60 and p95<=16.7 and 100*sum(v>33.3 for v in values)/len(values)<.1 and dropped==0,'peaks':sorted(bins.items()),'worst_rows':sorted(rows,key=lambda r:r['frame'],reverse=True)[:4]})
    assert len(sources)==1,'对照源码身份不一致'
    OUT.mkdir(parents=True,exist_ok=True);pictures=OUT/'画面-V001';pictures.mkdir(exist_ok=False)
    functional=next(p for p in sorted((DATA/'功能核验').iterdir(),reverse=True) if (p/'功能核验.json').exists() and read(p/'功能核验.json')['passed'])
    inspection=next(p for p in sorted((DATA/'画面检视').iterdir(),reverse=True) if (p/'检视回执.json').exists())
    for count in [500,650,800]:
        for family in ['cartoon','native']:
            shutil.copyfile(RUN/f'{family}-{count}-180s.png',pictures/f'{family}-{count}-长测.png')
            shutil.copyfile(functional/f'{family}-{count}-总览.png',pictures/f'{family}-{count}-同屏.png')
    for name in ['双角色-idle.png','双角色-move.png','双角色-attack.png','双角色-death.png','原作防线交战.png']:shutil.copyfile(inspection/name,pictures/name)
    pose=read(functional/'native-全顶点姿态.json');asset_rows=[]
    for role in ['Walker','Runner']:
        directory=ROOT/'local-data/断箭/批次/BA-26273833-014'/role;asset=read(directory/'压缩回执-r03.json');derivation=read(directory/'派生记录.json');asset_rows.append({'role':role,'bytes':asset['bytes'],'sha256':asset['sha256'],'bones':46,'clips':9,'triangles':derivation['triangles'],'source_unchanged':derivation['source_unchanged']})
    retirement=read(Path(read(DATA/'退役审查/当前审查.json')['directory'])/'审查.json')
    public={'schema':1,'version':'V001-GPU96','mode':'无窗口GPU压力对比，非前台稳定60帧验收','source_sha256':next(iter(sources)),'cases':[{k:v for k,v in c.items() if k not in ['peaks','worst_rows']} for c in cases],'assets':asset_rows,'pose':pose,'cartoon_retirement':{'files':retirement['files'],'bytes':retirement['bytes'],'deleted':False,'automatic_retirement_registered':False},'conditions':'工程师继续使用电脑，未建立整机空载条件；不含完整地图载具战斗负载；同屏为视锥内实例，允许互相遮挡'}
    (OUT/'性能摘要-V001.json').write_text(json.dumps(public,ensure_ascii=False,indent=2),encoding='utf-8')
    (audit/'统计复核.json').write_text(json.dumps({'source_sha256':next(iter(sources)),'cases':cases},ensure_ascii=False,indent=2),encoding='utf-8')
    rows_html=''
    for n in [500,650,800]:
        old=next(c for c in cases if c['count']==n and c['family']=='cartoon');new=next(c for c in cases if c['count']==n and c['family']=='native')
        rows_html+=f"<tr><th>{n}</th><td>{old['fps']:.1f} / {new['fps']:.1f}</td><td>{old['p95_ms']:.2f} / {new['p95_ms']:.2f}</td><td>{old['gpu_ms']:.3f} / {new['gpu_ms']:.3f}</td><td>{old['submit_cpu_ms']:.3f} / {new['submit_cpu_ms']:.3f}</td><td>{old['calls']:.0f} / {new['calls']:.0f}</td><td>{old['triangles']:,.0f} / {new['triangles']:,.0f}</td><td>{old['dropped_s']:.3f} / {new['dropped_s']:.3f}</td></tr>"
    max_frame=max(c['max_ms'] for c in cases);charts=''
    for n in [500,650,800]:
        lines='';top=max(50,max(c['max_ms'] for c in cases if c['count']==n));bottom=4
        def y(v):return 150-130*(math.log(max(v,bottom))-math.log(bottom))/(math.log(top)-math.log(bottom))
        for c in [c for c in cases if c['count']==n]:
            points=' '.join(f'{30+t/180*710:.2f},{y(v):.2f}' for t,v in c['peaks']);color='#d8e89b' if c['family']=='native' else '#8aa08a';lines+=f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="1.5"><title>{c["family"]}，最大{c["max_ms"]:.1f}毫秒</title></polyline>'
        grid=''.join(f'<line x1="30" x2="750" y1="{y(v):.2f}" y2="{y(v):.2f}" stroke="#3b493c"/><text x="0" y="{y(v)+4:.2f}" fill="#aebaa2" font-size="10">{v:g}</text>' for v in [4,8,16.7,33.3,100,1000] if v<=top)
        charts+=f'<div class="chart"><h3>{n}只 · 每秒最大帧时间</h3><svg viewBox="0 0 770 175" role="img" aria-label="{n}只每秒峰值帧时间，对数纵轴">{grid}{lines}</svg></div>'
    image_sets=''.join(f'<div class="image-set" data-count="{n}"'+(' hidden' if n!=800 else '')+f'><figure><img src="画面-V001/cartoon-{n}-同屏.png"><figcaption>旧卡通 · {n}只 · 同一总览镜头</figcaption></figure><figure><img src="画面-V001/native-{n}-同屏.png"><figcaption>Walker／Runner各半 · {n}只</figcaption></figure></div>' for n in [500,650,800])
    notes=''.join(f'<li>{"原作" if c["family"]=="native" else "旧卡通"}{c["count"]}只：最大帧{c["max_ms"]:.1f}ms，长帧{c["long33_pct"]:.4f}%，模拟欠时{c["dropped_s"]:.3f}s。</li>' for c in cases)
    env=cases[0]['environment'];budget=sum(c['budget_pass'] for c in cases);posemax=max(r['maximum'] for r in pose['reports'])
    doc=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>原作群尸运行与性能对比</title><style>
    :root{{color-scheme:dark;font-family:"Microsoft YaHei UI","Noto Sans SC",sans-serif;color:#e2e8d7;background:#172019}}*{{box-sizing:border-box}}body{{margin:0}}main{{max-width:1320px;margin:auto;padding:42px 36px}}.eyebrow{{font-size:12px;letter-spacing:.14em;color:#a5b492}}h1{{font-size:38px;letter-spacing:.04em;font-weight:550;margin:14px 0}}h2{{font-size:22px;font-weight:500;margin:42px 0 20px;border-top:1px solid #43503e;padding-top:22px}}h3{{font-size:14px;font-weight:400;color:#b9c7a8}}p,li{{font-size:14px;line-height:1.9;color:#b8c5ab}}.lead{{font-size:18px;max-width:980px;color:#d3dec5}}.strip{{display:flex;gap:36px;padding:24px 0;border-block:1px solid #43503e;margin:26px 0}}.strip b{{font-size:30px;font-weight:500;color:#d8e89b}}.strip small{{display:block;color:#9cab8d;font-size:12px;margin-top:7px}}table{{width:100%;border-collapse:collapse;font-size:12px;font-variant-numeric:tabular-nums}}th,td{{padding:13px 10px;text-align:left;border-bottom:1px solid #3b493c}}thead{{background:#263323}}th{{font-weight:500;color:#d8e89b}}.scroll{{overflow:auto}}.notice{{padding:16px 20px;border-left:3px solid #b0bf7c;background:#263323;line-height:1.9;font-size:13px;color:#c3cfb6}}button{{background:#263323;color:#dce6ce;border:1px solid #6b7a54;padding:8px 18px;margin-right:8px;cursor:pointer;border-radius:2px}}button.active{{background:#d8e89b;color:#243018}}.image-set,.portraits{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:20px}}[hidden]{{display:none!important}}figure{{margin:0;background:#222e22}}img{{display:block;width:100%;height:auto}}figcaption{{padding:12px 14px;font-size:12px;color:#a9b899;line-height:1.8}}.chart{{background:#202b21;padding:14px 18px;margin:15px 0}}a{{color:#d8e89b}}.small{{font-size:12px;color:#92a182;overflow-wrap:anywhere}}p,.notice,figcaption{{overflow-wrap:anywhere}}svg{{display:block;width:100%;height:auto}}@media(max-width:750px){{main{{padding:24px 16px}}h1{{font-size:29px}}.strip{{gap:18px;flex-wrap:wrap}}.image-set,.portraits{{grid-template-columns:1fr}}}}</style><main>
    <div class="eyebrow">运行候选 V001 / GPU96Hz / 原骨架与原生片段保留</div><h1>原作群尸<br>运行与180秒性能对比</h1>
    <p class="lead">Walker与Runner的LOD2已制作为独立运行版，接入GPU骨骼合批，并迁移两个日常启动入口。下表只使用最终96Hz参数的六组完整样本，初轮及不合格窗口记录另行保留。</p>
    <div class="strip"><div><b>330 / 447</b><small>单角色三角形</small></div><div><b>46 × 2</b><small>原骨架节点，未减骨</small></div><div><b>9 × 2</b><small>原生动作片段</small></div><div><b>{posemax*1000:.2f}mm</b><small>GPU帧间抽样最大顶点差</small></div></div>
    <div class="notice">工程师选择在测试期间继续使用电脑，本轮是<strong>无窗口硬件GPU压力对比</strong>，不是独立前台稳定60帧验收。全部实例持续位于视锥并更新动作，允许相互遮挡。此场景不含完整地图、载具战斗与爆炸负载。帧率接近约165Hz调度上限时，不能把它当成模型的绝对承载上限。</div>
    <h2>最终六组180秒对照</h2><p>各格均为“旧卡通 / 原作”。渲染尺寸640×360、30Hz共享群体逻辑、相同正交镜头及合批代码，预热10秒。原作各半，旧十三款均匀分配。绘制与三角形包含地面、网格、脚底阴影等场景开销。</p>
    <div class="scroll"><table><thead><tr><th>数量</th><th>平均FPS</th><th>P95 ms</th><th>GPU均值 ms</th><th>提交CPU ms</th><th>绘制次数</th><th>总三角形</th><th>欠时 s</th></tr></thead><tbody>{rows_html}</tbody></table></div>
    <p>六组中有{budget}组满足本轮无窗口帧预算及零模拟欠时条件。该计数不会改写为前台验收；出现长帧或欠时的组继续单列。</p>
    <h2>全部长帧保留</h2><p>下图取每秒最大帧时间，纵轴为对数坐标。浅黄为原作，灰绿为旧卡通；没有把长帧从统计中剔除。</p>{charts}<ul>{notes}</ul>
    <h2>同屏画面对照</h2><div><button data-show="500">500只</button><button data-show="650">650只</button><button class="active" data-show="800">800只</button></div>{image_sets}
    <h2>实际运行网格与原生动作</h2><p>以下双角色画面是专门的GPU姿态夹具，用于辨认外形和动作，不替代上方群体数量证据。两款各一个蒙皮网格、一个材质；原始工程、LOD2工程及原始片段另存。原包Avatar求解、GLB编码和GPU矩阵插值分别回读，未声称恢复原游戏状态机、事件回调或VFX。</p>
    <div class="portraits">{''.join(f'<figure><img src="画面-V001/双角色-{s}.png"><figcaption>{label} · Walker与Runner</figcaption></figure>' for s,label in [('idle','待机'),('move','移动'),('attack','攻击'),('death','死亡')])}</div>
    <h2>防线候选与逐步退役</h2><figure><img src="画面-V001/原作防线交战.png"><figcaption>8798原作防线V004候选，复用V003已通过玩法，仅更换角色适配；这张功能画面不代表完整战斗180秒性能验收。</figcaption></figure>
    <p>日常群体入口为8797，防线入口为8798。8794 V003仍保持通过身份与原18份运行源码，不重启或覆盖其验证凭证。旧卡通默认引用已迁出，8797对照使用自有冻结副本。</p>
    <p>旧卡通26份256／512运行文件合计{retirement['bytes']:,}字节已列精确审查清单；8794通过版、8769旧实验和正式资料库仍需分别交接，原有分享包为独立交付。尚未自动回收这些旧载荷，删除数为0；原件及最新可编辑工程继续保留。</p>
    <h2>环境与证据边界</h2><p>{html.escape(env['gpu'])}<br>{html.escape(env['userAgent'])}<br>统一源码SHA-256：<span class="small">{next(iter(sources))}</span></p>
    <p>逐帧JSON／CSV、原始画面、完整顶点采样和精确退役清单保存在本轮本地目录。统计程序独立重算帧数、FPS、P95、欠时及每帧模型分布；GPU时间仅为有效WebGL查询，不代表整机显卡时间。初轮48Hz全顶点偏差约5.3毫米，96Hz修订后约1.74毫米，最终表不混入旧参数数据。</p>
    <p><a href="性能摘要-V001.json">查看性能摘要JSON</a> · <a href="../../src/原作群尸/README.md">运行模块说明</a> · <a href="../../scripts/原作群尸/README.md">制作与复现</a></p><p class="small">技术、智能体视觉复核与工程师具体验收分别记录。原始文件及全部探索记录保留。</p>
    </main><script>for(const b of document.querySelectorAll('[data-show]'))b.onclick=()=>{{document.querySelectorAll('[data-show]').forEach(x=>x.classList.toggle('active',x===b));document.querySelectorAll('[data-count]').forEach(x=>x.hidden=x.dataset.count!==b.dataset.show)}};</script></html>'''
    target.write_text(doc,encoding='utf-8');print(json.dumps({'report':str(target),'cases':len(cases),'source_consistent':len(sources)==1,'budget_passed':budget},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
