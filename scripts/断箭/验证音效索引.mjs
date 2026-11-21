/** 验证真实索引与真实Three.js音频类；Web Audio为记录调用的替身，不作听审。 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import vm from 'node:vm';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const batch = process.argv[2] || 'BA-26273833-004';
if (!/^BA-\d+-\d+$/.test(batch)) throw new Error('批次编号无效');
const local = path.join(root, 'local-data/断箭/批次', batch);
const read = name => JSON.parse(fs.readFileSync(path.join(local, '音效索引库/数据', name + '.json'), 'utf8'));
const {AudioCatalog} = await import(pathToFileURL(path.join(root, 'src/音效索引/音效查询.mjs')));
const {VehicleAudioRuntime} = await import(pathToFileURL(path.join(root, 'src/音效索引/音频运行时.mjs')));
const pin = JSON.parse(fs.readFileSync(path.join(local, '输入清单.json'), 'utf8'));
const THREE = await import(pathToFileURL(path.join(root, 'local-data/tools/three', pin.three_version, 'build/three.core.js')));
const catalog = new AudioCatalog(read('音频资源'), read('载具绑定'), read('播放规则'));
const checks = [];
async function test(name, action) { await action(); checks.push({name, status: 'passed'}); }

await test('129入口101事件与双向引用守恒', () => {
  assert.equal(catalog.vehicles.length, 129);
  assert.equal(Object.keys(catalog.assets).length, 101);
  const bindings = catalog.vehicles.flatMap(v => v.bindings);
  assert.equal(bindings.length, 1612);
  assert.equal(bindings.filter(b => b.audio_id).length, 884);
  assert.equal(bindings.filter(b => b.status === 'empty_field').length, 672);
  assert.equal(bindings.filter(b => b.status === 'event_not_indexed').length, 56);
  assert.equal(catalog.summary.missing_event_paths.length, 7);
  assert.equal(catalog.searchVehicles('', 'air').length, 52);
  assert.equal(catalog.searchVehicles('', 'ground').length, 77);
  assert.equal(catalog.searchVehicles('', 'all', 'unresolved').length, 3);
  for (const asset of Object.values(catalog.assets)) for (const user of catalog.users(asset.audio_id)) {
    const binding = catalog.vehicle(user.model_id, user.config_id).bindings.find(b => b.binding_id === user.binding_id);
    assert.equal(binding.audio_id, asset.audio_id);
  }
  assert.equal(Object.values(catalog.assets).reduce((n, a) => n + a.users.length, 0), 884);
});

const samples = ['US_A10', 'US_AH64D', 'US_M1A2'].map(id => catalog.vehicles.find(v => v.model_id === id));
await test('三款样例单位武器弹药分别选择且配置不可省略', () => {
  for (const v of samples) {
    const shot = v.bindings.find(b => b.domain === 'weapon' && b.role_field === 'Shot' && b.audio_id);
    const ammo = v.bindings.find(b => b.domain === 'ammunition' && b.role_field === 'Impact' && b.origin.weapon_id === shot.origin.id);
    const request = {modelId: v.model_id, configId: v.config_id};
    assert.equal(catalog.resolve({...request, action: 'move'}).status, 'playable');
    assert.equal(catalog.resolve({...request, action: 'shot', weaponId: shot.origin.id}).binding.binding_id, shot.binding_id);
    assert.equal(catalog.resolve({...request, action: 'impact', weaponId: shot.origin.id, ammoId: ammo.origin.id, ammunitionPresetState: 'present'}).binding.binding_id, ammo.binding_id);
    assert.equal(catalog.resolve({...request, configId: undefined, action: 'move'}).status, 'configuration_not_found');
    assert.equal(catalog.resolve({...request, action: 'impact', weaponId: -999, ammoId: ammo.origin.id, ammunitionPresetState: 'present'}).status, 'no_binding');
  }
});

await test('弹药优先与空字段不回退；对象缺失才走武器Impact', () => {
  const vehicle = samples[1];
  const weapon = vehicle.bindings.find(b => b.playback_review);
  const route = weapon.playback_review.ammunition_routes[0];
  const request = {modelId: vehicle.model_id, configId: vehicle.config_id, action: 'impact', weaponId: weapon.origin.id, ammoId: route.ammunition_id};
  assert.equal(catalog.resolve({...request, ammunitionPresetState: 'present'}).asset.sha256, route.sample_sha256);
  assert.equal(catalog.resolve({...request, ammunitionPresetState: 'missing'}).status, 'event_not_indexed');
  assert.equal(catalog.resolve({...request, ammunitionPresetState: 'unknown'}).status, 'preset_state_unknown');
  const empty = catalog.vehicles.flatMap(v => v.bindings.filter(b => b.domain === 'ammunition' && b.role_field === 'MissileInterception' && b.status === 'empty_field').map(b => ({v, b})))[0];
  assert.equal(catalog.resolve({modelId: empty.v.model_id, configId: empty.v.config_id, action: 'interception', weaponId: empty.b.origin.weapon_id, ammoId: empty.b.origin.id, ammunitionPresetState: 'present'}).status, 'empty_field');
});

class Param {
  constructor(value = 1) { this.value = value; this.events = []; }
  setValueAtTime(value, time) { this.value = value; this.events.push(['set', value, time]); }
  setTargetAtTime(value, time) { this.value = value; this.events.push(['target', value, time]); }
  linearRampToValueAtTime(value, time) { this.value = value; this.events.push(['ramp', value, time]); }
  cancelScheduledValues() {}
  cancelAndHoldAtTime() {}
}
class Node {
  constructor() {
    this.gain = new Param(); this.playbackRate = new Param(); this.detune = new Param(); this.connections = new Set();
    for (const name of ['positionX', 'positionY', 'positionZ', 'orientationX', 'orientationY', 'orientationZ', 'forwardX', 'forwardY', 'forwardZ', 'upX', 'upY', 'upZ']) this[name] = new Param(0);
  }
  connect(node) { this.connections.add(node); }
  disconnect(node) { if (node) this.connections.delete(node); else this.connections.clear(); }
  start(time) { this.startTime = time; }
  stop(time) { this.stopTime = time; }
}
function setup(loadBuffer, rules = catalog.rules) {
  const context = {currentTime: 10, state: 'suspended', destination: new Node(), listener: new Node(),
    async resume() { this.state = 'running'; }, createGain: () => new Node(), createPanner: () => new Node(), createBufferSource: () => new Node()};
  THREE.AudioContext.setContext(context);
  const listener = new THREE.AudioListener();
  let now = 1000;
  const runtimeCatalog = Object.assign(Object.create(Object.getPrototypeOf(catalog)), catalog, {rules});
  const player = new VehicleAudioRuntime({THREE, listener, catalog: runtimeCatalog, baseUrl: 'http://127.0.0.1:8766/', loadBuffer, now: () => now});
  return {player, listener, context, advance: value => { now += value; }};
}
const buffer = {length: 48000, numberOfChannels: 2, duration: 1};
const playable = catalog.result(samples[1].bindings.find(b => b.audio_id));

await test('点击解锁前不读取不播放；锁定状态可重试', async () => {
  let loads = 0;
  const {player} = setup(async () => { loads++; return buffer; });
  assert.equal((await player.play(playable, {positional: false})).status, 'locked');
  assert.equal(loads, 0);
  await player.unlock();
  assert.equal((await player.play(playable, {positional: false})).status, 'playing');
  assert.equal(loads, 1);
  player.dispose();
});

await test('真实Three.js空间节点挂载、声音实例独立、共享一次解码', async () => {
  let resolve, loads = 0;
  const wait = new Promise(r => { resolve = r; });
  const {player, context} = setup(async () => { loads++; return wait; });
  await player.unlock();
  const body = new THREE.Object3D(); body.position.set(3, 2, -5);
  const one = player.play(playable, {action: 'move', instanceId: 'one', anchor: body});
  const two = player.play(playable, {action: 'move', instanceId: 'two', anchor: body});
  await Promise.resolve(); assert.equal(loads, 1);
  resolve(buffer);
  assert.equal((await one).status, 'playing'); assert.equal((await two).status, 'playing');
  assert.equal(body.children.length, 2);
  const [a, b] = body.children;
  assert.ok(a instanceof THREE.PositionalAudio);
  assert.equal(a.buffer, b.buffer); assert.notEqual(a.source, b.source); assert.equal(a.loop, false);
  body.updateMatrixWorld(true);
  assert.equal(a.panner.positionX.value, 3); assert.equal(a.panner.positionY.value, 2); assert.equal(a.panner.positionZ.value, -5);
  const entry = [...player.voices][0];
  player.stopAll();
  assert.equal(entry.sound.source.stopTime, context.currentTime + catalog.rules.actions.move.fade_out_seconds);
  assert.ok(entry.sound.gain.gain.events.some(([type, value]) => type === 'ramp' && value === 0));
  player.stopAll({immediate: true});
  assert.equal(body.children.length, 0);
  player.dispose();
});

await test('加载中停止或切换，不会迟到播放', async () => {
  let resolve;
  const {player} = setup(() => new Promise(r => { resolve = r; }));
  await player.unlock();
  const pending = player.play(playable, {positional: false});
  await Promise.resolve();
  player.stopAll({clearCache: true, immediate: true});
  resolve(buffer);
  assert.equal((await pending).status, 'cancelled');
  assert.equal(player.voices.size, 0);
  assert.equal(player.stats().cacheBytes, 0);
  player.dispose();
});

await test('并发及冷却限制覆盖正在读取的声音', async () => {
  let resolve;
  const {player, advance} = setup(() => new Promise(r => { resolve = r; }));
  await player.unlock();
  const anchor = new THREE.Object3D();
  const one = player.play(playable, {action: 'shot', anchor});
  assert.equal((await player.play(playable, {action: 'shot', anchor})).status, 'cooldown');
  advance(200);
  const two = player.play(playable, {action: 'shot', anchor});
  advance(200);
  assert.equal((await player.play(playable, {action: 'shot', anchor})).status, 'voice_limit');
  await Promise.resolve(); resolve(buffer); await Promise.all([one, two]);
  player.dispose();
});

await test('解码失败可重试；静音不丢音量；缓存预算受限', async () => {
  let loads = 0;
  const {player, listener} = setup(async () => { if (++loads === 1) throw new Error('模拟加载失败'); return buffer; });
  await player.unlock();
  assert.equal((await player.play(playable, {positional: false})).status, 'load_failed');
  assert.equal((await player.play(playable, {positional: false})).status, 'playing');
  player.setVolume(0.6); player.setMuted(true); assert.equal(listener.getMasterVolume(), 0);
  player.setMuted(false); assert.equal(listener.getMasterVolume(), 0.6); player.dispose();
  const small = setup(async () => buffer, {...catalog.rules, cache_max_bytes: 1}).player;
  await small.unlock();
  assert.equal((await small.play(playable, {positional: false})).status, 'load_failed');
  assert.equal(small.stats().cacheBytes, 0); small.dispose();
});

await test('页面真实脚本的查询、反查、样例选择与回退控件', async () => {
  class Element {
    constructor() { this.value = ''; this.events = {}; this.dataset = {}; this.classList = {toggle() {}}; this.hidden = false; this.attributes = {}; }
    set innerHTML(value) {
      this.html = value;
      if (value.includes('<option')) this.value = value.match(/<option value="([^"]*)"/)?.[1] ?? '';
    }
    get innerHTML() { return this.html ?? ''; }
    addEventListener(name, action) { this.events[name] = action; }
    setAttribute(name, value) { this.attributes[name] = value; }
    focus() {}
    scrollIntoView() {}
    click() { return this.events.click?.({target: this}); }
  }
  const html = fs.readFileSync(path.join(root, 'src/音效索引/索引验证.html'), 'utf8');
  const ids = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(match => [match[1], new Element()]));
  ids.volume.value = '0.35'; ids.category.value = 'all'; ids.status.value = 'all';
  const filters = new Element(), tabs = new Element();
  const actions = ['move', 'shot', 'impact', 'interception', 'death'].map(action => { const e = new Element(); e.dataset.action = action; e.textContent = action; return e; });
  const events = {};
  const document = {
    querySelector: selector => selector === '.filters' ? filters : selector === '.tabs' ? tabs : ids[selector.slice(1)],
    querySelectorAll: selector => selector === '[data-action]' ? actions : [],
    addEventListener: (name, callback) => { events[name] = callback; },
  };
  class StageStub {
    constructor() { this.camera = new THREE.PerspectiveCamera(); this.scene = new THREE.Scene(); this.muzzle = new THREE.Object3D(); }
    stopMotion() {} clear() {} dispose() {}
  }
  const context = vm.createContext({document, window: {addEventListener() {}}, location: {href: 'http://127.0.0.1:8766/'},
    THREE, AudioCatalog, VehicleAudioRuntime, SampleStage: StageStub, URL, performance, setTimeout, clearTimeout, console,
    fetch: async url => { const name = decodeURIComponent(new URL(url).pathname.split('/').at(-1)).replace('.json', ''); return {ok: true, json: async () => read(name)}; }});
  const code = fs.readFileSync(path.join(root, 'src/音效索引/页面.mjs'), 'utf8').replace(/^import .*;\r?\n/gm, '');
  await vm.runInContext(`(async()=>{${code}\n})()`, context, {timeout: 5000});
  assert.match(ids.metrics.innerHTML, /129/); assert.match(ids.detail.innerHTML, /US_A10/);
  assert.equal(actions.find(e => e.dataset.action === 'impact').disabled, false);
  assert.equal(ids.unlock.textContent, undefined);
  ids.search.value = '不存在的型号'; ids.search.events.input(); assert.match(ids.results.innerHTML, /没有匹配结果/);
  const click = async dataset => { const target = {dataset, closest() {return this;}}; await events.click({target}); };
  await click({sample: 'US_AH64D'}); assert.match(ids.detail.innerHTML, /AH-64D/);
  ids['missing-preset'].checked = true; ids['missing-preset'].events.change();
  assert.equal(actions.find(e => e.dataset.action === 'impact').disabled, true);
  assert.match(ids['action-state'].textContent, /事件未解析/);
  await ids['audio-tab'].click(); assert.match(ids.detail.innerHTML, /反向查询/);
  const user = Object.values(catalog.assets)[0].users[0];
  await click({vehicle: user.config_id}); assert.match(ids.detail.innerHTML, new RegExp(user.model_id));
  assert.equal(ids['vehicle-tab'].attributes['aria-selected'], 'true');
  await click({sample: 'US_M1A2'}); assert.match(ids.detail.innerHTML, /M1A2 Abrams/);
  assert.equal(ids['missing-preset'].checked, false);
});

const result = {status: 'passed', three_version: pin.three_version, checks,
  limitations: ['运行了真实Three.js AudioListener、Audio及PositionalAudio，Web Audio节点为替身。', '未运行浏览器、WebGL、真实解码、扬声器、布局或逐条听审。']};
fs.mkdirSync(path.join(local, '证据'), {recursive: true});
fs.writeFileSync(path.join(local, '证据/索引与播放逻辑核验.json'), JSON.stringify(result, null, 2));
console.log(JSON.stringify(result, null, 2));
