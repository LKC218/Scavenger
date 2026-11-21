"""从已保存地图工程生成125米地表块和共享媒体，不覆盖工程或首测。"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def read(p): return json.loads(p.read_text('utf8'))
def save(p, d): p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding='utf8')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def prepare(directory, output):
    import numpy as np
    from PIL import Image
    output.mkdir(parents=True, exist_ok=False)
    target = output / '贴图源'; target.mkdir()
    scene = read(directory.parents[2] / '地图数据/2000米/地表场景.json')
    crops, low = {}, {}
    for key, material in scene['materials'].items():
        path = material.get('basecolor')
        if not path: continue
        image = Image.open(path).convert('RGB')
        overview = image.copy(); overview.thumbnail((512, 512))
        filename = '总览-' + hashlib.sha256(key.encode()).hexdigest()[:16] + '.png'
        overview.save(target / filename); low[str(material.get('source_id', key))] = filename
    for part in scene['parts']:
        if part['category'] != 'terrain': continue
        original = Path(scene['materials'][part['materials'][0]]['basecolor'])
        image = np.asarray(Image.open(original).convert('RGB'))
        height, width = image.shape[:2]
        if width != 4096 or height != 4096: raise ValueError('2公里分块需要保留4096地表源图')
        padded = np.pad(image, ((1,1),(1,1),(0,0)), mode='edge')
        for iz in range(16):
            for ix in range(16):
                filename = f'地表-{part["gameobject"]}-{ix:02}-{iz:02}.png'
                Image.fromarray(padded[iz*256:iz*256+258,ix*256:ix*256+258]).save(target / filename)
                crops[f'{part["mesh_id"]}:{ix}:{iz}'] = filename
    save(output / '准备记录.json', {'source_directory': str(directory), 'source_map_sha256': sha(directory/'地图运行.json'),
                                  'terrain_crops': crops, 'overview': low, 'gutter_px': 1, 'terrain_pixels_per_chunk': 256})
    print('已准备256米制地表块贴图及总览', flush=True)


def clip(attributes, indices, materials, bounds):
    import numpy as np
    faces = attributes[indices]
    keep = ((faces[:,:,0].max(1)>=bounds[0])&(faces[:,:,0].min(1)<=bounds[1])&
            (faces[:,:,1].max(1)>=bounds[2])&(faces[:,:,1].min(1)<=bounds[3]))
    vertices, uv, triangles, mi = [], [], [], []
    for face, material in zip(faces[keep], materials[keep]):
        polygon = list(face)
        for axis, value, sign in [(0,bounds[0],1),(0,bounds[1],-1),(1,bounds[2],1),(1,bounds[3],-1)]:
            result = []
            for a,b in zip(polygon,polygon[1:]+polygon[:1]):
                ia,ib=sign*(a[axis]-value)>=-1e-8,sign*(b[axis]-value)>=-1e-8
                if ia: result.append(a)
                if ia!=ib: result.append(a+(b-a)*((value-a[axis])/(b[axis]-a[axis])))
            polygon=result
            if len(polygon)<3: break
        if len(polygon)<3: continue
        for n in range(1,len(polygon)-1):
            a,b,c=polygon[0],polygon[n],polygon[n+1]
            if np.linalg.norm(np.cross(b[:3]-a[:3],c[:3]-a[:3]))<1e-8: continue
            base=len(vertices);vertices.extend([a[:3],b[:3],c[:3]]);uv.extend([a[3:],b[3:],c[3:]])
            triangles.append([base,base+1,base+2]);mi.append(material)
    return np.asarray(vertices,dtype=np.float32),np.asarray(uv,dtype=np.float32),triangles,mi


def build(directory, output):
    import bpy
    import numpy as np
    from mathutils import Matrix
    sys.path.insert(0,str(Path(__file__).parent))
    from 地图媒体 import externalize
    start=time.time();record=read(output/'准备记录.json');runtime=read(directory/'地图运行.json')
    if record['source_map_sha256']!=sha(directory/'地图运行.json'):raise ValueError('输入清单变化')
    bpy.ops.wm.open_mainfile(filepath=str(directory/'Airbase-2000米.blend'))
    grounds=[o for o in bpy.data.objects if o.type=='MESH' and o.get('category') in ('road','terrain')]
    sources=[]
    for obj in grounds:
        mesh=obj.data;mesh.calc_loop_triangles()
        xyz=np.asarray([obj.matrix_world@v.co for v in mesh.vertices]);values=[];mi=[]
        for face in mesh.loop_triangles:
            for vertex,loop in zip(face.vertices,face.loops):values.append([*xyz[vertex],*mesh.uv_layers.active.data[loop].uv])
            mi.append(face.material_index)
        attributes=np.asarray(values,dtype=float);indices=np.arange(len(values)).reshape(-1,3)
        sources.append((obj,attributes,indices,np.asarray(mi)))
    assets=set();chunks=[];seams={};max_seam=0.;seam_pairs=0
    (output/'分块').mkdir(exist_ok=True)
    def export(path, objects):
        bpy.ops.object.select_all(action='DESELECT')
        for o in objects:o.select_set(True)
        bpy.context.view_layer.objects.active=objects[0]
        bpy.ops.export_scene.gltf(filepath=str(path),export_format='GLB',use_selection=True,export_yup=True,export_extras=True,export_animations=False,export_cameras=False,export_lights=False)
        assets.update(externalize(path,output))
    # 总览复用源网格，降低贴图规格；高细节块就绪前保持完整地表。
    overview=[]
    for source in grounds:
        obj=source.copy();obj.data=source.data.copy();bpy.context.scene.collection.objects.link(obj)
        for i,material in enumerate(obj.data.materials):
            mat=material.copy();name=record['overview'].get(str(material.get('source_id')))
            if name:
                image=bpy.data.images.load(str(output/'贴图源'/name),check_existing=True)
                for node in mat.node_tree.nodes:
                    if node.type=='TEX_IMAGE':node.image=image
            obj.data.materials[i]=mat
        overview.append(obj)
    export(output/'地表总览.glb',overview)
    for obj in overview:bpy.data.objects.remove(obj,do_unlink=True)
    for iz in range(16):
        for ix in range(16):
            minx,minz=-1000+ix*125,-1000+iz*125;bounds=[minx,minx+125,-minz-125,-minz]
            objects=[];triangles=0
            for source,attributes,indices,mi in sources:
                vertices,uv,faces,materials=clip(attributes,indices,mi,bounds)
                if not faces:continue
                mesh=bpy.data.meshes.new(f'块-{ix}-{iz}-{source.name}');mesh.from_pydata(vertices,[],faces);mesh.update()
                obj=bpy.data.objects.new(mesh.name,mesh);bpy.context.scene.collection.objects.link(obj);obj['category']=source['category'];obj['source_mesh_id']=source.get('source_mesh_id','')
                if source['category']=='terrain':
                    uv=np.column_stack([((vertices[:,0]-bounds[0])/125*256+1)/258,((vertices[:,1]-bounds[2])/125*256+1)/258])
                    mat=source.data.materials[0].copy();crop=record['terrain_crops'][f'{source["source_mesh_id"]}:{ix}:{iz}']
                    image=bpy.data.images.load(str(output/'贴图源'/crop),check_existing=False)
                    for node in mat.node_tree.nodes:
                        if node.type=='TEX_IMAGE':node.image=image
                    mesh.materials.append(mat)
                else:
                    for mat in source.data.materials:mesh.materials.append(mat)
                for polygon,material in zip(mesh.polygons,materials):polygon.material_index=material
                layer=mesh.uv_layers.new(name='UVMap');layer.data.foreach_set('uv',uv[np.asarray(faces).ravel()].ravel())
                objects.append(obj);triangles+=len(faces)
                # 相邻块的同源交点分别裁切后比较，保留真实道路与地形的高程区别。
                for vi,v in enumerate(vertices):
                    material_id=materials[vi//3]
                    for axis in [0,1]:
                        if abs((v[axis]+1000)/125-round((v[axis]+1000)/125))<1e-5 and abs(v[axis])<999.999:
                            key=(source.name,material_id,axis,round(float(v[axis]),3),round(float(v[1-axis]),3))
                            if key in seams:
                                height,owner=seams[key]
                                if owner!=(ix,iz):max_seam=max(max_seam,abs(height-float(v[2])));seam_pairs+=1
                            else:seams[key]=(float(v[2]),(ix,iz))
            if not objects:raise ValueError('地表块为空，禁止用平面填补')
            filename=f'分块/块-{ix:02}-{iz:02}.glb';export(output/filename,objects)
            chunks.append({'id':f'{ix}:{iz}','file':filename,'bounds':{'minX':minx,'maxX':minx+125,'minZ':minz,'maxZ':minz+125},'triangles':triangles})
            for obj in objects:bpy.data.objects.remove(obj,do_unlink=True)
        print(f'完成地表分块行 {iz+1}/16',flush=True)
    for row in runtime['prototypes'].values():
        path=output/row['file'];shutil.copyfile(directory/row['file'],path);assets.update(externalize(path,output))
    shutil.copyfile(directory/runtime['height']['file'],output/runtime['height']['file'])
    for instance in runtime['instances']:
        x,z=instance['matrix'][12],instance['matrix'][14];owner=lambda v:max(0,min(15,int((v+1000)//125)))
        instance['primary_chunk']=f'{owner(x)}:{owner(z)}'
        points=instance['footprint'];instance['covered_chunks']=[f'{a}:{b}' for a in range(owner(min(p[0] for p in points)),owner(max(p[0] for p in points))+1) for b in range(owner(min(p[1] for p in points)),owner(max(p[1] for p in points))+1)]
    if len({i['id'] for i in runtime['instances']})!=len(runtime['instances']):raise ValueError('稳定实例ID重复')
    runtime.update(ground='地表总览.glb',chunks=chunks,assets=sorted(assets),streaming={'chunkSize':125,'maxResident':49,'overviewSpan':480},navigation={'cell_m':4,'radius_m':5.3,'max_slope_degrees':30,'unload_independent':True},source_delivery_sha256=sha(directory/'地图运行.json'))
    save(output/'地图运行.json',runtime)
    save(output/'分块核验.json',{'status':'passed' if max_seam<=.01 else 'failed','chunks':len(chunks),'seam_pairs':seam_pairs,'max_seam_error_m':max_seam,'instances':len(runtime['instances']),'prototypes':len(runtime['prototypes']),'elapsed_s':time.time()-start,'files':{p.relative_to(output).as_posix():{'bytes':p.stat().st_size,'sha256':sha(p)} for p in output.rglob('*') if p.is_file() and p.suffix in ('.glb','.f32','.png')}})
    if max_seam>.01:raise ValueError('分块接缝超标')
    print('2公里运行分块完成',len(chunks),'块；接缝最大误差',max_seam,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',type=Path,required=True);parser.add_argument('--revision',required=True);parser.add_argument('--prepare',action='store_true')
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else None)
    directory=args.directory.resolve();directory.relative_to((ROOT/'local-data/断箭/批次').resolve());output=directory/'运行分块'/args.revision
    prepare(directory,output) if args.prepare else build(directory,output)
