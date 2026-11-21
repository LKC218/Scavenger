/* 在DOM替身中执行模型分类页面，独立核对旧CSV来源与媒体身份。 */
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..'),batch=process.argv[2];
if(!/^BA-26273833-\d{3}$/.test(batch||''))throw new Error('批次名不符');
const base=path.join(root,'local-data/断箭/批次'),batchRoot=path.join(base,batch),library=path.join(batchRoot,'模型音效浏览库');
const read=file=>JSON.parse(fs.readFileSync(file,'utf8').replace(/^\uFEFF/,''));
const sha=file=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const catalog=read(path.join(library,'模型音效目录.json'));
const source=read(path.join(base,'BA-26273833-007/音效浏览库/音效目录.json'));
const page=fs.readFileSync(path.join(library,'模型音效浏览库.html'),'utf8');
const blocks=[...page.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)];
const embedded=blocks.find(match=>match[1].includes('application/json'))[2];
assert.deepEqual(JSON.parse(embedded),catalog);
const code=blocks.find(match=>!match[1].includes('application/json'))[2];
const audioTags=page.match(/<audio\b[^>]+>/g);
assert.equal(audioTags.length,1);assert.match(audioTags[0],/controls preload="none"/);assert.doesNotMatch(audioTags[0],/\s(?:autoplay|loop)(?:\s|=|>)/);
// 读取带逗号、换行和双引号的原始CSV，作为独立关系来源。
function csv(file){const text=fs.readFileSync(file,'utf8').replace(/^\uFEFF/,'');const rows=[];let row=[],field='',quoted=false;for(let i=0;i<text.length;i++){const c=text[i];if(c==='"'){if(quoted&&text[i+1]==='"'){field+='"';i++;}else quoted=!quoted;}else if(c===','&&!quoted){row.push(field);field='';}else if(c==='\n'&&!quoted){row.push(field.replace(/\r$/,''));rows.push(row);row=[];field='';}else field+=c;}if(field||row.length){row.push(field.replace(/\r$/,''));rows.push(row);}const headers=rows.shift();return rows.filter(r=>r.length>1).map(r=>Object.fromEntries(headers.map((key,i)=>[key,r[i]])));}
const sourceRows=csv(path.join(base,'BA-26273833-005/型号音频清单.csv'));
class Element{
 constructor(){this.textContent='';this.innerHTML='';this.value='';this.events={};this.checked=false;this.pauses=0;this.loads=0;this.plays=0;const classes=new Set();this.classList={add:name=>classes.add(name),remove:name=>classes.delete(name),contains:name=>classes.has(name)};}
 addEventListener(name,callback){this.events[name]=callback;}
 pause(){this.pauses++;}load(){this.loads++;}play(){this.plays++;}removeAttribute(name){delete this[name];}
}
const ids=Object.fromEntries(['catalog','counts','footer','modes','model-controls','model-group','model','model-summary','categories','search','status','operation','result','tracks','detail','player','loop','media-status'].map(id=>[id,new Element()]));
ids.catalog.textContent=embedded;ids.status.value=ids.operation.value=ids['model-group'].value='all';
const document={getElementById:id=>ids[id],hidden:false,events:{},addEventListener(name,callback){this.events[name]=callback;}};
const window={events:{},addEventListener(name,callback){this.events[name]=callback;}};
const context=vm.createContext({document,window,console});
const exec=code=>vm.runInContext(code,context,{timeout:5000});
exec(code);
const results=[];function run(name,action){action();results.push({name,status:'passed'});}
const guids=()=>JSON.parse(exec('JSON.stringify(filtered().map(row=>row.guid).sort())'));
function reset(mode='purpose'){ids.search.value='';ids.status.value=ids.operation.value='all';context.testMode=mode;exec('setMode(testMode)');}
function selectModel(id){context.testModel=id;exec('openModel(testModel)');}
run('250事件完整保留且单播放器默认不自动播放',()=>{assert.equal(guids().length,250);assert.equal(ids.player.plays,0);assert.equal(ids.player.volume,0.35);assert.equal(ids.player.loop,false);assert.equal(catalog.summary.models,129);});
run('101已关联与149未关联互斥且覆盖全部事件',()=>{reset('model');const linked=guids();assert.equal(linked.length,101);reset('unassigned');const free=guids();assert.equal(free.length,149);assert.equal(new Set([...linked,...free]).size,250);assert.ok(free.every(guid=>catalog.events.find(row=>row.guid===guid).model_ids.length===0));reset();});
run('逐129型号按原始CSV精确筛选并保留3个无声音入口',()=>{let noAudio=0;for(const model of catalog.models){selectModel(model.id);const expected=[...new Set(sourceRows.filter(row=>row['型号']===model.id&&row['音频路径']).map(row=>source.events.find(event=>event.path===row['实际事件路径']&&event.check.sha256===row['完整SHA256']).guid))].sort();assert.deepEqual(guids(),expected);if(!expected.length){noAudio++;assert.equal(ids.player.src,undefined);assert.match(ids['model-summary'].innerHTML,/暂无已确认声音/);assert.match(ids.detail.innerHTML,/无关联型号/);}}assert.equal(noAudio,3);reset();});
run('空中地面分组与模型选项均依据真实模型分类',()=>{reset('model');for(const group of ['air','ground']){ids['model-group'].value=group;ids['model-group'].events.change();const expected=catalog.events.filter(event=>event.model_ids.some(id=>catalog.models.find(model=>model.id===id).category===group)).map(row=>row.guid).sort();assert.deepEqual(guids(),expected);for(const model of catalog.models){assert.equal(ids.model.innerHTML.includes(`value="${model.id}"`),model.category===group);}}reset();});
run('用途技术状态来源筛选及型号名称搜索',()=>{for(const category of catalog.categories){context.testCategory=category;exec('category=testCategory;renderRows()');assert.equal(guids().length,catalog.events.filter(event=>event.category===category).length);}reset();for(const state of ['ready','review','failed']){ids.status.value=state;ids.status.events.change();assert.equal(guids().length,catalog.events.filter(event=>event.status===state).length);}reset();for(const operation of ['reused','rendered']){ids.operation.value=operation;ids.operation.events.change();assert.equal(guids().length,catalog.events.filter(event=>event.operation===operation).length);}reset();const a10=catalog.models.find(model=>model.id==='US_A10');ids.search.value=a10.name;ids.search.events.input();assert.deepEqual(guids(),[...a10.event_guids].sort());reset();ids.search.value=catalog.events[7].guid;ids.search.events.input();assert.equal(guids().length,1);ids.search.value='没有对应结果-检查';ids.search.events.input();assert.equal(guids().length,0);assert.equal(ids.player.src,undefined);reset();});
run('逐250媒体引用仍指向007原件且摘要技术状态参数保持',()=>{const files=new Set();for(const row of catalog.events){context.testGuid=row.guid;exec('selectEvent(testGuid)');assert.equal(ids.player.src,row.audio_path);const original=source.events.find(event=>event.guid===row.guid);const file=path.resolve(library,row.audio_path);assert.equal(file,path.resolve(base,'BA-26273833-007/音效浏览库',original.audio_path));assert.equal(sha(file),original.check.sha256);assert.deepEqual(row.check,original.check);assert.deepEqual(row.render_profile,original.render_profile);assert.equal(row.status,original.status);files.add(file);}assert.equal(files.size,248);assert.equal(ids.player.plays,0);});
run('共享声音反向跳转型号并停止前一条播放',()=>{reset();const shared=catalog.events.find(event=>event.model_ids.length>1);context.testGuid=shared.guid;exec('selectEvent(testGuid)');assert.match(ids.detail.innerHTML,/已确认使用型号/);const before=ids.player.pauses;selectModel(shared.model_ids[0]);assert.ok(guids().includes(shared.guid));assert.ok(ids.player.pauses>before);assert.equal(ids.player.plays,0);reset();});
run('循环仅主动开启且切换型号隐藏离开都会停止',()=>{ids.loop.checked=true;ids.loop.events.change();assert.equal(ids.player.loop,true);selectModel('US_AC130J');assert.equal(ids.player.loop,false);assert.equal(ids.loop.checked,false);assert.equal(ids.player.src,undefined);reset();const before=ids.player.pauses;document.hidden=true;document.events.visibilitychange();window.events.pagehide();assert.equal(ids.player.pauses,before+2);});
run('CSV双向集合与原引用缺项完整保留',()=>{const modelRows=csv(path.join(library,'模型分类清单.csv'));assert.equal(modelRows.length,129);assert.deepEqual(modelRows.map(row=>row['型号']).sort(),catalog.models.map(row=>row.id).sort());const pairs=csv(path.join(library,'模型声音关联.csv'));const expected=catalog.events.flatMap(event=>event.model_ids.map(id=>id+'|'+event.guid)).sort();assert.deepEqual(pairs.map(row=>row['型号']+'|'+row['事件GUID']).sort(),expected);assert.equal(new Set(expected).size,expected.length);const audit=read(path.join(library,'引用复核.json')).rows;assert.equal(audit.length,1615);for(let i=0;i<audit.length;i++)for(const key of ['映射状态','源事件路径','字段链','命中分支复核'])assert.equal(audit[i][key],sourceRows[i][key]);assert.equal(audit.filter(row=>row['映射状态']==='empty_field').length,672);assert.equal(audit.filter(row=>row['映射状态']==='event_not_indexed').length,56);assert.equal(audit.filter(row=>row['映射状态']==='unresolved').length,3);});
run('媒体事件提示与HTML文本转义',()=>{ids.player.events.playing();assert.match(ids['media-status'].textContent,/正在播放/);ids.player.events.error();assert.match(ids['media-status'].textContent,/相对位置/);assert.equal(exec('escape("<声音>\\\"&")'),'&lt;声音&gt;&quot;&amp;');});
const output={status:'passed',batch,checks:results,page_sha256:sha(path.join(library,'模型音效浏览库.html')),summary:catalog.summary,browser_executed:false,listening_status:'pending',limitations:['DOM替身执行实际脚本，不代表真实浏览器媒体、布局或听审。','没有绕过既有file导航安全限制。']};
fs.writeFileSync(path.join(batchRoot,'页面脚本核验.json'),JSON.stringify(output,null,2));
console.log(JSON.stringify({status:'passed',checks:results.length,summary:catalog.summary,browser_executed:false},null,2));
