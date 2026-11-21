"""在独立 Blender 进程中测试当前丧尸 GLB；不导出正式游戏资源。"""

import argparse
import hashlib
import json
import math
import random
import re
import struct
import sys
import time
from pathlib import Path

import bpy
from mathutils import Vector


def 摘要(路径):
    return hashlib.sha256(路径.read_bytes()).hexdigest()


def 世界顶点(物, 图):
    评估 = 物.evaluated_get(图)
    return [评估.matrix_world @ v.co for v in 评估.data.vertices]


def 范围(顶点):
    return [[min(p[i] for p in 顶点) for i in range(3)],
            [max(p[i] for p in 顶点) for i in range(3)]]


def 设置摄影棚(场景, 位置, 目标, 宽度, 分辨率=(1280, 900)):
    场景.render.engine = 'CYCLES'
    场景.cycles.samples = 12
    场景.cycles.use_denoising = True
    场景.render.resolution_x, 场景.render.resolution_y = 分辨率
    场景.render.resolution_percentage = 100
    场景.render.image_settings.file_format = 'PNG'
    场景.world = bpy.data.worlds.new(场景.name + '环境')
    场景.world.use_nodes = True
    背景 = 场景.world.node_tree.nodes['Background']
    背景.inputs['Color'].default_value = (.30, .33, .38, 1)
    背景.inputs['Strength'].default_value = .65
    地面 = bpy.data.meshes.new(场景.name + '地面')
    地面.from_pydata([(-60, -60, -.005), (60, -60, -.005),
                     (60, 60, -.005), (-60, 60, -.005)], [], [(0, 1, 2, 3)])
    物 = bpy.data.objects.new('测试地面', 地面)
    场景.collection.objects.link(物)
    灯 = bpy.data.lights.new('测试柔光', 'AREA')
    灯.energy, 灯.size = 2400, 14
    物 = bpy.data.objects.new('测试柔光', 灯)
    物.location = (0, 0, 12)
    场景.collection.objects.link(物)
    数据 = bpy.data.cameras.new('测试镜头')
    数据.type, 数据.ortho_scale = 'ORTHO', 宽度
    相机 = bpy.data.objects.new('测试镜头', 数据)
    相机.location = 位置
    相机.rotation_euler = (Vector(目标) - 相机.location).to_track_quat('-Z', 'Y').to_euler()
    场景.collection.objects.link(相机)
    场景.camera = 相机
    场景.render.fps = 24
    场景.frame_start, 场景.frame_end = 0, 719
    return 相机


def 渲染(场景, 路径):
    bpy.context.window.scene = 场景
    场景.render.filepath = str(路径)
    bpy.ops.render.render(write_still=True)


def 快照(源, 场景, 目标位置, 名称, 朝向=0):
    图 = bpy.context.evaluated_depsgraph_get()
    评估 = 源.evaluated_get(图)
    网格 = bpy.data.meshes.new_from_object(评估, depsgraph=图)
    网格.transform(评估.matrix_world)
    下, 上 = 范围([v.co for v in 网格.vertices])
    中 = Vector(((下[0] + 上[0]) / 2, (下[1] + 上[1]) / 2, 下[2]))
    for v in 网格.vertices:
        v.co -= 中
    物 = bpy.data.objects.new(名称, 网格)
    场景.collection.objects.link(物)
    物.location = 目标位置
    物.rotation_euler.z = 朝向
    return 物


def 独立角色(源, 骨架, 场景):
    使用骨 = {源.vertex_groups[g.group].name for v in 源.data.vertices
              for g in v.groups if g.weight > 1e-5}
    保留 = set(使用骨)
    for 名 in 使用骨:
        保留.update(b.name for b in 骨架.data.bones[名].parent_recursive)
    新骨架 = 骨架.copy()
    新骨架.data = 骨架.data.copy()
    新骨架.name = '独立丧尸骨架'
    场景.collection.objects.link(新骨架)
    新骨架.parent = None
    新骨架.matrix_world = 骨架.matrix_world.copy()
    新骨架.animation_data_clear()
    新骨架.animation_data_create()
    动作 = 骨架.animation_data.action.copy()
    槽名 = 骨架.animation_data.action_slot.identifier
    for 层 in 动作.layers:
        for 条 in 层.strips:
            for 袋 in 条.channelbags:
                for 曲线 in list(袋.fcurves):
                    匹配 = re.match(r'pose\.bones\["([^"]+)"\]', 曲线.data_path)
                    if 匹配 and 匹配.group(1) not in 保留:
                        袋.fcurves.remove(曲线)
    槽 = next(s for s in 动作.slots if s.identifier == 槽名)
    for 多余槽 in list(动作.slots):
        if 多余槽 != 槽:
            动作.slots.remove(多余槽)
    动作.name = '单只原始动作未切段'
    新骨架.animation_data.action = 动作
    新骨架.animation_data.action_slot = 槽
    bpy.ops.object.select_all(action='DESELECT')
    新骨架.select_set(True)
    bpy.context.view_layer.objects.active = 新骨架
    bpy.ops.object.mode_set(mode='EDIT')
    for 骨 in list(新骨架.data.edit_bones):
        if 骨.name not in 保留:
            新骨架.data.edit_bones.remove(骨)
    bpy.ops.object.mode_set(mode='OBJECT')
    新物 = 源.copy()
    新物.data = 源.data.copy()
    新物.name = '独立丧尸示例'
    场景.collection.objects.link(新物)
    # 本文件的形态键已通过零位移检查，删除空形态键及其无效动画。
    if 新物.data.shape_keys:
        新物.shape_key_clear()
    新物.parent = 新骨架
    for 修改器 in 新物.modifiers:
        if 修改器.type == 'ARMATURE':
            修改器.object = 新骨架
    for 组 in list(新物.vertex_groups):
        if 组.name not in 保留:
            新物.vertex_groups.remove(组)
    return 新物, 新骨架, 使用骨


def main():
    解析 = argparse.ArgumentParser(description=__doc__)
    解析.add_argument('--input', required=True, type=Path)
    解析.add_argument('--output', required=True, type=Path)
    参数 = 解析.parse_args(sys.argv[sys.argv.index('--') + 1:])
    源, 输出 = 参数.input.resolve(), 参数.output.resolve()
    if 输出.exists() or 输出 == 源.parent or not 源.is_file():
        raise ValueError('输入须为现有文件，输出须为独立且不存在的目录')
    开始 = time.time()
    源摘要 = 摘要(源)
    内容 = 源.read_bytes()
    if struct.unpack_from('<4sII', 内容) != (b'glTF', 2, len(内容)):
        raise ValueError('输入不是完整的 GLB 2 文件')
    长度 = struct.unpack_from('<I', 内容, 12)[0]
    元数据 = json.loads(内容[20:20 + 长度])
    输出.mkdir(parents=True)
    (输出 / '动画序列').mkdir()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.render.fps = 24
    bpy.ops.import_scene.gltf(filepath=str(源))
    原场景 = bpy.context.scene
    原场景.name = '01原始整组动画'
    骨架 = next(o for o in 原场景.objects if o.type == 'ARMATURE')
    网格 = sorted([o for o in 原场景.objects if o.type == 'MESH' and
                  any(m.type == 'ARMATURE' and m.object == 骨架 for m in o.modifiers)],
                 key=lambda o: int(o.name))
    if len(网格) != 13:
        raise ValueError('本脚本仅适用于当前已核对的13只丧尸文件')
    for o in 原场景.objects:
        if o.type == 'MESH' and o not in 网格:
            o.hide_render = True
            o.hide_set(True)
    审计 = {'Blender版本': bpy.app.version_string, '源文件': str(源),
            '源字节': len(内容), '源摘要': 源摘要, '来源声明': 元数据['asset'],
            '帧率': 24, '动画秒数': float(骨架.animation_data.action.frame_range[1]) / 24,
            '原骨数': len(骨架.data.bones), '网格': [], '图像': []}
    原场景.frame_set(0)
    for o in 网格:
        o.data.calc_loop_triangles()
        空形态差 = max((v.co - o.data.shape_keys.key_blocks[0].data[i].co).length
                       for i, v in enumerate(o.data.shape_keys.key_blocks[1].data))
        if 空形态差 > 1e-8:
            raise ValueError('发现有效形态键，需要专门保留，停止简化')
        使用 = {o.vertex_groups[g.group].name for v in o.data.vertices for g in v.groups
                if g.weight > 1e-5}
        保留 = set(使用)
        for 名 in 使用:
            保留.update(b.name for b in 骨架.data.bones[名].parent_recursive)
        审计['网格'].append({'编号': o.name, '顶点': len(o.data.vertices),
                            '三角形': len(o.data.loop_triangles), '加权骨数': len(使用),
                            '含祖先骨数': len(保留), '材质': [m.name for m in o.data.materials],
                            '空形态最大差': 空形态差,
                            '初始包围盒': 范围(世界顶点(o, bpy.context.evaluated_depsgraph_get()))})
    for 图 in bpy.data.images:
        审计['图像'].append({'名': 图.name, '宽': 图.size[0], '高': 图.size[1], '通道': 图.channels})
    # 先在同一场景中比较拆分前后的实际蒙皮顶点，避免只按骨骼数量判断成功。
    单只, 单骨, 使用骨 = 独立角色(网格[0], 骨架, 原场景)
    最大差 = 0.0
    逐帧 = []
    首末 = []
    根 = next(b for b in 骨架.pose.bones if b.name in 使用骨 and 'Hip' in b.name)
    for 帧 in range(721):
        时 = min(帧, 719.5999755859375)
        原场景.frame_set(int(时), subframe=时 % 1)
        图 = bpy.context.evaluated_depsgraph_get()
        原顶, 新顶 = 世界顶点(网格[0], 图), 世界顶点(单只, 图)
        差 = max((a - b).length for a, b in zip(原顶, 新顶))
        最大差 = max(最大差, 差)
        逐帧.append({'帧': 时, '秒': 时 / 24, '拆分最大误差': 差,
                     '髋世界位置': list(骨架.matrix_world @ 根.matrix.translation)})
        if 帧 in (0, 720):
            首末.append(原顶)
    if 最大差 > 1e-4:
        raise ValueError(f'拆分改变了动画，最大误差 {最大差}')
    跳变 = [(a - b).length for a, b in zip(*首末)]
    审计['拆分'] = {'示例编号': '0', '保留骨数': len(单骨.data.bones),
                    '比较次数': len(逐帧), '全部顶点最大误差米': 最大差,
                    '首末顶点最大差米': max(跳变), '首末顶点平均差米': sum(跳变) / len(跳变),
                    '逐帧': 逐帧}
    原场景.frame_set(0)
    原顶 = 世界顶点(单只, bpy.context.evaluated_depsgraph_get())
    下, 上 = 范围(原顶)
    位移 = Vector((-(下[0] + 上[0]) / 2, -(下[1] + 上[1]) / 2, -下[2]))
    单场景 = bpy.data.scenes.new('02独立角色动画')
    控制 = bpy.data.objects.new('移动此对象可整体放置角色', None)
    单场景.collection.objects.link(控制)
    for 物 in (单只, 单骨):
        原场景.collection.objects.unlink(物)
        单场景.collection.objects.link(物)
    单骨.parent = 控制
    控制.location = 位移
    审计['拆分']['展示平移米'] = list(位移)
    设置摄影棚(原场景, (13, -21, 17), (0, -4, .7), 17)
    设置摄影棚(单场景, (-4, 6, 3.2), (0, 0, .85), 3.3, (640, 640))
    渲染(原场景, 输出 / '原始总览.png')
    单场景.frame_set(0)
    渲染(单场景, 输出 / '独立角色.png')
    # 全段按0.5秒生成视觉序列，逐帧数值比较已覆盖上面的721个时刻。
    单场景.cycles.samples = 4
    单场景.render.resolution_x = 单场景.render.resolution_y = 240
    for 序, 秒 in enumerate([i / 2 for i in range(60)] + [719.5999755859375 / 24]):
        帧 = 秒 * 24
        单场景.frame_set(int(帧), subframe=帧 % 1)
        渲染(单场景, 输出 / '动画序列' / f'{序:03d}.png')
    单场景.render.resolution_x = 单场景.render.resolution_y = 640
    单场景.cycles.samples = 12
    换色场景 = bpy.data.scenes.new('03整体彩色测试静态')
    群场景 = bpy.data.scenes.new('04百只群体静态预览')
    bpy.context.window.scene = 原场景
    原场景.frame_set(72)
    色彩 = [(1, 1, 1, 1), (.4, .85, .45, 1), (.5, .62, 1, 1), (1, .52, .3, 1)]
    for i, 颜色 in enumerate(色彩):
        o = 快照(网格[0], 换色场景, ((i - 1.5) * 1.8, 0, 0), f'整体染色示例{i}')
        材质 = o.data.materials[0].copy()
        o.data.materials[0] = 材质
        节点 = 材质.node_tree
        主 = next(n for n in 节点.nodes if n.type == 'BSDF_PRINCIPLED')
        连线 = 主.inputs['Base Color'].links[0]
        图口 = 连线.from_socket
        节点.links.remove(连线)
        混合 = 节点.nodes.new('ShaderNodeMixRGB')
        混合.blend_type = 'MULTIPLY'
        混合.inputs[0].default_value = 1
        混合.inputs[2].default_value = 颜色
        节点.links.new(图口, 混合.inputs[1])
        节点.links.new(混合.outputs[0], 主.inputs['Base Color'])
    设置摄影棚(换色场景, (8, -15, 8), (0, 0, .8), 9, (1280, 720))
    渲染(换色场景, 输出 / '整体换色.png')
    随机 = random.Random(261103)
    for i in range(100):
        bpy.context.window.scene = 原场景
        原场景.frame_set(随机.randrange(720))
        位置 = ((i % 10 - 4.5) * 1.5 + 随机.uniform(-.25, .25),
                (i // 10 - 4.5) * 1.5 + 随机.uniform(-.25, .25), 0)
        快照(网格[i % 13], 群场景, 位置, f'静态丧尸{i:03d}', 随机.uniform(-math.pi, math.pi))
    设置摄影棚(群场景, (19, -24, 27), (0, 0, .3), 23, (1440, 1080))
    渲染(群场景, 输出 / '百只群体.png')
    审计['群体'] = {'数量': 100, '类型': '从原动画不同时间取姿态的静态网格，无骨骼运行',
                    '三角形': sum(len(o.data.polygons) for o in 群场景.objects
                                  if o.type == 'MESH' and o.name.startswith('静态丧尸'))}
    文本 = bpy.data.texts.new('测试说明与来源')
    文本.write('13只丧尸原始动画、单只精简骨架、整体换色及100只静态预览。\n'
               '场景02按空格播放；24FPS，0至719帧，尚未切分游戏动作。\n'
               '场景03是整体染色，皮肤衣服同时变化；场景04不是性能测试。\n'
               '作者Jungle Jim，Super Low-poly Stylized Zombies (animated)。\n'
               'CC BY 4.0，来源及许可见本目录README和检查结果。\n'
               '当前工程增加展示场景，并对一个角色精简骨架；源GLB只读。')
    bpy.context.window.scene = 单场景
    单场景.frame_set(0)
    bpy.ops.object.select_all(action='DESELECT')
    单只.select_set(True)
    bpy.context.view_layer.objects.active = 单只
    for 屏幕 in bpy.data.screens:
        for 区域 in 屏幕.areas:
            if 区域.type == 'VIEW_3D':
                空间 = 区域.spaces.active
                空间.shading.type = 'MATERIAL'
                空间.region_3d.view_perspective = 'CAMERA'
    bpy.ops.file.pack_all()
    bpy.ops.wm.save_as_mainfile(filepath=str(输出 / '丧尸模型测试.blend'))
    审计['源处理后摘要'] = 摘要(源)
    if 审计['源处理后摘要'] != 源摘要:
        raise ValueError('源文件处理前后不一致')
    审计['用时秒'] = time.time() - 开始
    (输出 / '检查结果.json').write_text(json.dumps(审计, ensure_ascii=False, indent=2), encoding='utf-8')
    print('丧尸测试完成', 审计['拆分']['保留骨数'], 最大差, flush=True)


if __name__ == '__main__':
    main()
