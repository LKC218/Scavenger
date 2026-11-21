/* 在无浏览器条件下执行实际页面脚本；不模拟浏览器解码或布局验收。 */
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');
const batch = process.argv[2];
if (!/^BA-\d+-\d+$/.test(batch || '')) throw new Error('需要有效批次编号');
const batchRoot = path.join(root, 'local-data', '断箭', '批次', batch);
const source = fs.readFileSync(path.join(batchRoot, '音效配套库', '配套音效目录.html'), 'utf8');
const blocks = [...source.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)];
const data = blocks.find(match => match[1].includes('application/json'))[2];
const code = blocks.find(match => !match[1].includes('application/json'))[2];
class Element {
  constructor() { this.value = ''; this.innerHTML = ''; this.textContent = ''; this.events = {}; }
  addEventListener(name, callback) { this.events[name] = callback; }
}
const ids = Object.fromEntries(['delivery', 'metrics', 'search', 'category', 'status', 'models', 'result', 'detail', 'review-summary', 'release-status'].map(id => [id, new Element()]));
ids.delivery.textContent = data; ids.category.value = 'all'; ids.status.value = 'all';
const events = {}, media = {};
const document = {querySelector: selector => ids[selector.slice(1)], querySelectorAll: () => [], getElementById: id => media[id], addEventListener: (name, callback) => events[name] = callback};
const context = vm.createContext({document, console});
vm.runInContext(code, context, {timeout: 5000});
const results = [];
function run(name, action) { action(); results.push({name, status: 'passed'}); }
if (JSON.parse(data).release) run('正式版本与库级验收标记', () => { assert.match(ids['release-status'].textContent, /正式版 V1.*库级验收通过/); assert.equal(JSON.parse(data).release.acceptance, 'user_accepted_library'); });
run('初始129个资源入口与A10内容', () => { assert.match(ids.result.textContent, /129 \/ 129/); assert.match(ids.detail.innerHTML, /US_A10/); assert.match(ids.detail.innerHTML, /<audio/); assert.doesNotMatch(ids.detail.innerHTML, /\sautoplay(?:\s|=|>)/); });
run('空中与地面筛选', () => { ids.category.value = 'air'; vm.runInContext('filter()', context); assert.match(ids.result.textContent, /52 \/ 129/); ids.category.value = 'ground'; vm.runInContext('filter()', context); assert.match(ids.result.textContent, /77 \/ 129/); });
run('无配置引用的3项', () => { ids.category.value = 'all'; ids.status.value = 'unresolved'; vm.runInContext('filter()', context); assert.match(ids.result.textContent, /3 \/ 129/); assert.doesNotMatch(ids.detail.innerHTML, /<audio/); });
run('搜索型号与无结果', () => { ids.status.value = 'all'; ids.search.value = 'US_A10'; vm.runInContext('filter()', context); assert.match(ids.result.textContent, /1 \/ 129/); ids.search.value = '不存在型号-核验'; vm.runInContext('filter()', context); assert.match(ids.result.textContent, /0 \/ 129/); assert.match(ids.detail.innerHTML, /没有搜索结果/); });
run('单条播放互斥事件处理', () => { const first = {tagName: 'AUDIO', pauses: 0, pause() {this.pauses++;}}; const second = {tagName: 'AUDIO', pause() {}}; events.play({target:first}); events.play({target:second}); assert.equal(first.pauses, 1); });
run('循环仅由复选框改变', () => { media.test = {loop: false}; ids.detail.events.change({target:{dataset:{loop:'test'},checked:true}}); assert.equal(media.test.loop, true); ids.detail.events.change({target:{dataset:{loop:'test'},checked:false}}); assert.equal(media.test.loop, false); });
run('长名称转义与模型链接存在', () => { assert.equal(vm.runInContext('escape("<长名称>\\\"&")', context), '&lt;长名称&gt;&quot;&amp;'); const delivery = JSON.parse(data); for (const model of delivery.models) for (const link of Object.values(model.model_links)) assert.equal(fs.existsSync(path.resolve(batchRoot,'音效配套库',decodeURIComponent(link))), true); });
if(JSON.parse(data).summary.playback_review) run('命中分支说明保留缺项与原状态', () => {
  assert.match(ids['review-summary'].textContent, /7 条武器回退路径仍未解析/);
  ids.search.value='US_AH64D';vm.runInContext('filter()',context);
  assert.match(ids.detail.innerHTML,/正常命中采用对应弹药预设/);
  assert.match(ids.detail.innerHTML,/US_TankShell30mm_ImpactGround/);
  assert.match(ids.detail.innerHTML,/SFX_Explosion_20-30mm_FAST_US_HE/);
  assert.match(ids.detail.innerHTML,/部分映射/);
  const jumps=[...ids.detail.innerHTML.matchAll(/href="#(track-\d+)"/g)];
  assert.ok(jumps.length>0);
  for(const jump of jumps) assert.ok(ids.detail.innerHTML.includes(`id="${jump[1]}"`));
});
const output = {status:'passed', checks:results, limitations:['使用最小DOM替身执行真实脚本，未运行浏览器。','未验证音频媒体解码、视觉布局、窄屏与原生控件。','浏览器安全策略拒绝file协议；未改用其他表面绕过。'], listening_status:'pending'};
fs.writeFileSync(path.join(batchRoot,'证据','页面脚本检查.json'),JSON.stringify(output,null,2));
console.log(JSON.stringify(output,null,2));
