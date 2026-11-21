"""母版集合实例使用完整作者坐标系；点位置可编辑，不分解或改写源旋转缩放。"""

import bpy
import numpy as np

FRAME_ATTRIBUTE = '作者坐标系'
SOURCE_GROUP = '来源组'
SOURCE_INDEX = '来源编号'
SOURCE_FLAGS = '来源标记'


def instance_nodes(collection, name):
    tree = bpy.data.node_groups.new(name, 'GeometryNodeTree')
    # 没有本区摆放的原型也须保留链接，后续草丛等阶段继续使用同一来源集合。
    tree.use_fake_user = True
    tree.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    tree.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    nodes, links = tree.nodes, tree.links
    source = nodes.new('NodeGroupInput')
    output = nodes.new('NodeGroupOutput')
    info = nodes.new('GeometryNodeCollectionInfo')
    info.inputs['Collection'].default_value = collection
    info.inputs['Separate Children'].default_value = False
    info.inputs['Reset Children'].default_value = False
    points = nodes.new('GeometryNodeInstanceOnPoints')
    frame = nodes.new('GeometryNodeInputNamedAttribute')
    frame.data_type = 'FLOAT4X4'
    frame.inputs['Name'].default_value = FRAME_ATTRIBUTE
    position = nodes.new('GeometryNodeInputPosition')
    translation = nodes.new('FunctionNodeCombineTransform')
    multiply = nodes.new('FunctionNodeMatrixMultiply')
    transform = nodes.new('GeometryNodeSetInstanceTransform')
    links.new(source.outputs['Geometry'], points.inputs['Points'])
    links.new(info.outputs['Instances'], points.inputs['Instance'])
    links.new(points.outputs['Instances'], transform.inputs['Instances'])
    links.new(position.outputs['Position'], translation.inputs['Translation'])
    links.new(translation.outputs['Transform'], multiply.inputs[0])
    links.new(frame.outputs['Attribute'], multiply.inputs[1])
    links.new(multiply.outputs[0], transform.inputs['Transform'])
    links.new(transform.outputs['Instances'], output.inputs['Geometry'])
    tree['坐标规则'] = '点位置乘作者三阶坐标系；保留镜像、非均匀缩放和原始浮点矩阵'
    return tree


def instance_cloud(collection, name, matrices, groups, indices, flags, node_group=None):
    matrices = np.asarray(matrices, dtype=np.float32)
    mesh = bpy.data.meshes.new(name)
    mesh.vertices.add(len(matrices))
    mesh.vertices.foreach_set('co', matrices[:, :3, 3].ravel())
    frame = matrices.copy()
    frame[:, :3, 3] = 0
    attribute = mesh.attributes.new(FRAME_ATTRIBUTE, 'FLOAT4X4', 'POINT')
    attribute.data.foreach_set('value', frame.transpose(0, 2, 1).ravel())
    for key, values in [(SOURCE_GROUP, groups), (SOURCE_INDEX, indices), (SOURCE_FLAGS, flags)]:
        field = mesh.attributes.new(key, 'INT', 'POINT')
        field.data.foreach_set('value', np.asarray(values, dtype=np.int32))
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    modifier = obj.modifiers.new('作者集合实例', 'NODES')
    modifier.node_group = node_group or instance_nodes(collection, name + '-集合节点')
    obj['实例数'] = len(matrices)
    obj['作者原点保留'] = True
    return obj
