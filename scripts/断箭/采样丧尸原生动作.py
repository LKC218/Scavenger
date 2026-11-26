"""准备全新隔离Unity项目，以本机编辑器只读采样原作角色动作。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[2]


def batch_config(game, batch, selected, output):
    delivery = batch.resolve() / '交付'
    if not delivery.parent.is_relative_to((ROOT/'local-data/断箭/批次').resolve()):
        raise ValueError('批量解包目录越界')
    manifest = json.loads((delivery/'对象清单.json').read_text('utf-8'))
    dependencies = []
    bundle = None
    for row in manifest['sources']:
        path = (game/row['game_relative']).resolve()
        if not path.is_relative_to(game.resolve()) or path.stat().st_size != row['bytes']:
            raise ValueError(f'资源包范围或大小不符：{path.name}')
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != row['sha256']:
                raise ValueError(f'资源包摘要不符：{path.name}')
        if path.name.startswith('units_assets'):
            bundle = path
        elif path.suffix == '.bundle':
            dependencies.append(str(path))
    if not bundle:
        raise ValueError('批次没有单位资源包')
    names = selected or [name for name in manifest['roles'] if name != 'ZOM_Swarm1']
    if len(names) != len(set(names)) or any(name not in manifest['roles'] for name in names):
        raise ValueError('选中的角色重复或超出当前批次')
    prefabs = []
    for name in names:
        role = json.loads((delivery/'角色清单'/f'{name}.json').read_text('utf-8'))
        if not role['counts'].get('Animator'):
            raise ValueError(f'{name} 没有 Animator')
        prefabs.append({'name': name, 'path': role['source']})
    return {'bundle': str(bundle), 'dependencies': dependencies, 'prefabs': prefabs, 'output': str(output)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unity',type=Path,required=True)
    parser.add_argument('--project',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--batch',type=Path,help='按批次的角色清单和源包摘要采样')
    parser.add_argument('--role',action='append',help='指定角色，可重复传入')
    args=parser.parse_args();project=args.project.resolve();output=args.output.resolve()
    if not project.is_relative_to((ROOT/'tmp').resolve()) or project==(ROOT/'tmp').resolve():raise ValueError('项目必须位于独立tmp子目录')
    if not output.is_relative_to((ROOT/'local-data/断箭/批次').resolve()):raise ValueError('输出必须位于断箭批次')
    if project.exists() or output.exists():raise FileExistsError('项目或输出已存在，拒绝覆盖')
    if args.role and not args.batch:
        parser.error('--role 需要 --batch')
    config=json.loads((ROOT/'local-data/断箭/本地配置.json').read_text('utf-8'))
    game = Path(config['game_directory'])
    if args.batch:
        capture = batch_config(game, args.batch, args.role, output)
    else:
        manifest=json.loads((ROOT/'local-data/断箭/批次/BA-26273833-001/解析缓存/解析缓存清单.json').read_text('utf-8'))
        bundle=game/'BrokenArrow_Data/StreamingAssets/aa/PC'/manifest['source_relative']
        capture = {'bundle': str(bundle), 'output': str(output)}
    output.parent.mkdir(parents=True, exist_ok=True)
    project.mkdir(parents=True);(project/'Assets/Editor').mkdir(parents=True);(project/'ProjectSettings').mkdir();(project/'Packages').mkdir()
    (project/'Packages/manifest.json').write_text(json.dumps({'dependencies':{f'com.unity.modules.{x}':'1.0.0' for x in ['animation','assetbundle','jsonserialize']}}),encoding='utf-8')
    shutil.copyfile(Path(__file__).with_name('原生动作采样.cs'),project/'Assets/Editor/原生动作采样.cs')
    (project/'采样配置.json').write_text(json.dumps(capture,ensure_ascii=False),encoding='utf-8')
    command=[str(args.unity.resolve()),'-batchmode','-nographics','-projectPath',str(project),'-executeMethod','原生动作采样.Run','-logFile',str(project.parent/(project.name+'-Unity.log'))]
    result=subprocess.run(command,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if result.returncode or not output.is_file():raise RuntimeError('采样失败，请保留并检查独立日志')
    print(json.dumps({'status':'completed','output':str(output),'bytes':output.stat().st_size},ensure_ascii=False))


if __name__=='__main__':main()
