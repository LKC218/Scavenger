/**
 * Scavenger 9km 战场载具 3D 空间引擎混音控制器。
 * - 复用 VehicleAudioRuntime 进行按需惰性解码与缓存（仅懒加载当前出击编队 ≤6 辆，绝不预载全库 36 条）；
 * - 复用软限幅压缩母线 + 8 声部配额（地面/旋翼/固定翼三兵种各保底 1 个代表）；
 * - 严格在首次真实用户手势且 vehicleAudioEnabled=true 时才创建 AudioContext；
 * - 支持视距 span 平滑衰减（<=350m 全量 -> 1200m 0.30 -> >=2000m 彻底静音）、暂停自动静音与高空低通滤波；
 * - 严守载具点击/框选/下令零 UI 提示音边界。
 */

import { VehicleAudioRuntime } from '../音效索引/音频运行时.mjs';
import {
  DEFAULT_GAU8_ACOUSTIC_PRESET,
  buildAirspaceThunderIR,
  buildAsymmetricSaturatorCurve,
  buildShockPulseLoopBuffer,
  buildSupersonicCrackBuffer,
  createContinuousGunAcousticVoice,
  sliceRealGunTrack,
} from '../音效索引/机炮物理声学引擎.mjs';

export const DEFAULT_MAX_ENGINE_VOICES = 8;
export const ENGINE_AUDIBLE_RADIUS_M = 700;

/**
 * 战场静态声像路由槽位（预建于 Near/Mid/Far 各分桶上游，运行期零 StereoPannerNode 分配）。
 */
export const STATIC_PAN_LANES = Object.freeze([-0.68, -0.34, 0, 0.34, 0.68]);

/**
 * 战场通用声压等级表（Acoustic Power Tiers）：
 * - 按武器/事件真实能量区分参考全响距离、平滑归零极限距离与分桶等效距离系数；
 * - 彻底消除 2000m 硬切断崖，让热诱弹/小口径自然远距消隐、重磅航弹/坠机在 2km+ 仍具远雷压迫感。
 */
export const ACOUSTIC_POWER_TIERS = Object.freeze({
  small: Object.freeze({
    id: 'small',
    refDistM: 120,
    audibleMaxM: 780,
    bucketScale: 1.35,
  }),
  medium: Object.freeze({
    id: 'medium',
    refDistM: 220,
    audibleMaxM: 1550,
    bucketScale: 1.0,
  }),
  heavy: Object.freeze({
    id: 'heavy',
    refDistM: 320,
    audibleMaxM: 2400,
    bucketScale: 0.78,
  }),
  massive: Object.freeze({
    id: 'massive',
    refDistM: 420,
    audibleMaxM: 3200,
    bucketScale: 0.65,
  }),
});

function clamp01(v) {
  return Math.min(1, Math.max(0, Number(v) || 0));
}

function smoothstep01(t) {
  const x = clamp01(t);
  return x * x * (3 - 2 * x);
}

/**
 * 纯规则：计算 RTS 俯视虚拟监听点（在相机真实 3D 位置与地面注视中心之间按权重下沉，
 * 避免常规战术视距下屏幕正中爆炸被相机高度直接推入 Far 桶，同时保留拉高视角变沉的宏大感）。
 */
export function computeVirtualListenerPos(cameraPos, groundCenter = null, out = null, focusWeight = 0.35) {
  const cx = Number.isFinite(cameraPos?.x) ? cameraPos.x : 0;
  const cy = Number.isFinite(cameraPos?.y) ? cameraPos.y : 120;
  const cz = Number.isFinite(cameraPos?.z) ? cameraPos.z : 0;
  const target = out || { x: 0, y: 0, z: 0 };
  if (groundCenter && Number.isFinite(groundCenter.x) && Number.isFinite(groundCenter.z)) {
    const w = clamp01(focusWeight);
    const gx = groundCenter.x;
    const gy = Number.isFinite(groundCenter.y) ? groundCenter.y : 0;
    const gz = groundCenter.z;
    target.x = cx + (gx - cx) * w;
    target.y = Math.max(gy + 18, cy + (gy - cy) * w);
    target.z = cz + (gz - cz) * w;
  } else {
    target.x = cx;
    target.y = cy;
    target.z = cz;
  }
  return target;
}

/**
 * 纯规则：按相机世界右向量投影计算屏幕相对左右声像（[-maxPan, +maxPan]），
 * 旋转视角后方位始终严格匹配左右耳，且随距离拉远自动收拢声像宽度。
 */
export function computeCameraPan(worldPos, refPos, cameraRight, distMeters, maxPan = 0.72) {
  const dx = (Number(worldPos?.x) || 0) - (Number(refPos?.x) || 0);
  const dy = (Number(worldPos?.y) || 0) - (Number(refPos?.y) || 0);
  const dz = (Number(worldPos?.z) || 0) - (Number(refPos?.z) || 0);
  const rx = Number.isFinite(cameraRight?.x) ? cameraRight.x : 1;
  const ry = Number.isFinite(cameraRight?.y) ? cameraRight.y : 0;
  const rz = Number.isFinite(cameraRight?.z) ? cameraRight.z : 0;
  const rightDot = dx * rx + dy * ry + dz * rz;
  const d = Number.isFinite(distMeters) && distMeters > 0 ? distMeters : Math.hypot(dx, dy, dz);
  const farNarrow = 1.0 - 0.45 * smoothstep01((d - 260) / 940);
  const raw = (rightDot / Math.max(160, d)) * 0.80 * farNarrow;
  const lim = Math.max(0.1, Math.min(1.0, Number(maxPan) || 0.72));
  return Math.max(-lim, Math.min(lim, raw));
}

/**
 * 纯规则：将连续声像值映射至 5 档静态声像槽索引 [0..4]。
 */
export function selectStaticPanLane(panVal) {
  let bestIdx = 2;
  let bestErr = Infinity;
  const p = Number.isFinite(panVal) ? panVal : 0;
  for (let i = 0; i < STATIC_PAN_LANES.length; i += 1) {
    const err = Math.abs(p - STATIC_PAN_LANES[i]);
    if (err < bestErr) {
      bestErr = err;
      bestIdx = i;
    }
  }
  return bestIdx;
}

/**
 * 纯规则：按声源声压等级与 3D 距离计算平滑衰减增益（无硬切断崖）与等效分桶距离。
 */
export function computeAcousticAttenuation(distMeters, powerTier = 'medium') {
  const tier = ACOUSTIC_POWER_TIERS[powerTier] || ACOUSTIC_POWER_TIERS.medium;
  const d = Number.isFinite(distMeters) ? Math.max(0, distMeters) : tier.refDistM;
  const effectiveBucketDistM = d * tier.bucketScale;
  if (d <= tier.refDistM) {
    return { tierId: tier.id, distGain: 1.0, effectiveBucketDistM, audible: true };
  }
  if (d >= tier.audibleMaxM) {
    return { tierId: tier.id, distGain: 0.0, effectiveBucketDistM, audible: false };
  }
  const t = clamp01((d - tier.refDistM) / Math.max(1, tier.audibleMaxM - tier.refDistM));
  const inv = tier.refDistM / Math.max(tier.refDistM, d);
  const endFade = 1 - smoothstep01((t - 0.72) / 0.28);
  const distGain = clamp01(Math.pow(inv, 0.78) * endFade);
  return {
    tierId: tier.id,
    distGain,
    effectiveBucketDistM,
    audible: distGain > 0.002,
  };
}

/**
 * 纯规则：计算高速固定翼引擎的连续航空声学调制（多普勒频移 + 机头/尾喷前后半球指向性）。
 * - 迎头接近（cosAspect -> +1）：高亢多普勒 + 进气道高频涡扇哨音通透（LPF 上浮至 ~5800Hz）；
 * - 过顶转背飞（cosAspect -> -1）：音高滑落 + 尾喷宽带低频轰鸣增强（LPF 收沉至 ~2200–2700Hz，增益 +26%）。
 */
export function computeJetEngineAeroacoustics(pos, vel, listenerPos, baseMot) {
  const baseRate = Number(baseMot?.rate) || 1.0;
  const baseFilterHz = Number(baseMot?.filterHz) || 3400;
  const dx = (Number(pos?.x) || 0) - (Number(listenerPos?.x) || 0);
  const dy = (Number(pos?.y) || 0) - (Number(listenerPos?.y) || 0);
  const dz = (Number(pos?.z) || 0) - (Number(listenerPos?.z) || 0);
  const dist = Math.hypot(dx, dy, dz);

  const vx = Number(vel?.x) || 0;
  const vy = Number(vel?.y) || 0;
  const vz = Number(vel?.z) || 0;
  const speed = Math.hypot(vx, vy, vz);

  if (dist < 1e-3 || speed < 12) {
    return {
      rate: baseRate,
      filterHz: baseFilterHz,
      gainMul: 1.0,
      dopplerMul: 1.0,
      cosAspect: 0.0,
    };
  }

  // 径向速度：靠近监听点为负、远离为正
  const invDist = 1 / dist;
  const vRad = (dx * vx + dy * vy + dz * vz) * invDist;
  const clampedVRad = Math.max(-260, Math.min(260, vRad));
  const dopplerMul = Math.max(0.78, Math.min(1.24, 340 / (340 + clampedVRad * 0.50)));

  // cosAspect: +1 为迎头飞向监听点，-1 为机尾喷口正对监听点背飞
  const cosAspect = Math.max(-1, Math.min(1, (-vRad) / speed));
  const speedBlend = clamp01((speed - 12) / 48);

  // 迎头高亮涡扇哨音 vs 尾喷低沉轰鸣
  const frontFactor = Math.max(0, cosAspect) * speedBlend;
  const rearFactor = Math.max(0, -cosAspect) * speedBlend;
  const filterHz = Math.round(
    baseFilterHz
    + frontFactor * 2400
    - rearFactor * 1050,
  );
  const gainMul = 1.0 - frontFactor * 0.12 + rearFactor * 0.26;
  const rate = Math.max(0.74, Math.min(1.32, baseRate * dopplerMul));

  return {
    rate: Number(rate.toFixed(4)),
    filterHz: Math.max(1600, Math.min(7200, filterHz)),
    gainMul: Number(gainMul.toFixed(4)),
    dopplerMul: Number(dopplerMul.toFixed(4)),
    cosAspect: Number(cosAspect.toFixed(4)),
  };
}

/**
 * 纯规则：计算视距 span 对空间引擎音总增益的平滑衰减系数。
 * - span <= 350m: 1.0
 * - 350m ~ 1200m: 1.0 -> 0.30
 * - 1200m ~ 2000m: 0.30 -> 0.0
 * - span >= 2000m: 0.0
 */
export function computeSpanAudioGain(span) {
  const s = Number(span);
  if (!Number.isFinite(s) || s <= 350) return 1.0;
  if (s >= 2000) return 0.0;
  if (s <= 1200) {
    return 1.0 - 0.70 * smoothstep01((s - 350) / 850);
  }
  return 0.30 * (1.0 - smoothstep01((s - 1200) / 800));
}

/**
 * 归一化主角单位或混编单位为统一音频兵种通道：'ground' | 'heli' | 'plane'。
 */
export function normalizeAudioKind(unit) {
  const r = unit?.role || unit?.kind || unit?.channel || 'ground';
  if (r === 'helicopter' || r === 'heli') return 'heli';
  if (r === 'fixed_wing' || r === 'uav' || r === 'plane' || r === 'jet') return 'plane';
  return 'ground';
}

/**
 * 纯规则：依据载具当前机动状态返回目标增益、播放倍速与大气低通滤波截止频率。
 */
export function computeEngineMotionState(unit) {
  const kind = normalizeAudioKind(unit);
  const moving = Array.isArray(unit?.waypoints)
    ? (unit.wpIndex ?? 0) < unit.waypoints.length
    : Boolean(unit?.moving);
  const activeJob = Boolean(unit?.job && unit.job.type && unit.job.type !== 'idle');

  if (kind === 'ground') {
    return moving
      ? { name: '行驶', gain: 0.52, rate: 1.08, filterHz: 14000, throttleLevel: 0.8, afterburnerActive: false }
      : { name: '待机', gain: 0.24, rate: 0.88, filterHz: 14000, throttleLevel: 0.25, afterburnerActive: false };
  }
  if (kind === 'heli') {
    return moving
      ? { name: '旋翼飞行', gain: 0.42, rate: 1.04, filterHz: 11000, throttleLevel: 0.82, afterburnerActive: false }
      : { name: '悬停', gain: 0.28, rate: 1.00, filterHz: 11000, throttleLevel: 0.52, afterburnerActive: false };
  }
  const att = unit?.flightAttitude;
  if (att?.customControlled) {
    const fs = att.flightState || 'orbit';
    const throttle = Number.isFinite(att.throttleLevel) ? clamp01(att.throttleLevel) : 0.55;
    const pitchDeg = Number(att.pitchDeg) || 0;
    const ab = Boolean(att.afterburnerActive);

    if (fs === 'evacuated' || fs === 'crashed' || fs === 'crashing') {
      return { name: '关车', gain: 0.0, rate: 0.85, filterHz: 1800, throttleLevel: 0, afterburnerActive: false };
    }
    if (fs === 'egress' || fs === 'sprint' || fs === 'rtb' || throttle >= 0.95) {
      if (ab) {
        return { name: '加力全推', gain: 0.64, rate: 1.18, filterHz: 5800, throttleLevel: 1.0, afterburnerActive: true };
      }
      return { name: '满推拉升', gain: 0.56, rate: 1.14, filterHz: 5000, throttleLevel: 1.0, afterburnerActive: false };
    }
    if (fs === 'strafe' || fs === 'rocket' || fs === 'bomb' || fs === 'missile') {
      if (throttle < 0.78) {
        return { name: '入航切入', gain: 0.44, rate: 1.05, filterHz: 3800, throttleLevel: 0.70, afterburnerActive: false };
      }
      if (ab) {
        return { name: '加力突击', gain: 0.62, rate: 1.17, filterHz: 5600, throttleLevel: 1.0, afterburnerActive: true };
      }
      const diveBoost = Math.max(0, Math.min(1, -pitchDeg / 30));
      return {
        name: '俯冲突击',
        gain: Number((0.52 + diveBoost * 0.03).toFixed(3)),
        rate: Number((1.09 + diveBoost * 0.04).toFixed(3)),
        filterHz: Math.round(4500 + diveBoost * 350),
        throttleLevel: Number(throttle.toFixed(2)),
        afterburnerActive: false,
      };
    }
    if (fs === 'approach' || throttle >= 0.65) {
      return { name: '入航切入', gain: 0.44, rate: 1.05, filterHz: 3800, throttleLevel: 0.70, afterburnerActive: false };
    }
    return { name: '巡航盘旋', gain: 0.36, rate: 1.00, filterHz: 3200, throttleLevel: 0.55, afterburnerActive: false };
  }
  return (moving || activeJob)
    ? { name: '机动飞行', gain: 0.48, rate: 1.07, filterHz: 3600, throttleLevel: 0.85, afterburnerActive: false }
    : { name: '巡航盘旋', gain: 0.36, rate: 1.00, filterHz: 3200, throttleLevel: 0.55, afterburnerActive: false };
}

/**
 * 纯规则：8 声部空间配额调度（地面/旋翼/固定翼三兵种各保底 1 个最近代表）。
 */
export function chooseSpatialEngines(units, selectedIds, center, existingIds, maxVoices = DEFAULT_MAX_ENGINE_VOICES) {
  if (!Array.isArray(units) || !units.length || maxVoices <= 0) return [];
  const cx = Number(center?.x) || 0;
  const cz = Number(center?.z) || 0;
  const sel = selectedIds instanceof Set ? selectedIds : new Set(selectedIds || []);
  const ex = existingIds instanceof Set ? existingIds : new Set(existingIds || []);

  const candidates = [];
  for (const u of units) {
    if (!u || u.alive === false) continue;
    const ux = Number.isFinite(u.x) ? u.x : (u.node?.position?.x ?? 0);
    const uz = Number.isFinite(u.z) ? u.z : (u.node?.position?.z ?? 0);
    const dist = Math.hypot(ux - cx, uz - cz);
    if (dist >= ENGINE_AUDIBLE_RADIUS_M) continue;
    const score = dist - (sel.has(u.id) ? 120 : 0) - (ex.has(u.id) ? 25 : 0);
    candidates.push({ unit: u, kind: normalizeAudioKind(u), score });
  }
  candidates.sort((a, b) => a.score - b.score);

  const picked = [];
  const pickedIds = new Set();
  for (const kind of ['ground', 'heli', 'plane']) {
    const hit = candidates.find(c => c.kind === kind);
    if (hit && picked.length < maxVoices) {
      picked.push(hit.unit);
      pickedIds.add(hit.unit.id);
    }
  }
  for (const c of candidates) {
    if (picked.length >= maxVoices) break;
    if (!pickedIds.has(c.unit.id)) {
      picked.push(c.unit);
      pickedIds.add(c.unit.id);
    }
  }
  return picked;
}

/**
 * 复用软限幅压缩混音母线：mixInput -> drive -> compressor -> makeup -> softLimiter -> masterGain -> listener.gain
 */
function createVehicleMixBus(context, destinationGainNode, boostDb = 6) {
  const compressor = context.createDynamicsCompressor();
  compressor.threshold.value = -8;
  compressor.knee.value = 3;
  compressor.ratio.value = 20;
  compressor.attack.value = 0.003;
  compressor.release.value = 0.2;

  const drive = context.createGain();
  const makeup = context.createGain();
  const db = [0, 6, 12].includes(Number(boostDb)) ? Number(boostDb) : 6;
  drive.gain.value = 10 ** (Math.max(0, db - 3) / 20);
  makeup.gain.value = 10 ** (Math.min(3, db) / 20);

  const limiter = context.createWaveShaper();
  const curve = new Float32Array(65537);
  for (let i = 0; i < curve.length; i += 1) {
    const x = (i / (curve.length - 1)) * 2 - 1;
    const a = Math.abs(x);
    curve[i] = Math.sign(x) * (a <= 0.8 ? a : 0.8 + 0.15 * (1 - Math.exp(-(a - 0.8) / 0.03)));
  }
  limiter.curve = curve;
  limiter.oversample = 'none';

  const analyser = context.createAnalyser();
  analyser.fftSize = 1024;

  const mixInput = context.createGain();
  const engineDuckBus = context.createGain();
  engineDuckBus.gain.value = 1.0;
  engineDuckBus.connect(mixInput);

  destinationGainNode.disconnect();
  mixInput.connect(drive);
  drive.connect(compressor);
  compressor.connect(makeup);
  makeup.connect(limiter);
  limiter.connect(destinationGainNode);
  destinationGainNode.connect(context.destination);
  destinationGainNode.connect(analyser);

  return { mixInput, engineDuckBus, compressor, drive, makeup, limiter, analyser };
}

/**
 * 构建全局唯一战场物理声学传播总线（位于 mixInput 上游）：
 * - 唯一共享 3.6s 空域滚雷 ConvolverNode（运行期卷积开销恒定为 1 份）；
 * - Near (<260m) / Mid (260–680m) / Far (>680m) 三档固定静态分桶母线，每桶预建 5 档静态 StereoPanner 路由（运行期零新建 Filter/Panner）；
 * - subPunchInput 直入干声 mixInput（不经混响与低通，确保重爆次声冲击紧实不糊）；
 * - gunDryInput 供持续机炮动态声源干声直入 mixInput，湿声接 thunderSend。
 */
function createBattlefieldPropagationBus(context, mixInput) {
  const reverbIrBuf = buildAirspaceThunderIR(context);
  const convolver = context.createConvolver();
  convolver.buffer = reverbIrBuf;

  const thunderSend = context.createGain();
  thunderSend.gain.value = 1.0;

  const thunderOut = context.createGain();
  thunderOut.gain.value = 0.90;

  thunderSend.connect(convolver);
  convolver.connect(thunderOut);
  thunderOut.connect(mixInput);

  const buildPanLanes = (targetInput, widthScale = 1.0) => {
    const canPan = typeof context.createStereoPanner === 'function';
    const panNodes = [];
    const panInputs = [];
    for (let i = 0; i < STATIC_PAN_LANES.length; i += 1) {
      const panValue = STATIC_PAN_LANES[i] * widthScale;
      if (canPan && Math.abs(panValue) > 0.001) {
        const pNode = context.createStereoPanner();
        pNode.pan.value = Math.max(-1, Math.min(1, panValue));
        pNode.connect(targetInput);
        panNodes.push(pNode);
        panInputs.push(pNode);
      } else {
        panInputs.push(targetInput);
      }
    }
    return { panNodes, panInputs };
  };

  // Near Bus (<260m): LPF 8000Hz, Wet Send 14%, 全幅立体声像
  const nearInput = context.createGain();
  nearInput.gain.value = 1.0;
  const nearLpf = context.createBiquadFilter();
  nearLpf.type = 'lowpass';
  nearLpf.frequency.value = 8000;
  nearLpf.Q.value = 0.707;
  const nearWetSend = context.createGain();
  nearWetSend.gain.value = 0.14;
  nearInput.connect(nearLpf);
  nearLpf.connect(mixInput);
  nearInput.connect(nearWetSend);
  nearWetSend.connect(thunderSend);
  const nearPans = buildPanLanes(nearInput, 1.0);

  // Mid Bus (260–680m): LPF 2400Hz, Wet Send 36%, 适度收拢声像
  const midInput = context.createGain();
  midInput.gain.value = 1.0;
  const midLpf = context.createBiquadFilter();
  midLpf.type = 'lowpass';
  midLpf.frequency.value = 2400;
  midLpf.Q.value = 0.707;
  const midWetSend = context.createGain();
  midWetSend.gain.value = 0.36;
  midInput.connect(midLpf);
  midLpf.connect(mixInput);
  midInput.connect(midWetSend);
  midWetSend.connect(thunderSend);
  const midPans = buildPanLanes(midInput, 0.80);

  // Far Bus (>680m): LPF 520Hz + LowShelf +3.5dB, Wet Send 58%, 远距收窄声像
  const farInput = context.createGain();
  farInput.gain.value = 1.0;
  const farLpf = context.createBiquadFilter();
  farLpf.type = 'lowpass';
  farLpf.frequency.value = 520;
  farLpf.Q.value = 0.707;
  const farLowShelf = context.createBiquadFilter();
  farLowShelf.type = 'lowshelf';
  farLowShelf.frequency.value = 130;
  farLowShelf.gain.value = 3.5;
  const farWetSend = context.createGain();
  farWetSend.gain.value = 0.58;
  farInput.connect(farLpf);
  farLpf.connect(farLowShelf);
  farLowShelf.connect(mixInput);
  farInput.connect(farWetSend);
  farWetSend.connect(thunderSend);
  const farPans = buildPanLanes(farInput, 0.55);

  // 持续机炮干声通道
  const gunDryInput = context.createGain();
  gunDryInput.gain.value = 1.0;
  gunDryInput.connect(mixInput);

  // 重爆次声冲击锤独立干声直入通道（零混响、紧实胸腔重击）
  const subPunchInput = context.createGain();
  subPunchInput.gain.value = 1.0;
  subPunchInput.connect(mixInput);

  return {
    convolver,
    reverbIrBuf,
    thunderSend,
    thunderOut,
    gunDryInput,
    subPunchInput,
    subPunchRoute: 'dry_direct',
    panLanesPerBucket: STATIC_PAN_LANES.length,
    convolverCount: 1,
    near: { input: nearInput, panInputs: nearPans.panInputs, panNodes: nearPans.panNodes, lpf: nearLpf, wetSend: nearWetSend, lpfHz: 8000, wetSendGain: 0.14 },
    mid: { input: midInput, panInputs: midPans.panInputs, panNodes: midPans.panNodes, lpf: midLpf, wetSend: midWetSend, lpfHz: 2400, wetSendGain: 0.36 },
    far: { input: farInput, panInputs: farPans.panInputs, panNodes: farPans.panNodes, lpf: farLpf, lowShelf: farLowShelf, wetSend: farWetSend, lpfHz: 520, lowShelfDb: 3.5, wetSendGain: 0.58 },
  };
}

function getUnitWorldPos(u) {
  if (u?.node?.position) return u.node.position;
  return { x: Number(u?.x) || 0, y: Number(u?.y) || 0, z: Number(u?.z) || 0 };
}

/**
 * 9km 实战载具 3D 空间引擎音效控制器。
 */
export class VehicleSpatialAudioController {
  constructor({ THREE, scene, camera, enabled = true, volume = 0.45, maxVoices = DEFAULT_MAX_ENGINE_VOICES } = {}) {
    this.THREE = THREE;
    this.scene = scene;
    this.camera = camera;
    this.enabled = Boolean(enabled);
    this.volume = clamp01(volume);
    this.maxVoices = Math.max(1, Math.min(8, Number(maxVoices) || DEFAULT_MAX_ENGINE_VOICES));
    this.paused = false;
    this.disposed = false;

    this.listener = null;
    this.mix = null;
    this.battlefieldBus = null;
    this.runtime = null;
    this.waveBuf = null;
    this.audioMap = null;
    this.mapPromise = null;
    this.unlockPromise = null;

    this.engines = new Map();
    this.generation = 0;
    this.nextScheduleMs = 0;
    this.currentSpan = 135;
    this.spanGain = computeSpanAudioGain(135);
    this.effectiveMasterGain = this.enabled ? this.volume * this.spanGain : 0;
    this.rms = 0;
    this.peak = 0;
    this.maxObservedPeak = 0;
    this.dropped = 0;
    this.lastError = null;
    this.duckTriggerCount = 0;
    this.lastDuckDepth = 1.0;
    this.afterburnerAudioActive = false;
    this._engineVirtualListenerPos = { x: 0, y: 120, z: 0 };
  }

  async _ensureAudioMap() {
    if (this.audioMap) return this.audioMap;
    if (!this.mapPromise) {
      this.mapPromise = fetch('/api/vehicle-audio-map', { cache: 'no-store' })
        .then(r => {
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          return r.json();
        })
        .then(data => {
          this.audioMap = data?.vehicles || {};
          return this.audioMap;
        })
        .catch(err => {
          this.lastError = err?.message || String(err);
          this.mapPromise = null;
          return {};
        });
    }
    return this.mapPromise;
  }

  _ensureContextAndRuntime() {
    if (this.listener || this.disposed) return;
    const { THREE, scene } = this;
    this.listener = new THREE.AudioListener();
    scene.add(this.listener);
    this.mix = createVehicleMixBus(this.listener.context, this.listener.getInput(), 6);
    this.battlefieldBus = createBattlefieldPropagationBus(this.listener.context, this.mix.mixInput);
    // 所有引擎 PositionalAudio 经由 engineDuckBus 汇入 mixInput，便于重爆超压瞬时闪避而不压制武器声
    this.listener.getInput = () => this.mix.engineDuckBus;
    this.waveBuf = new Float32Array(1024);

    this.runtime = new VehicleAudioRuntime({
      THREE,
      listener: this.listener,
      baseUrl: location.href,
      catalog: {
        rules: {
          cache_max_bytes: 64 * 1024 * 1024,
          global_max_voices: 32,
          actions: {},
        },
      },
    });
    this.runtime.setMuted(!this.enabled || this.paused);
    this.runtime.setVolume(this.effectiveMasterGain);
  }

  /**
   * 仅在用户真实手势且开启引擎音时解锁 AudioContext，绝不提前触发。
   */
  async unlockByGesture() {
    if (this.disposed || !this.enabled) return false;
    if (!this.unlockPromise) {
      this.unlockPromise = (async () => {
        try {
          this._ensureContextAndRuntime();
          const [,] = await Promise.all([
            this.runtime.unlock(),
            this._ensureAudioMap(),
          ]);
          return Boolean(this.runtime?.unlocked);
        } catch (err) {
          this.lastError = err?.message || String(err);
          this.unlockPromise = null;
          return false;
        }
      })();
    }
    return this.unlockPromise;
  }

  setEnabled(flag, { fromGesture = false } = {}) {
    const next = Boolean(flag);
    if (this.enabled === next) return;
    this.enabled = next;
    if (!next) {
      this._stopAllEngines();
      if (this.runtime) this.runtime.setMuted(true);
    } else {
      if (this.runtime) this.runtime.setMuted(this.paused);
      if (fromGesture) {
        this.unlockByGesture();
      }
    }
    this._syncMasterGainImmediate(this.currentSpan);
  }

  setVolume(val) {
    this.volume = clamp01(val);
    this._syncMasterGainImmediate(this.currentSpan);
  }

  setPaused(flag) {
    this.paused = Boolean(flag);
    if (this.runtime) {
      this.runtime.setMuted(!this.enabled || this.paused);
    }
    this._syncMasterGainImmediate(this.currentSpan);
  }

  _syncMasterGainImmediate(span) {
    this.currentSpan = Number.isFinite(span) ? span : this.currentSpan;
    this.spanGain = computeSpanAudioGain(this.currentSpan);
    this.effectiveMasterGain = (this.enabled && !this.paused) ? (this.volume * this.spanGain) : 0;
    if (this.listener) {
      const clock = this.listener.context.currentTime;
      const g = this.listener.gain.gain;
      if (g.cancelAndHoldAtTime) g.cancelAndHoldAtTime(clock);
      else g.cancelScheduledValues(clock);
      g.setTargetAtTime(this.effectiveMasterGain, clock, 0.05);
      if (this.runtime) {
        this.runtime.masterVolume = this.effectiveMasterGain;
        this.runtime.muted = !this.enabled || this.paused;
      }
    }
  }

  _releaseEngine(id) {
    const entry = this.engines.get(id);
    if (!entry) return;
    entry.cancelled = true;
    if (entry.sound) {
      try {
        if (entry.sound.isPlaying) entry.sound.stop();
        entry.sound.setFilter(null);
        entry.sound.removeFromParent();
        entry.sound.disconnect();
        entry.sound.gain.disconnect();
      } catch {
        // 节点已断开时安全忽略
      }
    }
    entry.filter?.disconnect?.();
    entry.anchor?.removeFromParent?.();
    this.engines.delete(id);
  }

  _stopAllEngines() {
    this.generation += 1;
    for (const id of [...this.engines.keys()]) {
      this._releaseEngine(id);
    }
    this.nextScheduleMs = 0;
  }

  _resolveAssetForUnit(u) {
    if (!this.audioMap) return null;
    const slug = u.slug || (u.kind === 'heli' ? 'US_AH64D' : (u.kind === 'plane' || u.kind === 'jet') ? 'US_A10' : 'US_M1A2');
    const row = this.audioMap[slug];
    if (!row) return null;
    return {
      audio_id: row.audio_id,
      url: row.url,
      sha256: row.sha256,
      bytes: row.bytes,
      duration_seconds: row.duration_seconds || 6.38,
      sample_rate: row.sample_rate || 48000,
      channels: row.channels || 1,
      source_name: row.source_name || slug,
    };
  }

  async _startEngine(u) {
    if (!this.runtime?.unlocked || !this.enabled || this.paused || this.disposed) return;
    if (this.engines.has(u.id) || this.engines.size >= this.maxVoices) return;
    const asset = this._resolveAssetForUnit(u);
    if (!asset) return;

    const kind = normalizeAudioKind(u);
    const pos = getUnitWorldPos(u);
    const anchor = new this.THREE.Object3D();
    anchor.position.set(pos.x, pos.y + 1.0, pos.z);
    this.scene.add(anchor);

    const entry = {
      id: u.id,
      slug: u.slug || asset.source_name,
      kind,
      anchor,
      sound: null,
      filter: null,
      cancelled: false,
      state: '加载',
      targetGain: 0,
      targetRate: 1,
      filterHz: 12000,
      retireAt: 0,
      prevX: pos.x,
      prevY: pos.y,
      prevZ: pos.z,
      prevMs: 0,
      vx: 0,
      vy: 0,
      vz: 0,
      dopplerMul: 1.0,
      cosAspect: 0.0,
      throttleLevel: kind === 'plane' ? 0.55 : 0,
      afterburnerActive: false,
    };
    const token = this.generation;
    this.engines.set(u.id, entry);

    try {
      const buffer = await this.runtime.buffer(asset);
      if (token !== this.generation || entry.cancelled || this.engines.get(u.id) !== entry || !this.enabled || this.disposed) {
        this._releaseEngine(u.id);
        return;
      }
      const ctx = this.listener.context;
      const sound = new this.THREE.PositionalAudio(this.listener);
      const filter = ctx.createBiquadFilter();
      filter.type = 'lowpass';
      filter.Q.value = 0.707;

      const mot = computeEngineMotionState(u);
      filter.frequency.value = mot.filterHz;
      sound.setFilter(filter);
      sound.setBuffer(buffer);
      sound.setLoop(true);
      sound.setVolume(0);
      sound.setDistanceModel('inverse');
      sound.setRefDistance({ ground: 55, heli: 95, plane: 180 }[kind] || 65);
      sound.setRolloffFactor(1.0);

      // 按实例 ID 散列随机循环偏移，防止同型车辆相位相干叠加
      let hash = 0;
      for (const ch of String(u.id)) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
      sound.offset = ((hash % 1000) / 1000) * Math.max(0.1, buffer.duration - 0.1);
      sound.setPlaybackRate(mot.rate);

      entry.sound = sound;
      entry.filter = filter;
      entry.filterHz = mot.filterHz;
      anchor.add(sound);
      sound.play();
    } catch (err) {
      this.dropped += 1;
      this.lastError = err?.message || String(err);
      if (this.engines.get(u.id) === entry) this._releaseEngine(u.id);
    }
  }

  _collectActiveUnits(combatSystem) {
    if (!combatSystem) return [];
    if (combatSystem.combatMode === 'g01_vehicles') {
      return (combatSystem.heroUnits || []).filter(u => !u?.evacuated);
    }
    return (combatSystem.squadUnits || []).map(s => ({
      id: `squad-${s.index}`,
      slug: s.kind === 'heli' ? 'US_AH64D' : s.kind === 'jet' ? 'US_A10' : 'US_M1A2',
      kind: s.kind === 'jet' ? 'plane' : s.kind,
      x: s.x,
      y: s.y,
      z: s.z,
      moving: true,
      job: { type: 'move' },
    }));
  }

  /**
   * 重爆超压瞬时背景音闪避（Sidechain Ducking）：
   * 仅作用于 engineDuckBus，在爆炸声波真正抵达监听点的 startAtSec 时刻柔和压低引擎循环音再回弹，
   * 增强重磅航弹与坠机爆燃的空气超压重量感。
   */
  triggerEngineDuck(startAtSec, { depth = 0.56, holdSec = 0.18, releaseTc = 0.45 } = {}) {
    if (!this.mix?.engineDuckBus || !this.listener || !this.enabled || this.paused || this.disposed) return false;
    const ctx = this.listener.context;
    const t0 = Math.max(ctx.currentTime, Number.isFinite(startAtSec) ? startAtSec : ctx.currentTime);
    const clampedDepth = Math.max(0.35, Math.min(0.95, Number(depth) || 0.56));
    const g = this.mix.engineDuckBus.gain;
    try {
      g.cancelScheduledValues(t0);
      g.setValueAtTime(1.0, t0);
      g.linearRampToValueAtTime(clampedDepth, t0 + 0.025);
      g.setValueAtTime(clampedDepth, t0 + 0.025 + Math.max(0.05, holdSec));
      g.setTargetAtTime(1.0, t0 + 0.025 + Math.max(0.05, holdSec), Math.max(0.15, releaseTc));
      this.duckTriggerCount += 1;
      this.lastDuckDepth = Number(clampedDepth.toFixed(3));
      return true;
    } catch {
      return false;
    }
  }

  /**
   * 每帧更新监听点、视距衰减、8 声部增删及机械音调制（含固定翼连续多普勒与前后半球喷口指向性）。
   */
  step({ combatSystem, center, span, nowMs = performance.now() }) {
    this._syncMasterGainImmediate(span);
    if (!this.listener || this.disposed) return;

    const cx = Number(center?.x) || 0;
    const cz = Number(center?.z) || 0;
    this.listener.position.set(cx, 12, cz);
    if (this.camera?.quaternion) {
      this.listener.quaternion.copy(this.camera.quaternion);
    }
    this.listener.updateMatrixWorld();

    computeVirtualListenerPos(
      this.camera?.position || { x: cx, y: 140, z: cz },
      center,
      this._engineVirtualListenerPos,
      0.35,
    );

    if (!this.enabled || this.paused || !this.runtime?.unlocked || !this.audioMap) {
      this.rms = 0;
      return;
    }

    // 处于远景静默区 (>2000m) 时跳过新增引擎启动并平滑静音既有声部
    const clock = this.listener.context.currentTime;
    for (const [id, entry] of this.engines) {
      if (entry.retireAt && clock >= entry.retireAt) {
        this._releaseEngine(id);
      }
    }

    const activeUnits = this._collectActiveUnits(combatSystem);
    const unitById = new Map(activeUnits.map(u => [u.id, u]));

    if (nowMs >= this.nextScheduleMs) {
      this.nextScheduleMs = nowMs + 200;
      const keepList = this.spanGain > 0.001
        ? chooseSpatialEngines(activeUnits, combatSystem?.selectedIds, center, new Set(this.engines.keys()), this.maxVoices)
        : [];
      const keepIds = new Set(keepList.map(u => u.id));

      for (const [id, entry] of this.engines) {
        if (!keepIds.has(id) && !entry.retireAt) {
          entry.retireAt = clock + 0.2;
          if (entry.sound) {
            const g = entry.sound.gain.gain;
            if (g.cancelAndHoldAtTime) g.cancelAndHoldAtTime(clock);
            else g.cancelScheduledValues(clock);
            g.linearRampToValueAtTime(0, clock + 0.18);
          }
        }
      }
      for (const u of keepList) {
        if (!this.engines.has(u.id) && this.engines.size < this.maxVoices) {
          this._startEngine(u);
        }
      }
    }

    const kindCounts = {};
    for (const entry of this.engines.values()) {
      if (!entry.retireAt) kindCounts[entry.kind] = (kindCounts[entry.kind] ?? 0) + 1;
    }

    for (const [id, entry] of this.engines) {
      const u = unitById.get(id);
      if (!u || entry.retireAt) continue;
      const pos = getUnitWorldPos(u);
      entry.anchor.position.set(pos.x, pos.y + 1.0, pos.z);

      // 估算瞬时 3D 速度矢量（零分配平滑滤波，供固定翼多普勒与喷口指向性使用）
      const dtMs = entry.prevMs > 0 ? (nowMs - entry.prevMs) : 0;
      if (dtMs >= 8 && dtMs <= 300) {
        const invDt = 1000 / dtMs;
        const rawVx = (pos.x - entry.prevX) * invDt;
        const rawVy = (pos.y - entry.prevY) * invDt;
        const rawVz = (pos.z - entry.prevZ) * invDt;
        entry.vx = entry.vx * 0.65 + rawVx * 0.35;
        entry.vy = entry.vy * 0.65 + rawVy * 0.35;
        entry.vz = entry.vz * 0.65 + rawVz * 0.35;
      }
      entry.prevX = pos.x;
      entry.prevY = pos.y;
      entry.prevZ = pos.z;
      entry.prevMs = nowMs;

      if (!entry.sound) continue;

      const dist = Math.hypot(pos.x - cx, pos.z - cz);
      const edge = Math.max(0, Math.min(1, (ENGINE_AUDIBLE_RADIUS_M - dist) / 140));
      const mot = computeEngineMotionState(u);
      entry.throttleLevel = Number.isFinite(mot.throttleLevel) ? mot.throttleLevel : (entry.kind === 'plane' ? 0.55 : 0);
      entry.afterburnerActive = Boolean(mot.afterburnerActive);

      let effRate = mot.rate;
      let effFilterHz = mot.filterHz;
      let aeroGainMul = 1.0;
      if (entry.kind === 'plane') {
        const aero = computeJetEngineAeroacoustics(
          pos,
          { x: entry.vx, y: entry.vy, z: entry.vz },
          this._engineVirtualListenerPos,
          mot,
        );
        effRate = aero.rate;
        effFilterHz = aero.filterHz;
        aeroGainMul = aero.gainMul;
        entry.dopplerMul = aero.dopplerMul;
        entry.cosAspect = aero.cosAspect;
      }

      const targetGain = (mot.gain * aeroGainMul * edge) / Math.sqrt(Math.max(1, (kindCounts[entry.kind] ?? 1) * 0.6));
      const needUpdate = (
        entry.state !== mot.name
        || Math.abs((entry.targetGain ?? -1) - targetGain) > 0.005
        || Math.abs((entry.targetRate ?? 1) - effRate) > 0.008
        || Math.abs((entry.filterHz ?? 0) - effFilterHz) > 45
      );

      if (needUpdate) {
        const gParam = entry.sound.gain.gain;
        if (gParam.cancelAndHoldAtTime) gParam.cancelAndHoldAtTime(clock);
        else gParam.cancelScheduledValues(clock);
        gParam.setTargetAtTime(targetGain, clock, 0.09);

        if (entry.sound.source?.playbackRate) {
          entry.sound.source.playbackRate.setTargetAtTime(effRate, clock, 0.08);
        }
        if (entry.filter && Math.abs(entry.filterHz - effFilterHz) > 40) {
          entry.filter.frequency.setTargetAtTime(effFilterHz, clock, 0.12);
          entry.filterHz = effFilterHz;
        }
        entry.state = mot.name;
        entry.targetGain = targetGain;
        entry.targetRate = effRate;
      }
    }

    this.sampleAnalyserPeak();
  }

  sampleAnalyserPeak() {
    if (this.mix?.analyser && this.waveBuf && !this.disposed) {
      this.mix.analyser.getFloatTimeDomainData(this.waveBuf);
      let pk = 0;
      let sum = 0;
      for (let i = 0; i < this.waveBuf.length; i += 1) {
        const v = this.waveBuf[i];
        pk = Math.max(pk, Math.abs(v));
        sum += v * v;
      }
      this.rms = Math.sqrt(sum / this.waveBuf.length);
      this.peak = Math.max(this.peak, pk);
      this.maxObservedPeak = Math.max(this.maxObservedPeak, pk);
      return pk;
    }
    return 0;
  }

  getDebugState() {
    this.sampleAnalyserPeak();
    const active = [];
    let engineThrottleLevel = 0;
    for (const e of this.engines.values()) {
      if (e.retireAt) continue;
      const thr = Number((e.throttleLevel ?? 0).toFixed(2));
      if (e.kind === 'plane' && thr > engineThrottleLevel) {
        engineThrottleLevel = thr;
      }
      active.push({
        id: e.id,
        slug: e.slug,
        kind: e.kind,
        state: e.state,
        playing: Boolean(e.sound?.isPlaying),
        targetGain: Number((e.targetGain ?? 0).toFixed(4)),
        rate: Number((e.targetRate ?? 1).toFixed(3)),
        filterHz: e.filterHz,
        dopplerMul: Number((e.dopplerMul ?? 1).toFixed(4)),
        cosAspect: Number((e.cosAspect ?? 0).toFixed(4)),
        throttleLevel: thr,
        afterburnerActive: Boolean(e.afterburnerActive),
      });
    }
    const bus = this.battlefieldBus;
    return {
      enabled: this.enabled,
      volume: Number(this.volume.toFixed(3)),
      paused: this.paused,
      disposed: this.disposed,
      contextCreated: Boolean(this.listener),
      contextState: this.listener?.context?.state || 'uninitialized',
      unlocked: Boolean(this.runtime?.unlocked),
      mapLoaded: Boolean(this.audioMap),
      cachedLoopsCount: this.runtime?.cache?.size ?? 0,
      activeEngineCount: active.length,
      totalEngineNodes: this.engines.size,
      maxVoices: this.maxVoices,
      span: Number(this.currentSpan.toFixed(1)),
      spanGain: Number(this.spanGain.toFixed(4)),
      effectiveMasterGain: Number(this.effectiveMasterGain.toFixed(4)),
      rms: Number(this.rms.toFixed(6)),
      peak: Number(this.peak.toFixed(6)),
      maxObservedPeak: Number(this.maxObservedPeak.toFixed(6)),
      dropped: this.dropped,
      lastError: this.lastError,
      activeEngines: active,
      engineThrottleLevel,
      afterburnerAudioActive: Boolean(this.afterburnerAudioActive),
      engineDuckReady: Boolean(this.mix?.engineDuckBus),
      duckTriggerCount: this.duckTriggerCount,
      lastDuckDepth: this.lastDuckDepth,
      busInitialized: Boolean(bus),
      convolverCount: bus ? bus.convolverCount : 0,
      panLanesPerBucket: bus ? bus.panLanesPerBucket : 0,
      subPunchRoute: bus ? bus.subPunchRoute : null,
      bucketConfigs: bus ? {
        near: { maxDistM: 260, lpfHz: bus.near.lpfHz, wetSend: bus.near.wetSendGain },
        mid: { minDistM: 260, maxDistM: 680, lpfHz: bus.mid.lpfHz, wetSend: bus.mid.wetSendGain },
        far: { minDistM: 680, lpfHz: bus.far.lpfHz, lowShelfDb: bus.far.lowShelfDb, wetSend: bus.far.wetSendGain },
      } : null,
    };
  }

  dispose() {
    this.disposed = true;
    this._stopAllEngines();
    this.runtime?.dispose?.();
    if (this.battlefieldBus) {
      const b = this.battlefieldBus;
      for (const p of (b.near.panNodes || [])) { try { p.disconnect(); } catch {} }
      for (const p of (b.mid.panNodes || [])) { try { p.disconnect(); } catch {} }
      for (const p of (b.far.panNodes || [])) { try { p.disconnect(); } catch {} }
      try { b.near.input.disconnect(); } catch {}
      try { b.near.lpf.disconnect(); } catch {}
      try { b.near.wetSend.disconnect(); } catch {}
      try { b.mid.input.disconnect(); } catch {}
      try { b.mid.lpf.disconnect(); } catch {}
      try { b.mid.wetSend.disconnect(); } catch {}
      try { b.far.input.disconnect(); } catch {}
      try { b.far.lpf.disconnect(); } catch {}
      try { b.far.lowShelf.disconnect(); } catch {}
      try { b.far.wetSend.disconnect(); } catch {}
      try { b.gunDryInput.disconnect(); } catch {}
      try { b.subPunchInput?.disconnect?.(); } catch {}
      try { b.thunderSend.disconnect(); } catch {}
      try {
        b.convolver.disconnect();
        b.convolver.buffer = null;
      } catch {}
      try { b.thunderOut.disconnect(); } catch {}
      b.convolverCount = 0;
      this.battlefieldBus = null;
    }
    if (this.mix) {
      try {
        this.mix.engineDuckBus?.disconnect?.();
        this.mix.mixInput.disconnect();
        this.mix.drive.disconnect();
        this.mix.compressor.disconnect();
        this.mix.makeup.disconnect();
        this.mix.limiter.disconnect();
        this.mix.analyser.disconnect();
      } catch {}
    }
    if (this.listener) {
      try {
        this.listener.gain.disconnect();
        this.listener.removeFromParent();
      } catch {}
    }
    this.listener = null;
    this.mix = null;
    this.runtime = null;
  }
}

/**
 * 固定翼（A-10C 及通用底座）正式库事件音效资产表（来自设计卡 2.2.1 & 2.2.2，revision 8）。
 */
export const AIR_EVENT_AUDIO_CATALOG = Object.freeze({
  US_A10_Turbine_New: '/media/51edfad346ad840e1715c3f34bd7bb84e7365044389f78ebf930ee119fdb79c0.ogg',
  SFX_AIR_PLANEGUN_30mm_GAU8_US: '/media/7a56d552df58ec4b6269fd42c31c4260aa68f4f6e58c1f003e06c10029a410fc.ogg',
  SFX_AIR_PLANEGUN_30mm_GSH30_RU: '/media/93d08dc607473a4c0e25096c03d48a2c93b6b945b5238f700424892468a96428.ogg',
  SFX_AIR_AAG_25mm_GAU12_US: '/media/b30b3c0a7f08ca8ea444aed092a6e38db2ec9b0f4b23297a02926a3c041b2105.ogg',
  BA_DropBomb: '/media/7edd062e56362a807bee12c9dadaf0547170429361404413523dc67ad6c62b6a.ogg',
  SFX_AIR_AAM_Generic: '/media/a7762d2fc78d0298ad673ccf2d02619825f32dc509b03831a6a23d731c2a4199.ogg',
  SFX_GENERIC_ATGM_Missile_US: '/media/c2383ec74fd346d9e9b387c958d37d904d95af5834788e44089ae3b89915ec32.ogg',
  SFX_Explosion_20_30mm_SupFAST_HE: '/media/0863ed9dc9cf18bcd8fccf8716fd016b0e4738862c85d08051a8bf9ef121bf84.ogg',
  'SFX_Explosion_20-30mm_SupFAST_HE': '/media/0863ed9dc9cf18bcd8fccf8716fd016b0e4738862c85d08051a8bf9ef121bf84.ogg',
  'SFX_Expl_MISSILE_AA-A2A-A2G_Small': '/media/6e03752731621a18d86441e98217d106716a20970dabd2e482736c02e4d19c96.flac',
  'SFX_Expl_MISSILE_AA-A2A-A2G_Medium': '/media/22932097bba27205a7709db904d0b50e7e7febd847c2b9ac2e1029e5610cdff2.ogg',
  'SFX_Expl_MISSILE_AA-A2A-A2G_Big': '/media/40a24fae9618194ed6c575446b15e89730ac90d50737d12968889e6d8cd4d1ee.ogg',
  SFX_Explosion_Cruise_Ballistic_Big: '/media/98299d7dda142c195ba6178d72a6fda3f72165b8f8c7a4268ef4f0f5a4deb5ae.flac',
  Death_Exp_Vehicles_Small: '/media/07705d8b02a44fedd796084cf560a3ce882e3d03570a6b8c88e7f94dd90f6a75.ogg',
  BA_Chaff_Trap: '/media/593bd85aa489ec55b4a0bba92a0776a959072159395ce1c8e85e56e16dbee2be.ogg',
  BA_Whistle_Bomb_Big: '/media/8bb90f4a088a465feadb732dbc830c777d1a6a5d98d608808a855f2339da8227.ogg',
  BA_Whistle_Bomb_Medium: '/media/551567dd04a2e01ebd00cf0038db8e6910c2ab96f6a78f8d6517ffb2ca760898.ogg',
  BA_Whistle_Bomb_Small: '/media/02a9c9fbebde4ce3028a1fa9141ac0ac2fb7b1ef0f8863a418ded0fa389d7828.ogg',
  BigTurbine_Falling: '/media/252347e93af2523643d22cbe32bd66728f821835b1b6cbbbcd5d5b21af960075.ogg',
  SmallTurbine_Falling: '/media/5a8eca4486cacd949a69c26331149243803ffe8c455f0fea5a587abaf1fa5e49.ogg',
  Death_Exp_BigTurbine_Plane: '/media/09068fc980c0989172fb813f5070450fe23106fd11efeea89f18c73600dea099.flac',
  SFX_GENERIC_ROCKETPOD_Medium: '/media/b1450c02ab45ecd5ba22c27df6fd78e5919a2600ccf3a6eaa2828fe2c6fe3e2b.ogg',
  SFX_GENERIC_ROCKETPOD_Small: '/media/358aed471d8072e31989b112bd1211d96f7b3d099e2ffcee71281eb73b693a6f.ogg',
  'SFX_Expl_ROCKET_MISSILE-AT_80-105': '/media/bf26a35a927ad0ecb2f63f35c2be4a18fe7add2cae2894c7e44c33f3991167e1.ogg',
  'SFX_Expl_ROCKETPOD_62-77': '/media/82495050ef20941bc82950d42606facdb1be4332c576b0b47aede5b9c9fd52b3.ogg',
  SFX_Afterburner: '/media/e9aaf2cbc9d51f67c0842f4fb296f49e07b55e52fbc04a2d33d10227eeb2b7fb.ogg',
});

/**
 * 纯规则：按 3D 距离平滑计算物理声速传播延迟：
 * - 0–560m 严格遵循真实 ~340 m/s 线性声速（0–1650ms）；
 * - >560m 当传入 extendedFarMaxMs > maxDelayMs（如 heavy/massive 重爆默认 2600ms）时，
 *   以指数渐近方式平滑延展至 extendedFarMaxMs，既保留 600m vs 1500m 纵深尺度感又避免过久等待。
 */
export function computeAudioDelayMs(distMeters, {
  minDelayMs = 35,
  maxDelayMs = 1650,
  extendedFarMaxMs = null,
  speedOfSoundMps = 340,
} = {}) {
  const d = Number.isFinite(distMeters) ? Math.max(0, distMeters) : 120;
  const c = Math.max(100, speedOfSoundMps);
  const raw = (d / c) * 1000;
  const linearKneeDistM = (maxDelayMs / 1000) * c;
  if (Number.isFinite(extendedFarMaxMs) && extendedFarMaxMs > maxDelayMs && d > linearKneeDistM) {
    const extraSpan = extendedFarMaxMs - maxDelayMs;
    const extMs = maxDelayMs + extraSpan * (1 - Math.exp(-(d - linearKneeDistM) / 900));
    return Math.max(minDelayMs, Math.min(extendedFarMaxMs, Math.round(extMs)));
  }
  return Math.max(minDelayMs, Math.min(maxDelayMs, Math.round(raw)));
}

/**
 * 裁切音频首尾静音段，将有效起音对齐到 0–4ms 并大幅降低长尾静音内存占用。
 */
export function trimAudioBuffer(ctx, srcBuf, {
  thresholdRatio = 0.0001,
  padBeforeSec = 0.004,
  padAfterSec = 0.08,
} = {}) {
  if (!ctx || !srcBuf || srcBuf.length <= 0) return srcBuf;
  const chCount = srcBuf.numberOfChannels;
  const len = srcBuf.length;
  const sr = srcBuf.sampleRate;
  let peak = 0;
  const channels = [];
  for (let c = 0; c < chCount; c += 1) {
    const data = srcBuf.getChannelData(c);
    channels.push(data);
    for (let i = 0; i < len; i += 4) {
      const a = Math.abs(data[i]);
      if (a > peak) peak = a;
    }
  }
  const thresh = Math.max(1e-5, peak * thresholdRatio);
  let firstIdx = len;
  let lastIdx = 0;
  for (let c = 0; c < chCount; c += 1) {
    const data = channels[c];
    for (let i = 0; i < len; i += 1) {
      if (Math.abs(data[i]) >= thresh) {
        if (i < firstIdx) firstIdx = i;
        break;
      }
    }
    for (let i = len - 1; i >= 0; i -= 1) {
      if (Math.abs(data[i]) >= thresh) {
        if (i > lastIdx) lastIdx = i;
        break;
      }
    }
  }
  if (firstIdx >= lastIdx) return srcBuf;
  const start = Math.max(0, firstIdx - Math.round(padBeforeSec * sr));
  const end = Math.min(len, lastIdx + Math.round(padAfterSec * sr));
  const outLen = Math.max(1, end - start);
  const outBuf = ctx.createBuffer(chCount, outLen, sr);
  for (let c = 0; c < chCount; c += 1) {
    outBuf.copyToChannel(channels[c].subarray(start, end), c, 0);
  }
  return outBuf;
}

/**
 * 正式库 GAU-8 纠偏切片器（设计卡 2.2.3 节）：
 * - sustainBuffer: 原轨 [0.205s, 0.720s]，循环 [0.14s, 0.48s] + 20ms 等功率交叉淡化；
 * - releaseBuffer: 原轨 [0.68s, 3.20s]，头部 12ms 淡入保留自然余音。
 */
export function sliceGau8Buffers(ctx, srcBuf) {
  if (!ctx || !srcBuf) return null;
  const sr = srcBuf.sampleRate;
  const chCount = srcBuf.numberOfChannels;
  const total = srcBuf.length;

  const sStart = Math.min(total - 1, Math.round(0.205 * sr));
  const sEnd = Math.min(total, Math.round(0.720 * sr));
  const sLen = Math.max(Math.round(0.2 * sr), sEnd - sStart);
  const sustainBuf = ctx.createBuffer(chCount, sLen, sr);

  const loopStartSec = 0.14;
  const loopEndSec = Math.min(sLen / sr - 0.01, 0.48);
  const xfadeSamples = Math.min(Math.round(0.020 * sr), Math.round((loopEndSec - loopStartSec) * 0.25 * sr));
  const loopStartIdx = Math.round(loopStartSec * sr);
  const loopEndIdx = Math.round(loopEndSec * sr);

  for (let c = 0; c < chCount; c += 1) {
    const src = srcBuf.getChannelData(c);
    const dst = sustainBuf.getChannelData(c);
    const copyLen = Math.min(sLen, total - sStart);
    dst.set(src.subarray(sStart, sStart + copyLen), 0);
    // 20ms 等功率交叉淡化，确保 loopEnd -> loopStart 零爆音接缝
    for (let i = 0; i < xfadeSamples; i += 1) {
      const t = i / Math.max(1, xfadeSamples);
      const wOut = Math.cos(t * Math.PI * 0.5);
      const wIn = Math.sin(t * Math.PI * 0.5);
      const tailIdx = loopEndIdx - xfadeSamples + i;
      const headIdx = loopStartIdx + i;
      if (tailIdx >= 0 && tailIdx < sLen && headIdx >= 0 && headIdx < sLen) {
        dst[tailIdx] = dst[tailIdx] * wOut + dst[headIdx] * wIn;
      }
    }
  }

  const rStart = Math.min(total - 1, Math.round(0.68 * sr));
  const rEnd = Math.min(total, Math.round(3.20 * sr));
  const rLen = Math.max(Math.round(0.2 * sr), rEnd - rStart);
  const releaseBuf = ctx.createBuffer(chCount, rLen, sr);
  const fadeInSamples = Math.min(rLen, Math.round(0.012 * sr));
  for (let c = 0; c < chCount; c += 1) {
    const src = srcBuf.getChannelData(c);
    const dst = releaseBuf.getChannelData(c);
    const copyLen = Math.min(rLen, total - rStart);
    dst.set(src.subarray(rStart, rStart + copyLen), 0);
    for (let i = 0; i < fadeInSamples; i += 1) {
      dst[i] *= i / Math.max(1, fadeInSamples);
    }
  }

  return { sustainBuf, releaseBuf, loopStartSec, loopEndSec };
}

/**
 * 固定翼瞬态/武器/坠毁事件音频与阻尼微震控制器：
 * 挂载于 VehicleSpatialAudioController 通用战场物理声学传播总线（唯一共享 Convolver + Near/Mid/Far 分桶 + 软限幅），
 * 支持 3D 物理声速延迟、统一并发保护器、GAU-8/A V2 四层实时声学与二阶欠阻尼弹簧微震。
 */
export class AirEventAudioEngine {
  constructor(spatialAudioController) {
    this.spatial = spatialAudioController;
    this.buffers = new Map();
    this.rawBuffers = new Map();
    this.loadingPromises = new Map();
    this.gau8Slices = null;
    this.gsh30Slice = null;
    this.gau12Slice = null;
    this.realGunSlices = new Map();
    this._gunRealTrackKeys = new Set(['SFX_AIR_PLANEGUN_30mm_GSH30_RU', 'SFX_AIR_AAG_25mm_GAU12_US']);
    this._flushRawPromise = null;
    this._retireTimers = new Set();
    this.gau8Resources = null;
    this.subPunchBuf = null;
    this.sonicBoomNoiseBuf = null;
    this.staticBufCtx = null;

    this.activeGau8 = new Map(); // unitId -> { voice, preset, pos: {x,y,z}, vel: {x,y,z}, burstId }
    this.retiringVoices = new Set();
    this.lastGau8Snapshot = null;
    this.activeFalling = new Map(); // unitId -> { src, gain, panner, pos, vel, lastRate, lastPan, prevMs, cancelled }
    this.activeWhistles = new Map(); // projId -> { src, gain, panner, pos, vel, lastRate, lastPan, cancelled }
    this.activeAfterburners = new Map(); // unitId -> { src, gain, pos, lastActiveMs, cancelled }
    this.afterburnerDebounceMs = 180;
    this._cleanupTimers = new Map();
    this.whistleStartedCount = 0;
    this.fallingUpdateCount = 0;
    this.whistleUpdateCount = 0;
    this.pendingDelayed = [];
    this.recentEvents = [];
    this.sonicBoomPlayedCount = 0;
    this.lastSonicBoomMs = -999999;

    // 统一并发保护器状态（预分配环形窗口，零运行期 GC）
    this.lastEventTimeByKey = new Map();
    this.concurrencyWindows = new Map();
    this.dedupDroppedCount = 0;
    this.subPunchCount = 0;
    this.lastSubPunchMs = -999999;
    this.lastGroundCrackMs = -999999;
    this.bucketStats = { near: 0, mid: 0, far: 0 };
    this.nodeAllocationStats = {
      transientEvents: 0,
      lastEventNodes: { bufferSources: 0, gainNodes: 0, biquadFilters: 0, convolvers: 0 },
    };

    // 二阶欠阻尼弹簧微震（按冲击档位区分固有频宽 omega 与阻尼 zeta）
    this.shakeX = 0;
    this.shakeY = 0;
    this.shakeZ = 0;
    this.shakeVx = 0;
    this.shakeVy = 0;
    this.shakeVz = 0;
    this.shakeOmega = 28.0;
    this.shakeZeta = 0.72;
    this.lastShakeMag = 0;
    this.lastCamPos = { x: 0, y: 120, z: 0 };
    this.lastGroundCenter = { x: 0, y: 0, z: 0 };
    this.hasGroundCenter = false;
    this.virtualListenerPos = { x: 0, y: 120, z: 0 };
    this.cameraRight = { x: 1, y: 0, z: 0 };
    this.lastSpan = 260;
    this.outOfRangeCulledCount = 0;
  }

  _getAudioContext() {
    return this.spatial?.listener?.context || null;
  }

  _getMixDestination() {
    return this.spatial?.mix?.mixInput || this.spatial?.listener?.getInput?.() || null;
  }

  _getBattlefieldBus() {
    return this.spatial?.battlefieldBus || null;
  }

  _ensureStaticBuffers(ctx) {
    if (!ctx) return;
    if (this.staticBufCtx !== ctx) {
      this.staticBufCtx = ctx;
      // 1) 轻量次声冲击锤（Sub-Punch：62Hz -> 38Hz 指数扫频，无直流偏置）
      const sr = ctx.sampleRate;
      const spLen = Math.round(sr * 0.22);
      const spBuf = ctx.createBuffer(1, spLen, sr);
      const spData = spBuf.getChannelData(0);
      let phase = 0;
      for (let i = 0; i < spLen; i += 1) {
        const t = i / sr;
        const u = i / Math.max(1, spLen - 1);
        const freq = 62 * Math.pow(38 / 62, u);
        phase += (2 * Math.PI * freq) / sr;
        const envIn = Math.min(1, t / 0.008);
        const envOut = Math.exp(-t / 0.068) * (u > 0.85 ? (1 - u) / 0.15 : 1);
        spData[i] = Math.sin(phase) * envIn * envOut * 0.72;
      }
      this.subPunchBuf = spBuf;

      // 2) 音爆预建低频衰减噪声缓冲（避免 run-time Buffer 分配）
      const sbLen = Math.round(sr * 0.30);
      const sbBuf = ctx.createBuffer(1, sbLen, sr);
      const sbData = sbBuf.getChannelData(0);
      let lp = 0;
      for (let i = 0; i < sbLen; i += 1) {
        const white = Math.random() * 2 - 1;
        lp = lp * 0.92 + white * 0.08;
        sbData[i] = lp * Math.exp(-i / (sr * 0.075)) * 2.4;
      }
      this.sonicBoomNoiseBuf = sbBuf;

      // 3) 共享机炮物理声学资源（复用全局唯一 reverbIrBuf，不重复计算 3.6s IR）
      const bus = this._getBattlefieldBus();
      this.gau8Resources = {
        shockPulseLoopBuf: buildShockPulseLoopBuffer(ctx, DEFAULT_GAU8_ACOUSTIC_PRESET),
        crackBurstBuf: buildSupersonicCrackBuffer(ctx),
        reverbIrBuf: bus?.reverbIrBuf || null,
        satCurve: buildAsymmetricSaturatorCurve(2.65),
        gsh30Slice: this.gsh30Slice,
        gau12Slice: this.gau12Slice,
        realGunSlices: this.realGunSlices,
        impactBuf: this.buffers.get('SFX_Explosion_20-30mm_SupFAST_HE') || null,
      };
    } else if (this.gau8Resources) {
      if (!this.gau8Resources.reverbIrBuf && this._getBattlefieldBus()?.reverbIrBuf) {
        this.gau8Resources.reverbIrBuf = this._getBattlefieldBus().reverbIrBuf;
      }
      if (this.gsh30Slice && !this.gau8Resources.gsh30Slice) {
        this.gau8Resources.gsh30Slice = this.gsh30Slice;
      }
      if (this.gau12Slice && !this.gau8Resources.gau12Slice) {
        this.gau8Resources.gau12Slice = this.gau12Slice;
      }
      if (!this.gau8Resources.realGunSlices) {
        this.gau8Resources.realGunSlices = this.realGunSlices;
      }
      if (!this.gau8Resources.impactBuf) {
        this.gau8Resources.impactBuf = this.buffers.get('SFX_Explosion_20-30mm_SupFAST_HE') || null;
      }
    }
  }

  _processDecodedTrack(ctx, key, decoded) {
    if (key === 'SFX_AIR_PLANEGUN_30mm_GAU8_US') {
      this.gau8Slices = sliceGau8Buffers(ctx, decoded);
    } else if (key === 'SFX_AIR_PLANEGUN_30mm_GSH30_RU') {
      this.gsh30Slice = sliceRealGunTrack(ctx, decoded, {
        startSec: 0.20,
        endSec: 0.64,
        loopStartOffset: 0.05,
        loopEndOffset: 0.40,
        releaseStartSec: 0.58,
        releaseEndSec: 2.95,
      });
      if (this.gsh30Slice) this.realGunSlices.set(key, this.gsh30Slice);
      if (this.gau8Resources) this.gau8Resources.gsh30Slice = this.gsh30Slice;
    } else if (key === 'SFX_AIR_AAG_25mm_GAU12_US') {
      this.gau12Slice = sliceRealGunTrack(ctx, decoded, {
        startSec: 0.20,
        endSec: 1.18,
        loopStartOffset: 0.08,
        loopEndOffset: 0.92,
        releaseStartSec: 1.12,
        releaseEndSec: 2.60,
      });
      if (this.gau12Slice) this.realGunSlices.set(key, this.gau12Slice);
      if (this.gau8Resources) this.gau8Resources.gau12Slice = this.gau12Slice;
    } else if (this._gunRealTrackKeys.has(key) && decoded) {
      const dur = Math.max(0.4, decoded.duration || 1.0);
      const sStart = Math.min(dur * 0.15, 0.12);
      const sEnd = Math.max(sStart + 0.18, Math.min(dur * 0.55, 0.90));
      const rStart = Math.min(dur - 0.16, Math.max(sEnd - 0.06, dur * 0.45));
      const slice = sliceRealGunTrack(ctx, decoded, {
        startSec: sStart,
        endSec: sEnd,
        loopStartOffset: 0.05,
        loopEndOffset: Math.max(0.12, (sEnd - sStart) - 0.06),
        releaseStartSec: Math.max(0, rStart),
        releaseEndSec: dur,
      });
      if (slice) this.realGunSlices.set(key, slice);
    }
    const trimmed = trimAudioBuffer(ctx, decoded, { thresholdRatio: 0.0001, padBeforeSec: 0.004, padAfterSec: 0.08 });
    this.buffers.set(key, trimmed);
    if (key === 'SFX_Explosion_20-30mm_SupFAST_HE' && this.gau8Resources) {
      this.gau8Resources.impactBuf = trimmed;
    }
    return trimmed;
  }

  async preloadKeys(keys = []) {
    const unique = [...new Set(keys)].filter(k => Boolean(AIR_EVENT_AUDIO_CATALOG[k]));
    await Promise.all(unique.map(k => this._loadKey(k)));
    return this.buffers.size;
  }

  async preloadA10CoreAudio() {
    return this.preloadKeys([
      'SFX_AIR_PLANEGUN_30mm_GAU8_US',
      'SFX_AIR_PLANEGUN_30mm_GSH30_RU',
      'SFX_AIR_AAG_25mm_GAU12_US',
      'BA_DropBomb',
      'SFX_GENERIC_ATGM_Missile_US',
      'SFX_Explosion_20-30mm_SupFAST_HE',
      'SFX_Expl_MISSILE_AA-A2A-A2G_Small',
      'SFX_Expl_MISSILE_AA-A2A-A2G_Medium',
      'SFX_Expl_MISSILE_AA-A2A-A2G_Big',
      'SFX_Explosion_Cruise_Ballistic_Big',
      'Death_Exp_Vehicles_Small',
      'BA_Chaff_Trap',
      'BA_Whistle_Bomb_Big',
      'BigTurbine_Falling',
      'Death_Exp_BigTurbine_Plane',
      'SFX_GENERIC_ROCKETPOD_Small',
      'SFX_Expl_ROCKETPOD_62-77',
    ]);
  }

  async _loadKey(key) {
    if (this.buffers.has(key)) return this.buffers.get(key);
    const url = AIR_EVENT_AUDIO_CATALOG[key];
    if (!url) return null;
    if (this.loadingPromises.has(key)) return this.loadingPromises.get(key);

    const p = (async () => {
      try {
        const res = await fetch(url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const arr = await res.arrayBuffer();
        const ctx = this._getAudioContext();
        if (!ctx) {
          // AudioContext 未因用户手势解锁前先缓存元标记，等解锁后立即解码
          this.rawBuffers.set(key, arr);
          return null;
        }
        const decoded = await ctx.decodeAudioData(arr.slice(0));
        const trimmed = this._processDecodedTrack(ctx, key, decoded);
        this.rawBuffers.delete(key);
        return trimmed;
      } catch {
        return null;
      } finally {
        this.loadingPromises.delete(key);
      }
    })();
    this.loadingPromises.set(key, p);
    return p;
  }

  async flushRawDecodesAfterUnlock() {
    if (this._flushRawPromise) return this._flushRawPromise;
    const ctx = this._getAudioContext();
    if (!ctx || this.rawBuffers.size === 0) return;
    this._flushRawPromise = (async () => {
      const entries = [...this.rawBuffers.entries()];
      for (const [key, arr] of entries) {
        if (this.buffers.has(key)) {
          this.rawBuffers.delete(key);
          continue;
        }
        try {
          const decoded = await ctx.decodeAudioData(arr.slice(0));
          this._processDecodedTrack(ctx, key, decoded);
          this.rawBuffers.delete(key);
        } catch {
          // 单个解码失败不阻塞其余音轨
        }
      }
    })().finally(() => {
      this._flushRawPromise = null;
    });
    return this._flushRawPromise;
  }

  _recordEvent(ev) {
    this.recentEvents.push(ev);
    if (this.recentEvents.length > 64) this.recentEvents.shift();
  }

  _syncCameraRightFromCamera(camera) {
    const els = camera?.matrixWorld?.elements;
    if (els && els.length >= 3) {
      const rx = els[0];
      const ry = els[1];
      const rz = els[2];
      const len = Math.hypot(rx, ry, rz);
      if (len > 1e-5) {
        this.cameraRight.x = rx / len;
        this.cameraRight.y = ry / len;
        this.cameraRight.z = rz / len;
        return;
      }
    }
    const q = camera?.quaternion;
    if (q && Number.isFinite(q.x)) {
      const x = q.x;
      const y = q.y;
      const z = q.z;
      const w = q.w;
      const rx = 1 - 2 * (y * y + z * z);
      const ry = 2 * (x * y + w * z);
      const rz = 2 * (x * z - w * y);
      const len = Math.hypot(rx, ry, rz);
      if (len > 1e-5) {
        this.cameraRight.x = rx / len;
        this.cameraRight.y = ry / len;
        this.cameraRight.z = rz / len;
      }
    }
  }

  _distanceToListener(worldPos) {
    const dx = (worldPos?.x || 0) - this.virtualListenerPos.x;
    const dy = (worldPos?.y || 0) - this.virtualListenerPos.y;
    const dz = (worldPos?.z || 0) - this.virtualListenerPos.z;
    return Math.hypot(dx, dy, dz);
  }

  _distanceToCamera(worldPos) {
    return this._distanceToListener(worldPos);
  }

  _selectBucketName(distMeters) {
    if (distMeters < 260) return 'near';
    if (distMeters <= 680) return 'mid';
    return 'far';
  }

  _inferPowerTier(key, shakeTier) {
    if (shakeTier === 'crash' || key === 'Death_Exp_BigTurbine_Plane') return 'massive';
    if (
      shakeTier === 'heavy'
      || key === 'SFX_Explosion_Cruise_Ballistic_Big'
      || key === 'SFX_Expl_MISSILE_AA-A2A-A2G_Big'
      || key === 'Death_Exp_Vehicles_Small'
    ) {
      return 'heavy';
    }
    if (
      key === 'BA_Chaff_Trap'
      || key === 'BA_DropBomb'
      || key === 'SFX_Expl_MISSILE_AA-A2A-A2G_Small'
    ) {
      return 'small';
    }
    return 'medium';
  }

  _computeConcurrencyScale(dedupKey, nowMs, windowMs = 350) {
    let win = this.concurrencyWindows.get(dedupKey);
    if (!win) {
      win = { ring: new Float64Array(16).fill(-1e9), head: 0 };
      this.concurrencyWindows.set(dedupKey, win);
    }
    const ring = win.ring;
    ring[win.head] = nowMs;
    win.head = (win.head + 1) & 15;
    let activeCount = 0;
    const cutoff = nowMs - windowMs;
    for (let i = 0; i < 16; i += 1) {
      const ts = ring[i];
      if (ts > -1e8 && ts > cutoff && ts <= nowMs) {
        activeCount += 1;
      }
    }
    const n = Math.max(1, activeCount);
    return { n, scale: 1 / Math.sqrt(n) };
  }

  /**
   * 注入二阶欠阻尼阻尼微震脉冲（span <= 450m 全量，450–1200m 0.45×，>1200m 0），
   * 按冲击档位区分震屏脉宽与低频重击余震（heavy/crash 低频宽脉、medium 中频、strafe/light 高频短促）。
   */
  injectMicroShake(worldPos, tier = 'medium') {
    if (this.lastSpan > 1200) return;
    const distXZ = Math.hypot((worldPos?.x || 0) - this.virtualListenerPos.x, (worldPos?.z || 0) - this.virtualListenerPos.z);
    if (distXZ > 520) return;
    const spanMul = this.lastSpan <= 450 ? 1.0 : 0.45;
    const distMul = Math.max(0.2, 1.0 - distXZ / 520);
    let omega = 25.0;
    let zeta = 0.68;
    let ampBase = 1.05;
    if (tier === 'crash') {
      omega = 15.0;
      zeta = 0.56;
      ampBase = 2.6;
    } else if (tier === 'heavy') {
      omega = 17.5;
      zeta = 0.58;
      ampBase = 2.1;
    } else if (tier === 'medium') {
      omega = 25.0;
      zeta = 0.68;
      ampBase = 1.05;
    } else {
      omega = 38.0;
      zeta = 0.80;
      ampBase = 0.30;
    }
    this.shakeOmega = omega;
    this.shakeZeta = zeta;
    const amp = ampBase * spanMul * distMul;
    const phase = performance.now() * 0.073;
    this.shakeVx += Math.cos(phase) * amp * 22;
    this.shakeVy += (Math.sin(phase * 1.3) * 0.65 - 0.35) * amp * 24;
    this.shakeVz += Math.sin(phase * 0.9) * amp * 22;
  }

  /**
   * 每帧推进二阶欠阻尼微震弹簧、更新 RTS 虚拟监听点与相机右向量、清理过期延迟事件、连续更新活跃 GAU-8 实时距离与多普勒滑移。
   */
  step(cameraPos, spanMeters, dtSec, frameContext = null) {
    if (cameraPos) {
      this.lastCamPos.x = Number.isFinite(cameraPos.x) ? cameraPos.x : 0;
      this.lastCamPos.y = Number.isFinite(cameraPos.y) ? cameraPos.y : 120;
      this.lastCamPos.z = Number.isFinite(cameraPos.z) ? cameraPos.z : 0;
    }
    if (Number.isFinite(spanMeters)) this.lastSpan = spanMeters;

    const gc = frameContext?.groundCenter;
    if (gc && Number.isFinite(gc.x) && Number.isFinite(gc.z)) {
      this.lastGroundCenter.x = gc.x;
      this.lastGroundCenter.y = Number.isFinite(gc.y) ? gc.y : 0;
      this.lastGroundCenter.z = gc.z;
      this.hasGroundCenter = true;
    }
    computeVirtualListenerPos(
      this.lastCamPos,
      this.hasGroundCenter ? this.lastGroundCenter : null,
      this.virtualListenerPos,
      0.35,
    );

    const camObj = frameContext?.camera || this.spatial?.camera;
    if (camObj) {
      this._syncCameraRightFromCamera(camObj);
    }

    const nowMs = performance.now();
    if (this.pendingDelayed.length > 0) {
      let wIdx = 0;
      for (let i = 0; i < this.pendingDelayed.length; i += 1) {
        const item = this.pendingDelayed[i];
        if (item.playAtMs > nowMs) {
          this.pendingDelayed[wIdx] = item;
          wIdx += 1;
        }
      }
      this.pendingDelayed.length = wIdx;
    }

    const ctx = this._getAudioContext();
    if (ctx && ctx.state === 'running' && this.activeGau8.size > 0 && this.spatial?.enabled && !this.spatial?.paused) {
      const lx = this.virtualListenerPos.x;
      const ly = this.virtualListenerPos.y;
      const lz = this.virtualListenerPos.z;
      const audioNow = ctx.currentTime;
      for (const entry of this.activeGau8.values()) {
        if (!entry?.voice) continue;
        const dx = entry.pos.x - lx;
        const dy = entry.pos.y - ly;
        const dz = entry.pos.z - lz;
        const dist = Math.hypot(dx, dy, dz);
        const invDist = dist > 1e-3 ? (1 / dist) : 0;
        const radialSpeed = (dx * entry.vel.x + dy * entry.vel.y + dz * entry.vel.z) * invDist;
        const panVal = computeCameraPan(entry.pos, this.virtualListenerPos, this.cameraRight, dist, 0.72);
        try {
          entry.voice.updateTelemetry(dist, radialSpeed, panVal, audioNow);
        } catch {
          // 音频参数更新异常不影响主循环
        }
      }
    }

    const totalDt = Math.min(0.05, Math.max(0.001, Number(dtSec) || 0.016));
    const omega = this.shakeOmega || 28.0;
    const zeta = this.shakeZeta || 0.72;
    const k = omega * omega;
    const c = 2 * zeta * omega;
    const subSteps = Math.max(1, Math.ceil(totalDt / 0.008));
    const subDt = totalDt / subSteps;

    for (let i = 0; i < subSteps; i += 1) {
      const ax = -k * this.shakeX - c * this.shakeVx;
      const ay = -k * this.shakeY - c * this.shakeVy;
      const az = -k * this.shakeZ - c * this.shakeVz;
      this.shakeVx = Math.max(-80, Math.min(80, this.shakeVx + ax * subDt));
      this.shakeVy = Math.max(-80, Math.min(80, this.shakeVy + ay * subDt));
      this.shakeVz = Math.max(-80, Math.min(80, this.shakeVz + az * subDt));
      this.shakeX = Math.max(-3.0, Math.min(3.0, this.shakeX + this.shakeVx * subDt));
      this.shakeY = Math.max(-3.0, Math.min(3.0, this.shakeY + this.shakeVy * subDt));
      this.shakeZ = Math.max(-3.0, Math.min(3.0, this.shakeZ + this.shakeVz * subDt));
    }

    this.lastShakeMag = Math.hypot(this.shakeX, this.shakeY, this.shakeZ);
    if (this.lastShakeMag < 0.002 && Math.hypot(this.shakeVx, this.shakeVy, this.shakeVz) < 0.01) {
      this.shakeX = this.shakeY = this.shakeZ = 0;
      this.shakeVx = this.shakeVy = this.shakeVz = 0;
      this.lastShakeMag = 0;
    }

    if (this.spatial?.runtime?.unlocked && this.rawBuffers.size > 0) {
      this.flushRawDecodesAfterUnlock();
    }
    this.spatial?.sampleAnalyserPeak?.();
    return { x: this.shakeX, y: this.shakeY, z: this.shakeZ, magnitude: this.lastShakeMag };
  }

  getCameraMicroShakeOffset() {
    return { x: this.shakeX, y: this.shakeY, z: this.shakeZ, magnitude: this.lastShakeMag };
  }

  /**
   * 通用瞬态世界事件调度器（接入 RTS 虚拟监听点、声压等级平滑衰减、相机右向量 5 档静态声像、
   * Near/Mid/Far 分桶、唯一共享 Convolver、统一并发保护器与干声直入重爆 Sub-Punch）。
   * 严格约束：普通瞬态事件每次仅新建 1 个 AudioBufferSourceNode + 1 个 GainNode（零新建 Filter / Convolver / Panner）。
   */
  scheduleWorldEvent(
    key,
    worldPos,
    {
      delayMs = null,
      delayOffsetMs = 0,
      maxDelayMs = 1650,
      gain = 0.85,
      maxDurationSec = null,
      shakeTier = null,
      powerTier = null,
      dedupKey = null,
      minIntervalMs = 40,
      pitchJitter = 0.05,
      concurrencyWindowMs = 350,
      subPunch = false,
    } = {},
  ) {
    if (!AIR_EVENT_AUDIO_CATALOG[key]) return false;

    const nowMs = performance.now();
    const effectiveDedupKey = dedupKey || key;
    const minInt = Number.isFinite(minIntervalMs) ? Math.max(0, minIntervalMs) : 40;
    const prevAt = this.lastEventTimeByKey.get(effectiveDedupKey) ?? -999999;
    if (minInt > 0 && nowMs - prevAt < minInt) {
      this.dedupDroppedCount += 1;
      return false;
    }
    this.lastEventTimeByKey.set(effectiveDedupKey, nowMs);

    const dist = this._distanceToListener(worldPos);
    const effectivePowerTier = powerTier || this._inferPowerTier(key, shakeTier);
    const att = computeAcousticAttenuation(dist, effectivePowerTier);
    const bucket = this._selectBucketName(att.effectiveBucketDistM);

    const baseDelayMs = Number.isFinite(delayMs)
      ? Math.max(0, Math.min(3000, Math.round(delayMs)))
      : computeAudioDelayMs(dist, {
        maxDelayMs: Number.isFinite(maxDelayMs) ? maxDelayMs : 1650,
        extendedFarMaxMs: (effectivePowerTier === 'heavy' || effectivePowerTier === 'massive') ? 2600 : null,
      });
    const finalDelayMs = Math.max(0, Math.min(3200, baseDelayMs + (Number.isFinite(delayOffsetMs) ? Math.round(delayOffsetMs) : 0)));

    const panVal = computeCameraPan(worldPos, this.virtualListenerPos, this.cameraRight, dist, 0.72);
    const panLane = selectStaticPanLane(panVal);

    const jitterAmp = Number.isFinite(pitchJitter) ? Math.max(0, Math.min(0.25, pitchJitter)) : 0.05;
    const pitchRate = Number((1 + (Math.random() * 2 - 1) * jitterAmp).toFixed(4));
    const { n: concurrencyCount, scale: concurrencyScale } = this._computeConcurrencyScale(
      effectiveDedupKey,
      nowMs,
      Number.isFinite(concurrencyWindowMs) ? Math.max(50, concurrencyWindowMs) : 350,
    );

    if (shakeTier) {
      this.injectMicroShake(worldPos, shakeTier);
    }

    const wantSubPunch = Boolean(subPunch || shakeTier === 'heavy' || shakeTier === 'crash');
    const record = {
      key,
      powerTier: att.tierId,
      distMeters: Math.round(dist),
      distGain: Number(att.distGain.toFixed(4)),
      bucket,
      panVal: Number(panVal.toFixed(3)),
      panLane,
      delayMs: finalDelayMs,
      scheduledAtMs: nowMs,
      playAtMs: nowMs + finalDelayMs,
      pitchRate,
      concurrencyCount,
      concurrencyScale: Number(concurrencyScale.toFixed(4)),
      subPunch: false,
      audible: att.audible,
      played: false,
    };
    this.pendingDelayed.push(record);
    this._recordEvent(record);

    if (!att.audible) {
      this.outOfRangeCulledCount += 1;
      return true;
    }

    const ctx = this._getAudioContext();
    const dest = this._getMixDestination();
    if (!ctx || !dest || !this.spatial?.enabled || this.spatial?.paused) {
      return true;
    }

    this._ensureStaticBuffers(ctx);
    const bus = this._getBattlefieldBus();
    const bucketObj = bus?.[bucket];
    const bucketInput = bucketObj?.panInputs?.[panLane] || bucketObj?.input || dest;

    const playWithBuf = (buf) => {
      if (!buf || !this.spatial?.enabled || this.spatial?.paused || this.spatial?.disposed) return;
      try {
        const src = ctx.createBufferSource();
        src.buffer = buf;
        src.playbackRate.value = pitchRate;
        const gNode = ctx.createGain();
        gNode.gain.value = clamp01(gain) * att.distGain * concurrencyScale * 0.85;
        src.connect(gNode);
        gNode.connect(bucketInput);

        const startAt = ctx.currentTime + finalDelayMs * 0.001;
        if (Number.isFinite(maxDurationSec) && maxDurationSec > 0.1) {
          src.start(startAt, 0, Math.min(buf.duration, maxDurationSec));
        } else {
          src.start(startAt);
        }

        this.bucketStats[bucket] += 1;
        this.nodeAllocationStats.transientEvents += 1;
        this.nodeAllocationStats.lastEventNodes.bufferSources = 1;
        this.nodeAllocationStats.lastEventNodes.gainNodes = 1;
        this.nodeAllocationStats.lastEventNodes.biquadFilters = 0;
        this.nodeAllocationStats.lastEventNodes.convolvers = 0;

        if (wantSubPunch && this.subPunchBuf && nowMs - this.lastSubPunchMs >= 90) {
          this.lastSubPunchMs = nowMs;
          const spSrc = ctx.createBufferSource();
          spSrc.buffer = this.subPunchBuf;
          const spGain = ctx.createGain();
          spGain.gain.value = 0.42 * att.distGain * concurrencyScale;
          spSrc.connect(spGain);
          // 次声冲击锤直入干声母线，避免被远距混响糊成一团
          spGain.connect(bus?.subPunchInput || dest);
          spSrc.start(startAt);
          this.subPunchCount += 1;
          record.subPunch = true;
        }
        if ((effectivePowerTier === 'heavy' || effectivePowerTier === 'massive' || shakeTier === 'heavy' || shakeTier === 'crash') && dist < 650) {
          const prox = clamp01(1 - dist / 650);
          const baseDepth = (effectivePowerTier === 'massive' || shakeTier === 'crash') ? 0.48 : 0.58;
          const depth = baseDepth + (1 - baseDepth) * (1 - prox) * 0.65;
          this.spatial?.triggerEngineDuck?.(startAt, { depth, holdSec: 0.18, releaseTc: 0.45 });
        }
        record.played = true;
      } catch {
        // 音频上下文切换时安全忽略
      }
    };

    const cached = this.buffers.get(key);
    if (cached) {
      playWithBuf(cached);
    } else {
      this._loadKey(key).then(buf => { if (buf) playWithBuf(buf); });
    }
    return true;
  }

  /**
   * 30mm 地面弹着超音速马赫锥裂响（先撕地后雷鸣双段听感，带 85ms 防抖，走物理声速短延迟与相机相对声像，仅 1 BufferSource + 1 GainNode）。
   */
  triggerGroundImpactCrack(worldPos, { delayMs = null, gain = 0.40 } = {}) {
    const nowMs = performance.now();
    if (nowMs - this.lastGroundCrackMs < 85) return false;
    this.lastGroundCrackMs = nowMs;

    const ctx = this._getAudioContext();
    const dest = this._getMixDestination();
    if (!ctx || !dest || !this.spatial?.enabled || this.spatial?.paused) return false;

    this._ensureStaticBuffers(ctx);
    if (!this.gau8Resources?.crackBurstBuf) return false;

    const dist = this._distanceToListener(worldPos);
    const att = computeAcousticAttenuation(dist, 'medium');
    if (!att.audible) return false;

    const bucket = this._selectBucketName(att.effectiveBucketDistM);
    const panVal = computeCameraPan(worldPos, this.virtualListenerPos, this.cameraRight, dist, 0.72);
    const panLane = selectStaticPanLane(panVal);
    const finalDelayMs = Number.isFinite(delayMs)
      ? Math.max(0, Math.round(delayMs))
      : computeAudioDelayMs(dist, { minDelayMs: 12, maxDelayMs: 360 });

    const bus = this._getBattlefieldBus();
    const bucketObj = bus?.[bucket];
    const bucketInput = bucketObj?.panInputs?.[panLane] || bucketObj?.input || dest;

    try {
      const src = ctx.createBufferSource();
      src.buffer = this.gau8Resources.crackBurstBuf;
      src.playbackRate.value = 0.95 + Math.random() * 0.10;
      const gNode = ctx.createGain();
      gNode.gain.value = clamp01(gain) * att.distGain;
      src.connect(gNode);
      gNode.connect(bucketInput);
      const startAt = ctx.currentTime + finalDelayMs * 0.001;
      src.start(startAt, 0, 0.36);

      this.bucketStats[bucket] += 1;
      this.nodeAllocationStats.transientEvents += 1;
      this.nodeAllocationStats.lastEventNodes.bufferSources = 1;
      this.nodeAllocationStats.lastEventNodes.gainNodes = 1;
      this.nodeAllocationStats.lastEventNodes.biquadFilters = 0;
      this.nodeAllocationStats.lastEventNodes.convolvers = 0;
      this._recordEvent({
        key: 'GAU8_GROUND_MACH_CRACK',
        powerTier: 'medium',
        distMeters: Math.round(dist),
        distGain: Number(att.distGain.toFixed(4)),
        bucket,
        panVal: Number(panVal.toFixed(3)),
        panLane,
        delayMs: finalDelayMs,
        scheduledAtMs: nowMs,
        playAtMs: nowMs + finalDelayMs,
        pitchRate: Number(src.playbackRate.value.toFixed(4)),
        concurrencyCount: 1,
        concurrencyScale: 1,
        audible: true,
        played: true,
      });
      return true;
    } catch {
      return false;
    }
  }

  startGau8Burst(unitId, worldPos, burstId = 1, preset = DEFAULT_GAU8_ACOUSTIC_PRESET) {
    this.stopGau8Burst(unitId);
    const dist = this._distanceToListener(worldPos);
    const delayMs = computeAudioDelayMs(dist);
    const panVal = computeCameraPan(worldPos, this.virtualListenerPos, this.cameraRight, dist, 0.72);
    const nowMs = performance.now();
    const effectivePreset = preset || DEFAULT_GAU8_ACOUSTIC_PRESET;
    const rec = {
      key: 'SFX_AIR_PLANEGUN_30mm_GAU8_US',
      presetId: effectivePreset.presetId || 'gau8_30mm',
      powerTier: 'heavy',
      distMeters: Math.round(dist),
      bucket: this._selectBucketName(dist),
      panVal: Number(panVal.toFixed(3)),
      delayMs,
      scheduledAtMs: nowMs,
      playAtMs: nowMs + delayMs,
      burstId,
      played: false,
    };
    this.pendingDelayed.push(rec);
    this._recordEvent(rec);

    const ctx = this._getAudioContext();
    const dest = this._getMixDestination();
    if (!ctx || ctx.state !== 'running' || !dest || !this.spatial?.enabled || this.spatial?.paused) return;

    try {
      this._ensureStaticBuffers(ctx);
    } catch {
      return;
    }
    const bus = this._getBattlefieldBus();
    const dryDest = bus?.gunDryInput || dest;
    const wetDest = bus?.thunderSend || dest;

    // 懒确保实录火药纹理（若尚未解码则后台预取不阻塞本次激波层发声，支持预设自定义 key）
    const primaryKey = effectivePreset.primaryRealKey ?? 'SFX_AIR_PLANEGUN_30mm_GSH30_RU';
    const secondaryKey = effectivePreset.secondaryRealKey ?? 'SFX_AIR_AAG_25mm_GAU12_US';
    if (primaryKey) {
      this._gunRealTrackKeys.add(primaryKey);
      if (!this.realGunSlices.has(primaryKey)) {
        const existing = this.buffers.get(primaryKey);
        if (existing) this._processDecodedTrack(ctx, primaryKey, existing);
        else this._loadKey(primaryKey);
      }
    }
    if (secondaryKey) {
      this._gunRealTrackKeys.add(secondaryKey);
      if (!this.realGunSlices.has(secondaryKey)) {
        const existing = this.buffers.get(secondaryKey);
        if (existing) this._processDecodedTrack(ctx, secondaryKey, existing);
        else this._loadKey(secondaryKey);
      }
    }

    try {
      const voice = createContinuousGunAcousticVoice(ctx, dryDest, wetDest, this.gau8Resources, {
        distanceMeters: dist,
        radialSpeedMps: 0,
        panVal,
        preset: effectivePreset,
      });
      rec.played = true;
      this.activeGau8.set(unitId, {
        voice,
        preset: effectivePreset,
        burstId,
        pos: { x: worldPos?.x || 0, y: worldPos?.y || 120, z: worldPos?.z || 0 },
        vel: { x: 0, y: 0, z: 0 },
      });
      this.lastGau8Snapshot = voice.getSnapshot();
    } catch {
      // 安全降级忽略
    }
  }

  updateGau8Burst(unitId, muzzlePos, velVec) {
    const entry = this.activeGau8.get(unitId);
    if (!entry) return;
    if (muzzlePos) {
      entry.pos.x = Number(muzzlePos.x) || 0;
      entry.pos.y = Number(muzzlePos.y) || 0;
      entry.pos.z = Number(muzzlePos.z) || 0;
    }
    if (velVec) {
      entry.vel.x = Number(velVec.x) || 0;
      entry.vel.y = Number(velVec.y) || 0;
      entry.vel.z = Number(velVec.z) || 0;
    }
  }

  stopGau8Burst(unitId) {
    const entry = this.activeGau8.get(unitId);
    if (!entry) return;
    this.activeGau8.delete(unitId);
    const ctx = this._getAudioContext();
    try {
      const now = ctx ? ctx.currentTime : 0;
      const stopSec = entry.voice.stop(now);
      const snap = entry.voice.getSnapshot();
      this.lastGau8Snapshot = snap;
      this.retiringVoices.add(entry.voice);
      const cleanupDelayMs = Math.max(
        4500,
        Number.isFinite(snap?.tailWaitMs) && snap.tailWaitMs > 0 ? (snap.tailWaitMs + 200) : 0,
        Number.isFinite(stopSec) ? Math.round((stopSec - now + 4.3) * 1000) : 4500,
      );
      const timerId = setTimeout(() => {
        this._retireTimers.delete(timerId);
        this.retiringVoices.delete(entry.voice);
      }, cleanupDelayMs);
      this._retireTimers.add(timerId);
    } catch {}
  }

  startFallingLoop(unitId, worldPos) {
    this.stopFallingLoop(unitId);
    const dist = this._distanceToListener(worldPos);
    const att = computeAcousticAttenuation(dist, 'massive');
    const bucket = this._selectBucketName(att.effectiveBucketDistM);
    const panVal = computeCameraPan(worldPos, this.virtualListenerPos, this.cameraRight, dist, 0.72);
    const delayMs = computeAudioDelayMs(dist, { minDelayMs: 35, maxDelayMs: 900 });
    const nowMs = performance.now();
    const rec = {
      key: 'BigTurbine_Falling',
      powerTier: 'massive',
      distMeters: Math.round(dist),
      distGain: Number(att.distGain.toFixed(4)),
      bucket,
      panVal: Number(panVal.toFixed(3)),
      delayMs,
      scheduledAtMs: nowMs,
      playAtMs: nowMs + delayMs,
      audible: att.audible,
      played: false,
    };
    this.pendingDelayed.push(rec);
    this._recordEvent(rec);

    const ctx = this._getAudioContext();
    const dest = this._getMixDestination();
    if (!ctx || !dest || !this.spatial?.enabled || this.spatial?.paused) return;

    const bus = this._getBattlefieldBus();
    const bucketInput = bus?.[bucket]?.input || dest;

    const entry = {
      src: null,
      gain: null,
      panner: null,
      pos: { x: worldPos?.x || 0, y: worldPos?.y || 120, z: worldPos?.z || 0 },
      vel: { x: 0, y: -35, z: 0 },
      lastRate: 1.0,
      lastPan: panVal,
      prevMs: nowMs,
      cancelled: false,
    };
    this.activeFalling.set(unitId, entry);

    const launch = (buf) => {
      if (entry.cancelled || this.activeFalling.get(unitId) !== entry) return;
      if (!buf || !this.spatial?.enabled || this.spatial?.paused || this.spatial?.disposed) return;
      try {
        const src = ctx.createBufferSource();
        src.buffer = buf;
        src.loop = true;
        const g = ctx.createGain();
        const initGain = Math.max(0.001, 0.82 * att.distGain);
        const t0 = ctx.currentTime + delayMs * 0.001;
        g.gain.setValueAtTime(0.001, t0);
        g.gain.linearRampToValueAtTime(initGain, t0 + 0.06);
        const panner = typeof ctx.createStereoPanner === 'function' ? ctx.createStereoPanner() : null;
        if (panner) {
          panner.pan.setValueAtTime(panVal, t0);
          src.connect(g);
          g.connect(panner);
          panner.connect(bucketInput);
        } else {
          src.connect(g);
          g.connect(bucketInput);
        }
        src.start(t0);
        rec.played = true;
        entry.src = src;
        entry.gain = g;
        entry.panner = panner;
      } catch {}
    };
    const cached = this.buffers.get('BigTurbine_Falling');
    if (cached) launch(cached);
    else this._loadKey('BigTurbine_Falling').then(launch);
  }

  /**
   * 坠机螺旋下坠实时声学调制：按俯冲空速、径向逼近多普勒与相机右向量更新音高/响度/声像。
   */
  updateFallingLoop(unitId, worldPos, velVec = null) {
    const entry = this.activeFalling.get(unitId);
    if (!entry || entry.cancelled) return;
    const nowMs = performance.now();
    const px = Number(worldPos?.x) || entry.pos.x;
    const py = Number(worldPos?.y) || entry.pos.y;
    const pz = Number(worldPos?.z) || entry.pos.z;

    if (velVec && Number.isFinite(velVec.x)) {
      entry.vel.x = velVec.x;
      entry.vel.y = Number.isFinite(velVec.y) ? velVec.y : entry.vel.y;
      entry.vel.z = Number.isFinite(velVec.z) ? velVec.z : entry.vel.z;
    } else {
      const dtMs = nowMs - entry.prevMs;
      if (dtMs >= 8 && dtMs <= 300) {
        const invDt = 1000 / dtMs;
        entry.vel.x = (px - entry.pos.x) * invDt;
        entry.vel.y = (py - entry.pos.y) * invDt;
        entry.vel.z = (pz - entry.pos.z) * invDt;
      }
    }
    entry.pos.x = px;
    entry.pos.y = py;
    entry.pos.z = pz;
    entry.prevMs = nowMs;
    this.fallingUpdateCount += 1;

    const ctx = this._getAudioContext();
    if (!ctx || !entry.src || !entry.gain) return;

    const lx = this.virtualListenerPos.x;
    const ly = this.virtualListenerPos.y;
    const lz = this.virtualListenerPos.z;
    const dx = px - lx;
    const dy = py - ly;
    const dz = pz - lz;
    const dist = Math.hypot(dx, dy, dz);
    const speed = Math.hypot(entry.vel.x, entry.vel.y, entry.vel.z);
    const invDist = dist > 1e-3 ? (1 / dist) : 0;
    const vRad = (dx * entry.vel.x + dy * entry.vel.y + dz * entry.vel.z) * invDist;
    const dopplerMul = Math.max(0.84, Math.min(1.22, 340 / (340 + Math.max(-240, Math.min(240, vRad)) * 0.48)));
    const diveSurge = 0.94 + Math.min(0.28, speed / 340);
    const targetRate = Math.max(0.88, Math.min(1.42, diveSurge * dopplerMul));
    const att = computeAcousticAttenuation(dist, 'massive');
    const targetGain = Math.max(0.001, 0.84 * att.distGain);
    const panVal = computeCameraPan(entry.pos, this.virtualListenerPos, this.cameraRight, dist, 0.72);
    const now = ctx.currentTime;

    try {
      entry.src.playbackRate.setTargetAtTime(targetRate, now, 0.05);
      entry.gain.gain.setTargetAtTime(targetGain, now, 0.05);
      if (entry.panner) entry.panner.pan.setTargetAtTime(panVal, now, 0.05);
      entry.lastRate = Number(targetRate.toFixed(4));
      entry.lastPan = Number(panVal.toFixed(3));
    } catch {}
  }

  stopFallingLoop(unitId) {
    const active = this.activeFalling.get(unitId);
    if (!active) return;
    active.cancelled = true;
    this.activeFalling.delete(unitId);
    try {
      const ctx = this._getAudioContext();
      const t = ctx ? ctx.currentTime : 0;
      if (active.gain) active.gain.gain.setTargetAtTime(0.0001, t, 0.015);
      if (active.src) active.src.stop(t + 0.045);
      const disconnectFn = () => {
        try { active.src?.disconnect?.(); } catch {}
        try { active.gain?.disconnect?.(); } catch {}
        try { active.panner?.disconnect?.(); } catch {}
      };
      const tid = setTimeout(() => {
        this._cleanupTimers.delete(tid);
        disconnectFn();
      }, 90);
      this._cleanupTimers.set(tid, disconnectFn);
    } catch {}
  }

  /**
   * 重磅航弹下落呼啸追踪（同屏并发 ≤ 4）：
   * 随弹体下落实时更新距离衰减、相机右向量声像与多普勒逼近音高，触地时与爆炸声无缝咬合截断。
   */
  startOrdnanceWhistle(projId, worldPos, velVec = null, { key = 'BA_Whistle_Bomb_Big', maxDurationSec = 6.5 } = {}) {
    if (!projId || !AIR_EVENT_AUDIO_CATALOG[key]) return false;
    this.stopOrdnanceWhistle(projId, { immediate: true });
    // 严格限制同屏活跃航弹呼啸上限 ≤ 4，防止齐投堆叠节点
    while (this.activeWhistles.size >= 4) {
      const oldestId = this.activeWhistles.keys().next().value;
      if (!oldestId) break;
      this.stopOrdnanceWhistle(oldestId, { immediate: true });
    }

    const dist = this._distanceToListener(worldPos);
    const att = computeAcousticAttenuation(dist, 'medium');
    const bucket = this._selectBucketName(att.effectiveBucketDistM);
    const panVal = computeCameraPan(worldPos, this.virtualListenerPos, this.cameraRight, dist, 0.72);
    const delayMs = computeAudioDelayMs(dist, { minDelayMs: 35, maxDelayMs: 650 }) + 55;
    const nowMs = performance.now();
    this.whistleStartedCount += 1;

    const rec = {
      key,
      projId,
      powerTier: 'medium',
      distMeters: Math.round(dist),
      distGain: Number(att.distGain.toFixed(4)),
      bucket,
      panVal: Number(panVal.toFixed(3)),
      delayMs,
      scheduledAtMs: nowMs,
      playAtMs: nowMs + delayMs,
      audible: att.audible,
      played: false,
    };
    this.pendingDelayed.push(rec);
    this._recordEvent(rec);

    const ctx = this._getAudioContext();
    const dest = this._getMixDestination();
    if (!ctx || !dest || !this.spatial?.enabled || this.spatial?.paused) return true;

    const bus = this._getBattlefieldBus();
    const bucketInput = bus?.[bucket]?.input || dest;

    const entry = {
      src: null,
      gain: null,
      panner: null,
      pos: { x: worldPos?.x || 0, y: worldPos?.y || 120, z: worldPos?.z || 0 },
      vel: { x: velVec?.x || 0, y: velVec?.y || 0, z: velVec?.z || 0 },
      lastRate: 1.0,
      lastPan: panVal,
      cancelled: false,
    };
    this.activeWhistles.set(projId, entry);

    const launch = (buf) => {
      if (entry.cancelled || this.activeWhistles.get(projId) !== entry) return;
      if (!buf || !this.spatial?.enabled || this.spatial?.paused || this.spatial?.disposed) return;
      try {
        const src = ctx.createBufferSource();
        src.buffer = buf;
        src.loop = buf.duration < maxDurationSec;
        const g = ctx.createGain();
        const t0 = ctx.currentTime + delayMs * 0.001;
        const initGain = Math.max(0.001, 0.62 * att.distGain);
        g.gain.setValueAtTime(0.001, t0);
        g.gain.linearRampToValueAtTime(initGain, t0 + 0.05);
        const panner = typeof ctx.createStereoPanner === 'function' ? ctx.createStereoPanner() : null;
        if (panner) {
          panner.pan.setValueAtTime(panVal, t0);
          src.connect(g);
          g.connect(panner);
          panner.connect(bucketInput);
        } else {
          src.connect(g);
          g.connect(bucketInput);
        }
        src.start(t0);
        if (Number.isFinite(maxDurationSec) && maxDurationSec > 0.2) {
          src.stop(t0 + maxDurationSec);
        }
        rec.played = true;
        entry.src = src;
        entry.gain = g;
        entry.panner = panner;
      } catch {}
    };

    const cached = this.buffers.get(key);
    if (cached) launch(cached);
    else this._loadKey(key).then(launch);
    return true;
  }

  updateOrdnanceWhistle(projId, worldPos, velVec = null) {
    const entry = this.activeWhistles.get(projId);
    if (!entry || entry.cancelled) return;
    entry.pos.x = Number(worldPos?.x) || entry.pos.x;
    entry.pos.y = Number(worldPos?.y) || entry.pos.y;
    entry.pos.z = Number(worldPos?.z) || entry.pos.z;
    if (velVec) {
      entry.vel.x = Number(velVec.x) || 0;
      entry.vel.y = Number(velVec.y) || 0;
      entry.vel.z = Number(velVec.z) || 0;
    }
    this.whistleUpdateCount += 1;

    const ctx = this._getAudioContext();
    if (!ctx || !entry.src || !entry.gain) return;

    const dx = entry.pos.x - this.virtualListenerPos.x;
    const dy = entry.pos.y - this.virtualListenerPos.y;
    const dz = entry.pos.z - this.virtualListenerPos.z;
    const dist = Math.hypot(dx, dy, dz);
    const speed = Math.hypot(entry.vel.x, entry.vel.y, entry.vel.z);
    const invDist = dist > 1e-3 ? (1 / dist) : 0;
    const vRad = (dx * entry.vel.x + dy * entry.vel.y + dz * entry.vel.z) * invDist;
    const dopplerMul = Math.max(0.88, Math.min(1.18, 340 / (340 + Math.max(-220, Math.min(220, vRad)) * 0.42)));
    const fallPitch = 0.96 + Math.min(0.14, speed / 420);
    const targetRate = Math.max(0.90, Math.min(1.28, fallPitch * dopplerMul));
    const att = computeAcousticAttenuation(dist, 'medium');
    const targetGain = Math.max(0.001, 0.66 * att.distGain);
    const panVal = computeCameraPan(entry.pos, this.virtualListenerPos, this.cameraRight, dist, 0.72);
    const now = ctx.currentTime;

    try {
      entry.src.playbackRate.setTargetAtTime(targetRate, now, 0.05);
      entry.gain.gain.setTargetAtTime(targetGain, now, 0.05);
      if (entry.panner) entry.panner.pan.setTargetAtTime(panVal, now, 0.05);
      entry.lastRate = Number(targetRate.toFixed(4));
      entry.lastPan = Number(panVal.toFixed(3));
    } catch {}
  }

  stopOrdnanceWhistle(projId, { impactPos = null, immediate = false } = {}) {
    const entry = this.activeWhistles.get(projId);
    if (!entry) return;
    entry.cancelled = true;
    this.activeWhistles.delete(projId);
    try {
      const ctx = this._getAudioContext();
      const now = ctx ? ctx.currentTime : 0;
      const stopDelaySec = (!immediate && impactPos)
        ? computeAudioDelayMs(this._distanceToListener(impactPos), { minDelayMs: 25, maxDelayMs: 1650, extendedFarMaxMs: 2600 }) * 0.001
        : 0;
      const stopAt = now + stopDelaySec;
      if (entry.gain) {
        entry.gain.gain.cancelScheduledValues(stopAt);
        entry.gain.gain.setTargetAtTime(0.0001, stopAt, 0.015);
      }
      if (entry.src) entry.src.stop(stopAt + 0.045);
      const waitMs = Math.max(90, Math.round((stopDelaySec + 0.12) * 1000));
      const disconnectFn = () => {
        try { entry.src?.disconnect?.(); } catch {}
        try { entry.gain?.disconnect?.(); } catch {}
        try { entry.panner?.disconnect?.(); } catch {}
      };
      const tid = setTimeout(() => {
        this._cleanupTimers.delete(tid);
        disconnectFn();
      }, waitMs);
      this._cleanupTimers.set(tid, disconnectFn);
    } catch {}
  }

  /**
   * 固定翼加力燃烧室烘托声层（复用 SFX_Afterburner 资产与共享分桶总线，带 180ms 防抖与 250ms 淡入淡出）。
   * 仅具备 capabilities.afterburner 的超音速机型触发；A-10C 永不调用。
   */
  syncAfterburnerAudio(unitId, worldPos, active, { gain = 0.46 } = {}) {
    if (!unitId) return false;
    if (!active) {
      if (this.activeAfterburners.has(unitId)) {
        this.stopAfterburnerAudio(unitId, { immediate: false });
      }
      return false;
    }
    const nowMs = performance.now();
    const wx = Number(worldPos?.x) || 0;
    const wy = Number(worldPos?.y) || 240;
    const wz = Number(worldPos?.z) || 0;
    const existing = this.activeAfterburners.get(unitId);
    if (existing && !existing.cancelled) {
      existing.pos.x = wx;
      existing.pos.y = wy;
      existing.pos.z = wz;
      existing.lastActiveMs = nowMs;
      const ctx = this._getAudioContext();
      if (ctx && existing.gain) {
        const dist = this._distanceToListener(existing.pos);
        const att = computeAcousticAttenuation(dist, 'heavy');
        const targetGain = Math.max(0.001, gain * att.distGain);
        try {
          existing.gain.gain.setTargetAtTime(targetGain, ctx.currentTime, 0.08);
        } catch {}
      }
      if (this.spatial) this.spatial.afterburnerAudioActive = true;
      return true;
    }

    const dist = this._distanceToListener({ x: wx, y: wy, z: wz });
    const att = computeAcousticAttenuation(dist, 'heavy');
    const bucket = this._selectBucketName(att.effectiveBucketDistM);
    const panVal = computeCameraPan({ x: wx, y: wy, z: wz }, this.virtualListenerPos, this.cameraRight, dist, 0.72);
    const panLane = selectStaticPanLane(panVal);
    const entry = {
      src: null,
      gain: null,
      pos: { x: wx, y: wy, z: wz },
      lastActiveMs: nowMs,
      cancelled: false,
    };
    this.activeAfterburners.set(unitId, entry);
    if (this.spatial) this.spatial.afterburnerAudioActive = true;

    const ctx = this._getAudioContext();
    const dest = this._getMixDestination();
    if (!ctx || !dest || !this.spatial?.enabled || this.spatial?.paused) return true;
    const bus = this._getBattlefieldBus();
    const bucketInput = bus?.[bucket]?.panInputs?.[panLane] || bus?.[bucket]?.input || dest;

    const launch = (buf) => {
      if (entry.cancelled || this.activeAfterburners.get(unitId) !== entry) return;
      if (!buf || !this.spatial?.enabled || this.spatial?.paused || this.spatial?.disposed) return;
      try {
        const src = ctx.createBufferSource();
        src.buffer = buf;
        src.loop = true;
        const g = ctx.createGain();
        const t0 = ctx.currentTime;
        const targetGain = Math.max(0.002, gain * att.distGain);
        g.gain.setValueAtTime(0.0001, t0);
        g.gain.linearRampToValueAtTime(targetGain, t0 + 0.25);
        src.connect(g);
        g.connect(bucketInput);
        src.start(t0);
        entry.src = src;
        entry.gain = g;
      } catch {}
    };

    const cached = this.buffers.get('SFX_Afterburner');
    if (cached) launch(cached);
    else this._loadKey('SFX_Afterburner').then(launch);
    return true;
  }

  stopAfterburnerAudio(unitId, { immediate = false } = {}) {
    const entry = this.activeAfterburners.get(unitId);
    if (!entry) return;
    if (!immediate && (performance.now() - (entry.lastActiveMs || 0)) < this.afterburnerDebounceMs) {
      return;
    }
    entry.cancelled = true;
    this.activeAfterburners.delete(unitId);
    if (this.spatial) {
      this.spatial.afterburnerAudioActive = this.activeAfterburners.size > 0;
    }
    try {
      const ctx = this._getAudioContext();
      const now = ctx ? ctx.currentTime : 0;
      if (entry.gain) {
        entry.gain.gain.cancelScheduledValues(now);
        entry.gain.gain.setTargetAtTime(0.0001, now, immediate ? 0.015 : 0.08);
      }
      if (entry.src) entry.src.stop(now + (immediate ? 0.04 : 0.26));
      const waitMs = immediate ? 80 : 320;
      const disconnectFn = () => {
        try { entry.src?.disconnect?.(); } catch {}
        try { entry.gain?.disconnect?.(); } catch {}
      };
      const tid = setTimeout(() => {
        this._cleanupTimers.delete(tid);
        disconnectFn();
      }, waitMs);
      this._cleanupTimers.set(tid, disconnectFn);
    } catch {}
  }

  /**
   * 底座预留程序化双响音爆（仅超音速机型调用，12s 冷却；A-10C 永不调用）。
   * 已消除运行期 AudioBuffer 与 BiquadFilterNode 分配，改用预建 sonicBoomNoiseBuf 直入分桶。
   */
  playSonicBoom(worldPos) {
    const nowMs = performance.now();
    if (nowMs - this.lastSonicBoomMs < 12000) return false;
    this.lastSonicBoomMs = nowMs;
    this.sonicBoomPlayedCount += 1;
    this.injectMicroShake(worldPos, 'heavy');
    const dist = this._distanceToListener(worldPos);
    const att = computeAcousticAttenuation(dist, 'massive');
    const bucket = this._selectBucketName(att.effectiveBucketDistM);
    const panVal = computeCameraPan(worldPos, this.virtualListenerPos, this.cameraRight, dist, 0.72);
    const panLane = selectStaticPanLane(panVal);
    const delayMs = computeAudioDelayMs(dist);
    this._recordEvent({
      key: 'PROCEDURAL_SONIC_BOOM',
      powerTier: 'massive',
      distMeters: Math.round(dist),
      distGain: Number(att.distGain.toFixed(4)),
      bucket,
      panVal: Number(panVal.toFixed(3)),
      panLane,
      delayMs,
      scheduledAtMs: nowMs,
      playAtMs: nowMs + delayMs,
      audible: att.audible,
      played: att.audible,
    });

    if (!att.audible) return true;
    const ctx = this._getAudioContext();
    const dest = this._getMixDestination();
    if (!ctx || !dest || !this.spatial?.enabled || this.spatial?.paused) return true;

    this._ensureStaticBuffers(ctx);
    const bus = this._getBattlefieldBus();
    const bucketObj = bus?.[bucket];
    const bucketInput = bucketObj?.panInputs?.[panLane] || bucketObj?.input || dest;

    try {
      const baseDelaySec = delayMs * 0.001;
      const triggerPulse = (offsetSec, rate, durSec, peakGain) => {
        const src = ctx.createBufferSource();
        src.buffer = this.sonicBoomNoiseBuf;
        src.playbackRate.value = rate;
        const g = ctx.createGain();
        const peak = Math.max(0.002, peakGain * att.distGain);
        g.gain.setValueAtTime(peak, ctx.currentTime + baseDelaySec + offsetSec);
        g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + baseDelaySec + offsetSec + durSec);
        src.connect(g);
        g.connect(bucketInput);
        src.start(ctx.currentTime + baseDelaySec + offsetSec);
        src.stop(ctx.currentTime + baseDelaySec + offsetSec + durSec);
      };
      triggerPulse(0.0, 0.72, 0.16, 0.75);
      triggerPulse(0.18, 1.05, 0.28, 0.55);
      this.bucketStats[bucket] += 2;
    } catch {}
    return true;
  }

  getTelemetry() {
    this.spatial?.sampleAnalyserPeak?.();
    const loadedNames = [...new Set([...this.buffers.keys(), ...this.rawBuffers.keys()])];
    const activeGau8Details = [];
    for (const [unitId, entry] of this.activeGau8.entries()) {
      if (entry?.voice) {
        const snap = entry.voice.getSnapshot();
        activeGau8Details.push({ unitId, burstId: entry.burstId, ...snap });
        this.lastGau8Snapshot = snap;
      }
    }
    const bus = this._getBattlefieldBus();
    return {
      unlocked: Boolean(this.spatial?.runtime?.unlocked),
      loadedKeysCount: loadedNames.length,
      decodedKeysCount: this.buffers.size,
      loadedAssets: loadedNames,
      pendingDelayedCount: this.pendingDelayed.length,
      recentEvents: this.recentEvents.slice(-24),
      microShakeMagnitude: Number(this.lastShakeMag.toFixed(4)),
      sonicBoomPlayedCount: this.sonicBoomPlayedCount,
      afterburnerAudioActive: this.activeAfterburners.size > 0,
      activeAfterburnerCount: this.activeAfterburners.size,
      busInitialized: Boolean(bus),
      convolverCount: bus ? bus.convolverCount : 0,
      panLanesPerBucket: bus ? bus.panLanesPerBucket : 0,
      subPunchRoute: bus ? bus.subPunchRoute : null,
      virtualListenerPos: {
        x: Number(this.virtualListenerPos.x.toFixed(1)),
        y: Number(this.virtualListenerPos.y.toFixed(1)),
        z: Number(this.virtualListenerPos.z.toFixed(1)),
      },
      cameraRight: {
        x: Number(this.cameraRight.x.toFixed(4)),
        y: Number(this.cameraRight.y.toFixed(4)),
        z: Number(this.cameraRight.z.toFixed(4)),
      },
      outOfRangeCulledCount: this.outOfRangeCulledCount,
      bucketStats: { ...this.bucketStats },
      dedupDroppedCount: this.dedupDroppedCount,
      subPunchCount: this.subPunchCount,
      nodeAllocationStats: {
        transientEvents: this.nodeAllocationStats.transientEvents,
        lastEventNodes: { ...this.nodeAllocationStats.lastEventNodes },
      },
      activeGau8Count: this.activeGau8.size,
      activeGau8Details,
      lastGau8Snapshot: this.lastGau8Snapshot,
      retiringVoicesCount: this.retiringVoices.size,
      activeFallingCount: this.activeFalling.size,
      fallingUpdateCount: this.fallingUpdateCount,
      activeWhistleCount: this.activeWhistles.size,
      whistleStartedCount: this.whistleStartedCount,
      whistleUpdateCount: this.whistleUpdateCount,
      engineDuckReady: Boolean(this.spatial?.mix?.engineDuckBus),
      duckTriggerCount: this.spatial?.duckTriggerCount ?? 0,
      lastDuckDepth: this.spatial?.lastDuckDepth ?? 1.0,
      peak: Number((this.spatial?.peak ?? 0).toFixed(6)),
      maxObservedPeak: Number((this.spatial?.maxObservedPeak ?? 0).toFixed(6)),
    };
  }

  dispose() {
    for (const id of [...this.activeGau8.keys()]) {
      const entry = this.activeGau8.get(id);
      this.activeGau8.delete(id);
      try { entry?.voice?.dispose?.(); } catch {}
    }
    for (const tid of this._retireTimers) {
      clearTimeout(tid);
    }
    this._retireTimers.clear();
    for (const [tid, disconnectFn] of [...this._cleanupTimers.entries()]) {
      clearTimeout(tid);
      try { disconnectFn?.(); } catch {}
    }
    this._cleanupTimers.clear();
    for (const v of [...this.retiringVoices]) {
      try { v?.dispose?.(); } catch {}
    }
    this.retiringVoices.clear();
    const ctx = this._getAudioContext();
    const now = ctx ? ctx.currentTime : 0;
    for (const active of [...this.activeFalling.values()]) {
      active.cancelled = true;
      try { active.src?.stop?.(now); } catch {}
      try { active.src?.disconnect?.(); } catch {}
      try { active.gain?.disconnect?.(); } catch {}
      try { active.panner?.disconnect?.(); } catch {}
    }
    this.activeFalling.clear();
    for (const entry of [...this.activeWhistles.values()]) {
      entry.cancelled = true;
      try { entry.src?.stop?.(now); } catch {}
      try { entry.src?.disconnect?.(); } catch {}
      try { entry.gain?.disconnect?.(); } catch {}
      try { entry.panner?.disconnect?.(); } catch {}
    }
    this.activeWhistles.clear();
    for (const entry of [...this.activeAfterburners.values()]) {
      entry.cancelled = true;
      try { entry.src?.stop?.(now); } catch {}
      try { entry.src?.disconnect?.(); } catch {}
      try { entry.gain?.disconnect?.(); } catch {}
    }
    this.activeAfterburners.clear();
    if (this.spatial) {
      this.spatial.afterburnerAudioActive = false;
    }
    this.buffers.clear();
    this.rawBuffers.clear();
    this.realGunSlices.clear();
    this._flushRawPromise = null;
    this.pendingDelayed.length = 0;
    this.lastEventTimeByKey.clear();
    this.concurrencyWindows.clear();
    this.gau8Resources = null;
    this.subPunchBuf = null;
    this.sonicBoomNoiseBuf = null;
    this.staticBufCtx = null;
  }
}

/**
 * 全战场通用瞬态与连续武器物理声学事件引擎别名（地面/旋翼/固定翼/丧尸统一复用入口）。
 */
export const BattlefieldEventAudioEngine = AirEventAudioEngine;
