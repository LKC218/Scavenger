"""用相对链接原型、完整矩阵点实例和源表面生成米制全图母版，默认显示总览。"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

import bpy
import numpy as np
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
from 地图执行记录 import Journal, atomic_json, fingerprint
from 全图母版节点 import instance_cloud, instance_nodes, FRAME_ATTRIBUTE, SOURCE_GROUP, SOURCE_INDEX, SOURCE_FLAGS
from 全图表面构建 import SurfaceFactory
from 分页全图实例 import RECORD
from 重建地图装配 import prototype_matrix
from 估算全图容量 import memory_status

RUNTIME = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]], dtype=np.float32)


def read_records(directory):
    base = directory / '实例分页/r01'
    index = json.loads((base / '实例索引.json').read_text('utf-8'))
    arrays, trees = [], []
    for page in index['pages']:
        path = base / page['file']
        if fingerprint(path)['sha256'] != page['identity']['sha256']:
            raise ValueError('实例页摘要变化')
        data = np.fromfile(path, dtype=RECORD, offset=16)
        if len(data) != page['count']:
            raise ValueError('实例页计数变化')
        arrays.append(data)
        selected = data[(data['flags'] & 2) != 0]
        if len(selected):
            bounds = selected['bounds']
            low, high = bounds[:, :3].min(0), bounds[:, 3:].max(0)
            matrix = np.eye(4, dtype=np.float32)
            matrix[:3, :3] = np.diag((high - low) * .5)
            matrix[:3, 3] = (low + high) * .5
            trees.append((matrix, len(selected)))
    records = np.concatenate(arrays)
    if len(records) != index['count']:
        raise ValueError('母版实例数量不守恒')
    matrices = records['matrix'].reshape(-1, 4, 4).transpose(0, 2, 1)
    matrices = RUNTIME.T[None] @ matrices @ RUNTIME[None]
    return index, records, matrices, trees


def new_collection(name, parent):
    collection = bpy.data.collections.new(name)
    parent.children.link(collection)
    return collection


def proxy_collection(name, kind, color):
    collection = bpy.data.collections.new(name)
    if kind == 'cube':
        bpy.ops.mesh.primitive_cube_add(size=1)
    else:
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata([(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)], [],
                         [(0, 2, 4), (2, 1, 4), (1, 3, 4), (3, 0, 4), (2, 0, 5), (1, 2, 5), (3, 1, 5), (0, 3, 5)])
        obj = bpy.data.objects.new(name, mesh)
    if kind == 'cube':
        obj = bpy.context.object
        for current in list(obj.users_collection):
            current.objects.unlink(obj)
    collection.objects.link(obj)
    material = bpy.data.materials.new(name)
    material.diffuse_color = (*color, 1)
    material.use_nodes = True
    bsdf = material.node_tree.nodes.get('Principled BSDF')
    bsdf.inputs['Base Color'].default_value = (*color, 1)
    bsdf.inputs['Roughness'].default_value = 1
    obj.data.materials.append(material)
    return collection


def studio(scene):
    scene.render.engine = 'BLENDER_EEVEE'
    scene.render.resolution_x = scene.render.resolution_y = 1800
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    scene.world = bpy.data.worlds.new('白天审阅环境')
    scene.world.use_nodes = True
    scene.world.node_tree.nodes['Background'].inputs[0].default_value = (.44, .5, .6, 1)
    scene.world.node_tree.nodes['Background'].inputs[1].default_value = .8
    sun = bpy.data.objects.new('审阅日光', bpy.data.lights.new('审阅日光', 'SUN'))
    scene.collection.objects.link(sun)
    sun.data.energy = 2.5
    sun.rotation_euler = (.6, -.4, -.5)
    camera = bpy.data.objects.new('总览相机', bpy.data.cameras.new('总览相机'))
    scene.collection.objects.link(camera)
    camera.data.type = 'ORTHO'
    camera.data.ortho_scale = 9900
    camera.data.clip_end = 200000
    camera.location = (75, -350, 18000)
    camera.rotation_euler = (0, 0, 0)
    scene.camera = camera


def build(directory, revision, background_revision=None):
    capacity = json.loads((directory / '容量预检/r01.json').read_text('utf-8'))
    if not capacity['passed'] or memory_status()['physical_available'] < capacity['estimate']['master_ram'] + 1024**3:
        raise MemoryError('母版启动时余量不足，保持已有产物')
    output = directory / '总装配' / revision
    output.mkdir(parents=True, exist_ok=False)
    journal = Journal(directory)
    try:
        journal.update(phase='A2全图母版', operation='读取冻结实例及完整作者矩阵')
        index, records, matrices, tree_proxies = read_records(directory)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        scene = bpy.context.scene
        scene.unit_settings.system, scene.unit_settings.scale_length = 'METRIC', 1
        scene['任务'] = 'Airbase全图双层入库-V001-01a1a602'
        scene['范围说明'] = '完整Bank和树木及127个直接表面；草丛原型和密度单列，按授权不实例化'
        scene['显示说明'] = '默认显示总览。高细节按1公里集合切换，点位置和作者坐标系保持可编辑'
        surfaces = new_collection('源表面', scene.collection)
        high = new_collection('源摆放分区', scene.collection)
        overview = new_collection('总览聚合', scene.collection)
        regions = {i: new_collection(f'分区-{i % 9:02}-{i // 9:02}', high) for i in range(81)}
        for layer in scene.view_layers[0].layer_collection.children[high.name].children:
            layer.exclude = True
        links, collections, nodes = [], [], []
        for pi, p in enumerate(index['prototypes']):
            journal.update(operation='相对链接独立原型', current=pi + 1, total=len(index['prototypes']))
            folder = directory / Path(p['glb']).parent
            build_receipt = json.loads((folder / '构建凭证.json').read_text('utf-8'))
            file = folder / '原型.blend'
            if fingerprint(file)['sha256'] != build_receipt['files']['原型.blend']['sha256']:
                raise ValueError('独立原型工程变化')
            with bpy.data.libraries.load(str(file), link=True) as (available, requested):
                requested.collections = ['模型']
            collection = requested.collections[0]
            if collection is None:
                raise ValueError('原型模型集合链接失败')
            collections.append(collection)
            nodes.append(instance_nodes(collection, p['id']))
            links.append({'asset_id': p['id'], 'file': file.relative_to(directory).as_posix(), 'identity': fingerprint(file)})
        factory = SurfaceFactory(directory, background_revision=background_revision)
        for number, part in enumerate(factory.data['parts']):
            journal.update(operation='装配源表面及真实图集', current=number + 1, total=len(factory.data['parts']))
            factory.create(part, surfaces)
        positions = records['matrix'][:, [12, 14]]
        cells = np.clip(np.floor((positions - [index['bounds']['minX'], index['bounds']['minZ']]) / 1000), 0, 8).astype(int)
        region_ids = cells[:, 0] + cells[:, 1] * 9
        keys = region_ids * len(collections) + records['prototype']
        order = np.argsort(keys, kind='stable')
        unique, starts, counts = np.unique(keys[order], return_index=True, return_counts=True)
        clouds = []
        for number, (key, start, count) in enumerate(zip(unique, starts, counts)):
            if number % 64 == 0:
                if memory_status()['physical_available'] < 2 * 1024**3:
                    raise MemoryError('母版制作内存余量不足2GiB，停止本轮执行器')
                journal.update(operation='生成分区完整矩阵集合实例', current=number + 1, total=len(unique))
            selected = order[start:start + count]
            pi, region = int(key % len(collections)), int(key // len(collections))
            obj = instance_cloud(collections[pi], f'源实例-{region:02}-{pi:03}', matrices[selected], records['group'][selected],
                                 records['source_index'][selected], records['flags'][selected], nodes[pi])
            obj['原型编号'], obj['原型ID'], obj['分区编号'] = pi, index['prototypes'][pi]['id'], region
            regions[region].objects.link(obj)
            clouds.append({'object': obj.name, 'region': region, 'prototype': pi, 'count': int(count)})
        # 总览只作聚合表示，源高细节集合保留全部点，不把简化表示计作来源缺失。
        cube = proxy_collection('总览建筑原型', 'cube', (.3, .32, .34))
        building_ids = np.flatnonzero(np.isin(records['prototype'], [i for i, p in enumerate(index['prototypes']) if p['category'] == 'building']))
        boxes = records['bounds'][building_ids]
        coarse = np.tile(np.eye(4, dtype=np.float32), (len(boxes), 1, 1))
        coarse[:, :3, 3] = (boxes[:, :3] + boxes[:, 3:]) * .5
        for axis in range(3):
            coarse[:, axis, axis] = boxes[:, axis + 3] - boxes[:, axis]
        coarse = RUNTIME.T[None] @ coarse @ RUNTIME[None]
        obj = instance_cloud(cube, '总览建筑聚合', coarse, records['group'][building_ids], records['source_index'][building_ids],
                             records['flags'][building_ids])
        obj['表示层'] = '总览AABB聚合，不是基础几何'
        overview.objects.link(obj)
        canopy = proxy_collection('总览植被原型', 'sphere', (.065, .13, .035))
        tree_ids = np.flatnonzero((records['flags'] & 2) != 0)
        tree_boxes = records['bounds'][tree_ids]
        tree_matrices = np.tile(np.eye(4, dtype=np.float32), (len(tree_ids), 1, 1))
        tree_matrices[:, :3, 3] = (tree_boxes[:, :3] + tree_boxes[:, 3:]) * .5
        for axis in range(3):
            tree_matrices[:, axis, axis] = (tree_boxes[:, axis + 3] - tree_boxes[:, axis]) * .5
        tree_matrices = RUNTIME.T[None] @ tree_matrices @ RUNTIME[None]
        obj = instance_cloud(canopy, '总览植被聚合', tree_matrices, records['group'][tree_ids], records['source_index'][tree_ids],
                             records['flags'][tree_ids])
        overview.objects.link(obj)
        obj['表示层'] = '各源树位8面轮廓，不扩大为整页树冠；原型高细节完整保留在分区点实例'
        for library in bpy.data.libraries:
            library.filepath = bpy.path.relpath(library.filepath, start=str(output))
        for image in bpy.data.images:
            if image.library is None and image.source == 'FILE':
                if not image.packed_file:
                    image.pack()
                image.filepath = bpy.path.relpath(image.filepath, start=str(output))
        studio(scene)
        file = output / 'Airbase全图母版.blend'
        journal.update(operation='保存全图米制母版', source_instances=len(records), clouds=len(clouds))
        bpy.ops.wm.save_as_mainfile(filepath=str(file), check_existing=False, relative_remap=False)
        result = {'status': 'Bank树木及直接表面母版候选完成，草丛按授权省略，待独立重开及逐区审图',
            'instances': len(records), 'cloud_objects': len(clouds), 'clouds': clouds, 'surfaces': factory.records, 'libraries': links,
            'index': fingerprint(directory / '实例分页/r01/实例索引.json'), 'source_program': fingerprint(Path(__file__)),
            'node_program': fingerprint(Path(__file__).with_name('全图母版节点.py')), 'blend_identity': fingerprint(file),
            'source_normals_and_colors_retained': True, 'human_acceptance': '待人工验收'}
        if background_revision:
            metadata = directory / '背景派生' / background_revision / '背景底色.json'
            result['background_atlas'] = {'file': metadata.relative_to(directory).as_posix(), 'identity': fingerprint(metadata)}
        atomic_json(output / '母版构建凭证.json', result)
        scene.render.filepath = str(output / '总览-俯视.png')
        bpy.ops.render.render(write_still=True)
        camera = scene.camera
        camera.location = (75, -12500, 11000)
        camera.rotation_euler = (Vector((75, -350, 30)) - camera.location).to_track_quat('-Z', 'Y').to_euler()
        scene.render.filepath = str(output / '总览-斜视.png')
        bpy.ops.render.render(write_still=True)
        journal.close(status='全图母版候选完成，继续独立重开及细节制作', instances=len(records), clouds=len(clouds))
        print(json.dumps({'instances': len(records), 'clouds': len(clouds), 'surfaces': len(factory.records)}, ensure_ascii=False))
    except BaseException as error:
        journal.close(status='全图母版构建失败，保留恢复点', error=repr(error))
        raise


def verify(directory, revision, run):
    output = directory / '总装配' / revision
    evidence = output / '独立核验' / run
    evidence.mkdir(parents=True, exist_ok=False)
    record = json.loads((output / '母版构建凭证.json').read_text('utf-8'))
    blend = output / 'Airbase全图母版.blend'
    if fingerprint(blend)['sha256'] != record['blend_identity']['sha256']:
        raise ValueError('母版文件摘要变化')
    index, data, matrices, _ = read_records(directory)
    expected_keys = (data['group'].astype(np.uint64) << 32) | data['source_index'].astype(np.uint64)
    order = np.argsort(expected_keys)
    sorted_keys = expected_keys[order]
    seen = np.zeros(len(data), dtype=bool)
    journal = Journal(directory)
    try:
        bpy.ops.wm.open_mainfile(filepath=str(blend), load_ui=False)
        if bpy.context.scene.unit_settings.scale_length != 1:
            raise ValueError('母版米制设置不符')
        for library in bpy.data.libraries:
            if not library.filepath.startswith('//') or not Path(bpy.path.abspath(library.filepath)).is_file():
                raise ValueError('母版相对库链接不闭合')
        if any(img.source == 'FILE' and not img.packed_file for img in bpy.data.images):
            raise ValueError('母版存在未打包图像')
        max_error = 0
        for number, cloud in enumerate(record['clouds']):
            journal.update(phase='A2母版独立回读', operation='逐点核对作者矩阵及稳定来源', current=number + 1, total=len(record['clouds']))
            obj = bpy.data.objects[cloud['object']]
            mesh = obj.data
            count = len(mesh.vertices)
            points, frames = np.empty(count * 3, dtype=np.float32), np.empty(count * 16, dtype=np.float32)
            mesh.vertices.foreach_get('co', points)
            mesh.attributes[FRAME_ATTRIBUTE].data.foreach_get('value', frames)
            actual = frames.reshape(-1, 4, 4).transpose(0, 2, 1).copy()
            actual[:, :3, 3] = points.reshape(-1, 3)
            groups, indices = np.empty(count, dtype=np.int32), np.empty(count, dtype=np.int32)
            mesh.attributes[SOURCE_GROUP].data.foreach_get('value', groups)
            mesh.attributes[SOURCE_INDEX].data.foreach_get('value', indices)
            keys = (groups.astype(np.uint64) << 32) | indices.astype(np.uint64)
            positions = np.searchsorted(sorted_keys, keys)
            if np.any(positions >= len(data)) or not np.array_equal(sorted_keys[positions], keys):
                raise ValueError('母版实例来源不在冻结分页中')
            selected = order[positions]
            if seen[selected].any() or len(np.unique(selected)) != count or np.any(data['prototype'][selected] != cloud['prototype']):
                raise ValueError('母版来源重复或原型错配')
            seen[selected] = True
            max_error = max(max_error, float(np.max(np.abs(actual - matrices[selected]))))
            if max_error > .00002:
                raise ValueError('母版作者矩阵漂移')
        if not seen.all():
            raise ValueError('母版有遗漏的Bank或树木')
        for p in record['surfaces']:
            if len(bpy.data.objects[p['name']].data.polygons) != p['triangles']:
                raise ValueError('母版源表面面数变化')
        background_check = None
        if record.get('background_atlas'):
            metadata = directory / record['background_atlas']['file']
            if fingerprint(metadata)['sha256'] != record['background_atlas']['identity']['sha256']:
                raise ValueError('母版背景底色凭证变化')
            atlas = json.loads(metadata.read_text('utf-8'))
            surface = next(p for p in record['surfaces'] if p['source_key'] == atlas['source_key'])
            obj = bpy.data.objects[surface['name']];mesh = obj.data
            if mesh.uv_layers.get('源UV0') is None:
                raise ValueError('母版背景未保留源UV0')
            points = np.empty(len(mesh.vertices)*3, dtype=np.float32);mesh.vertices.foreach_get('co', points)
            world = np.asarray(obj.matrix_world);points = points.reshape(-1, 3) @ world[:3, :3].T + world[:3, 3]
            loops = np.empty(len(mesh.loops), dtype=np.int32);mesh.loops.foreach_get('vertex_index', loops)
            uv = np.empty(len(mesh.loops)*2, dtype=np.float32);mesh.uv_layers['UVMap'].data.foreach_get('uv', uv)
            bounds = atlas['bounds_source_xz'];size = bounds[1]-bounds[0]
            expected = np.column_stack([(points[:, 0]+4425-bounds[0])/size, (points[:, 1]+4850-bounds[2])/size])[loops]
            uv_error = float(np.max(np.abs(uv.reshape(-1, 2)-expected)))
            image_path = (metadata.parent / atlas['image']).resolve()
            images = [image for image in bpy.data.images if image.library is None and Path(bpy.path.abspath(image.filepath)).resolve() == image_path]
            if len(images) != 1 or hashlib.sha256(bytes(images[0].packed_file.data)).hexdigest() != atlas['image_identity']['sha256'] or uv_error > 1e-6:
                raise ValueError('母版背景打包图像或世界UV不符')
            background_check = {'source_key': atlas['source_key'], 'world_uv_max_error': uv_error, 'packed_image_sha256': atlas['image_identity']['sha256']}
        # 在新进程中实际求值每个原型的一份集合实例，核对完整作者部件矩阵。
        scene = bpy.context.scene
        scene.view_layers[0].layer_collection.children['总览聚合'].exclude = True
        probe = new_collection('独立原型求值', scene.collection)
        expected = {}
        for pi, p in enumerate(index['prototypes']):
            library_file = str((directory / Path(p['glb']).parent / '原型.blend').resolve())
            collection = next(c for c in bpy.data.collections if c.library and str(Path(bpy.path.abspath(c.library.filepath)).resolve()) == library_file)
            obj = instance_cloud(collection, f'求值-{pi:03}', np.eye(4)[None], [0], [pi], [0])
            probe.objects.link(obj)
            for part in collection.all_objects:
                if part.type == 'MESH':
                    expected[(library_file, part.name)] = np.asarray(prototype_matrix(part))
        scene.view_layers[0].update()
        actual_count, max_part_error = 0, 0
        for item in bpy.context.evaluated_depsgraph_get().object_instances:
            if not item.is_instance or item.object.type != 'MESH':
                continue
            obj = item.object.original
            if obj.library is None:
                continue
            key = (str(Path(bpy.path.abspath(obj.library.filepath)).resolve()), obj.name)
            if key not in expected:
                raise ValueError('求值出现未登记原型部件')
            error = float(np.abs(np.asarray(item.matrix_world) - expected[key]).max())
            max_part_error = max(max_part_error, error)
            actual_count += 1
        if actual_count != len(expected) or max_part_error > .001:
            raise ValueError(f'真实集合求值不符：{actual_count}/{len(expected)}，误差{max_part_error}')
        if fingerprint(blend)['sha256'] != record['blend_identity']['sha256']:
            raise ValueError('独立回读修改了母版源文件')
        result = {'passed': True, 'instances': int(seen.sum()), 'max_matrix_error': max_error, 'surface_parts': len(record['surfaces']),
            'actual_prototype_mesh_parts': actual_count, 'prototype_part_matrix_error': max_part_error,
            'blend_identity': fingerprint(blend), 'build_receipt': fingerprint(output / '母版构建凭证.json'),
            'scope': '完整矩阵、相对依赖、源表面及实际原型求值；草丛、逐区画面和运行验收另作核对', 'human_acceptance': '待人工验收'}
        result['background_check'] = background_check
        atomic_json(evidence / '核验凭证.json', result)
        journal.close(status='母版源实例与表面独立回读通过，继续A2', verified_instances=int(seen.sum()))
        print(json.dumps({'passed': True, 'instances': int(seen.sum()), 'mesh_parts': actual_count}, ensure_ascii=False))
    except BaseException as error:
        journal.close(status='全图母版独立回读失败，保留恢复点', error=repr(error))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--mode', choices=['build', 'verify'], default='build')
    parser.add_argument('--run', default='r01')
    parser.add_argument('--background-revision')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    directory = args.directory.resolve()
    directory.relative_to(ROOT / 'local-data/断箭/批次')
    for name in [args.revision, args.run, args.background_revision or 'unused']:
        if not name or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in name):
            raise ValueError('无效修订名')
    build(directory, args.revision, args.background_revision) if args.mode == 'build' else verify(directory, args.revision, args.run)
