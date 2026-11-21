/** Three.js适配器；索引查询独立于场景，声音实例独立于共享解码缓存。 */
export class VehicleAudioRuntime {
  constructor({THREE, listener, catalog, baseUrl, loadBuffer, now = () => performance.now(), onChange = () => {}}) {
    Object.assign(this, {THREE, listener, catalog, now, onChange});
    this.baseUrl = new URL(baseUrl);
    this.loadBuffer = loadBuffer ?? (url => new THREE.AudioLoader().loadAsync(url));
    this.cache = new Map();
    this.inflight = new Map();
    this.voices = new Set();
    this.lastPlay = new Map();
    this.generation = 0;
    this.unlocked = false;
    this.disposed = false;
    this.masterVolume = 0.35;
    this.muted = false;
  }

  async unlock() {
    if (this.disposed) throw new Error('播放器已释放');
    await this.listener.context.resume();
    if (this.listener.context.state !== 'running') throw new Error('声音未开启，请再次点击开启声音');
    this.unlocked = true;
    this.setVolume(this.masterVolume);
    this.onChange(this.stats());
  }

  setVolume(value) {
    this.masterVolume = Math.min(1, Math.max(0, Number(value) || 0));
    this.listener.setMasterVolume(this.muted ? 0 : this.masterVolume);
  }

  setMuted(value) { this.muted = Boolean(value); this.setVolume(this.masterVolume); }

  stats() {
    return {cached: this.cache.size, cacheBytes: [...this.cache.values()].reduce((s, r) => s + r.bytes, 0),
      pending: this.inflight.size, voices: this.voices.size, unlocked: this.unlocked};
  }

  prune(required = 0) {
    const limit = this.catalog.rules.cache_max_bytes;
    const pinned = new Set([...this.voices].map(v => v.asset.audio_id));
    for (const [id, row] of [...this.cache].sort((a, b) => a[1].used - b[1].used)) {
      if (this.stats().cacheBytes + required <= limit) break;
      if (!pinned.has(id)) this.cache.delete(id);
    }
    return this.stats().cacheBytes + required <= limit;
  }

  async buffer(asset) {
    const id = asset.audio_id;
    if (this.cache.has(id)) { const row = this.cache.get(id); row.used = this.now(); return row.buffer; }
    if (this.inflight.has(id)) return this.inflight.get(id).promise;
    // PCM解码后为浮点，先按源帧数估算，包含所有并行解码的预留量。
    const estimate = Math.ceil(asset.duration_seconds * asset.sample_rate) * asset.channels * 4;
    const reserved = [...this.inflight.values()].reduce((s, r) => s + r.bytes, 0);
    if (!this.prune(estimate + reserved)) throw new Error('音频缓存预算已满，请停止其他声音再试');
    const generation = this.generation;
    const promise = Promise.resolve().then(() => this.loadBuffer(new URL(asset.url, this.baseUrl).href)).then(buffer => {
      const bytes = buffer.length * buffer.numberOfChannels * 4;
      if (this.disposed || generation !== this.generation) return buffer;
      const others = [...this.inflight.entries()].filter(([key]) => key !== id).reduce((s, [, r]) => s + r.bytes, 0);
      if (!this.prune(bytes + others)) throw new Error('实际解码数据超过缓存预算');
      this.cache.set(id, {buffer, bytes, used: this.now()});
      return buffer;
    }).finally(() => { this.inflight.delete(id); this.onChange(this.stats()); });
    this.inflight.set(id, {promise, bytes: estimate});
    this.onChange(this.stats());
    return promise;
  }

  trigger(request, placement) {
    return this.play(this.catalog.resolve(request), {...placement, action: request.action});
  }

  async play(result, {action = 'shot', instanceId = 'sample', anchor, positional = true} = {}) {
    if (this.disposed) return {status: 'disposed'};
    if (result.status !== 'playable') return result;
    if (!this.unlocked || this.listener.context.state !== 'running') return {status: 'locked', reason: '先点击开启声音'};
    const rule = this.catalog.rules.actions[action];
    if (!rule) return {status: 'unsupported_action'};
    if (positional && !anchor) return {status: 'missing_anchor', reason: '空间声音需要明确的挂载点'};
    const group = `${instanceId}|${action}`;
    if (this.voices.size >= this.catalog.rules.global_max_voices || [...this.voices].filter(v => v.group === group).length >= rule.max_voices) return {status: 'voice_limit', reason: '达到声音并发上限'};
    if (this.now() - (this.lastPlay.get(group) ?? -Infinity) < rule.cooldown_ms) return {status: 'cooldown', reason: '动作冷却中'};
    const time = this.now();
    this.lastPlay.set(group, time);
    const entry = {group, asset: result.asset, sound: null, cancelled: false, generation: this.generation, disposed: false};
    this.voices.add(entry);
    this.onChange(this.stats());
    try {
      const buffer = await this.buffer(result.asset);
      if (entry.cancelled || entry.generation !== this.generation || this.disposed) return {status: 'cancelled'};
      if (!this.cache.has(result.asset.audio_id)) {
        const bytes = buffer.length * buffer.numberOfChannels * 4;
        const reserved = [...this.inflight.values()].reduce((s, r) => s + r.bytes, 0);
        if (!this.prune(bytes + reserved)) throw new Error('音频缓存预算已满');
        this.cache.set(result.asset.audio_id, {buffer, bytes, used: this.now()});
      }
      const sound = positional ? new this.THREE.PositionalAudio(this.listener) : new this.THREE.Audio(this.listener);
      entry.sound = sound;
      sound.setBuffer(buffer);
      sound.setLoop(false);
      sound.setVolume(rule.volume);
      if (positional) {
        sound.setDistanceModel(rule.distance_model);
        sound.setRefDistance(rule.ref_distance);
        sound.setRolloffFactor(rule.rolloff_factor);
        anchor.add(sound);
      }
      const ended = sound.onEnded.bind(sound);
      sound.onEnded = () => { ended(); this.cleanup(entry); };
      const gain = sound.gain.gain;
      const start = this.listener.context.currentTime;
      gain.cancelScheduledValues(start);
      gain.setValueAtTime(0, start);
      gain.linearRampToValueAtTime(rule.volume, start + rule.fade_in_seconds);
      entry.rule = rule;
      sound.play();
      this.onChange(this.stats());
      return {status: 'playing', audio_id: result.asset.audio_id, binding_id: result.binding.binding_id};
    } catch (error) {
      this.cleanup(entry);
      if (this.lastPlay.get(group) === time) this.lastPlay.delete(group);
      return {status: 'load_failed', reason: error.message};
    } finally {
      if (entry.cancelled || !entry.sound) this.cleanup(entry);
    }
  }

  cleanup(entry) {
    if (entry.disposed) return;
    entry.disposed = true;
    clearTimeout(entry.timer);
    this.voices.delete(entry);
    if (entry.sound) {
      entry.sound.removeFromParent();
      entry.sound.disconnect();
      entry.sound.gain.disconnect();
    }
    this.onChange(this.stats());
  }

  stopEntry(entry, immediate = false) {
    entry.cancelled = true;
    const sound = entry.sound;
    if (!sound?.isPlaying) { this.cleanup(entry); return; }
    const fade = immediate ? 0 : entry.rule.fade_out_seconds;
    const now = this.listener.context.currentTime;
    const gain = sound.gain.gain;
    if (gain.cancelAndHoldAtTime) gain.cancelAndHoldAtTime(now);
    else { const value = gain.value; gain.cancelScheduledValues(now); gain.setValueAtTime(value, now); }
    gain.linearRampToValueAtTime(0, now + fade);
    sound.stop(fade);
    if (immediate) { this.cleanup(entry); return; }
    entry.timer = setTimeout(() => this.cleanup(entry), (fade + 0.03) * 1000);
  }

  stopAll({clearCache = false, immediate = false} = {}) {
    this.generation += 1;
    for (const entry of this.voices) this.stopEntry(entry, immediate);
    this.lastPlay.clear();
    if (clearCache) {
      // 正在使用的缓冲区仍由淡出实例短暂持有，缓存引用立即释放。
      this.cache.clear();
    }
    this.onChange(this.stats());
  }

  dispose() {
    this.disposed = true;
    this.stopAll({clearCache: true, immediate: true});
    this.unlocked = false;
  }
}
