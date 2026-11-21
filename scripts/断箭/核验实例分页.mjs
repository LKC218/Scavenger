// 使用浏览器同一解码器独立回读所有二进制页，并核对来源、所有者及跨块引用。
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {decodeInstancePage} from '../../src/地图验证/实例页.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const directory = path.resolve(process.argv[2] || ''), revision = process.argv[3];
if (!directory.startsWith(path.join(root, 'local-data') + path.sep) || !revision || !/^[A-Za-z0-9_-]+$/.test(revision)) throw new Error('核验路径或修订无效');
const indexFile = path.join(directory, '实例索引.json'), index = JSON.parse(fs.readFileSync(indexFile, 'utf8'));
const output = path.join(directory, '独立核验', revision);
fs.mkdirSync(path.dirname(output), {recursive: true});
fs.mkdirSync(output, {recursive: false});
const ids = new Set(), pages = new Set(index.pages.map(page => page.id));
let total = 0, matrixBoundsFailures = 0;
const counts = Array(index.groups.length).fill(0);
for (const page of index.pages) {
  const file = path.resolve(directory, page.file);
  if (!file.startsWith(directory + path.sep)) throw new Error('实例页路径越界');
  const bytes = fs.readFileSync(file);
  if (bytes.length !== page.identity.bytes || crypto.createHash('sha256').update(bytes).digest('hex') !== page.identity.sha256) throw new Error('实例页摘要变化');
  const decoded = decodeInstancePage(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength), {
    prototypeCount: index.prototypes.length, groupCount: index.groups.length, expectedCount: page.count
  });
  for (const item of decoded.records) {
    if (ids.has(item.sourceId)) throw new Error('实例重复归属');
    ids.add(item.sourceId);counts[item.group]++;total++;
    const x = Math.max(0, Math.min(71, Math.floor((item.matrix[12] - index.bounds.minX) / 125)));
    const z = Math.max(0, Math.min(71, Math.floor((item.matrix[14] - index.bounds.minZ) / 125)));
    if (`${String(x).padStart(2, '0')}-${String(z).padStart(2, '0')}` !== page.id) throw new Error('实例唯一所有者不符');
    // 作者原点可能在形体之外，不能要求原点必须位于包围盒内。
    const b = item.bounds;
    const minX = Math.max(0, Math.floor((b[0] - index.bounds.minX) / 125)), maxX = Math.min(71, Math.floor((b[3] - index.bounds.minX) / 125));
    const minZ = Math.max(0, Math.floor((b[2] - index.bounds.minZ) / 125)), maxZ = Math.min(71, Math.floor((b[5] - index.bounds.minZ) / 125));
    for (let cz = minZ; cz <= maxZ; cz++) for (let cx = minX; cx <= maxX; cx++) {
      const cell = `${String(cx).padStart(2, '0')}-${String(cz).padStart(2, '0')}`;
      if (!index.chunk_references[cell]?.includes(page.id)) matrixBoundsFailures++;
    }
  }
}
if (total !== index.count || matrixBoundsFailures) throw new Error('实例总量或包围盒跨块引用不符');
for (const references of Object.values(index.chunk_references)) if (references.some(id => !pages.has(id))) throw new Error('跨块引用指向不存在的页');
const rejectionCases = [];
for (const [name, mutate] of [
  ['错误标识', data => data.setUint32(0, 0, true)], ['错误版本', data => data.setUint32(4, 99, true)],
  ['错误长度', data => data.setUint32(8, 0xffffffff, true)], ['越界原型', data => data.setUint32(16, 0xffffffff, true)],
  ['未知标记', data => data.setUint32(28, 8, true)], ['无效矩阵', data => data.setFloat32(32, NaN, true)]
]) {
  const example = fs.readFileSync(path.join(directory, index.pages[0].file));
  const buffer = example.buffer.slice(example.byteOffset, example.byteOffset + example.byteLength);mutate(new DataView(buffer));
  let rejected = false;
  try {decodeInstancePage(buffer, {prototypeCount:index.prototypes.length, groupCount:index.groups.length});} catch {rejected=true;}
  if (!rejected) throw new Error('未拒绝损坏实例页：' + name);rejectionCases.push(name);
}
const result = {passed:true, pages:index.pages.length, instances:total, groups:counts, duplicate_sources:0,
  empty_owner_cells:Array.from({length:72*72},(_,i)=>`${String(i%72).padStart(2,'0')}-${String(Math.floor(i/72)).padStart(2,'0')}`).filter(id=>!pages.has(id)),
  missing_cross_references:matrixBoundsFailures, rejected:rejectionCases,
  index_sha256:crypto.createHash('sha256').update(fs.readFileSync(indexFile)).digest('hex'),
  scope:'二进制、来源唯一性、所有者和包围盒引用；不代表碰撞、通行、显示或全图性能通过', human_acceptance:'待人工验收'};
fs.writeFileSync(path.join(output, '核验凭证.json'), JSON.stringify(result, null, 2), {flag:'wx'});
console.log(JSON.stringify({passed:true,pages:result.pages,instances:total}));
