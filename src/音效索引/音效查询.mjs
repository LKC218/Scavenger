/** 纯数据查询：保留配置身份、武器限定及弹药预设对象的条件分支。 */
export class AudioCatalog {
  constructor(resources, bindings, rules) {
    if (![resources, bindings, rules].every(x => x.schema_version === 1 && x.index_batch === resources.index_batch)) throw new Error('索引版本或批次不一致');
    this.assets = resources.assets;
    this.vehicles = bindings.vehicles;
    this.summary = bindings.summary;
    this.rules = rules;
  }

  vehicle(modelId, configId) {
    return this.vehicles.find(v => v.model_id === modelId && v.config_id === configId);
  }

  users(audioId) { return this.assets[audioId]?.users ?? []; }

  searchVehicles(query = '', category = 'all', status = 'all') {
    const q = query.trim().toLowerCase();
    return this.vehicles.filter(v => (category === 'all' || v.category === category) && (status === 'all' || v.mapping_status === status) &&
      (!q || [v.model_id, v.name, v.config_id, ...v.bindings.map(b => b.origin.name ?? '')].join(' ').toLowerCase().includes(q)));
  }

  searchAudio(query = '') {
    const q = query.trim().toLowerCase();
    return Object.values(this.assets).filter(a => !q || [a.label, a.event_path, a.event_guid, ...a.users.map(u => u.model_id)].join(' ').toLowerCase().includes(q));
  }

  result(binding) {
    if (!binding) return {status: 'no_binding', reason: '当前配置没有这条引用'};
    if (!binding.audio_id || binding.status !== 'confirmed') return {status: binding.status, binding, reason: binding.status === 'empty_field' ? '源字段为空，保持为空' : '事件未解析，未替配其他声音'};
    const asset = this.assets[binding.audio_id];
    return asset ? {status: 'playable', binding, asset} : {status: 'missing_asset', binding, reason: '索引资源不存在'};
  }

  resolve({modelId, configId, action, weaponId, ammoId, ammunitionPresetState = 'unknown'}) {
    const vehicle = this.vehicle(modelId, configId);
    if (!vehicle) return {status: 'configuration_not_found', reason: '必须提供当前模型及完整配置标识'};
    let matches;
    if (action === 'impact' || action === 'interception') {
      if (ammunitionPresetState === 'missing') {
        matches = vehicle.bindings.filter(b => b.domain === 'weapon' && b.origin.id === weaponId && b.role_field === 'Impact');
      } else if (ammunitionPresetState === 'present') {
        matches = vehicle.bindings.filter(b => b.domain === 'ammunition' && b.origin.weapon_id === weaponId && b.origin.id === ammoId && b.role_field === (action === 'impact' ? 'Impact' : 'MissileInterception'));
      } else return {status: 'preset_state_unknown', reason: '弹药预设对象状态未知，不推断武器回退'};
    } else {
      const role = {move: 'MoveEvent', death: 'DeathEvent', shot: 'Shot'}[action];
      if (!role) return {status: 'unsupported_action', reason: '动作未定义'};
      matches = vehicle.bindings.filter(b => b.role_field === role && (action === 'shot' ? b.domain === 'weapon' && b.origin.id === weaponId : b.domain === 'unit'));
    }
    if (matches.length > 1) return {status: 'ambiguous_binding', reason: '存在多个候选，需明确配置或关系'};
    return this.result(matches[0]);
  }
}
