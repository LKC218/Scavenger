"""只读索引GUI和VFX候选包，保留对象身份，不启动游戏或批量导出。"""
import hashlib
import json
from pathlib import Path

import UnityPy

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'local-data/原型/丧尸防线/V003-01a1a2af/交互资源'
SPRITES = ['CursorDefault', 'CursorAttack', 'CursorImpossible', 'CursorReverse', 'CursorFirePos',
           'CursorPrecisionStrike', 'SelectionCircle', 'TaskMarker_Attack', 'TaskMarker_Task',
           'Order_Attack', 'Order_Stop', 'Order_Reverse', 'Order_StrafingRun', 'Order_PrecisionStrike', 'Order_BombingRun', 'Order_ForceFire']


def main():
    config = json.loads((ROOT / 'local-data/断箭/本地配置.json').read_text(encoding='utf-8-sig'))
    source = Path(config['game_directory']) / 'BrokenArrow_Data/StreamingAssets/aa/PC'
    OUT.mkdir(parents=True, exist_ok=True)
    for prefix in ['prefabsgui', 'unitlabels', 'vfx']:
        path, = source.glob(prefix + '_assets_all_*.bundle')
        source_sha=hashlib.sha256(path.read_bytes()).hexdigest()
        previous=OUT/(prefix+'-索引.json')
        if previous.exists() and json.loads(previous.read_text(encoding='utf-8'))['sha256']!=source_sha:
            raise RuntimeError('原包身份变化，请建立新的制作轮次，不能覆盖V003证据')
        environment = UnityPy.load(str(path))
        rows = []
        for obj in environment.objects:
            if obj.type.name not in ['GameObject', 'Sprite', 'Texture2D', 'Material', 'MonoBehaviour', 'ParticleSystem']:
                continue
            name = obj.peek_name() or ''
            rows.append({'id': obj.path_id, 'type': obj.type.name, 'name': name})
        result = {'source': path.name, 'bytes': path.stat().st_size,
                  'sha256': source_sha,
                  'containers': [{'path': p, 'id': ptr.path_id} for p, ptr in environment.container.items()], 'objects': rows}
        (OUT / (prefix + '-索引.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        if prefix == 'prefabsgui':
            images = []
            candidates=[obj for obj in environment.objects if obj.type.name=='Sprite' and obj.peek_name() in SPRITES]
            sprite_names={obj.peek_name() for obj in candidates}
            candidates += [obj for obj in environment.objects if obj.type.name=='Texture2D' and obj.peek_name() in SPRITES and obj.peek_name() not in sprite_names]
            for obj in candidates:
                if obj.peek_name() not in SPRITES:
                    continue
                data = obj.read()
                image = data.image
                target = OUT / '图像' / (data.m_Name + '.png')
                target.parent.mkdir(parents=True, exist_ok=True)
                image.save(target)
                images.append({'name': data.m_Name, 'objectId': obj.path_id, 'source': path.name,
                               'type': obj.type.name, 'width': image.width, 'height': image.height,
                               'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'bytes': target.stat().st_size})
            (OUT / '图像来源.json').write_text(json.dumps(images, ensure_ascii=False, indent=2), encoding='utf-8')
        if prefix == 'vfx':
            objects={obj.path_id:obj for obj in environment.objects}
            presets=[]
            for entry,ptr in environment.container.items():
                if not entry.endswith(('AMMUNITION_VFX_PRESET_M230_BushMstr_HELI_US.asset','AMMUNITION_VFX_PRESET_MinigunAircraft.asset')):
                    continue
                preset=ptr.deref().read_typetree()
                collection=objects[preset['AmmunitionCollection']['m_PathID']].read_typetree()
                references={}
                for key in ['Shot','Tracer','InfantryImpact','GroundImpact','VehicleImpact','BuildingImpact']:
                    refs=collection[key] if isinstance(collection[key],list) else [collection[key]]
                    references[key]=[{'id':r['m_PathID'],'name':objects[r['m_PathID']].peek_name()} for r in refs]
                presets.append({'path':entry,'preset':preset,'collection':collection,'references':references})
            (OUT/'三车机炮特效原字段.json').write_text(json.dumps(presets,ensure_ascii=False,indent=2),encoding='utf-8')
        matches = [r for r in rows if any(w in r['name'].lower() for w in ['cursor', 'select', 'order', 'marker', 'waypoint', 'direction', 'path', 'impact', 'm230', 'minigun', 'gau8'])]
        print(json.dumps({'bundle': prefix, 'objects': len(rows), 'matching_objects': len(matches)}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
