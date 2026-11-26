/**
 * Scavenger A-10C GAU-8/A 30mm 四层物理混合声学引擎（V2）。
 *
 * 设计依据：对照 DCS World 官方 GAU-8/A 实机声学模型（BV1mp4y1r7hE）近/中/远三组实测频谱：
 * - 近距（<220m）：质心 650–900Hz，强 70/140/278Hz 倍频列 + 20–30% 高频火药爆震与转膛冲击，收尾干脆；
 * - 中距（220–650m）：质心 ~610Hz，LPF ~1800–2800Hz，140/280Hz 谐波最凸显（经典“拉链电锯 BRRRRT”），
 *   地面 30mm 弹着声先于炮声到达；
 * - 远距（650–1600m）：质心 140–220Hz，LPF 300–550Hz，>1kHz 跌破 2%，85% 能量聚于 50–150Hz 低沉号角咆哮，
 *   停火后拖出 2.5–3.6s 空域滚雷漫反射尾音。
 *
 * 复用范围：
 * - 试听台：src/音效索引/空战音效对比试听.html（实时拖拽距离/多普勒/四层配比试听）
 * - 实战引擎：后续确认定稿后由 src/程序化地图/载具空间音效.mjs 直接同源复用，零双份漂移。
 */

function clamp(v, lo, hi) {
  return Math.min(hi, Math.max(lo, Number.isFinite(v) ? v : lo));
}

function lerp(a, b, t) {
  return a + (b - a) * clamp(t, 0, 1);
}

function smoothstep(edge0, edge1, x) {
  const t = clamp((x - edge0) / Math.max(1e-6, edge1 - edge0), 0, 1);
  return t * t * (3 - 2 * t);
}

/**
 * 确定性伪随机数（避免跨会话音色漂移，同时保持自然微抖动）。
 */
function mulberry32(seed) {
  let a = seed >>> 0;
  return function next() {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export const DEFAULT_GAU8_ACOUSTIC_PRESET = Object.freeze({
  presetId: 'gau8_30mm',
  rpm: 3970,
  caliberMm: 30,
  barrelCount: 7,
  shockStrength: 0.95,
  realTextureStrength: 0.75,
  thunderTailStrength: 0.95,
  primaryRealKey: 'SFX_AIR_PLANEGUN_30mm_GSH30_RU',
  secondaryRealKey: 'SFX_AIR_AAG_25mm_GAU12_US',
  impactKey: 'SFX_Explosion_20-30mm_SupFAST_HE',
  withMachCrack: true,
});

function computeBandLabel(d) {
  if (d < 200) return '近距贴脸';
  if (d < 340) return '中近俯冲';
  if (d < 650) return '经典中距';
  if (d < 980) return '中远空域';
  return '超远山谷';
}

/**
 * 内部零 GC 填充距离声学参数对象。
 */
export function fillGau8DistanceProfile(distanceMeters, out, {
  maxDelaySec = 1.65,
  realismDelay = true,
} = {}) {
  const d = clamp(Number(distanceMeters) || 400, 60, 1600);
  const logNorm = clamp(Math.log(d / 80) / Math.log(1600 / 80), 0, 1);

  const rawDelay = d / 340;
  const soundDelaySec = realismDelay
    ? clamp(rawDelay, 0.05, maxDelaySec)
    : clamp(rawDelay * 0.45, 0.05, 0.55);

  const lpfCutoffHz = Math.round(9200 * Math.pow(260 / 9200, Math.pow(logNorm, 1.72)));
  const farFactor = smoothstep(0.42, 0.96, logNorm);
  const lowShelfDb = Number(lerp(0.0, 3.6, farFactor).toFixed(2));
  const presenceDb = Number(lerp(3.5, -2.5, smoothstep(0.32, 0.88, logNorm)).toFixed(2));

  const shockLayerWeight = Number(lerp(0.64, 0.84, farFactor).toFixed(3));
  const realLayerWeight = Number(lerp(0.96, 0.36, farFactor).toFixed(3));

  const dryGain = Number(lerp(0.92, 0.56, farFactor).toFixed(3));
  const wetGain = Number(lerp(0.14, 0.50, smoothstep(0.14, 0.92, logNorm)).toFixed(3));
  const wetLpfHz = Math.round(lerp(1800, 340, smoothstep(0.18, 0.90, logNorm)));

  const overallGain = Number(lerp(0.98, 0.50, smoothstep(0.15, 1.0, logNorm)).toFixed(3));
  const basePitchScale = Number(lerp(1.02, 0.88, smoothstep(0.45, 0.96, logNorm)).toFixed(3));

  out.distanceMeters = Math.round(d);
  out.logNorm = Number(logNorm.toFixed(3));
  out.bandLabel = computeBandLabel(d);
  out.soundDelaySec = Number(soundDelaySec.toFixed(3));
  out.lpfCutoffHz = lpfCutoffHz;
  out.lowShelfDb = lowShelfDb;
  out.presenceDb = presenceDb;
  out.shockLayerWeight = shockLayerWeight;
  out.realLayerWeight = realLayerWeight;
  out.dryGain = dryGain;
  out.wetGain = wetGain;
  out.wetLpfHz = wetLpfHz;
  out.overallGain = overallGain;
  out.basePitchScale = basePitchScale;
  return out;
}

/**
 * 纯规则：按相机到机头的 3D 距离（m）连续计算 GAU-8 近/中/远声学参数。
 */
export function computeGau8DistanceProfile(distanceMeters, opts = {}) {
  return fillGau8DistanceProfile(distanceMeters, {}, opts);
}

/**
 * 构建非对称软削波曲线：同时生成强 2 次（130Hz）与 3/4 次（195/260Hz）铜管谐波，无硬削波毛刺。
 */
export function buildAsymmetricSaturatorCurve(drive = 2.6, samples = 2048) {
  const curve = new Float32Array(samples);
  for (let i = 0; i < samples; i += 1) {
    const x = (i * 2) / (samples - 1) - 1;
    // 轻微正向偏置 + tanh 软饱和，模拟真实炮口超压非对称波形
    const biased = x * drive + 0.14 * x * x;
    curve[i] = Math.tanh(biased) * 0.94;
  }
  return curve;
}

const shockLoopBufferCache = new WeakMap();

/**
 * 构建参数化无缝循环「机炮炮口 N-Wave 激波脉冲列」立体声缓冲（按 ctx 与参数签名缓存）。
 * - 支持 rpm / caliberMm / barrelCount（默认 GAU-8/A: 3970 RPM / 30mm / 7 管）；
 * - 未来 F-16C M61（6000 RPM / 20mm / 6 管）、Su-25 GSH-30（3000 RPM / 30mm / 2 管）仅改配置即可零代码复用。
 */
export function buildShockPulseLoopBuffer(ctx, preset = DEFAULT_GAU8_ACOUSTIC_PRESET) {
  const rpm = clamp(Number(preset?.rpm) || 3970, 600, 9000);
  const caliberMm = clamp(Number(preset?.caliberMm) || 30, 12.7, 57);
  const barrelCount = Math.max(1, Math.min(12, Math.round(Number(preset?.barrelCount) || 7)));
  const sr = ctx.sampleRate;
  const sig = `${sr}|${rpm}|${caliberMm}|${barrelCount}`;

  let ctxMap = shockLoopBufferCache.get(ctx);
  if (!ctxMap) {
    ctxMap = new Map();
    shockLoopBufferCache.set(ctx, ctxMap);
  }
  if (ctxMap.has(sig)) {
    return ctxMap.get(sig);
  }

  const durationSec = 1.60;
  const total = Math.round(sr * durationSec);
  const buf = ctx.createBuffer(2, total, sr);
  const rand = mulberry32(20260410 ^ Math.round(rpm) ^ (Math.round(caliberMm * 10) << 8) ^ (barrelCount << 16));

  // 基础射速（默认 3970 RPM -> 66.17Hz，30mm 为基准口径）
  const fireFreqHz = rpm / 60;
  const basePeriodSec = 1 / fireFreqHz;
  const pulseCount = Math.max(8, Math.floor(durationSec / basePeriodSec));
  const exactPeriod = durationSec / pulseCount; // 严格整周期便于首尾无缝循环
  const caliberScale = clamp(caliberMm / 30, 0.55, 1.85);
  const hornBaseHz = fireFreqHz;
  const pulseLen = Math.round(clamp(0.0185 * Math.sqrt(caliberScale), 0.011, 0.028) * sr);
  const riseDurSec = 0.00045 * Math.sqrt(caliberScale);

  const L = buf.getChannelData(0);
  const R = buf.getChannelData(1);

  for (let p = 0; p < pulseCount; p += 1) {
    const jitterSec = (rand() - 0.5) * 0.00075;
    const centerSec = p * exactPeriod + jitterSec;
    const startIdx = Math.floor(centerSec * sr);

    // 转膛循环轻微强弱起伏（每 barrelCount 发微小呼吸感）
    const barrelMod = 0.88 + 0.12 * Math.sin((p / barrelCount) * Math.PI * 2 + 0.4) + (rand() - 0.5) * 0.12;
    const stereoSkew = (rand() - 0.5) * 0.14;
    const ampL = barrelMod * (1 + stereoSkew);
    const ampR = barrelMod * (1 - stereoSkew);

    let lpNoiseL = 0;
    let lpNoiseR = 0;
    for (let k = 0; k < pulseLen; k += 1) {
      const idx = (startIdx + k + total) % total;
      const t = k / sr;

      // 1) N-wave 超压冲击前沿（陡升 + 负压抽吸回弹）
      const rise = Math.min(1, t / riseDurSec);
      const blast = rise * (
        1.15 * Math.exp(-t / (0.0017 * caliberScale)) * Math.cos(2 * Math.PI * (135 / Math.sqrt(caliberScale)) * t)
        - 0.52 * Math.exp(-t / (0.0046 * caliberScale)) * Math.sin(2 * Math.PI * (68 / Math.sqrt(caliberScale)) * t)
      );

      // 2) 炮膛基频号角体（基频 + 2x + 4x 瞬态锁相共振）
      const horn = Math.exp(-t / (0.0078 * caliberScale)) * (
        0.58 * Math.sin(2 * Math.PI * hornBaseHz * t + 0.25)
        + 0.42 * Math.sin(2 * Math.PI * hornBaseHz * 2 * t + 0.6)
        + 0.20 * Math.sin(2 * Math.PI * hornBaseHz * 4 * t + 0.1)
      );

      // 3) 单发高压火药气体瞬态爆裂噪点（微低通避免尖刺白噪）
      const nL = rand() * 2 - 1;
      const nR = rand() * 2 - 1;
      lpNoiseL = lpNoiseL * 0.58 + nL * 0.42;
      lpNoiseR = lpNoiseR * 0.58 + nR * 0.42;
      const crackEnv = Math.exp(-t / 0.0022);

      L[idx] += (blast * 0.64 + horn * 0.56 + lpNoiseL * crackEnv * 0.48) * ampL;
      R[idx] += (blast * 0.64 + horn * 0.56 + lpNoiseR * crackEnv * 0.48) * ampR;
    }
  }

  // 归一化峰值至 ~0.88，留足安全动态余量
  let peak = 1e-6;
  for (let i = 0; i < total; i += 1) {
    const a = Math.max(Math.abs(L[i]), Math.abs(R[i]));
    if (a > peak) peak = a;
  }
  const scale = 0.88 / peak;
  for (let i = 0; i < total; i += 1) {
    L[i] *= scale;
    R[i] *= scale;
  }
  ctxMap.set(sig, buf);
  return buf;
}

/**
 * 构建 0.9s 超音速 30mm 弹道噼啪裂响（仅在地面靶区弹着阶段混入，凸显“先撕裂、后轰鸣”）。
 */
export function buildSupersonicCrackBuffer(ctx, { durationSec = 0.90, count = 42, seed = 991030 } = {}) {
  const sr = ctx.sampleRate;
  const dur = clamp(Number(durationSec) || 0.90, 0.2, 2.5);
  const total = Math.round(sr * dur);
  const buf = ctx.createBuffer(2, total, sr);
  const rand = mulberry32(seed);
  const L = buf.getChannelData(0);
  const R = buf.getChannelData(1);

  const pulseCount = Math.max(6, Math.round(count));
  for (let i = 0; i < pulseCount; i += 1) {
    const t0 = (i / pulseCount) * Math.max(0.1, dur - 0.04) + (rand() - 0.5) * 0.014;
    const startIdx = Math.max(0, Math.floor(t0 * sr));
    const pan = (rand() - 0.5) * 0.7;
    const amp = 0.45 + rand() * 0.55;
    const len = Math.round(0.011 * sr);
    let prev = 0;
    for (let k = 0; k < len && startIdx + k < total; k += 1) {
      const t = k / sr;
      const white = rand() * 2 - 1;
      // 一阶高通突出 1.2–4.5kHz 鞭裂感，去低频避免抢占炮声
      const hp = white - prev * 0.82;
      prev = white;
      const env = Math.exp(-t / 0.0018) * (1 - Math.exp(-t / 0.00018));
      const v = hp * env * amp;
      L[startIdx + k] += v * (1 - pan * 0.5);
      R[startIdx + k] += v * (1 + pan * 0.5);
    }
  }

  let peak = 1e-6;
  for (let i = 0; i < total; i += 1) {
    peak = Math.max(peak, Math.abs(L[i]), Math.abs(R[i]));
  }
  const scale = 0.85 / peak;
  for (let i = 0; i < total; i += 1) {
    L[i] *= scale;
    R[i] *= scale;
  }
  return buf;
}

/**
 * 构建 3.6s 立体声空域地形漫反射滚雷脉冲响应（Convolver IR）。
 * 特点：随回响时间拉长，内部低通截止频率逐步向下扫描（越晚越低沉），精准还原山谷/平原滚雷余震。
 */
export function buildAirspaceThunderIR(ctx, { durationSec = 3.60, seed = 303900 } = {}) {
  const sr = ctx.sampleRate;
  const dur = clamp(Number(durationSec) || 3.60, 0.8, 6.0);
  const total = Math.round(sr * dur);
  const buf = ctx.createBuffer(2, total, sr);
  const rand = mulberry32(seed);

  for (let ch = 0; ch < 2; ch += 1) {
    const dst = buf.getChannelData(ch);
    let lp1 = 0;
    let lp2 = 0;
    // 早期离散地面/山脊反射脉冲
    const earlyMs = ch === 0 ? [55, 118, 210, 340, 520] : [68, 134, 245, 390, 580];
    for (const ms of earlyMs) {
      const idx = Math.round((ms / 1000) * sr);
      if (idx < total) dst[idx] += (0.55 - ms / 1400) * (rand() > 0.5 ? 1 : -1);
    }

    for (let i = 0; i < total; i += 1) {
      const t = i / sr;
      // 前 35ms 平滑起音（让直达声先冲出来）
      const preEnv = Math.min(1, t / 0.045);
      // 双段指数衰减：前 1.2s 饱满漫反射 + 后 2.4s 悠长低频远雷
      const decay = 0.68 * Math.exp(-t / 0.82) + 0.42 * Math.exp(-t / 1.95);
      // 随时间拉长，滤波系数不断增大（越来越闷、越来越沉）
      const alpha = lerp(0.84, 0.985, smoothstep(0.05, 2.8, t));
      const n = (rand() * 2 - 1) * preEnv * decay + dst[i] * 0.45;
      lp1 = lp1 * alpha + n * (1 - alpha);
      lp2 = lp2 * (alpha * 0.98) + lp1 * (1 - alpha * 0.98);
      dst[i] = lp2;
    }

    // 末尾 180ms 平滑归零防尾端咔哒声
    const tailFade = Math.round(0.18 * sr);
    for (let k = 0; k < tailFade; k += 1) {
      dst[total - 1 - k] *= k / tailFade;
    }
  }

  // 归一化能量（控制 Convolver 增益稳定可控）
  let rmsSum = 0;
  const L = buf.getChannelData(0);
  const R = buf.getChannelData(1);
  for (let i = 0; i < total; i += 1) {
    rmsSum += L[i] * L[i] + R[i] * R[i];
  }
  const rms = Math.sqrt(rmsSum / (2 * total)) + 1e-6;
  const norm = 0.095 / rms;
  for (let i = 0; i < total; i += 1) {
    L[i] = clamp(L[i] * norm, -0.95, 0.95);
    R[i] = clamp(R[i] * norm, -0.95, 0.95);
  }
  return buf;
}

/**
 * 裁剪实录素材的无缝循环主段与尾段（供第 2 层实录火药纹理使用）。
 */
export function sliceRealGunTrack(ctx, srcBuf, { startSec, endSec, loopStartOffset, loopEndOffset, releaseStartSec, releaseEndSec }) {
  if (!ctx || !srcBuf) return null;
  const sr = srcBuf.sampleRate;
  const chCount = srcBuf.numberOfChannels;
  const total = srcBuf.length;

  const sStart = Math.min(total - 1, Math.max(0, Math.round(startSec * sr)));
  const sEnd = Math.min(total, Math.max(sStart + Math.round(0.12 * sr), Math.round(endSec * sr)));
  const sLen = sEnd - sStart;
  const sustainBuf = ctx.createBuffer(chCount, sLen, sr);

  const loopStartSec = Math.max(0.01, Math.min(sLen / sr - 0.08, loopStartOffset));
  const loopEndSec = Math.max(loopStartSec + 0.06, Math.min(sLen / sr - 0.01, loopEndOffset));
  const xfadeSamples = Math.min(Math.round(0.022 * sr), Math.round((loopEndSec - loopStartSec) * 0.25 * sr));
  const loopStartIdx = Math.round(loopStartSec * sr);
  const loopEndIdx = Math.round(loopEndSec * sr);

  for (let c = 0; c < chCount; c += 1) {
    const src = srcBuf.getChannelData(c);
    const dst = sustainBuf.getChannelData(c);
    dst.set(src.subarray(sStart, sEnd), 0);
    for (let i = 0; i < xfadeSamples; i += 1) {
      const t = i / Math.max(1, xfadeSamples);
      const tailIdx = loopEndIdx - xfadeSamples + i;
      const headIdx = loopStartIdx + i;
      if (tailIdx >= 0 && tailIdx < sLen && headIdx >= 0 && headIdx < sLen) {
        dst[tailIdx] = dst[tailIdx] * Math.cos(t * Math.PI * 0.5) + dst[headIdx] * Math.sin(t * Math.PI * 0.5);
      }
    }
  }

  const rStart = Math.min(total - 1, Math.max(0, Math.round(releaseStartSec * sr)));
  const rEnd = Math.min(total, Math.max(rStart + Math.round(0.15 * sr), Math.round((releaseEndSec || srcBuf.duration) * sr)));
  const rLen = rEnd - rStart;
  const releaseBuf = ctx.createBuffer(chCount, rLen, sr);
  const fadeInSamples = Math.min(rLen, Math.round(0.012 * sr));
  for (let c = 0; c < chCount; c += 1) {
    const src = srcBuf.getChannelData(c);
    const dst = releaseBuf.getChannelData(c);
    dst.set(src.subarray(rStart, rEnd), 0);
    for (let i = 0; i < fadeInSamples; i += 1) {
      dst[i] *= i / Math.max(1, fadeInSamples);
    }
  }
  return { sustainBuf, releaseBuf, loopStartSec, loopEndSec };
}

/**
 * 一次性预构建物理声学共享资源（支持外部传入预建 reverbIrBuf 避免重复计算，支持 preset 参数化）。
 */
export function createGau8AcousticResources(ctx, {
  gsh30Buf = null,
  gau12Buf = null,
  impactBuf = null,
  reverbIrBuf = null,
  preset = DEFAULT_GAU8_ACOUSTIC_PRESET,
} = {}) {
  return {
    shockPulseLoopBuf: buildShockPulseLoopBuffer(ctx, preset),
    crackBurstBuf: buildSupersonicCrackBuffer(ctx),
    reverbIrBuf: reverbIrBuf || buildAirspaceThunderIR(ctx),
    satCurve: buildAsymmetricSaturatorCurve(2.65),
    gsh30Slice: gsh30Buf ? sliceRealGunTrack(ctx, gsh30Buf, {
      startSec: 0.20,
      endSec: 0.64,
      loopStartOffset: 0.05,
      loopEndOffset: 0.40,
      releaseStartSec: 0.58,
      releaseEndSec: 2.95,
    }) : null,
    gau12Slice: gau12Buf ? sliceRealGunTrack(ctx, gau12Buf, {
      startSec: 0.20,
      endSec: 1.18,
      loopStartOffset: 0.08,
      loopEndOffset: 0.92,
      releaseStartSec: 1.12,
      releaseEndSec: 2.60,
    }) : null,
    impactBuf,
  };
}

/**
 * 触发一次完整的 GAU-8/A V2 四层物理混合扫射。
 * 支持在播放期间调用 handle.updateDistance(d, dopplerRate, pan) 实时平滑改变距离与多普勒音高。
 */
export function triggerGau8AcousticBurst(ctx, destination, resources, {
  distanceMeters = 420,
  durationSec = 1.8,
  withGroundImpact = true,
  realismDelay = true,
  shockStrength = 0.85,
  realTextureStrength = 0.72,
  thunderTailStrength = 0.85,
  flybyDoppler = false,
  onNodeCreated = null,
} = {}) {
  const register = (node) => {
    if (typeof onNodeCreated === 'function') onNodeCreated(node);
    return node;
  };

  const profile = computeGau8DistanceProfile(distanceMeters, { realismDelay });
  const t0 = ctx.currentTime + 0.025;
  const gunDelay = withGroundImpact ? profile.soundDelaySec : 0.01;
  const gunStart = t0 + gunDelay;
  const gunStop = gunStart + Math.max(0.35, durationSec);

  // ==================== 1. 构建共享距离声学处理链 ====================
  const preSum = ctx.createGain();
  preSum.gain.value = 1.0;

  // 非对称饱和（强化 65/130/195/260Hz 铜管谐波骨架）
  const shaper = ctx.createWaveShaper();
  shaper.curve = resources.satCurve;
  shaper.oversample = '2x';

  // 双级串联低通（24dB/oct 空气高频吸收）
  const lpf1 = ctx.createBiquadFilter();
  lpf1.type = 'lowpass';
  lpf1.Q.value = 0.72;
  lpf1.frequency.setValueAtTime(profile.lpfCutoffHz, gunStart);

  const lpf2 = ctx.createBiquadFilter();
  lpf2.type = 'lowpass';
  lpf2.Q.value = 0.65;
  lpf2.frequency.setValueAtTime(Math.min(14000, profile.lpfCutoffHz * 1.18), gunStart);

  // 远距胸腔号角低频补偿（135Hz lowshelf）
  const lowShelf = ctx.createBiquadFilter();
  lowShelf.type = 'lowshelf';
  lowShelf.frequency.value = 135;
  lowShelf.gain.setValueAtTime(profile.lowShelfDb, gunStart);

  // 中高频火药撕裂激励（2.2kHz peaking）
  const presence = ctx.createBiquadFilter();
  presence.type = 'peaking';
  presence.frequency.value = 2200;
  presence.Q.value = 0.85;
  presence.gain.setValueAtTime(profile.presenceDb, gunStart);

  // 立体声像（飞越模式动态左右划过）
  const panner = typeof ctx.createStereoPanner === 'function' ? ctx.createStereoPanner() : null;
  if (panner) panner.pan.setValueAtTime(flybyDoppler ? -0.48 : 0, gunStart);

  // 干/湿（空域滚雷 Convolver）双支路
  const dryBus = ctx.createGain();
  dryBus.gain.setValueAtTime(profile.dryGain, gunStart);

  const convolver = ctx.createConvolver();
  convolver.buffer = resources.reverbIrBuf;
  const wetLpf = ctx.createBiquadFilter();
  wetLpf.type = 'lowpass';
  wetLpf.Q.value = 0.6;
  wetLpf.frequency.setValueAtTime(profile.wetLpfHz, gunStart);

  const wetBus = ctx.createGain();
  wetBus.gain.setValueAtTime(profile.wetGain * clamp(thunderTailStrength, 0, 1.5), gunStart);

  const masterOut = ctx.createGain();
  masterOut.gain.setValueAtTime(profile.overallGain, gunStart);

  preSum.connect(shaper);
  shaper.connect(lpf1);
  lpf1.connect(lpf2);
  lpf2.connect(lowShelf);
  lowShelf.connect(presence);

  const postTone = panner || presence;
  if (panner) presence.connect(panner);

  postTone.connect(dryBus);
  dryBus.connect(masterOut);

  postTone.connect(convolver);
  convolver.connect(wetLpf);
  wetLpf.connect(wetBus);
  wetBus.connect(masterOut);

  masterOut.connect(destination);

  // ==================== 2. 第 1 层：65Hz 物理激波号角核心 ====================
  const rateSources = [];
  const shockSrc = register(ctx.createBufferSource());
  shockSrc.buffer = resources.shockPulseLoopBuf;
  shockSrc.loop = true;
  shockSrc.playbackRate.setValueAtTime(profile.basePitchScale, gunStart);
  const shockGain = ctx.createGain();
  const shockTargetGain = 0.78 * profile.shockLayerWeight * clamp(shockStrength, 0, 1.5);
  shockGain.gain.setValueAtTime(0.001, gunStart);
  shockGain.gain.linearRampToValueAtTime(shockTargetGain, gunStart + 0.016);
  shockGain.gain.setValueAtTime(shockTargetGain, gunStop - 0.025);
  shockGain.gain.linearRampToValueAtTime(0.001, gunStop + 0.055);
  shockSrc.connect(shockGain);
  shockGain.connect(preSum);
  shockSrc.start(gunStart);
  shockSrc.stop(gunStop + 0.07);
  rateSources.push({ src: shockSrc, baseRate: 1.0 });

  // ==================== 3. 第 2 层：实采火药爆震与气体撕裂（GSH30 + GAU12） ====================
  const realMul = profile.realLayerWeight * clamp(realTextureStrength, 0, 1.5);
  const realGains = [];
  if (resources.gsh30Slice && realMul > 0.01) {
    const s30 = register(ctx.createBufferSource());
    s30.buffer = resources.gsh30Slice.sustainBuf;
    s30.loop = true;
    s30.loopStart = resources.gsh30Slice.loopStartSec;
    s30.loopEnd = resources.gsh30Slice.loopEndSec;
    s30.playbackRate.setValueAtTime(0.86 * profile.basePitchScale, gunStart);

    const g30 = ctx.createGain();
    const g30Val = 0.56 * realMul;
    g30.gain.setValueAtTime(0.001, gunStart);
    g30.gain.linearRampToValueAtTime(g30Val, gunStart + 0.018);
    g30.gain.setValueAtTime(g30Val, gunStop - 0.025);
    g30.gain.linearRampToValueAtTime(0.001, gunStop + 0.05);
    s30.connect(g30);
    g30.connect(preSum);
    s30.start(gunStart);
    s30.stop(gunStop + 0.065);
    rateSources.push({ src: s30, baseRate: 0.86 });
    realGains.push({ node: g30, scale: 0.56 });

    // 停火瞬间接入真实空域炮口余音，与 Convolver 滚雷无缝衔接
    const rel30 = register(ctx.createBufferSource());
    rel30.buffer = resources.gsh30Slice.releaseBuf;
    rel30.playbackRate.setValueAtTime(0.84 * profile.basePitchScale, gunStop - 0.015);
    const rg30 = ctx.createGain();
    rg30.gain.setValueAtTime(Math.max(0.32, g30Val * 1.15), gunStop - 0.015);
    rel30.connect(rg30);
    rg30.connect(preSum);
    rel30.start(gunStop - 0.015);
    rateSources.push({ src: rel30, baseRate: 0.84 });
  }

  if (resources.gau12Slice && realMul > 0.01) {
    const s12 = register(ctx.createBufferSource());
    s12.buffer = resources.gau12Slice.sustainBuf;
    s12.loop = true;
    s12.loopStart = resources.gau12Slice.loopStartSec;
    s12.loopEnd = resources.gau12Slice.loopEndSec;
    s12.playbackRate.setValueAtTime(0.95 * profile.basePitchScale, gunStart);

    const hp12 = ctx.createBiquadFilter();
    hp12.type = 'highpass';
    hp12.frequency.value = 120;

    const g12 = ctx.createGain();
    const g12Val = 0.42 * realMul;
    g12.gain.setValueAtTime(0.001, gunStart);
    g12.gain.linearRampToValueAtTime(g12Val, gunStart + 0.02);
    g12.gain.setValueAtTime(g12Val, gunStop - 0.025);
    g12.gain.linearRampToValueAtTime(0.001, gunStop + 0.05);
    s12.connect(hp12);
    hp12.connect(g12);
    g12.connect(preSum);
    s12.start(gunStart);
    s12.stop(gunStop + 0.065);
    rateSources.push({ src: s12, baseRate: 0.95 });
    realGains.push({ node: g12, scale: 0.42 });
  }

  // ==================== 4. 第 3 层：地面 30mm 超音速弹着 & 马赫锥裂响（音速先行） ====================
  if (withGroundImpact) {
    // 4a. 超音速弹道裂响（直通母线，不受机身距离低通影响，营造“子弹先撕开耳膜”）
    const crackSrc = register(ctx.createBufferSource());
    crackSrc.buffer = resources.crackBurstBuf;
    crackSrc.loop = true;
    const crackGain = ctx.createGain();
    const crackLevel = lerp(0.32, 0.22, profile.logNorm);
    crackGain.gain.setValueAtTime(0.001, t0);
    crackGain.gain.linearRampToValueAtTime(crackLevel, t0 + 0.02);
    crackGain.gain.setValueAtTime(crackLevel, t0 + durationSec - 0.04);
    crackGain.gain.linearRampToValueAtTime(0.001, t0 + durationSec + 0.06);
    crackSrc.connect(crackGain);
    crackGain.connect(destination);
    crackSrc.start(t0);
    crackSrc.stop(t0 + durationSec + 0.08);

    // 4b. 地面 30mm HEI 密集弹着爆裂
    if (resources.impactBuf) {
      const pulseStep = 0.21;
      const count = Math.max(2, Math.floor(durationSec / pulseStep));
      for (let i = 0; i < count; i += 1) {
        const at = t0 + i * pulseStep + ((i % 3) - 1) * 0.016;
        const imp = register(ctx.createBufferSource());
        imp.buffer = resources.impactBuf;
        imp.playbackRate.value = 0.92 + ((i * 37) % 15) * 0.01;
        const ig = ctx.createGain();
        ig.gain.value = 0.46 / Math.sqrt(1 + Math.min(2, i * 0.35));
        imp.connect(ig);
        ig.connect(destination);
        imp.start(at, 0.14, 0.38);
      }
    }
  }

  // ==================== 5. 迎头低空飞越多普勒自动化曲线（可选） ====================
  if (flybyDoppler) {
    // 模拟 A-10 从前方 620m 俯冲扫射掠过头顶（最近 95m）并拉起至 480m
    const midTime = gunStart + durationSec * 0.56;
    lpf1.frequency.setValueAtTime(1150, gunStart);
    lpf1.frequency.exponentialRampToValueAtTime(7600, midTime);
    lpf1.frequency.exponentialRampToValueAtTime(1250, gunStop + 0.5);

    lpf2.frequency.setValueAtTime(1400, gunStart);
    lpf2.frequency.exponentialRampToValueAtTime(9200, midTime);
    lpf2.frequency.exponentialRampToValueAtTime(1500, gunStop + 0.5);

    dryBus.gain.setValueAtTime(0.68, gunStart);
    dryBus.gain.linearRampToValueAtTime(0.98, midTime);
    dryBus.gain.linearRampToValueAtTime(0.62, gunStop + 0.4);

    wetBus.gain.setValueAtTime(0.45 * thunderTailStrength, gunStart);
    wetBus.gain.linearRampToValueAtTime(0.22 * thunderTailStrength, midTime);
    wetBus.gain.linearRampToValueAtTime(0.68 * thunderTailStrength, gunStop + 0.3);

    if (panner) {
      panner.pan.setValueAtTime(-0.55, gunStart);
      panner.pan.linearRampToValueAtTime(0.0, midTime);
      panner.pan.linearRampToValueAtTime(0.62, gunStop + 0.35);
    }

    for (const item of rateSources) {
      item.src.playbackRate.setValueAtTime(item.baseRate * 1.09, gunStart);
      item.src.playbackRate.linearRampToValueAtTime(item.baseRate * 1.02, midTime - 0.12);
      item.src.playbackRate.linearRampToValueAtTime(item.baseRate * 0.89, gunStop + 0.15);
    }
  }

  // ==================== 6. 实时动态调节句柄（试听台滑块 & 实战每帧共用） ====================
  const updateDistance = (newDistMeters, dopplerMul = 1.0, panVal = 0) => {
    const p = computeGau8DistanceProfile(newDistMeters, { realismDelay });
    const now = ctx.currentTime;
    const tc = 0.045;
    lpf1.frequency.setTargetAtTime(p.lpfCutoffHz, now, tc);
    lpf2.frequency.setTargetAtTime(Math.min(14000, p.lpfCutoffHz * 1.18), now, tc);
    lowShelf.gain.setTargetAtTime(p.lowShelfDb, now, tc);
    presence.gain.setTargetAtTime(p.presenceDb, now, tc);
    dryBus.gain.setTargetAtTime(p.dryGain, now, tc);
    wetBus.gain.setTargetAtTime(p.wetGain * clamp(thunderTailStrength, 0, 1.5), now, tc);
    wetLpf.frequency.setTargetAtTime(p.wetLpfHz, now, tc);
    masterOut.gain.setTargetAtTime(p.overallGain, now, tc);

    const effPitch = p.basePitchScale * clamp(dopplerMul, 0.78, 1.22);
    for (const item of rateSources) {
      try { item.src.playbackRate.setTargetAtTime(item.baseRate * effPitch, now, tc); } catch {}
    }
    if (panner && Number.isFinite(panVal)) {
      panner.pan.setTargetAtTime(clamp(panVal, -1, 1), now, tc);
    }
    return p;
  };

  const tailSec = lerp(1.3, 3.6, profile.logNorm);
  return {
    profile,
    gunStart,
    gunStop,
    totalDurationSec: gunDelay + durationSec + tailSec,
    updateDistance,
  };
}

/**
 * 实战主干持续机炮物理声源（R3 共享主干版）：
 * - 严禁内部新建 ConvolverNode！湿声输出接入外部全局唯一共享 wetConvolverInput；
 * - 支持按 preset（rpm / caliberMm / barrelCount）参数化复用与 N-Wave + 实录纹理四层混合；
 * - updateTelemetry() 仅调用 setTargetAtTime（零新建节点、零运行期 GC）；
 * - stop() 含远距延迟安全保护窗口，淡出循环层并接入 GSH30 真实尾音与共享 3.6s 滚雷拖尾。
 */
export function createContinuousGunAcousticVoice(
  ctx,
  dryDestination,
  wetConvolverInput,
  resources,
  {
    distanceMeters = 420,
    radialSpeedMps = 0,
    panVal = 0,
    preset = DEFAULT_GAU8_ACOUSTIC_PRESET,
    realismDelay = true,
    maxDelaySec = 1.65,
    minAudibleWindowSec = 0.30,
    voiceHeadroom = 0.64,
  } = {},
) {
  if (!ctx || ctx.state === 'closed') {
    return {
      gunStartSec: 0,
      updateTelemetry() {},
      stop() { return 0; },
      dispose() {},
      getSnapshot() { return null; },
    };
  }
  const effectivePreset = preset || DEFAULT_GAU8_ACOUSTIC_PRESET;
  const shockStrength = clamp(Number(effectivePreset.shockStrength ?? 0.95), 0, 1.5);
  const realTextureStrength = clamp(Number(effectivePreset.realTextureStrength ?? 0.75), 0, 1.5);
  const thunderTailStrength = clamp(Number(effectivePreset.thunderTailStrength ?? 0.95), 0, 1.5);

  const profileBuf = {};
  fillGau8DistanceProfile(distanceMeters, profileBuf, { maxDelaySec, realismDelay });

  const now0 = ctx.currentTime;
  const gunStartSec = now0 + 0.015 + profileBuf.soundDelaySec;
  const initDoppler = clamp(340 / (340 + clamp(radialSpeedMps, -220, 220) * 0.45), 0.84, 1.18);
  const initEffPitch = profileBuf.basePitchScale * initDoppler;

  // ==================== 1. 构建单次扫射专属实时距离滤波链（无内部 ConvolverNode） ====================
  const preSum = ctx.createGain();
  preSum.gain.value = 1.0;

  const shaper = ctx.createWaveShaper();
  shaper.curve = resources?.satCurve || buildAsymmetricSaturatorCurve(2.65);
  shaper.oversample = '2x';

  const lpf1 = ctx.createBiquadFilter();
  lpf1.type = 'lowpass';
  lpf1.Q.value = 0.72;
  lpf1.frequency.setValueAtTime(profileBuf.lpfCutoffHz, now0);

  const lpf2 = ctx.createBiquadFilter();
  lpf2.type = 'lowpass';
  lpf2.Q.value = 0.65;
  lpf2.frequency.setValueAtTime(Math.min(14000, profileBuf.lpfCutoffHz * 1.18), now0);

  const lowShelf = ctx.createBiquadFilter();
  lowShelf.type = 'lowshelf';
  lowShelf.frequency.value = 135;
  lowShelf.gain.setValueAtTime(profileBuf.lowShelfDb, now0);

  const presence = ctx.createBiquadFilter();
  presence.type = 'peaking';
  presence.frequency.value = 2200;
  presence.Q.value = 0.85;
  presence.gain.setValueAtTime(profileBuf.presenceDb, now0);

  const panner = typeof ctx.createStereoPanner === 'function' ? ctx.createStereoPanner() : null;
  if (panner) panner.pan.setValueAtTime(clamp(panVal, -1, 1), now0);

  const dryBus = ctx.createGain();
  dryBus.gain.setValueAtTime(profileBuf.dryGain * profileBuf.overallGain * voiceHeadroom, now0);

  const wetLpf = ctx.createBiquadFilter();
  wetLpf.type = 'lowpass';
  wetLpf.Q.value = 0.6;
  wetLpf.frequency.setValueAtTime(profileBuf.wetLpfHz, now0);

  const wetBus = ctx.createGain();
  wetBus.gain.setValueAtTime(
    profileBuf.wetGain * thunderTailStrength * profileBuf.overallGain * voiceHeadroom,
    now0,
  );

  preSum.connect(shaper);
  shaper.connect(lpf1);
  lpf1.connect(lpf2);
  lpf2.connect(lowShelf);
  lowShelf.connect(presence);

  const postTone = panner || presence;
  if (panner) presence.connect(panner);

  if (dryDestination) {
    postTone.connect(dryBus);
    dryBus.connect(dryDestination);
  }
  if (wetConvolverInput) {
    postTone.connect(wetLpf);
    wetLpf.connect(wetBus);
    wetBus.connect(wetConvolverInput);
  }

  // ==================== 2. 激波脉冲列层（参数化缓存） ====================
  const shockLoopBuf = buildShockPulseLoopBuffer(ctx, effectivePreset);
  const shockSrc = ctx.createBufferSource();
  shockSrc.buffer = shockLoopBuf;
  shockSrc.loop = true;
  shockSrc.playbackRate.setValueAtTime(initEffPitch, now0);

  const shockGain = ctx.createGain();
  const initShockTarget = 0.78 * profileBuf.shockLayerWeight * shockStrength;
  shockGain.gain.setValueAtTime(0.0001, now0);
  shockGain.gain.setValueAtTime(0.0001, gunStartSec);
  shockGain.gain.linearRampToValueAtTime(Math.max(0.001, initShockTarget), gunStartSec + 0.016);
  shockSrc.connect(shockGain);
  shockGain.connect(preSum);
  shockSrc.start(gunStartSec);

  // ==================== 3. 实录火药纹理层（通用切片映射 + 兼容 GSH30 / GAU12） ====================
  const primaryKey = effectivePreset.primaryRealKey ?? 'SFX_AIR_PLANEGUN_30mm_GSH30_RU';
  const secondaryKey = effectivePreset.secondaryRealKey ?? 'SFX_AIR_AAG_25mm_GAU12_US';
  const defaultPrimaryKey = 'SFX_AIR_PLANEGUN_30mm_GSH30_RU';
  const defaultSecondaryKey = 'SFX_AIR_AAG_25mm_GAU12_US';
  const primarySlice = primaryKey
    ? (resources?.realGunSlices?.get?.(primaryKey) || (primaryKey === defaultPrimaryKey ? resources?.gsh30Slice : null) || resources?.gsh30Slice || null)
    : null;
  const secondarySlice = secondaryKey
    ? (resources?.realGunSlices?.get?.(secondaryKey) || (secondaryKey === defaultSecondaryKey ? resources?.gau12Slice : null) || resources?.gau12Slice || null)
    : null;

  const initRealMul = profileBuf.realLayerWeight * realTextureStrength;
  let s30 = null;
  let g30 = null;
  let s12 = null;
  let hp12 = null;
  let g12 = null;
  let rel30 = null;
  let rg30 = null;

  if (primarySlice?.sustainBuf && initRealMul > 0.01) {
    s30 = ctx.createBufferSource();
    s30.buffer = primarySlice.sustainBuf;
    s30.loop = true;
    s30.loopStart = primarySlice.loopStartSec;
    s30.loopEnd = primarySlice.loopEndSec;
    s30.playbackRate.setValueAtTime(0.86 * initEffPitch, now0);

    g30 = ctx.createGain();
    const g30Val = Math.max(0.001, 0.56 * initRealMul);
    g30.gain.setValueAtTime(0.0001, now0);
    g30.gain.setValueAtTime(0.0001, gunStartSec);
    g30.gain.linearRampToValueAtTime(g30Val, gunStartSec + 0.018);
    s30.connect(g30);
    g30.connect(preSum);
    s30.start(gunStartSec);
  }

  if (secondarySlice?.sustainBuf && initRealMul > 0.01) {
    s12 = ctx.createBufferSource();
    s12.buffer = secondarySlice.sustainBuf;
    s12.loop = true;
    s12.loopStart = secondarySlice.loopStartSec;
    s12.loopEnd = secondarySlice.loopEndSec;
    s12.playbackRate.setValueAtTime(0.95 * initEffPitch, now0);

    hp12 = ctx.createBiquadFilter();
    hp12.type = 'highpass';
    hp12.frequency.value = 120;

    g12 = ctx.createGain();
    const g12Val = Math.max(0.001, 0.42 * initRealMul);
    g12.gain.setValueAtTime(0.0001, now0);
    g12.gain.setValueAtTime(0.0001, gunStartSec);
    g12.gain.linearRampToValueAtTime(g12Val, gunStartSec + 0.02);
    s12.connect(hp12);
    hp12.connect(g12);
    g12.connect(preSum);
    s12.start(gunStartSec);
  }

  // 状态标量（热路径零对象分配）
  let disposed = false;
  let stopped = false;
  let stopScheduledSec = 0;
  let lastTailWaitMs = 0;
  let cleanupTimerId = null;
  let curDistanceMeters = profileBuf.distanceMeters;
  let curRadialSpeedMps = Number(radialSpeedMps) || 0;
  let curDopplerMul = initDoppler;
  let curLpfCutoffHz = profileBuf.lpfCutoffHz;
  let curDryGain = profileBuf.dryGain;
  let curWetGain = profileBuf.wetGain;
  let curPanVal = clamp(panVal, -1, 1);
  let curBandLabel = profileBuf.bandLabel;
  let updateCount = 0;

  const disconnectAll = () => {
    if (disposed) return;
    disposed = true;
    if (cleanupTimerId !== null) {
      clearTimeout(cleanupTimerId);
      cleanupTimerId = null;
    }
    const nodes = [shockSrc, shockGain, s30, g30, s12, hp12, g12, rel30, rg30, preSum, shaper, lpf1, lpf2, lowShelf, presence, panner, dryBus, wetLpf, wetBus];
    for (let i = 0; i < nodes.length; i += 1) {
      const n = nodes[i];
      if (!n) continue;
      if (typeof n.stop === 'function') {
        try { n.stop(ctx.currentTime); } catch {}
      }
      try { n.disconnect(); } catch {}
    }
  };

  /**
   * 实时更新距离与多普勒参数：仅调用 setTargetAtTime，零新建节点、零对象分配。
   */
  const updateTelemetry = (nextDistMeters, nextRadialSpeedMps = 0, nextPanVal = 0, nowArg) => {
    if (disposed || !ctx || ctx.state === 'closed') return;
    const now = Number.isFinite(nowArg) ? nowArg : ctx.currentTime;
    if (!Number.isFinite(now)) return;
    const tc = 0.045;

    fillGau8DistanceProfile(nextDistMeters, profileBuf, { maxDelaySec, realismDelay });

    const vRad = clamp(Number(nextRadialSpeedMps) || 0, -220, 220);
    const dopplerMul = clamp(340 / (340 + vRad * 0.45), 0.84, 1.18);
    const effPitch = profileBuf.basePitchScale * dopplerMul;
    const clampedPan = Number.isFinite(nextPanVal) ? clamp(nextPanVal, -1, 1) : curPanVal;

    try {
      lpf1.frequency.setTargetAtTime(profileBuf.lpfCutoffHz, now, tc);
      lpf2.frequency.setTargetAtTime(Math.min(14000, profileBuf.lpfCutoffHz * 1.18), now, tc);
      lowShelf.gain.setTargetAtTime(profileBuf.lowShelfDb, now, tc);
      presence.gain.setTargetAtTime(profileBuf.presenceDb, now, tc);
      dryBus.gain.setTargetAtTime(profileBuf.dryGain * profileBuf.overallGain * voiceHeadroom, now, tc);
      wetBus.gain.setTargetAtTime(
        profileBuf.wetGain * thunderTailStrength * profileBuf.overallGain * voiceHeadroom,
        now,
        tc,
      );
      wetLpf.frequency.setTargetAtTime(profileBuf.wetLpfHz, now, tc);
      if (panner) {
        panner.pan.setTargetAtTime(clampedPan, now, tc);
      }

      if (!stopped && now >= gunStartSec) {
        const shockTarget = 0.78 * profileBuf.shockLayerWeight * shockStrength;
        const realMul = profileBuf.realLayerWeight * realTextureStrength;
        shockGain.gain.setTargetAtTime(Math.max(0.0001, shockTarget), now, tc);
        if (g30) g30.gain.setTargetAtTime(Math.max(0.0001, 0.56 * realMul), now, tc);
        if (g12) g12.gain.setTargetAtTime(Math.max(0.0001, 0.42 * realMul), now, tc);
      }
    } catch {
      return;
    }

    try { shockSrc.playbackRate.setTargetAtTime(effPitch, now, tc); } catch {}
    if (s30) {
      try { s30.playbackRate.setTargetAtTime(0.86 * effPitch, now, tc); } catch {}
    }
    if (s12) {
      try { s12.playbackRate.setTargetAtTime(0.95 * effPitch, now, tc); } catch {}
    }
    if (rel30) {
      try { rel30.playbackRate.setTargetAtTime(0.84 * effPitch, now, tc); } catch {}
    }

    curDistanceMeters = profileBuf.distanceMeters;
    curRadialSpeedMps = Number(vRad.toFixed(2));
    curDopplerMul = Number(dopplerMul.toFixed(4));
    curLpfCutoffHz = profileBuf.lpfCutoffHz;
    curDryGain = profileBuf.dryGain;
    curWetGain = profileBuf.wetGain;
    curPanVal = Number(clampedPan.toFixed(3));
    curBandLabel = profileBuf.bandLabel;
    updateCount += 1;
  };

  /**
   * 停火：保护远距延迟安全窗口，淡出循环层并接入 GSH30 真实尾音与共享 3.6s 滚雷拖尾。
   */
  const stop = (nowArg) => {
    if (disposed || stopped) return stopScheduledSec;
    stopped = true;
    const now = Number.isFinite(nowArg) ? nowArg : ctx.currentTime;
    const safeStopSec = Math.max(now + 0.025, gunStartSec + Math.max(0.18, minAudibleWindowSec));
    stopScheduledSec = safeStopSec;

    const fadeEnd = safeStopSec + 0.055;
    try {
      shockGain.gain.cancelScheduledValues(safeStopSec);
      shockGain.gain.setTargetAtTime(0.0001, safeStopSec, 0.014);
      shockSrc.stop(fadeEnd + 0.02);
    } catch {}

    if (s30 && g30) {
      try {
        g30.gain.cancelScheduledValues(safeStopSec);
        g30.gain.setTargetAtTime(0.0001, safeStopSec, 0.014);
        s30.stop(fadeEnd + 0.02);
      } catch {}
    }
    if (s12 && g12) {
      try {
        g12.gain.cancelScheduledValues(safeStopSec);
        g12.gain.setTargetAtTime(0.0001, safeStopSec, 0.014);
        s12.stop(fadeEnd + 0.02);
      } catch {}
    }

    if (primarySlice?.releaseBuf && realTextureStrength > 0.01) {
      try {
        const relStart = Math.max(ctx.currentTime, safeStopSec - 0.015);
        const relMul = profileBuf.realLayerWeight * realTextureStrength;
        rel30 = ctx.createBufferSource();
        rel30.buffer = primarySlice.releaseBuf;
        rel30.playbackRate.setValueAtTime(0.84 * profileBuf.basePitchScale * curDopplerMul, relStart);

        rg30 = ctx.createGain();
        rg30.gain.setValueAtTime(Math.max(0.28, 0.56 * relMul * 1.12), relStart);
        rel30.connect(rg30);
        rg30.connect(preSum);
        rel30.start(relStart);
      } catch {}
    }

    const tailWaitMs = Math.max(400, Math.round(((safeStopSec - ctx.currentTime) + 4.1) * 1000));
    lastTailWaitMs = tailWaitMs;
    cleanupTimerId = setTimeout(disconnectAll, tailWaitMs);
    return safeStopSec;
  };

  const getSnapshot = () => ({
    presetId: effectivePreset.presetId || 'gau8_30mm',
    rpm: Number(effectivePreset.rpm) || 3970,
    caliberMm: Number(effectivePreset.caliberMm) || 30,
    barrelCount: Number(effectivePreset.barrelCount) || 7,
    distanceMeters: curDistanceMeters,
    radialSpeedMps: curRadialSpeedMps,
    dopplerMul: curDopplerMul,
    lpfCutoffHz: curLpfCutoffHz,
    dryGain: curDryGain,
    wetGain: curWetGain,
    panVal: curPanVal,
    bandLabel: curBandLabel,
    soundDelaySec: profileBuf.soundDelaySec,
    gunStartSec: Number(gunStartSec.toFixed(3)),
    stopScheduledSec: Number(stopScheduledSec.toFixed(3)),
    tailWaitMs: lastTailWaitMs,
    stopped,
    disposed,
    updateCount,
    hasRealGsh30: Boolean(s30),
    hasRealGau12: Boolean(s12),
    hasReleaseTail: Boolean(rel30),
  });

  return {
    startTimeSec: gunStartSec,
    updateTelemetry,
    stop,
    dispose: disconnectAll,
    getSnapshot,
  };
}
