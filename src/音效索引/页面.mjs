import * as THREE from 'three';
import {AudioCatalog} from './音效查询.mjs';
import {VehicleAudioRuntime} from './音频运行时.mjs';
import {SampleStage} from './三维样例.mjs';

const $ = selector => document.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const labels = {confirmed: '引用完整', partial: '部分映射', unresolved: '无配置引用', empty_field: '源字段为空', event_not_indexed: '事件未解析'};
const roles = {MoveEvent: '发动机 / 运动', DeathEvent: '载具毁伤', Shot: '射击 / 发射', Impact: '命中 / 爆炸', MissileInterception: '拦截爆炸', InfantryPeopleDeathEvent: '乘员死亡', InfantryPeopleZombieEvent: '特殊乘员事件', InfantryHitEvent: '乘员命中'};
const baseUrl = new URL('./', location.href);
let catalog, mode = 'vehicle', currentVehicle, selectedAudio, runtime, listener;
let stageGeneration = 0;
const stage = new SampleStage($('#viewport'), $('#stage-placeholder'), text => { $('#model-state').textContent = text; });
function message(text, error = false) { $('#message').textContent = text; $('#message').classList.toggle('error', error); }

function stop(clearCache = false) {
  runtime?.stopAll({clearCache, immediate: true});
  stage.stopMotion();
}

async function ensureAudio() {
  if (!listener) {
    listener = new THREE.AudioListener();
    stage.camera.add(listener);
    runtime = new VehicleAudioRuntime({THREE, listener, catalog, baseUrl, onChange(stats) {
      $('#runtime-state').textContent = `缓存 ${stats.cached} 条 / ${(stats.cacheBytes / 1048576).toFixed(1)} MiB · 读取 ${stats.pending} · 播放 ${stats.voices}`;
      if (![...runtime.voices].some(v => v.group.endsWith('|move') && !v.cancelled)) stage.stopMotion();
    }});
  }
  await runtime.unlock();
  runtime.setVolume($('#volume').value);
  runtime.setMuted($('#mute').checked);
  $('#unlock').textContent = '声音已开启';
  message('声音已开启。选择动作或点击试听开始播放。');
}

function setVehicle(vehicle) {
  stop(true);
  stageGeneration += 1;
  stage.clear();
  currentVehicle = vehicle;
  $('#missing-preset').checked = false;
  $('#model-state').textContent = '尚未加载';
  renderVehicle();
  configureWeapons();
  $('#load-model').disabled = !vehicle.sample_model_url;
  $('#load-model').title = vehicle.sample_model_url ? '只在点击后读取真实静态GLB' : '三维样例仅包括 A-10、AH-64D、M1A2';
}

function evidence(binding) {
  return `<details class="evidence"><summary>查看来源</summary><pre>${esc(JSON.stringify({source_event_path: binding.source_event_path, preset_path: binding.preset_path, origin: binding.origin, field_chain: binding.field_chain, playback_review: binding.playback_review}, null, 2))}</pre></details>`;
}

function renderVehicle() {
  const v = currentVehicle;
  const count = new Set(v.bindings.filter(b => b.audio_id).map(b => b.audio_id)).size;
  $('#detail').innerHTML = `<div class="hero"><div><p class="eyebrow">载具档案 / ${v.category === 'air' ? '空中' : '地面'}</p><span class="badge ${v.mapping_status === 'confirmed' ? '' : 'warn'}">${esc(labels[v.mapping_status])}</span><span class="badge ${v.model_status === 'passed' ? '' : 'warn'}">模型${v.model_status === 'passed' ? '静态检查通过' : '待修参考'}</span><h2>${esc(v.name || v.model_id)}</h2><p class="mono">${esc(v.model_id)} · ${esc(v.variant)}<br>配置 ${esc(v.config_id)}</p><p class="muted">${count} 个共享声音 · ${v.bindings.length} 条字段引用</p></div><img src="${esc(v.thumbnail_url)}" alt="${esc(v.model_id)}静态预览"></div><p class="note">${esc(v.configuration_note)}。声音按武器与弹药分别关联；未解析事件及空字段保持原状态。</p><div class="table-wrap"><table><thead><tr><th>作用 / 来源</th><th>音效与映射状态</th><th>试听</th></tr></thead><tbody>${v.bindings.map(b => {
    const a = catalog.assets[b.audio_id];
    return `<tr><td>${esc(roles[b.role_field] || b.role_field)}<small>${esc(b.origin.name || (b.domain === 'unit' ? '单位 ' + b.origin.id : b.origin.id))}${b.origin.weapon_id ? ' · 武器 ' + esc(b.origin.weapon_id) : ''}</small></td><td>${a ? `<button class="event-link" data-audio="${esc(a.audio_id)}">${esc(a.label)}</button><small>${a.duration_seconds.toFixed(2)} 秒 · ${new Set(a.users.map(u => u.model_id)).size} 个型号共享</small>` : `<span class="${b.status === 'event_not_indexed' ? 'error' : 'muted'}">${esc(labels[b.status] || b.status)}</span><small class="mono">${esc(b.event_path || '空值保留')}</small>`}${b.playback_review ? '<small>正常弹药命中分支已定位；该武器回退仍未解析。</small>' : ''}${evidence(b)}</td><td><button data-preview="${esc(b.binding_id)}" ${a ? '' : 'disabled'}>试听</button></td></tr>`;
  }).join('') || '<tr><td colspan="3">没有当前单位引用，未猜配其他型号。</td></tr>'}</tbody></table></div>`;
}

function renderAudio(audioId) {
  selectedAudio = audioId;
  const a = catalog.assets[audioId];
  const models = new Set(a.users.map(u => u.model_id));
  const representative = a.users[0];
  $('#detail').innerHTML = `<div class="hero"><div><p class="eyebrow">音效档案 / 反向查询</p><span class="badge">有限事件渲染</span><h2>${esc(a.label)}</h2><p class="mono">${esc(a.event_guid)}</p><p>${a.duration_seconds.toFixed(2)} 秒 · ${a.sample_rate / 1000} kHz · ${a.channels} 声道<br>${models.size} 个型号 · ${a.users.length} 条引用</p><button class="primary" data-preview="${esc(representative?.binding_id)}">试听该声音</button></div><div><p class="muted">循环关闭 · 逐条听审未登记</p><p class="mono">${esc(a.event_path)}</p><details class="evidence"><summary>文件指纹与渲染配置</summary><pre>${esc(JSON.stringify({sha256: a.sha256, render_profile: a.render_profile, source_batch: a.source_batch}, null, 2))}</pre></details></div></div><div class="table-wrap"><table><thead><tr><th>使用型号</th><th>作用</th><th>单位 / 武器 / 弹药</th></tr></thead><tbody>${a.users.map(u => `<tr><td><button class="event-link" data-vehicle="${esc(u.config_id)}">${esc(u.model_id)}</button></td><td>${esc(roles[u.role_field] || u.role_field)}</td><td>${esc(u.domain)} · ${u.origin_id}${u.weapon_id ? ' · 武器 ' + u.weapon_id : ''}</td></tr>`).join('')}</tbody></table></div>`;
}

function setMode(next) {
  mode = next;
  $('#search').value = '';
  $('#search').placeholder = mode === 'vehicle' ? '型号、名称或武器' : '事件、编号或使用型号';
  $('.filters').hidden = mode === 'audio';
  $('#vehicle-tab').setAttribute('aria-selected', String(mode === 'vehicle'));
  $('#audio-tab').setAttribute('aria-selected', String(mode === 'audio'));
  $('#catalog-panel').setAttribute('aria-labelledby', mode === 'vehicle' ? 'vehicle-tab' : 'audio-tab');
  renderResults();
}

function renderResults() {
  if (!catalog) return;
  const rows = mode === 'vehicle' ? catalog.searchVehicles($('#search').value, $('#category').value, $('#status').value) : catalog.searchAudio($('#search').value);
  $('#result-count').textContent = `${rows.length} / ${mode === 'vehicle' ? catalog.vehicles.length : Object.keys(catalog.assets).length} 条档案`;
  $('#results').innerHTML = rows.map(row => mode === 'vehicle' ? `<button class="result-item" data-vehicle="${esc(row.config_id)}" aria-current="${row.config_id === currentVehicle?.config_id}"><strong>${esc(row.model_id)}</strong><small>${esc(row.name || row.variant)} · ${esc(labels[row.mapping_status])}</small></button>` : `<button class="result-item" data-audio="${esc(row.audio_id)}" aria-current="${row.audio_id === selectedAudio}"><strong>${esc(row.label)}</strong><small>${row.duration_seconds.toFixed(2)} 秒 · ${new Set(row.users.map(u => u.model_id)).size} 个型号</small></button>`).join('') || '<p class="empty">没有匹配结果</p>';
}

function configureWeapons() {
  const weapons = currentVehicle.bindings.filter(b => b.domain === 'weapon' && b.role_field === 'Shot');
  $('#weapon').innerHTML = weapons.map(b => `<option value="${b.origin.id}">${esc(b.origin.name || b.origin.id)}</option>`).join('') || '<option value="">没有武器引用</option>';
  const preferred = weapons.find(b => /GAU.?8|M230|M256/i.test(b.origin.name));
  if (preferred) $('#weapon').value = String(preferred.origin.id);
  configureAmmunition();
}

function configureAmmunition() {
  const weapon = Number($('#weapon').value);
  const ammunition = currentVehicle.bindings.filter(b => b.domain === 'ammunition' && b.role_field === 'Impact' && b.origin.weapon_id === weapon);
  $('#ammunition').innerHTML = ammunition.map(b => `<option value="${b.origin.id}">${esc(b.origin.name || b.origin.id)}</option>`).join('') || '<option value="">没有弹药引用</option>';
  updateActions();
}

function request(action) {
  return {modelId: currentVehicle.model_id, configId: currentVehicle.config_id, action,
    weaponId: $('#weapon').value ? Number($('#weapon').value) : null, ammoId: $('#ammunition').value ? Number($('#ammunition').value) : null,
    ammunitionPresetState: $('#missing-preset').checked ? 'missing' : 'present'};
}

function updateActions() {
  const notes = [];
  document.querySelectorAll('[data-action]').forEach(button => {
    const result = catalog.resolve(request(button.dataset.action));
    button.disabled = result.status !== 'playable';
    button.title = result.status === 'playable' ? result.asset.label : result.reason;
    if (result.status !== 'playable') notes.push(`${button.textContent}：${result.reason}`);
  });
  $('#action-state').textContent = notes.join('；') || '各动作已有确证声音；有限片段播放，循环关闭。';
}

async function playAction(action) {
  if (!runtime?.unlocked) { message('先点击顶部“开启声音”。'); $('#unlock').focus(); return; }
  if (!stage.model) { message('先加载三款样例之一，再验证空间动作；表格中的试听不需要加载模型。'); return; }
  const placement = catalog.rules.actions[action].anchor;
  const frozenImpact = placement === 'impact' ? new THREE.Object3D() : null;
  if (frozenImpact) { frozenImpact.position.copy(stage.impact.position); stage.scene.add(frozenImpact); }
  const result = await runtime.trigger(request(action), {instanceId: currentVehicle.config_id, anchor: frozenImpact || stage[placement]});
  if (frozenImpact) {
    // 声音已经保留其世界位置；结束后移除不再使用的空节点。
    const empty = () => { if (!frozenImpact.children.length) frozenImpact.removeFromParent(); else setTimeout(empty, 500); };
    empty();
  }
  if (result.status === 'playing') {
    if (action === 'move') stage.move();
    if (action === 'shot') stage.flash();
    message(`${catalog.rules.actions[action].label}：${catalog.assets[result.audio_id].label}`);
  } else message(result.reason || result.status, result.status === 'load_failed');
}

document.addEventListener('click', async event => {
  const button = event.target.closest('button');
  if (!button || !catalog) return;
  try {
    if (button.dataset.vehicle) {
      const vehicle = catalog.vehicles.find(v => v.config_id === button.dataset.vehicle);
      if (!vehicle) return;
      setVehicle(vehicle);
      if (mode !== 'vehicle') setMode('vehicle');
      renderResults();
    } else if (button.dataset.audio) {
      stop();
      if (mode !== 'audio') setMode('audio');
      renderAudio(button.dataset.audio);
      renderResults();
    } else if (button.dataset.preview) {
      if (!runtime?.unlocked) { message('先点击顶部“开启声音”。'); $('#unlock').focus(); return; }
      stop();
      const binding = catalog.vehicles.flatMap(v => v.bindings).find(b => b.binding_id === button.dataset.preview);
      const result = await runtime.play(catalog.result(binding), {action: 'shot', instanceId: 'preview', positional: false});
      message(result.status === 'playing' ? '正在试听：' + catalog.assets[result.audio_id].label : result.reason || result.status, result.status === 'load_failed');
    } else if (button.dataset.sample) {
      setVehicle(catalog.vehicles.find(v => v.model_id === button.dataset.sample));
      setMode('vehicle');
      $('#stage-heading').scrollIntoView({block: 'start'});
    } else if (button.dataset.action) await playAction(button.dataset.action);
  } catch (error) { message(error.message, true); }
});

$('#unlock').addEventListener('click', () => { if (catalog) ensureAudio().catch(error => message(error.message, true)); });
$('#stop').addEventListener('click', () => { runtime?.stopAll(); stage.stopMotion(); message('全部声音已停止。'); });
$('#clear-cache').addEventListener('click', () => { stop(true); message('已停止声音并清空解码缓存。'); });
$('#volume').addEventListener('input', () => runtime?.setVolume($('#volume').value));
$('#mute').addEventListener('change', () => runtime?.setMuted($('#mute').checked));
$('#search').addEventListener('input', renderResults);
$('#category').addEventListener('change', renderResults);
$('#status').addEventListener('change', renderResults);
$('#vehicle-tab').addEventListener('click', () => { if (catalog) { setMode('vehicle'); renderVehicle(); } });
$('#audio-tab').addEventListener('click', () => { if (catalog) { setMode('audio'); renderAudio(selectedAudio || Object.keys(catalog.assets)[0]); renderResults(); } });
$('.tabs').addEventListener('keydown', event => { if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { const target = mode === 'vehicle' ? $('#audio-tab') : $('#vehicle-tab'); target.click(); target.focus(); } });
$('#weapon').addEventListener('change', configureAmmunition);
$('#ammunition').addEventListener('change', updateActions);
$('#missing-preset').addEventListener('change', updateActions);
for (const axis of ['x', 'y', 'z']) $(`#anchor-${axis}`).addEventListener('input', event => { stage.muzzle.position[axis] = Number(event.target.value); });
$('#load-model').addEventListener('click', async () => {
  if (!currentVehicle?.sample_model_url) return;
  const generation = ++stageGeneration;
  $('#load-model').disabled = true;
  stop();
  try { await stage.load(currentVehicle, baseUrl); }
  catch (error) { if (generation === stageGeneration) { $('#model-state').textContent = '模型加载失败'; message(error.message, true); } }
  finally { if (generation === stageGeneration) $('#load-model').disabled = false; }
});
window.addEventListener('pagehide', () => { runtime?.dispose(); stage.dispose(); listener?.removeFromParent(); });
document.addEventListener('visibilitychange', () => { if (document.hidden) { stop(); } });

try {
  const [assets, bindings, rules] = await Promise.all(['音频资源', '载具绑定', '播放规则'].map(async name => {
    const response = await fetch(new URL(`数据/${name}.json`, baseUrl));
    if (!response.ok) throw new Error(`索引读取失败：${name}`);
    return response.json();
  }));
  catalog = new AudioCatalog(assets, bindings, rules);
  $('#metrics').innerHTML = [[catalog.summary.models, '载具入口'], [catalog.summary.audio_events, '共享声音'], [catalog.summary.models_with_audio, '有声音'], [catalog.summary.missing_event_paths.length, '路径缺项']].map(([n, label]) => `<span class="metric"><strong>${n}</strong><span>${label}</span></span>`).join('');
  setVehicle(catalog.vehicles.find(v => v.model_id === 'US_A10'));
  renderResults();
  message('索引已加载。选择型号或切换“按音效”查询；声音与三维模型按需读取。');
} catch (error) { message(error.message, true); $('#detail').textContent = '索引未加载，请使用项目提供的本地启动脚本。'; }
