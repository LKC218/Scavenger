/**
 * 第一道自动闸门：静态二进制、扩展量化、节点清零、DrawCall、包围盒与轴心结构校验。
 * 用法：
 *   node scripts/程序化载具/验证静态结构.mjs --dir local-data/程序化载具/V001-b4570e71/V1-样本验证
 */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';

const ROOT = path.resolve(import.meta.dirname, '../..');
const MODULES = path.join(ROOT, 'local-data/tools/网页资源优化/1/node_modules');
const loadMod = async p => import(pathToFileURL(path.join(MODULES, p)).href);

const {NodeIO} = await loadMod('@gltf-transform/core/dist/index.js');
const {ALL_EXTENSIONS} = await loadMod('@gltf-transform/extensions/dist/index.js');
const {MeshoptDecoder} = await loadMod('meshoptimizer/index.js');
await MeshoptDecoder.ready;

const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({'meshopt.decoder': MeshoptDecoder});

const args = process.argv.slice(2);
const opt = (k, d) => {
  const i = args.indexOf(k);
  return i >= 0 && i + 1 < args.length ? args[i + 1] : d;
};
const targetDir = path.resolve(ROOT, opt('--dir', 'local-data/程序化载具/V001-b4570e71/V1-样本验证'));
const sha256 = b => crypto.createHash('sha256').update(b).digest('hex');

function parseGlbJson(buf) {
  const dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
  const magic = buf.subarray(0, 4).toString('ascii');
  const version = dv.getUint32(4, true);
  const length = dv.getUint32(8, true);
  const jlen = dv.getUint32(12, true);
  if (magic !== 'glTF' || version !== 2 || length !== buf.length) {
    throw new Error(`GLB 头或长度非法: magic=${magic} ver=${version} len=${length}/${buf.length}`);
  }
  return JSON.parse(buf.subarray(20, 20 + jlen).toString('utf8'));
}

const metaDir = path.join(targetDir, 'meta');
const metaFiles = fs.readdirSync(metaDir).filter(f => f.endsWith('.json')).sort();
const reports = [];
let passCount = 0;
let failCount = 0;

for (const mf of metaFiles) {
  const meta = JSON.parse(fs.readFileSync(path.join(metaDir, mf), 'utf8'));
  const errors = [];
  const warnings = [];

  for (const lodKey of ['lod0', 'lod1', 'lod2']) {
    const info = meta.lods[lodKey];
    const p = path.join(ROOT, info.path);
    if (!fs.existsSync(p)) {
      errors.push(`${lodKey} 文件不存在: ${info.path}`);
      continue;
    }
    const buf = fs.readFileSync(p);
    if (sha256(buf) !== info.sha256) {
      errors.push(`${lodKey} SHA256 与元数据不符`);
    }
    const gltf = parseGlbJson(buf);
    const exts = new Set(gltf.extensionsUsed || []);
    if (!exts.has('KHR_mesh_quantization')) errors.push(`${lodKey} 缺少 KHR_mesh_quantization`);
    if (!exts.has('EXT_meshopt_compression')) errors.push(`${lodKey} 缺少 EXT_meshopt_compression`);
    if (lodKey !== 'lod2' && (gltf.images || []).length > 0 && !exts.has('KHR_texture_basisu')) {
      errors.push(`${lodKey} 有贴图但缺少 KHR_texture_basisu`);
    }

    // 检查所有节点均有网格（0 废空节点）
    const emptyNodes = (gltf.nodes || []).filter(n => n.mesh === undefined);
    if (emptyNodes.length > 0) {
      errors.push(`${lodKey} 残留 ${emptyNodes.length} 个空节点`);
    }

    // 检查外置 KTX2 贴图存在且文件头为有效 KTX2
    for (const im of gltf.images || []) {
      if (!im.uri) {
        errors.push(`${lodKey} 存在未外置图像`);
      } else {
        const texFile = path.join(targetDir, im.uri);
        if (!fs.existsSync(texFile)) {
          errors.push(`${lodKey} 外置贴图缺失: ${im.uri}`);
        } else {
          const hdr = fs.readFileSync(texFile).subarray(0, 12);
          const magicKtx = Buffer.from([0xab, 0x4b, 0x54, 0x58, 0x20, 0x32, 0x30, 0xbb, 0x0d, 0x0a, 0x1a, 0x0a]);
          if (!hdr.equals(magicKtx)) {
            errors.push(`${lodKey} 外置贴图非有效 KTX2 头: ${im.uri}`);
          }
        }
      }
    }

    // 检查 DrawCall（不透明材质数 <= 3，LOD2 单一合并壳）
    const prims = (gltf.meshes || []).reduce((a, m) => a + (m.primitives || []).length, 0);
    if (lodKey === 'lod2' && prims !== 1) {
      errors.push(`lod2 应为单一合并壳 (实际 prims=${prims})`);
    }
    if (lodKey === 'lod0' && meta.opaqueMaterialsCount > 3) {
      errors.push(`lod0 不透明材质数超限: ${meta.opaqueMaterialsCount} > 3`);
    }
    if (lodKey === 'lod0' && prims > 10) {
      errors.push(`lod0 DrawCall 过多: ${prims}`);
    }

    // 剥离外置图像引用后用 NodeIO 完整解码校验 Meshopt 与全部顶点坐标无 NaN/Inf
    try {
      const dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
      const jlen = dv.getUint32(12, true);
      const binTail = buf.subarray(20 + jlen);
      const stripped = JSON.parse(JSON.stringify(gltf));
      delete stripped.images;
      delete stripped.textures;
      for (const m of stripped.materials || []) {
        if (m.pbrMetallicRoughness) {
          delete m.pbrMetallicRoughness.baseColorTexture;
          delete m.pbrMetallicRoughness.metallicRoughnessTexture;
        }
        delete m.normalTexture;
        delete m.occlusionTexture;
        delete m.emissiveTexture;
      }
      if (Array.isArray(stripped.extensionsUsed)) {
        stripped.extensionsUsed = stripped.extensionsUsed.filter(x => x !== 'KHR_texture_basisu');
      }
      if (Array.isArray(stripped.extensionsRequired)) {
        stripped.extensionsRequired = stripped.extensionsRequired.filter(x => x !== 'KHR_texture_basisu');
      }
      const jb = Buffer.from(JSON.stringify(stripped), 'utf8');
      const pad = (4 - (jb.length % 4)) % 4;
      const pjb = pad ? Buffer.concat([jb, Buffer.alloc(pad, 0x20)]) : jb;
      const total = 20 + pjb.length + binTail.length;
      const cleanBuf = Buffer.alloc(total);
      cleanBuf.write('glTF', 0, 4, 'ascii');
      cleanBuf.writeUInt32LE(2, 4);
      cleanBuf.writeUInt32LE(total, 8);
      cleanBuf.writeUInt32LE(pjb.length, 12);
      cleanBuf.writeUInt32LE(0x4e4f534a, 16);
      pjb.copy(cleanBuf, 20);
      binTail.copy(cleanBuf, 20 + pjb.length);

      const doc = await io.readBinary(new Uint8Array(cleanBuf));
      for (const m of doc.getRoot().listMeshes()) {
        for (const pr of m.listPrimitives()) {
          const pa = pr.getAttribute('POSITION');
          if (!pa || pa.getCount() < 3) {
            errors.push(`${lodKey} 存在空几何 primitive`);
            continue;
          }
          const arr = pa.getArray();
          for (let i = 0; i < arr.length; i++) {
            if (!Number.isFinite(arr[i])) {
              errors.push(`${lodKey} 存在 NaN/Inf 顶点`);
              break;
            }
          }
        }
      }
    } catch (e) {
      errors.push(`${lodKey} 解码回读异常: ${String(e.message || e)}`);
    }
  }

  // 检查 LOD 递增单调性与轴心范围
  if (meta.lods.lod1.triangles > meta.lods.lod0.triangles) {
    errors.push(`LOD1 面数(${meta.lods.lod1.triangles}) 大于 LOD0(${meta.lods.lod0.triangles})`);
  }
  if (meta.lods.lod2.triangles > meta.lods.lod1.triangles && meta.lods.lod1.triangles > 350) {
    warnings.push(`LOD2 面数(${meta.lods.lod2.triangles}) 偏高`);
  }
  const bMin = meta.bounds.min, bMax = meta.bounds.max;
  for (const s of meta.slots) {
    if (s.slot === 'hull') continue;
    const [px, py, pz] = s.pivot;
    const margin = 3.0;
    if (
      px < bMin[0] - margin || px > bMax[0] + margin ||
      py < bMin[1] - margin || py > bMax[1] + margin ||
      pz < bMin[2] - margin || pz > bMax[2] + margin
    ) {
      errors.push(`槽位 ${s.slot} 轴心越界: [${px},${py},${pz}]`);
    }
  }

  const passed = errors.length === 0;
  if (passed) passCount += 1;
  else failCount += 1;
  reports.push({id: meta.id, name: meta.name, passed, errors, warnings});
}

const outReport = {
  schema_version: 1,
  checked_at: new Date().toISOString(),
  target_dir: path.relative(ROOT, targetDir).replaceAll('\\', '/'),
  total: metaFiles.length,
  passed: passCount,
  failed: failCount,
  details: reports,
};
fs.writeFileSync(path.join(targetDir, '静态结构校验回执.json'), JSON.stringify(outReport, null, 2), 'utf8');
console.log(`[静态结构校验] 完成: ${passCount}/${metaFiles.length} 通过, ${failCount} 失败`);
for (const r of reports.filter(x => !x.passed)) {
  console.error(`  FAIL ${r.name}: ${r.errors.join(' ; ')}`);
}
if (failCount > 0) process.exit(1);
