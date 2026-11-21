"""只在2公里全部必需证据通过后封存G3，绑定首测、工程、运行媒体和HTTP源码。"""
import argparse
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import quote
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]


def read(p): return json.loads(p.read_text('utf8'))
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p, data): p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf8')
def require(condition, message):
    if not condition: raise ValueError(message)


def run(args):
    directory, runtime, first = args.directory.resolve(), args.runtime.resolve(), args.first.resolve()
    for path in [directory, runtime, first]: path.relative_to(ROOT / 'local-data/断箭/批次')
    output = directory / '阶段通过-2000米.json'
    require(not output.exists(), '已有阶段凭证，拒绝覆盖')
    batch=directory.parents[2]
    inputs=read(batch/'输入清单.json')
    source_root=Path(inputs['game_directory'])
    source_checks=[]
    for row in inputs['files']:
        stat=(source_root/row['path']).stat()
        require(stat.st_size==row['bytes'] and stat.st_mtime_ns==row['mtime_ns'], '输入文件元数据变化：'+row['path'])
        source_checks.append({'path':row['path'],'bytes':stat.st_size,'mtime_ns':stat.st_mtime_ns})
    steam=inputs['steam_manifest']
    require(sha(Path(steam['path']))==steam['sha256'], 'Steam构建清单变化')
    save(batch/'输入只读复核.json',{'status':'passed','build':inputs['build'],'check':'文件大小与修改时间一致，Steam清单摘要一致；不等同于重新计算全部资源摘要','files':source_checks,'steam_manifest_sha256':steam['sha256']})
    model, geometry, layout = read(runtime/'地图运行.json'), read(directory/'独立回读核验.json'), read(directory/'布局独立核验.json')
    compression, routes = read(runtime/'压缩核验.json'), read(runtime/'固定路线核验.json')
    visual = read(runtime/'浏览器视觉审阅.json')
    require(read(first)['status']=='passed' and read(first)['gate']=='G2', '首测关口未通过')
    require(model['size']==2000 and all(abs(n-2000)<.05 for n in geometry['ground_span_m']), '区域尺度不符')
    require(geometry['height_max_error_m']<=.1 and len(geometry['height_samples'])>=100, '高度独立回读未通过')
    require(layout['status']=='passed' and layout['instances']==len(model['instances']) and layout['matrix_max_error']<1e-5, '作者布局未通过')
    require(layout['unique_ids']==len(model['instances']) and layout['negative_instance_determinants']==0, '实例身份或镜像未核验')
    seams = read(directory/'运行分块/r02/分块核验.json')
    require(seams['status']=='passed' and seams['chunks']==256 and seams['max_seam_error_m']<=.01, '分块接缝未通过')
    require(routes['status']=='passed' and len(routes['routes'])>=10 and all(r['status']=='passed' for r in routes['routes']), '固定通行未通过')
    require(visual['status']=='passed' and set(visual['browsers'])=={'chrome','msedge'}, '双浏览器实际审图未通过')
    for row in visual['images']: require(sha(runtime/row['file'])==row['sha256'], '已审图像变化')
    fixture=runtime/visual['fixture_source']['file']
    require(sha(fixture)==visual['fixture_source']['sha256'], '坡面附加夹具源码变化')
    require(all(r['geometry_exact'] and r['external_roundtrip'] for r in compression['files']), '压缩几何回读未通过')
    for name, row in compression['finalFiles'].items():
        p=runtime/name;require(p.stat().st_size==row['bytes'] and sha(p)==row['sha256'], '运行媒体变化：'+name)
    evidence = [directory/'独立回读核验.json', directory/'布局独立核验.json', directory/'视觉审阅.json',
                directory/'运行分块/r02/分块核验.json', runtime/'地图运行.json', runtime/'压缩核验.json',
                runtime/'固定路线核验.json', runtime/'浏览器视觉审阅.json', fixture]
    metrics=[]
    for browser in ['chrome','msedge']:
        p=runtime/'浏览器验收'/args.run/browser/'浏览器验收.json';r=read(p);evidence.append(p)
        require(r['status']=='passed' and r['window'] and not r['errors'], '浏览器未通过：'+browser)
        require(r['identity']['revision']==args.revision and r['identity']['map_sha256']==sha(runtime/'地图运行.json'), '浏览器版本不符')
        require(r['map_transfer_bytes']<=80*1024**2 and r['cold_ready_seconds']<=15, '冷加载超预算')
        require(r['initial']['triangles']<=2000000, '默认局部提交超预算')
        require(r['negative_static_instances']==0 and r['initial']['mapStats']['mirroredPrototypeMeshes']>=1, '源镜像原型未安全转换')
        require(len(r['samples'])==3, '性能矩阵不完整')
        for sample, count, duration in zip(r['samples'], [0,10,100], [60,60,180]):
            require(sample['totalUnits']==count and sample['duration']>=duration-.05 and sample['p95']<=16.67, '负载或整帧未达标')
            require(max(t['drawCalls'] for t in sample['timeline'])<=350 and max(t['triangles'] for t in sample['timeline'])<=2000000, '典型局部提交超预算')
            require(max(t['textureEstimateBytes'] for t in sample['timeline'])<=512*1024**2, '纹理估算超预算')
        cross=r['streaming'];require(cross['elapsed_seconds']>=90 and all(c['missing']==0 for c in cross['coverage']), '跨区空洞或时间不足')
        require(max(t['mapStats']['loadedChunks'] for t in cross['timeline'])<=49 and cross['final']['mapStats']['evicted']>0, '分块回收未通过')
        require(not cross['final']['mapStats']['errors'], '分块加载错误')
        extra_path=runtime/('浏览器附加-'+args.revision)/browser/'坡面核验.json';extra=read(extra_path);evidence.append(extra_path)
        require(extra['status']=='passed' and extra['identity']['revision']==args.revision, '坡面附加夹具未通过')
        require(max(t['error'] for t in extra['pose']['rows'])<.001 and extra['terrain_impact']['height_error']<.05, '坡面炮口或弹道高程超差')
        load=r['samples'][-1];metrics.append({'browser':browser,'version':r['version'],'window':True,
            'P95_ms':load['p95'],'P99_ms':load['p99'],'max_ms':load['max'],'duration_s':load['duration'],
            'cold_ready_s':r['cold_ready_seconds'],'map_transfer_bytes':r['map_transfer_bytes'],
            'draw_max':max(t['drawCalls'] for t in load['timeline']),'triangles_max':max(t['triangles'] for t in load['timeline']),
            'visible_unit_range':[min(t['visibleUnits'] for t in load['timeline']),max(t['visibleUnits'] for t in load['timeline'])],
            'moving_unit_range':[min(t['movingUnits'] for t in load['timeline']),max(t['movingUnits'] for t in load['timeline'])],
            'voices_max':max(t['voices'] for t in load['timeline']),'engine_voices_max':max(t['engines'] for t in load['timeline']),
            'texture_estimate_bytes_max':max(t['textureEstimateBytes'] for t in load['timeline']),
            'shots':load['final']['shots'],'hits':load['final']['hits'],'cross_seconds':cross['elapsed_seconds'],
            'ground_rays':sum(c['tested'] for c in cross['coverage']),'ground_missing':sum(c['missing'] for c in cross['coverage']),
            'evictions':cross['final']['mapStats']['evicted']})
    freeze=runtime/('浏览器源码-'+args.revision);identity=read(freeze/'服务身份.json')
    current=json.load(urlopen(f'http://127.0.0.1:{args.port}/api/status'))
    require(current['pid']==identity['pid'] and current['revision']==args.revision and current['app']=='scavenger-airbase-map', '服务身份不符')
    http={}
    for name,row in identity['sources'].items():
        require(sha(freeze/name)==row['http_sha256'], '冻结源码变化：'+name)
        body=urlopen(f'http://127.0.0.1:{args.port}/'+quote(name)).read();digest=hashlib.sha256(body).hexdigest()
        require(digest==row['http_sha256'], 'HTTP源码变化：'+name);http[name]=digest
    manifest=urlopen(f'http://127.0.0.1:{args.port}/manifest.json').read()
    require(hashlib.sha256(manifest).hexdigest()==identity['manifest_sha256'], '运行清单变化')
    save(runtime/'HTTP核验.json',{'status':'passed','identity':current,'sources':http,'manifest_sha256':identity['manifest_sha256']})
    # 保存实际清单用于后续读回；不改变冻结来源或已有通过凭证。
    (freeze/'运行清单.json').write_bytes(manifest)
    evidence.extend([freeze/'服务身份.json',runtime/'HTTP核验.json'])
    receipt={'status':'passed','gate':'G3','size_m':2000,'timestamp':time.time(),'source_build':model['source_build'],
        'first_gate':{'path':first.relative_to(ROOT).as_posix(),'sha256':sha(first)},
        'input_manifest_sha256':sha(batch/'输入清单.json'),'region_sha256':sha(batch/'区域冻结-2000米.json'),
        'input_readonly_check_sha256':sha(batch/'输入只读复核.json'),
        'runtime':runtime.relative_to(directory).as_posix(),'runtime_map_sha256':sha(runtime/'地图运行.json'),
        'evidence':{p.relative_to(directory).as_posix():sha(p) for p in evidence},
        'media':{p.name:{'bytes':p.stat().st_size,'sha256':sha(p)} for p in directory.iterdir() if p.suffix in ('.blend','.glb','.f32')},
        'runtime_files':compression['finalFiles'],'metrics':metrics,
        'conditions':read(first)['conditions'],'hardware':read(first)['hardware'],
        'limits':['源数据重建，无原游戏截图对照','地表为源图层底色近似，未复刻全部原Shader',
                  '100单位在场模拟，局部入画与发声数量分别记录，不代表100同屏',
                  '固定翼不包含起降，导航为Demo派生','仅限本机和已测镜头负载；全图总览另记，不承诺任意编队和镜头']}
    save(output,receipt);print(json.dumps({'status':'passed','receipt':str(output),'sha256':sha(output),'metrics':metrics},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ['directory','runtime','first']: p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--run',required=True);p.add_argument('--revision',required=True);p.add_argument('--port',type=int,required=True)
    run(p.parse_args())
