/**
 * SCVH 2m 高度场单职责 Web Worker（Phase 3B · SCAV-15）：
 * - 复用纯数据模块 高度支撑.mjs::decodeSCVHHeightfield 在独立线程解压并完成 2,025 万格二维差分累加；
 * - 同线程完成 2251×2251 GPU 打包高度纹理 RGBA8 填充与 4m 半分辨率 roleGridHalf 降采样，
 *   避免主线程承担 ~120 MiB 临时解压/法线/角色数组与 ~87ms+ 同步阻塞；
 * - 加载就绪后主动发送 { type: 'ready' } 握手信号，结果经 Set 去重零拷贝 Transfer 返回主线程；
 * - 若解码异常且原始缓冲未损坏，自动将原缓冲移回主线程兜底。
 */
import { decodeSCVHHeightfield } from './高度支撑.mjs?v=680p-v9';

function buildPackedAndHalfRole(hf, packSize) {
  const sw = hf.width;
  const sh = hf.height;
  const { qGrid, nyGrid, roleGrid } = hf;

  const w = Math.max(2, Number(packSize) || 2251);
  const h = w;
  const packedRgba = new Uint8Array(w * h * 4);
  const exactHalfStep = (sw === (w - 1) * 2 + 1) && (sh === (h - 1) * 2 + 1);
  for (let iz = 0; iz < h; iz++) {
    const sz = exactHalfStep
      ? Math.min(sh - 1, iz * 2)
      : Math.min(sh - 1, Math.round((iz / (h - 1)) * (sh - 1)));
    const srcRow = sz * sw;
    const dstRow = iz * w;
    for (let ix = 0; ix < w; ix++) {
      const sx = exactHalfStep
        ? Math.min(sw - 1, ix * 2)
        : Math.min(sw - 1, Math.round((ix / (w - 1)) * (sw - 1)));
      const q = qGrid[srcRow + sx];
      const di = (dstRow + ix) * 4;
      packedRgba[di] = q & 0xff;
      packedRgba[di + 1] = (q >> 8) & 0xff;
      packedRgba[di + 2] = nyGrid ? nyGrid[srcRow + sx] : 250;
      packedRgba[di + 3] = roleGrid ? roleGrid[srcRow + sx] : 1;
    }
  }

  const hw = Math.floor((sw + 1) / 2);
  const hh = Math.floor((sh + 1) / 2);
  const roleGridHalf = new Uint8Array(hw * hh);
  if (roleGrid) {
    for (let iz = 0; iz < hh; iz++) {
      const sz = Math.min(sh - 1, iz * 2);
      const rowOff = sz * sw;
      const dstOff = iz * hw;
      for (let ix = 0; ix < hw; ix++) {
        roleGridHalf[dstOff + ix] = roleGrid[rowOff + Math.min(sw - 1, ix * 2)];
      }
    }
  } else {
    roleGridHalf.fill(1);
  }

  return { packedRgba, roleGridHalf, halfWidth: hw };
}

self.addEventListener('message', async (event) => {
  const msg = event.data;
  if (!msg || msg.type !== 'decodeSCVH') return;
  const { id, buffer, packSize = 2251 } = msg;
  try {
    if (!(buffer instanceof ArrayBuffer) || buffer.byteLength === 0) {
      throw new Error('SCVH Worker 收到空缓冲或已分离 ArrayBuffer');
    }
    const hf = await decodeSCVHHeightfield(buffer);
    const { packedRgba, roleGridHalf, halfWidth } = buildPackedAndHalfRole(hf, packSize);

    const compactHf = {
      width: hf.width,
      height: hf.height,
      minX: hf.minX,
      minZ: hf.minZ,
      maxX: hf.maxX,
      maxZ: hf.maxZ,
      stepX: hf.stepX,
      stepZ: hf.stepZ,
      minH: hf.minH,
      maxH: hf.maxH,
      qGrid: hf.qGrid,
      roleGridHalf,
      halfWidth,
      roleGrid: null,
      nyGrid: null,
      bytes: hf.qGrid.byteLength + roleGridHalf.byteLength,
    };

    const transferSet = new Set();
    if (compactHf.qGrid?.buffer instanceof ArrayBuffer) transferSet.add(compactHf.qGrid.buffer);
    if (roleGridHalf.buffer instanceof ArrayBuffer) transferSet.add(roleGridHalf.buffer);
    if (packedRgba.buffer instanceof ArrayBuffer) transferSet.add(packedRgba.buffer);
    const transfers = Array.from(transferSet);

    self.postMessage(
      {
        type: 'decodeSCVH:ok',
        id,
        hf: compactHf,
        packedRgba,
        packSize,
      },
      transfers,
    );
  } catch (err) {
    const transfers = [];
    const returnBuf = (buffer instanceof ArrayBuffer && buffer.byteLength > 0) ? buffer : null;
    if (returnBuf) transfers.push(returnBuf);
    self.postMessage(
      {
        type: 'decodeSCVH:err',
        id,
        message: String(err?.message || err || 'SCVH Worker 解码失败'),
        buffer: returnBuf,
      },
      transfers,
    );
  }
});

// 模块加载完成后立即通知主线程完成握手
self.postMessage({ type: 'ready' });
