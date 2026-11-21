"""将指定浏览器的完整有效矩阵生成离线HTML、研究摘要和CSV。"""
import argparse
import base64
import csv
import hashlib
import io
import json
from pathlib import Path
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'local-data/丧尸模型/性能测试/ZOM-PERF-261103-001'
OUT=DATA/'浏览器验证'
REPORT=ROOT/'reports/丧尸模型/五百只同屏性能测试报告.html'
RECORD=ROOT/'research/丧尸模型/性能测试记录.json'

TEMPLATE=r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>丧尸同屏 · 动态容量实测</title><link rel="icon" href="data:,"><style>
:root{--bg:#f0f0e6;--ink:#243327;--muted:#687461;--line:#cbd0bd;--green:#45623b;--lime:#d4ee80;--orange:#ac7047}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:"Microsoft YaHei UI","Microsoft YaHei",sans-serif;line-height:1.8}a{color:#45663c}nav{background:#223126;color:#dfe7ce;display:flex;justify-content:space-between;gap:20px;padding:18px max(24px,calc((100vw - 1240px)/2));font-size:12px;letter-spacing:2px}nav a{color:var(--lime)}main{max-width:1240px;margin:auto;padding:52px 28px 70px}.eyebrow{font-size:11px;letter-spacing:3px;color:var(--muted)}h1{font-weight:400;font-size:clamp(36px,5vw,66px);line-height:1.25;margin:18px 0 22px;letter-spacing:2px}h1 em{font-family:Consolas;font-style:normal;font-size:1.3em;color:#49673f}h2{font-size:26px;font-weight:400;letter-spacing:1px;margin:0 0 14px}h3{font-size:16px;font-weight:500}p{font-size:13px;max-width:920px}.lead{font-size:15px;color:var(--muted);max-width:900px}.tag{display:inline-block;background:#dfe5d0;border:1px solid #bdc9ad;font-size:10px;padding:3px 9px;margin:0 8px 8px 0}.kpis{display:grid;grid-template-columns:repeat(4,1fr);border-block:1px solid var(--line);margin:32px 0 35px}.kpis>div{padding:24px 18px 24px 0}.kpis span,.kpis small{display:block;font-size:10px;color:var(--muted)}.kpis b{font:40px/1.6 Consolas;font-weight:400}.kpis b i{font-size:15px;font-style:normal;margin-left:6px}section{margin-top:54px;padding-top:24px;border-top:1px solid var(--line)}.number{font:12px Consolas;color:var(--muted);margin-bottom:8px}.two{display:grid;grid-template-columns:1.4fr 1fr;gap:30px;align-items:start}.photo{display:block;width:100%;border:1px solid var(--line);background:#273522}.caption{font-size:10px;color:var(--muted);margin:8px 0 22px}.panel{background:#e5e8da;border:1px solid var(--line);padding:22px}.filters{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin:20px 0}label{font-size:11px;color:var(--muted)}select,button{font:12px "Microsoft YaHei";background:#f5f5ee;border:1px solid #aebba3;color:var(--ink);padding:8px 12px;margin-left:6px;cursor:pointer}button{margin:0}select:focus-visible,button:focus-visible{outline:2px solid var(--green);outline-offset:2px}.chart-wrap{overflow:auto;background:#e7eadf;padding:18px 18px 10px;border:1px solid var(--line)}svg{width:100%;height:auto;display:block}.chart-wrap svg{min-width:1000px}.legend{display:flex;gap:22px;font-size:11px;color:var(--muted);margin:12px 0}.legend i{width:12px;height:9px;display:inline-block;margin-right:6px}.empty{min-height:180px;display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:13px}.table-wrap{overflow:auto;border:1px solid var(--line)}table{border-collapse:collapse;font-size:11px;width:100%;white-space:nowrap}th{text-align:left;background:#dfe5d3;color:#4a5e40;font-weight:400}th,td{padding:10px 12px;border-bottom:1px solid var(--line)}td.n{font-family:Consolas}tr.long td{background:#e6ecd9}.note{font-size:11px;color:var(--muted)}.rules{display:grid;grid-template-columns:repeat(3,1fr);gap:20px}.rules article{border-left:2px solid #92a77a;padding-left:16px}.rules h3{margin:0 0 7px}.rules p{font-size:12px;margin:0}.code{font:11px Consolas;overflow-wrap:anywhere;background:#e4e7d8;padding:12px}ul{font-size:13px;padding-left:20px}li{margin:7px 0}.workflow{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--line);border:1px solid var(--line);margin:20px 0}.workflow div{background:#e7ebdd;padding:18px;font-size:12px}.workflow b{font:24px Consolas;display:block;color:#74905c}footer{border-top:1px solid var(--line);margin-top:40px;padding-top:20px;font-size:11px;color:var(--muted)}@media(max-width:760px){main{padding:32px 18px}.two,.rules{grid-template-columns:1fr}.kpis{grid-template-columns:1fr 1fr}.kpis b{font-size:32px}.workflow{grid-template-columns:1fr 1fr}nav{letter-spacing:0}.filters{gap:8px}}@media print{nav,.filters,.print-button{display:none}main{max-width:none;padding:0}.two{grid-template-columns:1fr}section{break-inside:avoid}table{font-size:8px}th,td{padding:5px}body{background:white}.table-wrap{overflow:visible}}
</style></head><body><nav><span>群体实验 / 动态性能记录</span><a href="http://127.0.0.1:8769/">打开本机测试场 ↗</a></nav><main><div class="eyebrow">THREE.JS · WEBGL2 · 2026-11-03</div><h1><em>500</em> 只是起点。<br>多少只，能稳定60帧。</h1><p class="lead">独立骨骼动画、整群目标移动、障碍通道与真实帧时间。内置浏览器与独立桌面浏览器分别记录，宿主前台比例随样本公开；功能截图、模型核验和性能分别留证。</p><span class="tag" id="sample-count"></span><span class="tag">640×360 默认像素画面</span><span class="tag"><span id="browser-tag"></span></span><div class="kpis"><div><span>最高双场景通过档 · 最低FPS</span><b id="k-fps"></b><small>开阔 / 通道 · 已测环境</small></div><div><span>最高通过档 · 最慢P95</span><b id="k-p95"></b><small>帧间隔，包含真实长帧</small></div><div><span>500只基线的动画更新</span><b>500<i>只</i></b><small>独立骨架及动作相位</small></div><div><span>500只基线的角色几何</span><b>40<i>万三角形</i></b><small>另含场地、标记和简化投影</small></div></div>
<div class="panel"><h2 id="capacity-title">容量边界</h2><p id="capacity-result"></p><p>判据：180秒平均≥60 FPS，P95≤16.7ms，超过33.3ms的帧少于0.1%；全群位于视锥内、持续骨骼动画、无模拟欠时。20秒探索只用于收窄范围。更高数量压缩在同一场地，拥挤与身体交叠随密度增加。</p></div><div class="two"><div>__OPEN_IMAGE__<p class="caption">500只开阔群体移动，可见页面500只实测截图。截图中的即时数值不替代下方完整样本统计。</p></div><div class="panel"><h2>本轮完成什么</h2><p>同一款23骨骼、800三角形角色实例化为500个独立蒙皮角色，共享几何与贴图，使用8份共享配色材质。待机与移动是本项目自制循环，移动速度关联步频。</p><p>群体采用共享流场与空间网格分离；右键地面更新目标区域，窄通道检查拥堵与绕行。模拟固定30Hz，画面插值，目标每24秒换向保持负载。</p><p id="headline-result"></p><p class="note">60 FPS对应约16.7ms。平均帧率、P95/P99及长帧比例需要一起阅读。</p></div></div>
<section><div class="number">01 / 对照实验</div><h2>把渲染、动画与移动分开看</h2><p>四种模式使用同一批角色、固定种子和总览镜头。每组预热10秒，探索记录20秒、常规记录60秒，关键数量用180秒长测复核。图上短测时长可能不同，以标签为准；跨源码版本不能作严格增量对照。</p><div class="filters"><label>场景<select id="mode"><option value="static">静态角色</option><option value="animated">原地骨骼动画</option><option value="open" selected>开阔群体移动</option><option value="channel">障碍窄通道</option></select></label><label>内部宽度<select id="width"><option value="640">640 × 360</option><option value="480">480 × 270</option><option value="1600">1600 × 900</option></select></label><span class="note">仅显示已测组合；分辨率未测组合保持缺项。</span></div><div class="legend"><span><i style="background:#527345"></i>Chrome</span><span><i style="background:#b08157"></i>Edge</span><span>虚线：60 FPS</span></div><div class="chart-wrap" id="bars"></div><p class="note">柱高表示完整样本平均FPS。屏幕刷新与浏览器调度可能限制上限；低分辨率只减少部分GPU像素成本，动画、绘制调用与群体逻辑仍存在。</p></section>
<section><div class="number">02 / 长时间采样</div><h2>观察尾部帧时间与长帧</h2><div class="filters"><label>浏览器<select id="browser"><option value="chrome">Chrome</option><option value="msedge">Edge</option></select></label><label>数量<select id="long-count"></select></label><label>场景<select id="long-mode"><option value="open">开阔移动 · 180秒</option><option value="channel">通道移动 · 180秒</option></select></label></div><div class="chart-wrap" id="timeline"></div><p class="caption">每个等长时间窗取最长帧，保留尖峰；折线不是每帧原始数据。原始JSON和CSV保存在本地测试批次，页面可自行重新采样导出。</p><div id="long-table" class="table-wrap"></div><h3>CPU成本拆分</h3><div id="cpu-table" class="table-wrap"></div><p class="note">群体模拟、Mixer更新、位置与矩阵同步、渲染提交分别计时。CPU提交可能包含驱动上传准备；GPU为异步查询结果，两者不能相加当成完整帧时间。表格未分摊浏览器调度、UI和垃圾回收开销。</p></section>
<section><div class="number">03 / 实际交互与资源</div><h2>群体运动与动画证据</h2><div class="two"><div>__CHANNEL_IMAGE__<p class="caption">容量档的约4米中央通道，整群受障碍约束推进。简化局部分离允许拥挤时身体交叠，不代表完整人群物理。</p></div><div>__DETAIL_IMAGE__<p class="caption">100只原地骨骼动画近景：1600×900内部画面，无窗口功能示意。该图不用于前台性能结论。</p></div></div><div class="workflow"><div><b>01</b>右键地面<br>整群前往目标区域</div><div><b>02</b>WASD / 滚轮 / 中键<br>平移、缩放和旋转</div><div><b>03</b>C聚焦 / F复位<br>不自动跟随</div><div><b>04</b>采样 / 导出<br>预热后保存JSON和CSV</div></div><div class="rules"><article><h3>模型回读</h3><p id="model-proof"></p></article><article><h3>浏览器功能</h3><p id="checks-proof"></p></article><article><h3>资源共享</h3><p>500个独立骨架、一个共享角色几何。骨纹理与原贴图分别计数，纹理数量不是显存字节。几何、动画与实际位移均检查。</p></article></div></section>
<section><div class="number">04 / 完整样本表</div><h2>所有页面样本，可核对配置与宿主状态</h2><div id="all-table" class="table-wrap"></div><p class="note">视锥/动画列为采样期间最小值。静态模式有意停止动画，其他模式持续更新全部角色。长测行采用浅绿底色。GPU栏为有效异步查询的平均值，不支持则显示缺项。</p></section>
<section><div class="number">05 / 环境与边界</div><h2>这份结果适用于什么条件</h2><div id="environment" class="panel"></div><ul><li>独立测试场，使用单款低面数角色、简模地面与障碍，无攻击、死亡、弹丸、粒子和实时阴影负载。</li><li>标准采样期间锁定总览；500只位于视锥内且没有通过远处冻结或自动降分辨率减少工作量。页面隐藏或失焦的整组样本判为无效；宿主前后台混合样本另列前台比例，不能当作独立窗口前台验收。</li><li>模拟超额时间会累计为欠时；不能通过截断帧间隔隐藏卡顿。无窗口功能检查不并入前台性能结论。</li><li>本机高性能独显结果不能外推到核显、手机、Safari或完整战斗场景；后续应在目标设备按同样方法复测。</li><li>行走循环为基础测试动作，未进行足部IK或专业动作捕捉校准；正式游戏动画质量需另行制作与验收。</li></ul><p class="panel" id="pending"></p><h3>版本身份</h3><div class="code" id="identity"></div></section>
<section><div class="number">06 / 可复用经验</div><h2>下一阶段怎么优化</h2><div class="rules"><article><h3>先比较增量</h3><p>同数量静态与动画的差异用于定位动画成本；同动作原地与移动的差异用于定位群体与场景同步。保持镜头与资源相同。</p></article><article><h3>控制CPU工作量</h3><p>共享流场按事件重算，空间网格限制邻居查询；独立动画相位保留，资源共享。先根据实测处理主要成本，再评估GPU动画烘焙。</p></article><article><h3>保留测量条件</h3><p>记录内部渲染尺寸、窗口状态、GPU、模式、源码及资产摘要；先做足够长的前台测试，再决定是否加入阴影、战斗与更复杂AI。</p></article></div><p id="bottleneck"></p><p class="note">详细技术约定与经验维护在项目模块README和“项目开发经验”文档；本报告由固定采样生成，不执行本机命令。</p></section>
<footer><p>模型：<a href="https://sketchfab.com/jungle_jim">Jungle Jim</a> · <a href="https://sketchfab.com/3d-models/super-low-poly-stylized-zombies-animated-6c112bed707b4444864b606299f8b85a">Super Low-poly Stylized Zombies (animated)</a> · <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>。派生修改：编号0拆分、去空形态键、统一高度、制作待机与移动循环、测试配色。未作为正式游戏资产入库。</p><p>技术依据：<a href="https://threejs.org/docs/pages/WebGLRenderer.html">Three.js渲染统计</a>、<a href="https://registry.khronos.org/webgl/extensions/EXT_disjoint_timer_query_webgl2/">WebGL2 GPU计时扩展</a>。报告、图表和截图均内嵌，可离线阅读；外部来源链接和本机测试场需要对应网络或服务。</p><button class="print-button" onclick="window.print()">打印 / 保存PDF</button></footer></main>
<script id="data" type="application/json">__DATA__</script><script>
const data=JSON.parse(document.getElementById('data').textContent),samples=data.samples,$=id=>document.getElementById(id),names={static:'静态',animated:'原地动画',open:'开阔移动',channel:'通道移动'},bn=data.browser_labels,fmt=(n,d=2)=>n==null?'—':Number(n).toFixed(d);
const longs=samples.filter(s=>s.duration===180);
const counts=[...new Set(longs.map(s=>s.count))].sort((a,b)=>a-b);
const passed=counts.filter(n=>data.code_versions.some(v=>['open','channel'].every(m=>longs.some(s=>s.count===n&&s.mode===m&&s.config.code_sha256===v&&s.stable60))));
const best=passed.length?Math.max(...passed):0,chosen=best?longs.filter(s=>s.count===best&&s.stable60):longs;
const minFps=Math.min(...chosen.map(s=>s.summary.fps)),maxP95=Math.max(...chosen.map(s=>s.summary.p95Ms));
$('long-count').innerHTML=counts.map(n=>'<option value='+n+'>'+n+'只</option>').join('');$('long-count').value=String(best||counts[0]);
$('capacity-title').textContent=best?best+'只：当前最高双场景长测通过档':'容量边界：尚无双场景长测通过档';
$('capacity-result').textContent='以本机RX 7900 XTX、当前已测浏览器和640×360为限。'+(best?'最高已通过档为'+best+'只；更高档位的失败或短测记录见完整表。':'已测候选尚未满足全部条件。')+'这不是所有设备、所有游戏场景的绝对上限。宿主前台比例、源码版本和真实移动数量随组列出；日常工作并行，未建立整机空载条件。';
$('browser-tag').textContent=data.browsers.map(b=>bn[b]).join(' / ');$('browser').innerHTML=data.browsers.map(b=>'<option value='+b+'>'+bn[b]+'</option>').join('');document.querySelector('.legend').innerHTML=data.browsers.map((b,j)=>'<span><i style=background:'+(['#527345','#b08157'][j])+'></i>'+bn[b]+'</span>').join('')+'<span>虚线：60 FPS</span>';$('sample-count').textContent=samples.length+'组有效页面采样';$('k-fps').innerHTML=fmt(minFps,1)+'<i>FPS</i>';$('k-p95').innerHTML=fmt(maxP95)+'<i>ms</i>';
$('headline-result').textContent=`${best||'候选'}只双场景长测的最低平均帧率为${fmt(minFps,1)} FPS，最慢P95为${fmt(maxP95)}ms。以下图表同时保留P99、最长帧、长帧比例与模拟欠时；失败档位保留在完整表。`;
function table(headers,rows){return '<table><thead><tr>'+headers.map(x=>'<th>'+x+'</th>').join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr'+(r.long?' class="long"':'')+'>'+r.values.map(x=>'<td class="n">'+x+'</td>').join('')+'</tr>').join('')+'</tbody></table>';}
function bars(){const mode=$('mode').value,width=Number($('width').value),ss=samples.filter(s=>s.mode===mode&&s.width===width&&s.duration<180);if(!ss.length){$('bars').innerHTML='<div class="empty">此组合没有20／60秒短测；180秒记录请查看下一节。</div>';return;}const ns=[...new Set(ss.map(s=>s.count))].sort((a,b)=>a-b),ymax=Math.max(80,Math.ceil(Math.max(...ss.map(s=>s.summary.fps))/20)*20),base=225,scale=180/ymax,slot=900/ns.length;let svg='<svg viewBox="0 0 1000 290" role="img" aria-label="不同数量的平均FPS探索">';for(let y=0;y<=ymax;y+=20)svg+=`<line x1="60" y1="${base-y*scale}" x2="970" y2="${base-y*scale}" stroke="#c5ceba"/><text x="43" y="${base-y*scale+4}" text-anchor="end" fill="#78836e" font-size="11">${y}</text>`;svg+=`<line x1="60" y1="${base-60*scale}" x2="970" y2="${base-60*scale}" stroke="#7f8c73" stroke-dasharray="5 5"/>`;ns.forEach((n,i)=>{const x=65+slot*(i+.5),a=ss.filter(s=>s.count===n);a.forEach((s,j)=>{const bw=Math.min(55,slot/(a.length+1)),h=s.summary.fps*scale,bx=x+(j-(a.length-1)/2)*bw-bw*.4;svg+=`<rect x="${bx}" y="${base-h}" width="${bw*.8}" height="${h}" fill="#527345"/><text x="${bx+bw*.4}" y="${base-h-9}" text-anchor="middle" font-size="11" fill="#33462e">${fmt(s.summary.fps,1)}</text>`;});svg+=`<text x="${x}" y="249" text-anchor="middle" font-size="11" fill="#687461">${n}只</text><text x="${x}" y="266" text-anchor="middle" font-size="10" fill="#687461">${a.map(s=>s.duration+'秒').join('/')}</text>`;});$('bars').innerHTML=svg+'</svg>';}
function longView(){const s=longs.find(s=>s.browser===$('browser').value&&s.mode===$('long-mode').value&&s.count===Number($('long-count').value)),a=s?.summary;if(!s){$('timeline').innerHTML='<div class=empty>该数量与场景没有长测。</div>';$('long-table').innerHTML=$('cpu-table').innerHTML='';return;}const ymax=Math.max(40,Math.ceil(Math.max(...s.timeline.map(p=>p[1]))/20)*20),scale=190/ymax,base=230;let svg='<svg viewBox="0 0 1000 270" role="img" aria-label="长测时间窗最大帧时间">';for(let y=0;y<=ymax;y+=ymax/4)svg+=`<line x1="55" y1="${base-y*scale}" x2="975" y2="${base-y*scale}" stroke="#c5ceba"/><text x="44" y="${base-y*scale+4}" text-anchor="end" fill="#78836e" font-size="11">${fmt(y,0)}</text>`;svg+=`<line x1="55" y1="${base-16.667*scale}" x2="975" y2="${base-16.667*scale}" stroke="#789168" stroke-dasharray="5 5"/><polyline fill="none" stroke="#527345" stroke-width="1.7" points="${s.timeline.map(p=>(55+p[0]*920)+','+(base-p[1]*scale)).join(' ')}"/><text x="55" y="255" font-size="11" fill="#687461">0秒</text><text x="975" y="255" text-anchor="end" font-size="11" fill="#687461">${fmt(a.elapsedS,1)}秒</text></svg>`;$('timeline').innerHTML=svg;
$('long-table').innerHTML=table(['平均FPS','P95 ms','P99 ms','最长帧 ms','>33.3ms %','>50ms %','GPU ms','模拟欠时 s'],[{values:[fmt(a.fps,1),fmt(a.p95Ms),fmt(a.p99Ms),fmt(a.maxMs),fmt(a.over33Pct,3),fmt(a.over50Pct,3),fmt(a.gpuMs),fmt(a.simulationDroppedS,4)]}]);$('cpu-table').innerHTML=table(['群体模拟 ms/帧','Mixer动画 ms/帧','位置与矩阵 ms/帧','渲染提交 CPU ms/帧','平均移动数','绘制调用'],[{values:[fmt(a.cpuMs.simulation),fmt(a.cpuMs.animation),fmt(a.cpuMs.sync),fmt(a.cpuMs.submit),fmt(a.meanMoving,0),fmt(a.meanCalls,0)]}]);}
$('all-table').innerHTML=table(['浏览器','数量','模式','内部尺寸','记录秒','FPS','P95 ms','P99 ms','最长 ms','GPU ms','视锥/动画','欠时 s','宿主前台 %','源码版本','60帧判定'],samples.map(s=>({long:s.duration===180,values:[bn[s.browser],s.count,names[s.mode],s.width+'×'+s.width*9/16,fmt(s.summary.elapsedS,1),fmt(s.summary.fps,1),fmt(s.summary.p95Ms),fmt(s.summary.p99Ms),fmt(s.summary.maxMs),fmt(s.summary.gpuMs),s.summary.minVisible+'/'+s.summary.minAnimated,fmt(s.summary.simulationDroppedS,3),fmt(s.hostForegroundPct,1),s.config.code_sha256.slice(0,8),s.duration===180?(s.stable60?'通过':'未达标'):'探索／常规']})));
const model=data.model;$('model-proof').textContent=`导出GLB在全新Blender进程回读：${model.bones}骨骼、${model.vertices}顶点。两个片段首尾最大误差${Math.max(...model.checks.map(c=>c.loop_error_m)).toExponential(1)}米，移动姿态顶点最大变化${fmt(model.checks.find(c=>c.name==='移动').pose_change_m,3)}米。`;
$('checks-proof').textContent='原500只版本：'+data.functional.map(f=>bn[f.browser]+' '+f.checks+'组真实输入检查通过').join('；')+'（无窗口功能检查）。包含目标更新、暂停、镜头、通道、动画和分辨率；内置浏览器另完成实际采样和JSON、CSV下载。';
$('environment').textContent=`CPU：${data.hardware.cpu.trim()}；内存：${data.hardware.ramGiB} GiB；系统：${data.hardware.os}。实际WebGL GPU：${samples[0].environment.gpu}。浏览器：${data.versions.map(v=>bn[v.browser]+' '+v.version).join('；')}。${data.pending} 内置浏览器宿主前台约每0.5秒只读观察，覆盖预热与采样；这不是逐帧原生窗口观测。各组实际视口：${[...new Set(samples.map(s=>s.environment.viewport.join('×')))].join('、')}；DPR 1、固定16:9三维区域。Three.js 0.187.0，WebGL2，关闭抗锯齿、实时阴影和后处理。`;
$('pending').textContent=data.pending||'所选桌面浏览器矩阵已完成。';$('identity').textContent='源码SHA-256：'+data.code_sha256+'\n派生GLB SHA-256：'+data.asset.sha256+'\n原始GLB SHA-256：'+data.source_sha256;
const totals={simulation:0,animation:0,sync:0,submit:0},cn={simulation:'群体模拟',animation:'Mixer动画',sync:'位置与矩阵同步',submit:'渲染CPU提交'};longs.forEach(s=>Object.keys(totals).forEach(k=>totals[k]+=s.summary.cpuMs[k]/longs.length));const lead=Object.keys(totals).sort((a,b)=>totals[b]-totals[a])[0];$('bottleneck').textContent=`本次已测长测中，记录的CPU分项以${cn[lead]}平均耗时最大（约${fmt(totals[lead])}ms/帧）。这是本场景的测量结果；增加阴影、武器或复杂地形后需要重新定位。`;
for(const id of ['mode','width'])$(id).onchange=bars;for(const id of ['browser','long-mode','long-count'])$(id).onchange=longView;bars();longView();
</script></body></html>'''


def picture(name):
    with Image.open(OUT/name) as im:
        im=im.convert('RGB');im.thumbnail((1400,1100));stream=io.BytesIO();im.save(stream,'JPEG',quality=87)
    return '<img class="photo" alt="真实浏览器测试画面" src="data:image/jpeg;base64,'+base64.b64encode(stream.getvalue()).decode()+'">'


def timeline(rows):
    start=rows[0]['t'];span=max(1,rows[-1]['t']-start);buckets={}
    for r in rows:
        k=min(239,int((r['t']-start)/span*240));buckets[k]=max(buckets.get(k,0),r['frame'])
    return [[(k+.5)/240,v] for k,v in sorted(buckets.items())]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser-set',choices=('iab','desktop'),default='iab');args=parser.parse_args()
    browsers=('iab',) if args.browser_set=='iab' else ('chrome','msedge')
    samples=[];versions=[];functional=[]
    for browser in ('chrome','msedge'):
        func=json.loads((OUT/(browser+'-功能检查.json')).read_text('utf-8'))
        assert not func.get('failure') and not func['errors']
        functional.append({'browser':browser,'checks':len(func['checks']),'headless':True})
    for browser in browsers:
        summary=json.loads((OUT/(browser+'-性能汇总.json')).read_text('utf-8'))
        if browser=='iab':summary['samples']+=json.loads((OUT/'iab-容量汇总.json').read_text('utf-8'))['samples']
        else:assert len(summary['samples'])==16 and not summary.get('failure'), '桌面性能矩阵未完成'
        versions.append({'browser':browser,'version':summary['browser_version']})
        for row in summary['samples']:
            p=OUT/row['file'];raw=json.loads(p.read_text('utf-8'));assert raw['valid'] and raw['headless'] is False
            if browser!='iab':assert raw.get('nativeForegroundVerified')
            item={k:row[k] for k in ('count','mode','width','duration','valid','summary')}
            item.update(browser=browser,environment=raw['environment'],config=raw['config'],nativeForegroundChecks=raw['nativeForegroundChecks'],windowMode=raw.get('windowMode','独立原生前台窗口'),hostForegroundPct=raw.get('foregroundObservation',{}).get('hostForegroundPct',100),raw_file=p.name,
                        raw_sha256=hashlib.sha256(p.read_bytes()).hexdigest(),timeline=timeline(raw['rows']))
            item['stable60']=item['duration']==180 and item['width']==640 and item['mode'] in ('open','channel') and a_stable(raw['summary'],item['count'])
            samples.append(item)
    codes=sorted({s['config']['code_sha256'] for s in samples})
    model=json.loads((DATA/'导出回读.json').read_text('utf-8'));asset=json.loads((DATA/'资源清单.json').read_text('utf-8'))
    assert model['passed'] and asset['source_unchanged']
    passed_counts=[n for n in {s['count'] for s in samples} if any(all(any(s['count']==n and s['mode']==m and s['stable60'] and s['config']['code_sha256']==code for s in samples) for m in ('open','channel')) for code in codes)]
    data={'schema':2,'date':'2026-11-03','scope':('本机内置可见页面、宿主条件逐组记录' if args.browser_set=='iab' else '本机独立Chrome及Edge原生前台窗口')+'、单款真实骨骼角色、独立群体实验',
          'acceptance':{'durationS':180,'internal':[640,360],'meanFpsMin':60,'p95MsMax':16.7,'over33PctExclusiveMax':.1,'simulationDroppedS':0,'requireAllInFrustum':True,'requireAllAnimated':True,'modes':['open','channel']},
          'highestBothScenesPassed':max(passed_counts,default=0),
          'browsers':list(browsers),'browser_labels':{'iab':'Codex内置浏览器','chrome':'Chrome','msedge':'Edge'},
          'pending':'Chrome与Edge独立窗口前台矩阵待补测；工程师暂需使用电脑，本报告先记录内置可见面板结果。' if args.browser_set=='iab' else '',
          'hardware':json.loads((DATA/'测试硬件.json').read_text('utf-8')),'versions':versions,'functional':functional,
          'model':model,'asset':asset['asset'],'source_sha256':asset['source_sha256'],'code_sha256':' / '.join(codes),'code_versions':codes,
          'samples':samples,'source_url':asset['source_url'],'license':asset['license'],'changes':asset['changes']}
    RECORD.write_text(json.dumps(data,ensure_ascii=False,indent=2),'utf-8')
    report=TEMPLATE.replace('__DATA__',json.dumps(data,ensure_ascii=False,separators=(',',':')).replace('</','<\\/'))
    prefix='iab' if args.browser_set=='iab' else 'chrome';extension='jpg' if prefix=='iab' else 'png'
    for token,name in [('__OPEN_IMAGE__',f'{prefix}-15-500-open-640-180.{extension}'),('__CHANNEL_IMAGE__','iab-容量通道.jpg' if prefix=='iab' else f'{prefix}-16-500-channel-640-180.{extension}'),('__DETAIL_IMAGE__','动作近景.png')]:report=report.replace(token,picture(name))
    REPORT.write_text(report,'utf-8')
    with (RECORD.parent/'性能测试摘要.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['浏览器','数量','模式','内部宽','记录秒','平均FPS','P95毫秒','P99毫秒','最长帧毫秒','GPU毫秒','视锥最小数','动画最小数','模拟欠时秒','大于33毫秒百分比','宿主前台百分比','源码摘要','稳定60帧长测通过'])
        for s in samples:
            a=s['summary'];writer.writerow([s['browser'],s['count'],s['mode'],s['width'],a['elapsedS'],a['fps'],a['p95Ms'],a['p99Ms'],a['maxMs'],a['gpuMs'],a['minVisible'],a['minAnimated'],a['simulationDroppedS'],a['over33Pct'],s['hostForegroundPct'],s['config']['code_sha256'],s['stable60']])
    print(json.dumps({'samples':len(samples),'report_bytes':REPORT.stat().st_size,'record_bytes':RECORD.stat().st_size},ensure_ascii=False))


def a_stable(a,count):
    return a['fps']>=60 and a['p95Ms']<=16.7 and a['over33Pct']<.1 and a['simulationDroppedS']==0 and a['minVisible']==count and a['minAnimated']==count


if __name__=='__main__':main()
