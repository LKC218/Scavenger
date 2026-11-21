/* 在DOM替身中执行新浏览库实际脚本；不冒充浏览器媒体或听审。 */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');
const batch = process.argv[2];
if(!/^BA-\d+-\d+$/.test(batch || ''))throw new Error('批次编号无效');
const batchRoot = path.join(root, 'local-data/断箭/批次', batch);
const library = path.join(batchRoot, '音效浏览库');
const page = fs.readFileSync(path.join(library, '音效浏览库.html'), 'utf8');
const catalog = JSON.parse(fs.readFileSync(path.join(library, '音效目录.json'), 'utf8'));
const blocks = [...page.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)];
const embedded = blocks.find(match => match[1].includes('application/json'))[2];
assert.deepEqual(JSON.parse(embedded), catalog);
const code = blocks.find(match => !match[1].includes('application/json'))[2];
const playerTag = page.match(/<audio\b[^>]+>/g);
assert.equal(playerTag.length, 1);
assert.match(playerTag[0], /controls preload="none"/);
assert.doesNotMatch(playerTag[0], /\s(?:autoplay|loop)(?:\s|=|>)/);
class Element {
  constructor(){this.textContent='';this.innerHTML='';this.value='';this.events={};this.checked=false;this.pauses=0;this.loads=0;this.plays=0;this.classList={add(){},remove(){}};}
  addEventListener(name,callback){this.events[name]=callback;}
  pause(){this.pauses++;}
  load(){this.loads++;}
  play(){this.plays++;}
  removeAttribute(name){delete this[name];}
}
const ids=Object.fromEntries(['catalog','counts','footer','categories','search','status','operation','result','tracks','detail','player','loop','media-status'].map(id=>[id,new Element()]));
ids.catalog.textContent=embedded;ids.status.value=ids.operation.value='all';
const document={getElementById:id=>ids[id],hidden:false,events:{},addEventListener(name,callback){this.events[name]=callback;}};
const window={events:{},addEventListener(name,callback){this.events[name]=callback;}};
const context=vm.createContext({document,window,console});
vm.runInContext(code,context,{timeout:5000});
const results=[];
function run(name,action){action();results.push({name,status:'passed'});}
function reset(){ids.search.value='';ids.status.value=ids.operation.value='all';vm.runInContext("category='all';renderCategories();renderRows();",context);}
run('250事件初始呈现与禁止自动播放',()=>{assert.match(ids.result.textContent,/250 \/ 250/);assert.equal(ids.player.plays,0);assert.equal(ids.player.volume,0.35);assert.equal(ids.player.loop,false);});
run('逐用途筛选无重漏',()=>{for(const category of catalog.categories){context.testCategory=category;vm.runInContext('category=testCategory;renderRows();',context);assert.equal(vm.runInContext('filtered().length',context),catalog.events.filter(row=>row.category===category).length);}reset();});
run('状态与来源筛选',()=>{for(const state of ['ready','review','failed']){ids.status.value=state;vm.runInContext('renderRows()',context);assert.equal(vm.runInContext('filtered().length',context),catalog.events.filter(row=>row.status===state).length);}reset();for(const operation of ['reused','rendered']){ids.operation.value=operation;vm.runInContext('renderRows()',context);assert.equal(vm.runInContext('filtered().length',context),catalog.events.filter(row=>row.operation===operation).length);}reset();});
run('GUID搜索与无结果清理播放源',()=>{ids.search.value=catalog.events[10].guid;vm.runInContext('renderRows()',context);assert.match(ids.result.textContent,/1 \/ 250/);ids.search.value='不存在的音效-验收';vm.runInContext('renderRows()',context);assert.match(ids.result.textContent,/0 \/ 250/);assert.equal(ids.player.src,undefined);assert.match(ids.detail.innerHTML,/没有搜索结果/);reset();});
run('逐事件选中与媒体路径摘要',()=>{const files=new Set();for(const row of catalog.events){context.testGuid=row.guid;vm.runInContext('selectEvent(testGuid)',context,{timeout:5000});assert.equal(ids.player.src,row.audio_path||undefined);assert.equal(ids.player.loop,false);assert.equal(ids.loop.checked,false);if(row.audio_path){const file=path.resolve(library,row.audio_path);assert.ok(file.startsWith(library+path.sep));assert.ok(fs.statSync(file).isFile());assert.equal(crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex'),row.check.sha256);files.add(file);}}assert.equal(files.size,catalog.summary.unique_wav);assert.equal(ids.player.plays,0);});
run('循环主动开启并在切换后重置',()=>{ids.loop.checked=true;ids.loop.events.change();assert.equal(ids.player.loop,true);context.testGuid=catalog.events[0].guid;vm.runInContext('selectEvent(testGuid)',context);assert.equal(ids.player.loop,false);assert.equal(ids.loop.checked,false);});
run('隐藏及页面离开停止',()=>{const before=ids.player.pauses;document.hidden=true;document.events.visibilitychange();window.events.pagehide();assert.equal(ids.player.pauses,before+2);});
run('媒体状态事件与文本转义',()=>{ids.player.events.playing();assert.match(ids['media-status'].textContent,/正在播放/);ids.player.events.error();assert.match(ids['media-status'].textContent,/媒体加载失败/);assert.equal(vm.runInContext('escape("<声音>\\\"&")',context),'&lt;声音&gt;&quot;&amp;');});
const output={status:'passed',batch,checks:results,event_count:catalog.events.length,summary:catalog.summary,
  browser_executed:false,source_sha256:crypto.createHash('sha256').update(page).digest('hex'),
  limitations:['最小DOM替身执行实际脚本，不是浏览器媒体、布局、窄屏或听审验收。','既有file导航安全限制未绕过。','新浏览库需工程师实际打开后验收，不继承003页面的用户验收。']};
fs.writeFileSync(path.join(batchRoot,'证据','浏览页面脚本核验.json'),JSON.stringify(output,null,2));
console.log(JSON.stringify({status:output.status,checks:results.length,events:catalog.events.length,summary:catalog.summary,browser_executed:false},null,2));
