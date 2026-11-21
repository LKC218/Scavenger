"""只读核查两个已批准丧尸预制体的源层级、蒙皮与原生动画依赖。"""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import struct
import time

import numpy as np
import UnityPy
from UnityPy.helpers.MeshHelper import MeshHandler
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
SOURCES = {
    'Walker': 'Assets/Resources_moved/ModelPrefabs/Zombie/Walker/ZOM_Walker1.prefab',
    'Runner': 'Assets/Resources_moved/ModelPrefabs/Zombie/Runner/ZOM_Runner1.prefab',
}


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)


def fingerprint(path):
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'path': str(path), 'bytes': path.stat().st_size, 'sha256': digest}


def extract(batch):
    cache = ROOT/'local-data/断箭/批次/BA-26273833-001/解析缓存'
    manifest = json.loads((cache/'解析缓存清单.json').read_text('utf-8'))
    config = json.loads((ROOT/'local-data/断箭/本地配置.json').read_text('utf-8'))
    game_bundle = Path(config['game_directory'])/'BrokenArrow_Data/StreamingAssets/aa/PC'/manifest['source_relative']
    inputs = []
    for node in manifest['nodes']:
        item = fingerprint(cache/node['name'])
        if item['sha256'] != node['sha256']:
            raise ValueError('既有缓存摘要变化')
        inputs.append(item)
    inputs.append(fingerprint(game_bundle))
    with game_bundle.open('rb') as stream:
        signature = bytearray()
        while (value := stream.read(1)) != b'\0': signature.extend(value)
        header_version = struct.unpack('>I', stream.read(4))[0]
    write(batch/'输入身份.json', {'build': '26273833', 'signature': signature.decode(), 'header_version': header_version,
                                'engine': manifest['engine'], 'unitypy': UnityPy.__version__, 'files': inputs})
    env = UnityPy.Environment(path=str(cache))
    af = env.load_file(str(cache/manifest['nodes'][0]['name']))
    discovery = json.loads((batch/'来源探查.json').read_text('utf-8'))
    for role, info in discovery['roles'].items():
        target = batch/'角色'/role
        target.mkdir(parents=True, exist_ok=False)
        materials = {}
        meshes = []
        for node in info['nodes']:
            for component in node['components']:
                if component['type'] != 'SkinnedMeshRenderer': continue
                data = component['data']; mid = data['m_Mesh']['m_PathID']
                obj = af.objects[mid]; mesh = obj.read(); handler = MeshHandler(mesh); handler.process()
                triangles, slots = [], []
                for i, ts in enumerate(handler.get_triangles()):
                    offset = mesh.m_SubMeshes[i].baseVertex or 0
                    triangles.extend(tuple(int(k)+offset for k in t) for t in ts); slots.extend([i]*len(ts))
                bind = np.array([[[getattr(pose,f'e{i}{j}') for j in range(4)] for i in range(4)] for pose in mesh.m_BindPose])
                arrays = dict(vertices=np.asarray(handler.m_Vertices)[:,:3], normals=np.asarray(handler.m_Normals)[:,:3],
                              uv0=np.asarray(handler.m_UV0)[:,:2], triangles=np.asarray(triangles,dtype=np.int32),
                              material_indices=np.asarray(slots,dtype=np.int32), weights=np.asarray(handler.m_BoneWeights),
                              indices=np.asarray(handler.m_BoneIndices,dtype=np.int32), bind=bind)
                arrays['tangents']=np.asarray(handler.m_Tangents or [])
                if not np.isfinite(arrays['vertices']).all() or not np.allclose(arrays['weights'].sum(axis=1),1,atol=2e-4):
                    raise ValueError('顶点或源蒙皮权重异常')
                np.savez_compressed(target/f'{node["name"]}.npz',**arrays)
                meshes.append({'name':node['name'],'node_id':node['id'],'renderer_id':component['id'],'mesh_id':str(mid),
                               'vertices':len(arrays['vertices']),'triangles':len(triangles),'bone_ids':[str(x['m_PathID']) for x in data['m_Bones']],
                               'materials':[str(x['m_PathID']) for x in data['m_Materials']]})
                for ptr in data['m_Materials']:
                    material_id = ptr['m_PathID']
                    if str(material_id) in materials: continue
                    mat = af.objects[material_id].read_typetree()
                    shader = UnityPy.classes.PPtr(**mat['m_Shader'],assetsfile=af).deref().read_typetree()
                    textures = {}
                    seen = {}
                    for prop, value in mat['m_SavedProperties']['m_TexEnvs']:
                        tid = value['m_Texture']['m_PathID']
                        if not tid: continue
                        if tid not in seen:
                            texture = UnityPy.classes.PPtr(**value['m_Texture'],assetsfile=af).deref().read()
                            png = target/f'{tid}.png'; texture.image.save(png)
                            seen[tid] = {'file':png.name,'width':texture.m_Width,'height':texture.m_Height,'format':int(texture.m_TextureFormat)}
                        textures[prop] = dict(seen[tid],scale=value['m_Scale'],offset=value['m_Offset'])
                    normal = textures.get('_NormalMap')
                    if normal:
                        raw = np.asarray(Image.open(target/normal['file']).convert('RGBA'),dtype=np.float32)/255
                        x=raw[:,:,0]*raw[:,:,3]*2-1; y=raw[:,:,1]*2-1
                        z=np.sqrt(np.maximum(1-np.minimum(x*x+y*y,1),0))
                        pixels=np.clip((np.stack([x,y,z],axis=-1)*.5+.5)*255,0,255).astype(np.uint8)
                        normal['decoded_file']='法线XYZ.png'; Image.fromarray(pixels).save(target/normal['decoded_file'])
                    materials[str(material_id)]={'raw':mat,'shader_name':shader.get('m_ParsedForm',{}).get('m_Name'),'textures':textures}
        animator=next(c['data'] for n in info['nodes'] for c in n['components'] if c['type']=='Animator')
        controller=af.objects[animator['m_Controller']['m_PathID']].read_typetree()
        clips=[]
        for clip_id in dict.fromkeys(p['m_PathID'] for p in controller['m_AnimationClips']):
            clip=af.objects[clip_id]; c=clip.read_typetree()
            raw=clip.get_raw_data(); rawpath=target/'原生动画'/f'{c["m_Name"]}-{clip_id}.bin';rawpath.parent.mkdir(exist_ok=True)
            with rawpath.open('xb') as stream:stream.write(raw)
            clips.append({'name':c['m_Name'],'id':str(clip_id),'raw':rawpath.relative_to(target).as_posix(),'sha256':hashlib.sha256(raw).hexdigest(),
                          'duration':c['m_MuscleClip']['m_StopTime']-c['m_MuscleClip']['m_StartTime'],'sample_rate':c['m_SampleRate'],
                          'events':c['m_Events'],'bindings':c['m_ClipBindingConstant']['genericBindings']})
        write(target/'角色数据.json',dict(info,meshes=meshes,materials=materials,clips=clips))
        print(json.dumps({'role':role,'meshes':[{k:m[k] for k in ['name','vertices','triangles']} for m in meshes],'clips':[c['name'] for c in clips]},ensure_ascii=False),flush=True)


def inspect(batch, resume=False):
    started = time.time()
    cache = ROOT/'local-data/断箭/批次/BA-26273833-001/解析缓存'
    manifest = json.loads((cache/'解析缓存清单.json').read_text('utf-8'))
    config = json.loads((ROOT/'local-data/断箭/本地配置.json').read_text('utf-8'))
    installed = re.search(r'"buildid"\s+"(\d+)"', Path(config['steam_manifest']).read_text('utf-8'))[1]
    if installed != '26273833':
        raise ValueError('安装构建号变化，须重新核对来源')
    if batch.exists() and not resume:
        raise FileExistsError('批次已存在，拒绝覆盖')
    if resume:
        task = json.loads((batch/'任务.json').read_text('utf-8'))
        if task['sources'] != SOURCES or (batch/'来源探查.json').exists():
            raise ValueError('恢复身份不符或已完成')
    else:
        batch.mkdir()
        write(batch/'任务.json', {'task': '断箭丧尸双模型提取-V001-01a1aa48', 'version': 'V001', 'build': installed,
                                 'sources': SOURCES, 'authorization': '工程师确认提取Walker和Runner各一个，检查模型、材质、骨骼及原生动画并保留Blender工程'})
    env = UnityPy.Environment()
    for node in manifest['nodes']:
        path = cache/node['name']
        if path.stat().st_size != node['size']:
            raise ValueError('源缓存大小变化')
        if not path.name.endswith('.resS'):
            env.load_file(str(path))
    print('源缓存已载入', flush=True)
    result = {'build': installed, 'unitypy': UnityPy.__version__, 'engine': manifest['engine'], 'roles': {}, 'animation_candidates': []}
    for role, source in SOURCES.items():
        # 定向使用源文件容器，避免Environment.container展开全库预载依赖。
        af = next(af for af in env.assets if source in af.container)
        ptr = af.container[source]
        af = ptr.deref().assets_file
        go = ptr.read_typetree()
        tid = next(c['component']['m_PathID'] for c in go['m_Component'] if af.objects[c['component']['m_PathID']].type.name == 'Transform')
        nodes = []
        def walk(identity, parent=None):
            tr = af.objects[identity].read_typetree()
            go = af.objects[tr['m_GameObject']['m_PathID']].read_typetree()
            components = []
            for c in go['m_Component']:
                obj = af.objects[c['component']['m_PathID']]
                try:
                    data = obj.read_typetree()
                except Exception as error:
                    data = {'parse_error': str(error)}
                components.append({'id': str(obj.path_id), 'type': obj.type.name, 'data': data})
            nodes.append({'id': str(identity), 'name': go['m_Name'], 'parent': str(parent) if parent else None, 'active': go['m_IsActive'], 'transform': tr, 'components': components})
            for child in tr['m_Children']:
                walk(child['m_PathID'], identity)
        walk(tid)
        result['roles'][role] = {'source': source, 'id': str(ptr.path_id), 'asset_file': af.name, 'nodes': nodes,
                                  'component_counts': dict(Counter(c['type'] for n in nodes for c in n['components']))}
        print(role, len(nodes), result['roles'][role]['component_counts'], flush=True)
    for obj in env.objects:
        if obj.type.name not in {'AnimationClip', 'AnimatorController', 'AnimatorOverrideController', 'Avatar'}:
            continue
        try:
            data = obj.read_typetree()
            name = data.get('m_Name', '')
            if re.search('zomb|walker|runner', name, re.I):
                target = batch/'来源依赖'/f'{obj.type.name}-{obj.path_id}.json'
                write(target, data)
                result['animation_candidates'].append({'name': name, 'type': obj.type.name, 'id': str(obj.path_id), 'json': target.relative_to(batch).as_posix()})
        except Exception as error:
            print('动画对象读取失败', obj.path_id, str(error)[:200], flush=True)
    write(batch/'来源探查.json', result)
    print(json.dumps({'seconds': time.time()-started, 'candidates': result['animation_candidates']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', default='BA-26273833-013')
    parser.add_argument('--resume-inspect', action='store_true')
    parser.add_argument('--stage',choices=['inspect','extract'],default='inspect')
    args = parser.parse_args()
    batch = (ROOT/'local-data/断箭/批次'/args.batch).resolve()
    if batch.parent != (ROOT/'local-data/断箭/批次').resolve():
        raise ValueError('批次路径越界')
    if args.stage=='inspect': inspect(batch, args.resume_inspect)
    else: extract(batch)
