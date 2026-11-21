"""准备全新隔离Unity项目，以本机编辑器只读采样原作角色动作。"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[2]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unity',type=Path,required=True)
    parser.add_argument('--project',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();project=args.project.resolve();output=args.output.resolve()
    if not project.is_relative_to((ROOT/'tmp').resolve()) or project==(ROOT/'tmp').resolve():raise ValueError('项目必须位于独立tmp子目录')
    if not output.is_relative_to((ROOT/'local-data/断箭/批次').resolve()):raise ValueError('输出必须位于断箭批次')
    if project.exists() or output.exists():raise FileExistsError('项目或输出已存在，拒绝覆盖')
    project.mkdir(parents=True);(project/'Assets/Editor').mkdir(parents=True);(project/'ProjectSettings').mkdir();(project/'Packages').mkdir()
    (project/'Packages/manifest.json').write_text(json.dumps({'dependencies':{f'com.unity.modules.{x}':'1.0.0' for x in ['animation','assetbundle','jsonserialize']}}),encoding='utf-8')
    shutil.copyfile(Path(__file__).with_name('原生动作采样.cs'),project/'Assets/Editor/原生动作采样.cs')
    config=json.loads((ROOT/'local-data/断箭/本地配置.json').read_text('utf-8'))
    manifest=json.loads((ROOT/'local-data/断箭/批次/BA-26273833-001/解析缓存/解析缓存清单.json').read_text('utf-8'))
    bundle=Path(config['game_directory'])/'BrokenArrow_Data/StreamingAssets/aa/PC'/manifest['source_relative']
    (project/'采样配置.json').write_text(json.dumps({'bundle':str(bundle),'output':str(output)},ensure_ascii=False),encoding='utf-8')
    command=[str(args.unity.resolve()),'-batchmode','-nographics','-projectPath',str(project),'-executeMethod','原生动作采样.Run','-logFile',str(project.parent/(project.name+'-Unity.log'))]
    result=subprocess.run(command,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if result.returncode or not output.is_file():raise RuntimeError('采样失败，请保留并检查独立日志')
    print(json.dumps({'status':'completed','output':str(output),'bytes':output.stat().st_size},ensure_ascii=False))


if __name__=='__main__':main()
