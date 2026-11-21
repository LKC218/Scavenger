"""从当前索引生成可读文件名和冻结清单；只写独立预览，不迁移媒体。"""

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re
import sys
import time

from 统一资源 import ROOT, HOME, current, read, sha

sys.path.insert(0, str(ROOT / 'scripts/维护清理'))
from 清理工具 import lock

TASK = '前线资产库可读命名V001-01a1a4e6'
OUTPUT = ROOT / 'local-data/资源库命名/可读命名V001-01a1a4e6/复核版'
MEDIA = HOME / '当前资源'

# 释义只描述已有事件名，不代表听审通过或特定载具确证。
AUDIO_LABELS = {
    'BigTurbine_Falling': '大型涡轮坠落',
    'Death_Exp_Veh_AmmoDetonation': '载具弹药殉爆',
    'Death_Exp_Tank_AmmoDetonation': '坦克弹药殉爆',
    'Death_Delay_Exp_Tank_AmmoDetonation': '坦克弹药延迟殉爆',
    'Death_Exp_Vehicles_Small': '小型载具毁伤爆炸',
    'Death_Exp_BigTurbine_Plane': '大型涡轮飞机毁伤爆炸',
    'US_AbramsM1A2_Engine': 'M1A2发动机',
    'US_AbramsM1A2_Engine（当前无缝循环）': 'M1A2发动机无缝循环',
    'US_AH-64Longbow_Rotor（当前无缝循环）': 'AH-64旋翼无缝循环',
    'US_A10_Turbine_New（当前无缝循环）': 'A-10涡轮无缝循环',
    'SFX_BULLET_HCal_Impact_Single': '大口径弹单次命中',
    'SFX_BULLET_HCal_MG_Impact_Burst': '大口径机枪连射命中',
    'SFX_BULLET_SMCal_Impact_Single': '小口径弹单次命中',
    'SFX_BULLET_SMCal_MG_Impact_Burst': '小口径机枪连射命中',
    'SFX_BulletFly': '弹丸飞行',
    'SFX_BulletShell_Drop': '弹壳掉落',
    'SFX_MINIGUN_Tail': '转管机枪尾声',
    'SFX_RIFLE_Tail_SmallCaliber': '小口径步枪尾声',
    'BuildingDestructionSFX': '建筑毁坏',
    'BurnVehicle': '载具燃烧',
    'BurnVehicle_Delay_8sec': '载具燃烧延迟8秒',
    'BA_DropBomb': '投弹',
    'BA_Chaff_Trap': '箔条干扰',
}


def component(value, limit=72):
    """同时处理Windows非法字符、设备名、尾点和过长文件名。"""
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f\s]+', '-', str(value))
    value = re.sub('-+', '-', value).strip(' .-') or '未命名'
    if re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', value, re.I):
        value = '资源-' + value
    if len(value) > limit:
        value = value[:limit-9] + '-' + hashlib.sha256(value.encode()).hexdigest()[:8]
    return value


def model_info(row):
    identity = row['id']
    category = {'air': '空中载具', 'ground': '地面载具'}.get(row.get('category'))
    if not category:
        category = '角色' if 'zombie' in identity else ('靶场装配' if identity.startswith('runtime:') else '其他模型')
    name = component(row['name'], 58)
    stable = component(identity.removeprefix('model:'), 48)
    return category, name, component(stable + '-' + name, 110)


def audio_info(row):
    uncertain = row.get('category') == '特殊用途候选'
    category = '待辨识' if uncertain else component(row.get('category', '待辨识'))
    label = AUDIO_LABELS.get(row['name'], category)
    state = '待复核-' if row.get('quality_status') == 'review' else ''
    label = state + label
    return category, component(label), '事件名释义待听审' if row['name'] in AUDIO_LABELS else '沿用索引类别待细分'


def propose(catalog, registry, masters, routes):
    """一份物理文件只有一个目标路径，多项事件共用内容时合并关联。"""
    candidates = {}

    def add(record, kind, identity, original, target, basis):
        path = Path(record['path'])
        path = (path if path.is_absolute() else ROOT / path).resolve()
        if not path.is_relative_to(MEDIA.resolve()):
            raise ValueError('清单源文件不在当前资源内：' + str(path))
        old = path.relative_to(ROOT).as_posix()
        target = (MEDIA / target).relative_to(ROOT).as_posix()
        entry = {'kind': kind, 'id': identity, 'name': original,
                 'old_path': old, 'new_path': target, 'sha256': record['sha256'],
                 'bytes': record['bytes'], 'naming_basis': basis}
        candidates.setdefault(old, []).append(entry)

    cards = {row['id']: row for row in catalog['models']}
    all_models = {row['id']: row for row in registry.values() if row['kind'] == 'model'}
    for identity, row in sorted(all_models.items()):
        card = cards.get(identity, row)
        category, name, folder = model_info(card)
        for variant in row['variants']:
            resolution, level = variant['resolution'], variant['level']
            specification = f'{resolution}远景' if level == 2 else (f'{resolution}默认' if resolution == 256 else f'{resolution}细节档')
            add(variant, '模型', identity, row['name'], Path('模型') / category / folder / f'{name}-{specification}.glb', '索引名称与真实规格')
        if identity in cards:
            add(masters[identity], '主工程', identity, card['name'], Path('主工程') / category / f'{folder}.blend', '稳定编号与索引名称')
            if card.get('preview_url') in routes:
                preview = routes[card['preview_url']]
                add(preview, '预览', identity, card['name'], Path('预览') / category / f'{folder}{Path(preview["path"]).suffix}', '对应模型名称')

    for row in sorted(catalog['audio'], key=lambda row: row['id']):
        category, label, basis = audio_info(row)
        short = hashlib.sha256(row['id'].encode()).hexdigest()[:10]
        filename = f'{label}-{component(row["name"], 72)}-{short}{Path(routes[row["url"]]["path"]).suffix}'
        add(routes[row['url']], '音效', row['id'], row['name'], Path('音效') / category / filename, basis)

    for identity, record in sorted(masters.items()):
        if identity in cards:
            continue
        row = all_models.get(identity) or cards.get(identity.removesuffix(':source'))
        if identity.startswith('loop:'):
            audio = next((a for a in catalog['audio'] if a['id'] == 'audio:' + identity), None)
            if not audio:
                raise ValueError('循环源缺少声音身份：' + identity)
            _, label, _ = audio_info(audio)
            target = Path('循环源') / f'{label}-{component(identity)}.wav'
            name = audio['name']
        elif row:
            category, name, folder = model_info(row)
            target = Path('装配源') / category / f'{folder}-装配源.glb'
        else:
            raise ValueError('不能辨识装配源身份：' + identity)
        add(record, '循环源' if identity.startswith('loop:') else '装配源', identity, name, target, '主工程登记中的重建源身份')

    files, seen = [], {}
    for old, choices in sorted(candidates.items()):
        choices.sort(key=lambda row: (row['id'], row['new_path']))
        item = dict(choices[0])
        item['asset_ids'] = sorted({row['id'] for row in choices})
        item['asset_names'] = sorted({row['name'] for row in choices})
        if len({(row['sha256'], row['bytes']) for row in choices}) != 1:
            raise ValueError('同一路径出现不同文件身份：' + old)
        key = item['new_path'].casefold()
        if key in seen and seen[key] != old:
            raise ValueError('目标命名冲突：' + item['new_path'])
        seen[key] = old
        destination = ROOT / item['new_path']
        if destination.exists() and destination.resolve() != (ROOT / old).resolve():
            raise ValueError('目标已存在：' + item['new_path'])
        if len(str(destination)) > 240:
            raise ValueError('目标路径过长：' + item['new_path'])
        files.append(item)
    return files


def evidence(output, pointer_sha, files):
    """只保存影响分析；活动保护与冻结交付不是可移动授权。"""
    registry_path = ROOT / 'local-data/维护清理/登记.json'
    tasks = read(registry_path)['tasks']
    overlaps = []
    old_paths = [(ROOT / row['old_path']).resolve() for row in files]
    for name, task in tasks.items():
        if task.get('status') not in ('active', 'unknown'):
            continue
        touched = []
        for scope in task.get('paths', []):
            path = (ROOT / scope).resolve()
            if any(old.is_relative_to(path) or path.is_relative_to(old) for old in old_paths):
                touched.append(scope)
        if touched:
            overlaps.append({'task': name, 'status': task['status'], 'paths': touched})
    delivery = ROOT / 'local-data/丧尸模型/十三款优化/交接/当前交付.json'
    frozen = read(delivery) if delivery.exists() else None
    result = {'task_registry_sha256': sha(registry_path), 'overlapping_tasks': overlaps,
              'delivery': None if frozen is None else {'path': delivery.relative_to(ROOT).as_posix(), 'sha256': sha(delivery), 'batch_id': frozen['batch_id'], 'pointer_matches': frozen['library_pointer_sha256'] == pointer_sha},
              'required_before_migration': ['核对并交接活动服务及制作消费者，保留其他任务保护', '逐文件核查占用并复核摘要、预期指针与命名清单', '生成独立新版索引，更新全部当前路径及来源别名，历史索引不回写', '重新冻结十三款交付，更新其指针与实际规格路径', '受控刷新或重启已核实身份的本机消费者，验证加载、试听和定位', '保留逐文件迁移及回滚凭证，不能将本预览当作退役或删除授权'],
              'automatic_migration_ready': False}
    (output / '迁移依赖核查.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def prepare(output):
    output = Path(output).resolve()
    boundary = ROOT / 'local-data/资源库命名'
    if not output.is_relative_to(boundary) or output == boundary:
        raise ValueError('输出须位于独立资源库命名轮次目录')
    if output.exists():
        raise ValueError('输出目录已存在，拒绝覆盖冻结预览')
    with lock():
        pointer_sha = sha(HOME / '当前版本.json')
        pointer, catalog, routes, base = current()
        files = propose(catalog, read(base / '模型登记.json'), read(base / '主工程.json'), routes)
        actual = {path.relative_to(ROOT).as_posix() for path in MEDIA.rglob('*') if path.is_file()}
        if actual != {row['old_path'] for row in files}:
            raise ValueError('索引未覆盖全部当前资源文件，须先核对差集：' + str(sorted(actual ^ {row['old_path'] for row in files})[:20]))
        for row in files:
            path = ROOT / row['old_path']
            stat = path.stat()
            if path.is_symlink() or path.is_junction() or stat.st_size != row['bytes'] or sha(path) != row['sha256']:
                raise ValueError('源文件身份异常：' + row['old_path'])
            row['mtime_ns'] = stat.st_mtime_ns
        if sha(HOME / '当前版本.json') != pointer_sha:
            raise ValueError('预览期间索引指针已变化')
        output.mkdir(parents=True, exist_ok=False)
        dependency = evidence(output, pointer_sha, files)
        manifest = {'schema': 1, 'task': TASK, 'status': '待命名样例确认未迁移', 'created_at': time.time(),
                    'pointer_sha256': pointer_sha, 'revision': pointer['revision'], 'index_directory': pointer['directory'],
                    'counts': dict(Counter(row['kind'] for row in files)), 'file_count': len(files),
                    'bytes': sum(row['bytes'] for row in files), 'files': files}
        manifest_path = output / '命名预览.json'
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        with (output / '完整路径对照.csv').open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['类别', '资源编号', '原始名称', '旧路径', '拟用新路径', '字节', 'SHA256', '名称依据', '共用资源编号'])
            for row in files:
                writer.writerow([row['kind'], row['id'], row['name'], row['old_path'], row['new_path'], row['bytes'], row['sha256'], row['naming_basis'], '；'.join(row['asset_ids'])])
        sample_ids = {'model:US_M1A2', 'model:US_A10', 'model:zombie-01', 'runtime:tank', 'loop:tankMove'}
        samples = [row for row in files if row['id'] in sample_ids or row['name'] in AUDIO_LABELS or '待辨识' in row['new_path'] or '待复核' in row['new_path']]
        lines = ['# 可读命名样例V001', '', '状态：待工程师确认命名，本轮未迁移资源、未切换索引、未改服务。', '',
                 f'当前索引：{pointer["revision"]}；冻结文件：{len(files)}份；模型{len(catalog["models"])}入口、声音{len(catalog["audio"])}事件。', '',
                 '完整摘要保存在索引；同一文件关联多个事件时只保留一份，完整对照列出全部关联编号。', '',
                 '名称释义来源于已有名称及类别，不代表听审或人工验收；待修、静音、用途候选状态保持。', '',
                 '| 类别 | 拟用路径（当前资源下） |', '| --- | --- |']
        for row in samples:
            lines.append(f'| {row["kind"]} | `{row["new_path"].split("当前资源/", 1)[1]}` |')
        lines += ['', '## 迁移前必须处理的依赖', '']
        lines += ['- ' + text for text in dependency['required_before_migration']]
        lines += ['', f'预览清单SHA256：`{sha(manifest_path)}`。', '']
        (output / '命名样例.md').write_text('\n'.join(lines), encoding='utf-8')
        (output / 'README.md').write_text('# 可读命名预览\n\n- `命名样例.md`：人工审核入口。\n- `完整路径对照.csv`：全部文件的一对一路径及共享事件关联。\n- `命名预览.json`：源摘要、修改时间、当前指针及拟用路径；不是执行或删除授权。\n- `迁移依赖核查.json`：当前任务保护重叠及丧尸交付摘要约束。\n\n本轮只生成预览。确认名称后实施路径迁移和消费者交接；旧索引、原始归档及历史凭证保持原记录。没有生成媒体副本或可再生成缓存。\n', encoding='utf-8')
    return {'status': manifest['status'], 'counts': manifest['counts'], 'files': len(files), 'manifest_sha256': sha(manifest_path), 'output': str(output)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=OUTPUT, help='独立且尚不存在的本地预览目录')
    args = parser.parse_args()
    print(json.dumps(prepare(args.output), ensure_ascii=False))
