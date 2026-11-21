"""由独立Blender后台进程只读打开迁移后的静态及骨骼主工程。"""

import json
from pathlib import Path
import bpy

ROOT = Path(__file__).resolve().parents[2]
HOME = ROOT / 'local-data/资源库'
OUTPUT = ROOT / 'local-data/资源库命名/可读迁移V002-01a1a4e6'
pointer = json.loads((HOME / '当前版本.json').read_text('utf-8'))
masters = json.loads((HOME / pointer['directory'] / '主工程.json').read_text('utf-8'))
results = []
for identity in ['model:US_M1A2', 'model:zombie-01']:
    path = (ROOT / masters[identity]['path']).resolve()
    if not path.is_relative_to(HOME / '当前资源/主工程'):
        raise ValueError('工程路径超出范围')
    bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False)
    external = [image.name for image in bpy.data.images if image.source in ['FILE', 'TILED'] and not (image.packed_file or image.packed_files)]
    results.append({'id': identity, 'path': masters[identity]['path'], 'objects': len(bpy.data.objects), 'armatures': len(bpy.data.armatures), 'actions': len(bpy.data.actions), 'external_images': external, 'passed': not external})
    if external or not bpy.data.objects:
        raise ValueError('工程回读失败')
(OUTPUT / '工程回读核验.json').write_text(json.dumps({'revision': pointer['revision'], 'samples': results, 'passed': True, 'saved_blend': False}, ensure_ascii=False, indent=2), encoding='utf-8')
print('迁移后两份主工程只读回读通过', flush=True)
