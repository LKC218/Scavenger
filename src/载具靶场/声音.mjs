import * as THREE from 'three';
import {VehicleAudioRuntime} from '/audio-runtime.mjs';
import {WEAPONS} from './模拟.mjs';

/** 主音量输入 → 驱动增益 → 压缩 → 输出增益 → 软限幅；0dB复现改前参数。 */
export function createMix(context,input,{boostDb=6}={}){
  const compressor=context.createDynamicsCompressor();compressor.threshold.value=-8;compressor.knee.value=3;compressor.ratio.value=20;compressor.attack.value=.003;compressor.release.value=.2;
  // 原生压缩器带自动补偿；阈值固定，增强在前后分配，后置最多3dB。
  const drive=context.createGain(),makeup=context.createGain(),valid=value=>[0,6,12].includes(Number(value))?Number(value):6;
  const gains=db=>[10**(Math.max(0,db-3)/20),10**(Math.min(3,db)/20)];
  boostDb=valid(boostDb);[drive.gain.value,makeup.gain.value]=gains(boostDb);
  const setBoost=value=>{const db=valid(value),now=context.currentTime,target=gains(db);for(const [i,parameter] of [drive.gain,makeup.gain].entries()){parameter.cancelAndHoldAtTime(now);parameter.setTargetAtTime(target[i],now,.035);}return db;};
  const limiter=context.createWaveShaper(),curve=new Float32Array(65537);
  for(let i=0;i<curve.length;i++){const x=i/(curve.length-1)*2-1,a=Math.abs(x);curve[i]=Math.sign(x)*(a<=.8?a:.8+.15*(1-Math.exp(-(a-.8)/.03)));}limiter.curve=curve;limiter.oversample='none';
  const analyser=context.createAnalyser();analyser.fftSize=1024;
  input.disconnect();input.connect(drive);drive.connect(compressor);compressor.connect(makeup);makeup.connect(limiter);limiter.connect(context.destination);limiter.connect(analyser);
  return{compressor,drive,makeup,limiter,analyser,setBoost};
}

/** 持续机械声的游戏化状态；不使用行驶布尔值开关整个引擎。 */
export function engineState(u){
  if(u.kind==='tank')return u.moving?{name:'行驶',gain:.55,rate:1.1}:{name:'待机',gain:.28,rate:.9};
  if(u.kind==='heli')return u.moving?{name:'旋翼飞行',gain:.4,rate:1.035}:{name:'悬停',gain:.28,rate:1};
  return u.job&&u.job.type!=='move'?{name:'攻击飞行',gain:.5,rate:1.07}:{name:'巡航盘旋',gain:.42,rate:1};
}

/** 每型保留一个邻近代表，其余按选择、距离和已发声状态分配。 */
export function chooseEngines(units,selected,center,existing){
  const distance=u=>Math.hypot(u.x-center.x,u.z-center.z);
  const score=u=>distance(u)-(selected.has(u.id)?120:0)-(existing.has(u.id)?25:0);
  const candidates=units.filter(u=>distance(u)<700).sort((a,b)=>score(a)-score(b));
  const picked=[];
  for(const kind of ['tank','heli','plane']){const u=candidates.find(u=>u.kind===kind);if(u)picked.push(u);}
  for(const u of candidates)if(picked.length<8&&!picked.includes(u))picked.push(u);
  return picked;
}

/** 战术监听点、持续机械声与瞬态事件；共享运行时保持原接口。 */
export class BattlefieldAudio{
  constructor(view,manifest,toast){
    Object.assign(this,{view,manifest,toast});this.active=false;this.anchors=new Map();this.engines=new Map();this.gunRuns=new Map();this.derived=new Map();this.generation=0;this.nextEngine=0;this.dropped=0;this.peak=0;this.airAudio={sustainStarts:0,sustainShots:0,releases:0,impacts:0,tailReplacements:0};
    this.listener=new THREE.AudioListener();view.scene.add(this.listener);
    const rule=(volume,max=3,cooldown=40)=>({max_voices:max,cooldown_ms:cooldown,volume,distance_model:'inverse',ref_distance:100,rolloff_factor:1,fade_in_seconds:.015,fade_out_seconds:.12});
    // 给八个持续机械声预留独立声部，不被密集爆炸挤掉。
    const heliRule=rule(.65,2,60);heliRule.fade_in_seconds=.003;heliRule.fade_out_seconds=.012;
    this.runtime=new VehicleAudioRuntime({THREE,listener:this.listener,baseUrl:location.href,catalog:{rules:{cache_max_bytes:80*1024*1024,global_max_voices:40,actions:{shot:rule(.65,4),heliGun:heliRule,planeGun:{...rule(.65,2,0),fade_in_seconds:.003,fade_out_seconds:.035},planeTail:{...rule(.65,2,0),fade_in_seconds:.035,fade_out_seconds:.035},airImpact:{...rule(.65,3,0),fade_in_seconds:.002,fade_out_seconds:.035},impact:rule(.65,8)}}}});
    this.runtime.setVolume(1);this.boostDb=6;
    Object.assign(this,createMix(this.listener.context,this.listener.getInput()));this.wave=new Float32Array(1024);
  }
  async toggle(){
    if(this.active){this.active=false;this.stop();this.runtime.setMuted(true);return false;}
    const token=this.generation;await this.runtime.unlock();this.runtime.setMuted(false);await Promise.all(Object.values(this.manifest.audio).map(a=>this.runtime.buffer(a)));
    if(token!==this.generation)return false;await this.prepareHeliShot();await this.prepareAirAudio();this.active=true;return true;
  }
  cacheSlice(asset,source,start,end,suffix){
    const first=Math.max(0,Math.round(start*source.sampleRate)),last=Math.min(source.length,Math.round(end*source.sampleRate));
    if(last<=first)throw new Error('原生音效派生区间无效');
    const buffer=this.listener.context.createBuffer(source.numberOfChannels,last-first,source.sampleRate);
    for(let c=0;c<source.numberOfChannels;c++)buffer.copyToChannel(source.getChannelData(c).subarray(first,last),c);
    const result={...asset,audio_id:asset.audio_id+':'+suffix,duration_seconds:buffer.duration};
    this.runtime.cache.set(result.audio_id,{buffer,bytes:buffer.length*buffer.numberOfChannels*4,used:performance.now()});
    return result;
  }
  async prepareAirAudio(){
    if(this.derived.size)return;
    for(const key of ['heliImpact','planeImpact']){
      const asset=this.manifest.audio[key];if(!asset)continue;const source=await this.runtime.buffer(asset);let peak=0;
      for(let c=0;c<source.numberOfChannels;c++)for(const value of source.getChannelData(c))peak=Math.max(peak,Math.abs(value));
      let first=source.length,last=0;
      for(let c=0;c<source.numberOfChannels;c++){const a=source.getChannelData(c);for(let i=0;i<a.length;i++)if(Math.abs(a[i])>peak*.0001){first=Math.min(first,i);last=Math.max(last,i);}}
      if(last<=first)throw new Error('命中事件没有有效波形');
      const start=Math.max(0,first/source.sampleRate-.005),end=Math.min(source.duration,last/source.sampleRate+.09);
      this.derived.set(key,this.cacheSlice(asset,source,start,end,'aligned-impact-v3'));
      this.airAudio[key]={sourceId:asset.audio_id,sourceDuration:source.duration,start,end,samplesUnchanged:true};
    }
    const profile=this.manifest.airAudioProfile;if(!profile)return;
    const asset=this.manifest.audio.planeShot,source=await this.runtime.buffer(asset);
    this.derived.set('planeShot',this.cacheSlice(asset,source,profile.attackStart,profile.releaseEnd,'native-sustain-v3'));
    this.derived.set('planeRelease',this.cacheSlice(asset,source,profile.releaseStart,profile.releaseEnd,'native-release-v3'));
    const sustained=this.runtime.cache.get(this.derived.get('planeShot').audio_id).buffer,frames=Math.round(sustained.sampleRate*.02),a=Math.round(profile.loopStart*sustained.sampleRate),b=Math.round(profile.loopEnd*sustained.sampleRate);
    // 原FMOD渲染没有公开采样循环点；仅在网页循环接缝做20毫秒交叉淡化，源文件与音调不变。
    for(let c=0;c<sustained.numberOfChannels;c++){const data=sustained.getChannelData(c),head=data.slice(a-frames,a);for(let i=0;i<frames;i++){const t=i/(frames-1);data[b-frames+i]=data[b-frames+i]*(1-t)+head[i]*t;}}
    this.airAudio.planeSource={sourceId:asset.audio_id,...profile,loopJoinSeconds:.02,loopJoin:'网页派生交叉淡化，非原作循环点'};
  }
  async prepareHeliShot(){
    if(this.heliShotAsset||!this.manifest.audio.heliShot)return;
    const asset=this.manifest.audio.heliShot,source=await this.runtime.buffer(asset);let peak=0;
    for(let c=0;c<source.numberOfChannels;c++)for(const sample of source.getChannelData(c))peak=Math.max(peak,Math.abs(sample));
    let first=source.length,last=0;
    for(let c=0;c<source.numberOfChannels;c++){const a=source.getChannelData(c);for(let i=0;i<a.length;i++){const level=Math.abs(a[i]);if(level>peak*.001)first=Math.min(first,i);if(level>peak*.0001)last=Math.max(last,i);}}
    const start=Math.max(0,first-Math.round(source.sampleRate*.01)),end=Math.min(source.length,last+Math.round(source.sampleRate*.1));
    if(end<=start)throw new Error('M230音效没有有效声音段');
    const buffer=this.listener.context.createBuffer(source.numberOfChannels,end-start,source.sampleRate);
    for(let c=0;c<source.numberOfChannels;c++)buffer.copyToChannel(source.getChannelData(c).subarray(start,end),c);
    const audio_id=asset.audio_id+':aligned-native-v2';this.heliShotAsset={...asset,audio_id,duration_seconds:buffer.duration};
    this.runtime.cache.set(audio_id,{buffer,bytes:buffer.length*buffer.numberOfChannels*4,used:performance.now()});
    this.heliShotAlignment={sourceId:asset.audio_id,sourceDuration:source.duration,leadingTrimSeconds:start/source.sampleRate,trailingTrimSeconds:(source.length-end)/source.sampleRate,duration:buffer.duration,samplesUnchanged:true,attempts:0,starts:0,voiceSteals:0,maxVoicesPerUnit:0};
  }
  setVolume(value){this.runtime.setVolume(value);}
  setEnhancement(value){this.boostDb=this.setBoost(value);this.resetStats();return this.boostDb;}
  resetStats(){this.peak=0;this.currentPeak=0;this.rms=0;this.dropped=0;}
  releaseEngine(id){
    const entry=this.engines.get(id);if(!entry)return;
    entry.cancelled=true;
    if(entry.sound){if(entry.sound.isPlaying)entry.sound.stop();entry.sound.removeFromParent();entry.sound.disconnect();entry.sound.gain.disconnect();}
    entry.anchor.removeFromParent();this.anchors.delete('engine-'+id);this.engines.delete(id);
  }
  stop(){this.generation++;this.gunRuns.clear();for(const id of [...this.engines.keys()])this.releaseEngine(id);this.runtime.stopAll({immediate:true});for(const a of this.anchors.values())a.removeFromParent();this.anchors.clear();this.nextEngine=0;}
  async startEngine(u){
    const asset=this.manifest.audio[u.kind+'Move'];if(!asset?.loop)return;
    const anchor=new THREE.Object3D();anchor.position.set(u.x,u.y+1,u.z);anchor.userData.expires=Infinity;this.view.scene.add(anchor);
    const entry={anchor,kind:u.kind,sound:null,cancelled:false,state:'加载',retireAt:0},token=this.generation;
    this.engines.set(u.id,entry);this.anchors.set('engine-'+u.id,anchor);
    try{
      const buffer=await this.runtime.buffer(asset);
      if(token!==this.generation||entry.cancelled||this.engines.get(u.id)!==entry)return;
      const sound=new THREE.PositionalAudio(this.listener);entry.sound=sound;anchor.add(sound);sound.setBuffer(buffer);sound.setLoop(true);sound.setVolume(0);
      sound.setDistanceModel('inverse');sound.setRefDistance({tank:45,heli:90,plane:140}[u.kind]);sound.setRolloffFactor(1);
      // 同型实例分散循环相位，避免完全相干叠加；音频时钟驱动，不跟模拟步长重播。
      let hash=0;for(const c of String(u.id))hash=(hash*31+c.charCodeAt(0))>>>0;
      sound.offset=(hash%1000)/1000*buffer.duration;sound.setPlaybackRate(engineState(u).rate);sound.play();
    }catch(error){if(this.engines.get(u.id)===entry)this.releaseEngine(u.id);this.dropped++;this.toast('机械声音加载失败：'+error.message);}
  }
  updateEngines(sim,selected,now){
    const clock=this.listener.context.currentTime;
    for(const [id,e] of this.engines)if(e.retireAt&&clock>=e.retireAt)this.releaseEngine(id);
    if(now>=this.nextEngine){
      this.nextEngine=now+200;
      const near=chooseEngines(sim.units,selected,this.view.center,this.engines),keep=new Set(near.map(u=>u.id));
      for(const [id,e] of this.engines)if(!keep.has(id)&&!e.retireAt){
        e.retireAt=clock+.2;
        if(e.sound){const gain=e.sound.gain.gain;gain.cancelAndHoldAtTime(clock);gain.linearRampToValueAtTime(0,clock+.18);}
      }
      // 淡出声部仍占预算；释放后下一次调度再补位，物理声源始终不超过八个。
      for(const u of near)if(!this.engines.has(u.id)&&this.engines.size<8)this.startEngine(u);
    }
    const counts={};for(const e of this.engines.values())if(!e.retireAt)counts[e.kind]=(counts[e.kind]??0)+1;
    for(const [id,e] of this.engines){
      const u=sim.units.find(u=>u.id===id);if(!u||e.retireAt)continue;
      e.anchor.position.set(u.x,u.y+1,u.z);if(!e.sound)continue;
      const state=engineState(u),distance=Math.hypot(u.x-this.view.center.x,u.z-this.view.center.z),edge=Math.max(0,Math.min(1,(700-distance)/140));
      const gain=state.gain*edge/Math.sqrt(Math.max(1,(counts[u.kind]??1)*.6));
      if(e.state!==state.name||Math.abs((e.targetGain??-1)-gain)>.005){
        const parameter=e.sound.gain.gain;parameter.cancelAndHoldAtTime(clock);parameter.setTargetAtTime(gain,clock,.09);
        e.sound.source.playbackRate.setTargetAtTime(state.rate,clock,.14);e.state=state.name;e.targetGain=gain;e.targetRate=state.rate;
      }
    }
  }
  play(key,position,action,instanceId){
    if(!this.active)return;const asset=this.derived.get(key)??(key==='heliShot'&&this.heliShotAsset?this.heliShotAsset:this.manifest.audio[key]);if(!asset)return;
    if(action==='heliGun'){
      // 原渲染是带长尾音的事件；每枪重触发同一发声组，旧尾音短淡出，避免10秒占位挤掉后续枪声。
      for(const v of this.runtime.voices)if(v.group===instanceId+'|heliGun'&&!v.cancelled){this.runtime.stopEntry(v);this.heliShotAlignment.voiceSteals++;}
      this.heliShotAlignment.attempts++;
    }
    if(Math.hypot(position.x-this.listener.position.x,position.z-this.listener.position.z)>320){this.dropped++;return;}
    const anchorId=Symbol(instanceId);
    this.anchors.get(anchorId)?.removeFromParent();
    const anchor=new THREE.Object3D();anchor.position.set(position.x,position.y,position.z);this.view.scene.add(anchor);anchor.userData.expires=this.listener.context.currentTime+asset.duration_seconds+1;this.anchors.set(anchorId,anchor);
    const token=this.generation;const promise=this.runtime.play({status:'playable',asset,binding:{binding_id:asset.audio_id}},{action,instanceId,anchor}).then(r=>{if(action==='heliGun'&&r.status==='playing')this.heliShotAlignment.starts++;if(r.status!=='playing'){this.dropped++;anchor.removeFromParent();if(this.anchors.get(anchorId)===anchor)this.anchors.delete(anchorId);}if(r.status==='load_failed')this.toast('声音加载失败：'+r.reason);if(token!==this.generation)anchor.removeFromParent();return r;});return{anchor,promise};
  }
  sustain(e){
    this.airAudio.sustainShots++;const now=this.listener.context.currentTime,current=this.gunRuns.get(e.unit);
    if(current){current.lastShot=now;return;}
    for(const voice of this.runtime.voices)if(voice.group===e.unit+'|planeTail'&&!voice.cancelled)this.runtime.stopEntry(voice);
    const played=this.play('planeShot',e.position,'planeGun',e.unit);if(!played)return;
    const run={...played,lastShot:now,entry:null};this.gunRuns.set(e.unit,run);const token=this.generation;
    played.promise.then(result=>{if(token!==this.generation||this.gunRuns.get(e.unit)!==run)return;if(result.status!=='playing'){this.gunRuns.delete(e.unit);return;}
      const entry=[...this.runtime.voices].find(v=>v.group===e.unit+'|planeGun'&&v.asset.audio_id===this.derived.get('planeShot').audio_id&&!v.cancelled);if(!entry)return;
      run.entry=entry;entry.sound.setLoopStart(this.manifest.airAudioProfile.loopStart);entry.sound.setLoopEnd(this.manifest.airAudioProfile.loopEnd);entry.sound.setLoop(true);run.anchor.userData.expires=Infinity;this.airAudio.sustainStarts++;
    });
  }
  event(e){
    if(e.type==='shot'){if(e.weapon==='planeGun'&&this.manifest.airAudioProfile)this.sustain(e);else this.play(WEAPONS[e.weapon].audio+'Shot',e.position,e.weapon==='gun'&&this.heliShotAsset?'heliGun':'shot',e.unit);}
    if(e.type==='impact'){
      if(['gun','planeGun'].includes(e.weapon)){
        const group='impact-'+e.owner+'-'+e.weapon;
        const tails=[...this.runtime.voices].filter(v=>v.group===group+'|airImpact'&&!v.cancelled);
        if(tails.length>=2){this.runtime.stopEntry(tails[0]);this.airAudio.tailReplacements++;}
        this.play(WEAPONS[e.weapon].audio+'Impact',e.position,'airImpact',group);this.airAudio.impacts++;
      }else this.play(WEAPONS[e.weapon].audio+'Impact',e.position,'impact','impact-'+e.serial);
    }
  }
  step(sim,selected,now){
    this.listener.position.set(this.view.center.x,8,this.view.center.z);this.listener.quaternion.copy(this.view.camera.quaternion);this.listener.updateMatrixWorld();
    if(!this.active||sim.paused)return;
    for(const [id,run] of this.gunRuns){
      const unit=sim.units.find(u=>u.id===id);if(unit)run.anchor.position.set(unit.x,unit.y+2.8,unit.z);
      if(this.listener.context.currentTime-run.lastShot>.18||!unit){
        this.gunRuns.delete(id);if(run.entry)this.runtime.stopEntry(run.entry);run.anchor.userData.expires=this.listener.context.currentTime+.2;
        if(unit){this.play('planeRelease',{x:unit.x,y:unit.y+2.8,z:unit.z},'planeTail',id);this.airAudio.releases++;}
      }
    }
    this.updateEngines(sim,selected,now);
    if(this.heliShotAlignment){const groups={};for(const v of this.runtime.voices)if(v.group.endsWith('|heliGun'))groups[v.group]=(groups[v.group]??0)+1;this.heliShotAlignment.maxVoicesPerUnit=Math.max(this.heliShotAlignment.maxVoicesPerUnit,...Object.values(groups));}
    for(const [id,a] of this.anchors)if(this.listener.context.currentTime>a.userData.expires){a.removeFromParent();this.anchors.delete(id);}
    this.analyser.getFloatTimeDomainData(this.wave);let peak=0,sum=0;for(const value of this.wave){peak=Math.max(peak,Math.abs(value));sum+=value*value;}this.currentPeak=peak;this.peak=Math.max(this.peak,peak);this.rms=Math.sqrt(sum/this.wave.length);
  }
  stats(){const events=this.runtime.stats(),states={};for(const e of this.engines.values())if(e.sound?.isPlaying&&!e.retireAt)states[e.state]=(states[e.state]??0)+1;return{...events,airAudio:this.airAudio,sustainVoices:this.gunRuns.size,heliShotAlignment:this.heliShotAlignment??null,heliGunVoices:[...this.runtime.voices].filter(v=>v.group.endsWith('|heliGun')).length,voices:events.voices+this.engines.size,eventVoices:events.voices,active:this.active,volume:this.runtime.masterVolume,boostDb:this.boostDb,rms:this.rms??0,rmsDb:20*Math.log10(Math.max(this.rms??0,1e-6)),engines:this.engines.size,engineStates:states,dropped:this.dropped,peak:this.peak,currentPeak:this.currentPeak??0,compressionDb:this.compressor.reduction,listener:this.listener.position.toArray()};}
  dispose(){this.stop();this.runtime.dispose();this.listener.gain.disconnect(this.drive);this.analyser.disconnect();this.drive.disconnect();this.compressor.disconnect();this.makeup.disconnect();this.limiter.disconnect();this.listener.removeFromParent();}
}
