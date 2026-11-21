// BAIP只携带摆放与来源身份；显示驻留和模拟引用由调用方分别管理。
export function decodeInstancePage(buffer, {prototypeCount, groupCount, expectedCount} = {}) {
  if (!(buffer instanceof ArrayBuffer) || buffer.byteLength < 16) throw new Error('实例页头部不完整');
  const data = new DataView(buffer);
  if (data.getUint32(0, true) !== 0x50494142 || data.getUint32(4, true) !== 1) throw new Error('实例页身份或版本无效');
  const count = data.getUint32(8, true), stride = data.getUint32(12, true);
  if (stride !== 104 || count > 1000000 || buffer.byteLength !== 16 + count * stride) throw new Error('实例页长度与记录格式不符');
  if (expectedCount !== undefined && count !== expectedCount) throw new Error('实例页计数不符');
  const records = [];
  for (let index = 0; index < count; index++) {
    const offset = 16 + index * stride;
    const prototype = data.getUint32(offset, true), group = data.getUint32(offset + 4, true);
    const sourceIndex = data.getUint32(offset + 8, true), flags = data.getUint32(offset + 12, true);
    if (prototypeCount !== undefined && prototype >= prototypeCount || groupCount !== undefined && group >= groupCount || flags & ~3) {
      throw new Error('实例页来源或标记越界');
    }
    const matrix = new Float32Array(16), bounds = new Float32Array(6);
    for (let i = 0; i < 16; i++) matrix[i] = data.getFloat32(offset + 16 + i * 4, true);
    for (let i = 0; i < 6; i++) bounds[i] = data.getFloat32(offset + 80 + i * 4, true);
    if (![...matrix, ...bounds].every(Number.isFinite) || bounds.some((value, i) => i < 3 && value > bounds[i + 3])) {
      throw new Error('实例页包含无效矩阵或包围盒');
    }
    if (matrix[3] !== 0 || matrix[7] !== 0 || matrix[11] !== 0 || matrix[15] !== 1) throw new Error('实例页矩阵不是仿射变换');
    records.push({prototype, group, sourceIndex, flags, matrix, bounds, sourceId: `${group}:${sourceIndex}`});
  }
  return {count, records, bytes: buffer.byteLength};
}
