"""用新 Blender 进程重开测试工程，检查保存后的骨骼动作和嵌入纹理。"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def main():
    解析 = argparse.ArgumentParser(description=__doc__)
    解析.add_argument('--evidence', required=True, type=Path)
    解析.add_argument('--output', required=True, type=Path)
    参数 = 解析.parse_args(sys.argv[sys.argv.index('--') + 1:])
    if 参数.output.exists():
        raise FileExistsError('回执已存在，拒绝覆盖')
    证据 = json.loads(参数.evidence.read_text(encoding='utf-8'))
    原 = bpy.data.scenes['01原始整组动画']
    单 = bpy.data.scenes['02独立角色动画']
    平移 = Vector(证据['拆分']['展示平移米'])
    逐帧 = []
    for 帧 in (0, 24, 120, 240, 360, 480, 600, 719.5999755859375):
        顶点 = []
        for 场景, 名 in ((原, '0'), (单, '独立丧尸示例')):
            bpy.context.window.scene = 场景
            场景.frame_set(int(帧), subframe=帧 % 1)
            o = 场景.objects[名].evaluated_get(bpy.context.evaluated_depsgraph_get())
            顶点.append([o.matrix_world @ v.co for v in o.data.vertices])
        差 = max((a - (b - 平移)).length for a, b in zip(*顶点))
        if 差 > 1e-4:
            raise AssertionError(f'工程重开后动作差异超出容差：{差}')
        逐帧.append({'帧': 帧, '最大误差米': 差})
    图片 = [i for i in bpy.data.images if i.source == 'FILE']
    assert len(图片) == 26 and all(i.packed_file for i in 图片)
    assert len(单.objects['独立丧尸骨架'].data.bones) == 23
    assert len(bpy.data.scenes) == 4
    assert len([o for o in bpy.data.scenes['04百只群体静态预览'].objects
                if o.type == 'MESH' and o.name.startswith('静态丧尸')]) == 100
    路径 = Path(bpy.data.filepath)
    结果 = {'通过': True, 'Blender版本': bpy.app.version_string,
            '工程名': 路径.name, '工程字节': 路径.stat().st_size,
            '工程摘要': hashlib.sha256(路径.read_bytes()).hexdigest(),
            '场景': list(bpy.data.scenes.keys()), '嵌入贴图数': len(图片),
            '单只骨数': 23, '重开对比': 逐帧,
            '限制': '仅验证Blender保存与回读；不验证GLB导出或Three.js性能'}
    参数.output.write_text(json.dumps(结果, ensure_ascii=False, indent=2), encoding='utf-8')
    print('工程独立重开核验通过', flush=True)


if __name__ == '__main__':
    main()
