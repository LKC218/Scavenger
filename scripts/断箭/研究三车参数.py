"""只读整理既有三车配装与参数，生成对丧尸研究数据、Markdown和离线HTML。"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
TASK = '三车对丧尸参数研究-V001-01a1a2af'
DATA = ROOT/'research/断箭/三车对丧尸研究/第一版-01a1a2af'
LOCAL = ROOT/'local-data/断箭/玩法研究/三车对丧尸-V001-01a1a2af'
REPORT = ROOT/'reports/断箭/三车对丧尸参数研究-第一版-01a1a2af.html'
STATIC = ROOT/'local-data/断箭/批次/BA-26273833-001'
DB = STATIC/'证据/装配/源配置核验.json'
ASSEMBLY = STATIC/'装配映射.json'
NATIVE = ROOT/'local-data/断箭/批次/BA-26273833-003/证据/原生方法地址索引.json'
SUGGESTIONS = DATA/'网页原型建议参数.json'
TEMPLATE = Path(__file__).with_name('三车参数报告模板.html')
SAMPLES = [('tank', 245, 'US_M1A2', 'M1A2'), ('heli', 19, 'US_AH64D', 'AH-64D'), ('plane', 282, 'US_A10', 'A-10C')]
MAIN_AMMO = [253, 10, 164, 293, 287]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def identity(path):
    return {'path': path.relative_to(ROOT).as_posix(), 'bytes': path.stat().st_size,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def choose(rows, options, field, index):
    overrides = {r[field] for r in options if r.get(field, 0) > 0}
    if len(overrides) > 1:
        raise ValueError('选项覆盖不唯一：'+field)
    if overrides:
        selected = next(iter(overrides))
        assert selected in rows, (field, selected, rows)
        return index[selected], '已选Options.'+field
    defaults = [index[i] for i in rows if index[i].get('IsDefault')]
    if len(defaults) == 1:
        return defaults[0], '单位关联且IsDefault=true'
    if len(rows) == 1:
        return index[rows[0]], '单位唯一关联'
    raise ValueError('基础配置不唯一：'+field)


def extract():
    frozen = [identity(p) for p in [DB, ASSEMBLY, NATIVE, ROOT/'src/载具靶场/模拟.mjs', ROOT/'src/载具靶场/飞行.mjs']]
    source = read(DB)['database']
    tables = source['tables']
    indexes = {name: {r['Id']: r for r in rows} for name, rows in tables.items()}
    assert len(tables) == 24
    plans = read(ASSEMBLY)['roots']
    vehicles = []
    for kind, uid, model, label in SAMPLES:
        plan = next(p for p in plans if p['selected_unit_id'] == uid and p['selected_unit']['ModelFileName'] == model)
        unit = indexes['Units'][uid]
        assert unit['ModelFileName'] == model
        options = [indexes['Options'][oid] for oid in plan['selected_option_ids']]
        active = {slot['turret_id'] for slot in plan['slot_turrets']}
        child_relations = []
        changed = True
        while changed:
            changed = False
            for rel in tables['TurretUnits']:
                tid = rel['TurretId']
                if rel['UnitId'] == uid and tid not in active and indexes['Turrets'][tid].get('ParentTurretId') in active:
                    active.add(tid)
                    child_relations.append(rel)
                    changed = True
        weapon_relations = [dict(table='TurretWeapons', **r) for r in tables['TurretWeapons'] if r['TurretId'] in active]
        weapon_relations += [dict(table='SquadWeapons', **r) for r in tables['SquadWeapons'] if r['UnitId'] == uid]
        wids = sorted({r['WeaponId'] for r in weapon_relations})
        weapons = []
        for wid in wids:
            relations = [r for r in tables['WeaponAmmunitions'] if r['UnitId'] == uid and r['WeaponId'] == wid and r['Quantity'] > 0]
            weapons.append({'record': indexes['Weapons'][wid],
                            'mount_relations': [r for r in weapon_relations if r['WeaponId'] == wid],
                            'ammunitions': [{'relation': r, 'record': indexes['Ammunitions'][r['AmmunitionId']]} for r in relations]})
        mobility_relations = [r for r in tables['UnitPropulsions'] if r['UnitId'] == uid]
        armor_relations = [r for r in tables['UnitArmors'] if r['UnitId'] == uid]
        mobility, mproof = choose([r['MobilityId'] for r in mobility_relations], options, 'MobilityId', indexes['Mobility'])
        armor, aproof = choose([r['ArmorId'] for r in armor_relations], options, 'ArmorId', indexes['Armors'])
        fly = indexes['PlaneFlyPresets'].get(mobility.get('FlyPresetId'))
        vehicles.append({'kind': kind, 'label': label, 'model_id': 'model:'+model, 'unit': unit,
                         'configuration': {'selected_option_ids': plan['selected_option_ids'], 'selected_options': options,
                                           'selection_evidence': plan.get('selected_options', []),
                                           'mount_slots': [{k: s.get(k) for k in ['slot', 'turret_id', 'source', 'option_id', 'relation_id']} for s in plan['slot_turrets']],
                                           'note': '沿既有静态装配；无显式默认的类别沿用原基础预设策略，不冒充玩家存档。'},
                         'turrets': [indexes['Turrets'][tid] for tid in sorted(active)], 'child_relations': child_relations,
                         'weapons': weapons, 'mobility': {'record': mobility, 'selected_by': mproof, 'relations': mobility_relations},
                         'armor_reference': {'record': armor, 'selected_by': aproof, 'relations': armor_relations},
                         'flight_preset': fly})
    methods = read(NATIVE)
    wanted = {'GetPenetrationForAmmunition', 'GetPenetration', 'CalculateHitDamage', 'DamageFormulaKinetic',
              'DamageFormulaHEAT', 'DealAOEDamage', 'DealUnitDamage', 'CalculateMissileHitChance'}
    names = [r for r in methods if r['type'].startswith('BrokenArrow.') and
             (r['name'] in wanted or (r['name'] in ['Update', 'InternalUpdate'] and any(
                 k in r['type'] for k in ['HitDetectionSystem', 'TrajectoryShellSystem', 'ShellHitSystem'])))]
    raw = {'schema': 1, 'build': '26273833', 'version': 'V001', 'verification': '既有快照只读重整；未启动游戏或重新读取安装目录',
           'source_identity': [identity(DB), identity(ASSEMBLY), identity(NATIVE)],
           'database_source': {k: source[k] for k in ['source', 'path_id', 'raw_sha256']},
           'table_counts': {name: len(rows) for name, rows in tables.items()}, 'vehicles': vehicles,
           'method_signatures': names,
           'limits': ['所有源字段保持原值；距离、速度、时间的运行换算未验证，枚举和位掩码未解码。',
                      'Quantity为关系行值，可能按武器或挂架使用，未换算整车总备弹。',
                      '方法名和签名证明声明存在，不证明调用顺序、分支与公式；原作流程为研究索引。']}
    code = ('import {WEAPONS} from '+json.dumps((ROOT/'src/载具靶场/模拟.mjs').as_uri())+';'
            'import {FLIGHT} from '+json.dumps((ROOT/'src/载具靶场/飞行.mjs').as_uri())+';'
            'process.stdout.write(JSON.stringify({weapons:WEAPONS,flight:FLIGHT}));')
    proc = subprocess.run(['node', '--input-type=module', '--eval', code], cwd=ROOT, check=True, capture_output=True, text=True, encoding='utf-8')
    current = json.loads(proc.stdout)
    sim = (ROOT/'src/载具靶场/模拟.mjs').read_text(encoding='utf-8')
    speed = re.search(r'const air=u.kind===\x27heli\x27,speed=air\?([\d.]+):([\d.]+)', sim)
    assert speed, '移动速度源码结构变化，请重新核对'
    current.update(schema=1, status='当前源码参数，尚未接入丧尸战斗', movement={'tank_speed_mps': float(speed[2]), 'heli_speed_mps': float(speed[1])},
                   source_identity=[identity(ROOT/'src/载具靶场/模拟.mjs'), identity(ROOT/'src/载具靶场/飞行.mjs')],
                   impact_note='命中目标全额，爆炸半径内其他靶统一55%；没有随距离衰减或逐目标爆炸遮挡。')
    assert frozen == raw['source_identity']+current['source_identity'], '读取期间输入发生变化'
    return raw, current


def ammo_rows(raw):
    for v in raw['vehicles']:
        for w in v['weapons']:
            for a in w['ammunitions']:
                yield v, w['record'], a


def markdown(raw, current, proposal):
    lines = ['# 三车参数对照与对丧尸设计建议', '', '版本：V001。工程师已确认载具打丧尸、普通丧尸100生命、不强制爆头、保护基地或防线；载具受损后续讨论。', '',
             '## 证据与单位', '', '原值来自构建26273833历史数据库和固定配装，不代表当前安装最新版本。源字段原样保留，运行换算和枚举尚未核验。当前值来自实际靶场源码；建议值是独立设计初值，未接入或试玩。', '',
             '## 三车原始移动参数', '', '| 载具 | 单位ID | 机动ID | 道路速度原值 | 越野原值 | 倒车原值 | 转率原值 | 加速度原值 | 飞行预设ID |', '| --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for v in raw['vehicles']:
        m = v['mobility']['record']
        lines.append('| '+' | '.join(str(x) for x in [v['label'], v['unit']['Id'], m['Id'], m['MaxSpeedRoad'], m['MaxCrossCountrySpeed'], m['MaxSpeedReverse'], m['TurnRate'], m['Acceleration'], m['FlyPresetId']])+' |')
    lines += ['', '固定翼Mobility中的0不代表飞机不能加速，应继续读取PlaneFlyPresets；原始JSON包含完整关联。', '', '## 固定配装与弹药原值', '',
              '| 载具 | 武器ID／弹药ID | 弹药名称 | 地面／低空／高空射程原值 | 伤害原值 | 伤害半径原值 | 数量关系原值 |', '| --- | --- | --- | --- | --- | --- | --- |']
    for v, w, a in ammo_rows(raw):
        r = a['record']
        lines.append(f"| {v['label']} | {w['Id']}／{r['Id']} | {r['Name'].strip()} | {r['GroundRange']}／{r['LowAltRange']}／{r['HighAltRange']} | {r['Damage']} | {r['HealthAOERadius']} | {a['relation']['Quantity']} |")
    lines += ['', '数量是WeaponAmmunitions关系行值，不是整机总量；例如多个相同挂架不可不经调用核对直接相加或相乘。完整炮塔槽、武器、弹药及选项见原始JSON。', '', '## 网页建议值与当前值', '',
              '| 武器 | 当前射程／间隔／伤害／半径 | 建议射程／间隔／伤害／外半径 | 对丧尸职责 |', '| --- | --- | --- | --- |']
    for p in proposal['weapons']:
        c = current['weapons'][p['current_key']]
        lines.append(f"| {p['label']} | {c['range']}米／{c['reload']}秒／{c['damage']}／{c['blast']}米 | {p['range_m']}米／{p['interval_s']}秒／{p['damage']}／{p['outer_radius_m']}米 | {p['role']} |")
    lines += ['', '所有建议伤害均采用普通丧尸100生命的独立量表，不能用原作Damage倍率换算。机炮间隔是逻辑弹间隔；连发组和组间休息另列于建议JSON。炸弹射程只作任务引导参考，是否投放仍由高度、速度、弹道与预计落点决定。', '',
              '## 弹药判定流程图', '', '下图为本项目建议，不是已还原的原作调用图。原作已确认的表字段与原生方法签名分别归档；没有推断其精确调用次序。', '',
              '```mermaid', 'flowchart TD', '  A[指定目标或攻击区域] --> B{武器允许攻击该目标}', '  B -->|否| X[提示不适配]', '  B -->|是| C{射程和射界有效}',
              '  C -->|射程外| M[地面或旋翼按现有规则接近；飞机规划进场]', '  M --> C', '  C -->|遮挡| W[停止并提示受阻]', '  C -->|有效| D[停车或悬停并瞄准；飞机保持前飞]',
              '  D --> E{装填或连发节奏允许发射}', '  E -->|等待| E', '  E -->|允许| F[生成逻辑弹丸并启动装填]', '  F --> G{弹药类型}',
              '  G -->|机炮或炮弹| H[扫掠线段与简化碰撞体，取最近命中]', '  G -->|制导导弹| I[有限转向追踪；目标失效则飞向最后位置]',
              '  G -->|普通炸弹| J[继承机体速度并受重力影响]', '  H --> K[碰撞或落地后生成一次命中事件]', '  I --> K', '  J --> K',
              '  K --> L[空间网格查询活体候选，去重并检查距离和建筑遮挡]', '  L --> N[直击和范围伤害合并取较大值，每弹每目标只结算一次]',
              '  N --> O[扣血、死亡及击杀归属，触发独立表现]', '```', '',
              '## 三类武器的简化规则', '',
              '1. 机炮／直射炮弹使用前后位置扫掠，避免高速穿过小目标。丧尸用低成本胶囊或圆柱，不逐骨骼三角形碰撞；首轮不穿透多只、不强制爆头。',
              '2. 爆炸按三维距离衰减，实心建筑挡住爆炸到目标中心的射线则本次范围伤害为0；边缘阈值与内部满伤半径显式配置。伤害查询不依赖特效显示上限。',
              '3. 保留现有发射后自主追踪作为项目规则。原AH-64D弹药164为LaserGuided=true，未证明发射后不管；转换为本项目规则必须明确记录。目标死亡不自动换活靶，继续飞向最后有效位置再落地或寿命结束。',
              '4. Mk84普通炸弹离机后不追踪丧尸群；以预测落点覆盖区域。固定翼单轮攻击后脱离并盘旋，不自动连续轰炸。',
              '5. 不把原作StressDamage直接改名为丧尸恐惧。击退、减速、压制、断肢、尸体物理和履带碾压均后置。', '',
              '## 爆炸计算建议', '',
              '`d = length(目标受击中心 - 爆心)`；`d <= r0`为满伤，`r0 < d <= R`线性降至`edge × D`，`d > R`为0。边界R包含，刚超过R即为0。直击与范围伤害取较大值，不重复相加。', '',
              '`falloff(d) = 1`（内圈）；`1 - (1-edge) × (d-r0)/(R-r0)`（衰减圈）。最终伤害先检查阵营、活体和遮挡，再计算`D × falloff`。所有参数属于本项目建议。', '',
              '## 测试与后续决策', '',
              '优先测单只100生命、密集群、稀疏群、隔墙、半径边缘、高速移动目标、导弹目标提前死亡、取消命令后的在途弹、双弹同帧死亡。不同数量下分别记录模拟CPU、查询次数、逻辑弹数、死亡队列、尸体／特效数和整帧P95，不能套用独立群体测试的帧率。', '',
              '基地压力作为失败来源；防线生命、丧尸接触伤害、波次密度与目标优先级还需后续定案。先保留无限储备与装填，后续用弹药成本、补给、出动间隔和待命区约束空中优势；本轮不擅自加入有限弹药。', '',
              '## 文件与来源', '', '[原始参数](三车原始参数.json)、[当前靶场参数](当前靶场参数.json)、[建议参数](网页原型建议参数.json)。', '',
              '[官方手册](https://ftp.matrixgames.com/pub/BrokenArrow/BrokenArrowManualEBOOK.pdf)用于核对射界、装填以外的操作约束及固定翼攻击方式；[官方入门指南](https://brokenarrow_backend.slitherine.com/news/broken-arrows-survival-guide)区分发现目标和捕获目标。文档为相应公开版本，不代替当前构建实操。', '',
              '## 验证与保留', '', '生成器核对所有原始引用、当前源码及建议公式；页面检查另有本地回执。设计和听感、战斗性能、平衡验收仍待实施后验证。本轮首次交付，无被替代载荷和退役清单；原数据库和原生方法索引作为研究依赖保留。']
    return '\n'.join(lines)+'\n'


def verify(raw, current, proposal):
    assert [v['unit']['Id'] for v in raw['vehicles']] == [245, 19, 282]
    for v, w, a in ammo_rows(raw):
        assert a['relation']['UnitId'] == v['unit']['Id']
        assert a['relation']['WeaponId'] == w['Id']
        assert a['relation']['AmmunitionId'] == a['record']['Id']
    assert len(list(ammo_rows(raw))) == 12
    assert set(MAIN_AMMO) <= {a['record']['Id'] for _, _, a in ammo_rows(raw)}
    assert proposal['zombie']['health'] == 100 and not proposal['zombie']['requires_headshot']
    for p in proposal['weapons']:
        assert p['current_key'] in current['weapons']
        assert p['damage'] > 0 and p['interval_s'] > 0 and p['range_m'] > 0
        assert 0 <= p['inner_radius_m'] < p['outer_radius_m'] and 0 <= p['edge_multiplier'] <= 1
    checks = ['三车固定身份和配装关系', '12条正数量武器弹药关系及5种首轮弹药', '当前参数来自实际规则模块', '建议值独立且与100生命基准一致']
    for record in raw['source_identity']+current['source_identity']:
        assert identity(ROOT/record['path']) == record, record['path']
    checks.append('五份输入身份一致')
    return checks


def generate(check_only=False):
    raw, current = extract()
    proposal = read(SUGGESTIONS)
    checks = verify(raw, current, proposal)
    if check_only:
        assert read(DATA/'三车原始参数.json') == raw, '归档原值与输入不一致'
        assert read(DATA/'当前靶场参数.json') == current, '归档当前值与源码不一致'
        assert (DATA/'三车参数对照与判定流程.md').read_text(encoding='utf-8') == markdown(raw, current, proposal)
        assert REPORT.read_text(encoding='utf-8') == render(raw, current, proposal)
        checks.append('归档数据、Markdown与HTML均可重复生成')
    else:
        save(DATA/'三车原始参数.json', raw)
        save(DATA/'当前靶场参数.json', current)
        (DATA/'三车参数对照与判定流程.md').write_text(markdown(raw, current, proposal), encoding='utf-8')
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(render(raw, current, proposal), encoding='utf-8')
    receipt = {'task': TASK, 'version': 'V001', 'checked_at_utc': datetime.now(timezone.utc).isoformat(), 'passed': True,
               'mode': 'verify' if check_only else 'generate', 'checks': checks, 'inputs': raw['source_identity']+current['source_identity'],
               'outputs': [identity(p) for p in [DATA/'三车原始参数.json', DATA/'当前靶场参数.json', SUGGESTIONS, DATA/'三车参数对照与判定流程.md', REPORT]],
               'acceptance': '数据与文档技术核验；玩法未接入，设计等待工程师确认'}
    save(LOCAL/('数据复核.json' if check_only else '生成回执.json'), receipt)
    print(json.dumps({'passed': True, 'checks': checks, 'ammo_relations': len(list(ammo_rows(raw))), 'report': REPORT.relative_to(ROOT).as_posix()}, ensure_ascii=False))


def render(raw, current, proposal):
    payload = json.dumps({'raw': raw, 'current': current, 'proposal': proposal}, ensure_ascii=False).replace('<', '\\u003c')
    return TEMPLATE.read_text(encoding='utf-8').replace('__DATA__', payload)


def check_page():
    from playwright.sync_api import sync_playwright
    tested_identity = [identity(p) for p in [REPORT, TEMPLATE, SUGGESTIONS]]
    result = {'checks': [], 'errors': [], 'headless': True, 'network_requests': [], 'passed': False,
              'tested_identity': tested_identity, 'browser_versions': {}, 'viewports': [[1440, 1000], [390, 844]],
              'checked_at_utc': datetime.now(timezone.utc).isoformat(), 'design_acceptance': '等待工程师确认'}
    LOCAL.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        for channel in ['chrome', 'msedge']:
            browser = pw.chromium.launch(channel=channel, headless=True)
            result['browser_versions'][channel] = browser.version
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.on('pageerror', lambda e: result['errors'].append(str(e)))
            page.on('request', lambda req: result['network_requests'].append(req.url) if req.url.startswith(('http:', 'https:')) else None)
            page.goto(REPORT.as_uri())
            for kind, count in [('tank', 4), ('heli', 4), ('plane', 4)]:
                page.locator(f'button[data-vehicle="{kind}"]').click()
                assert page.locator('#ammo-table tbody tr').count() == count
                assert json.loads(page.locator('#raw-details').text_content())['kind'] == kind
            page.locator('button[data-vehicle="tank"]').click()
            for distance, expected in [(0, 180), (2, 180), (4, 126), (7, 45), (7.1, 0)]:
                page.locator('#distance').fill(str(distance))
                page.locator('#distance').dispatch_event('input')
                assert abs(float(page.locator('#damage-result').get_attribute('data-damage'))-expected) < 1e-9
            page.locator('#distance').fill('4')
            page.locator('#distance').dispatch_event('input')
            assert page.locator('#damage-result').inner_text() == '126.0'
            for p in read(SUGGESTIONS)['weapons']:
                page.locator('#blast-weapon').select_option(p['id'])
                page.locator('#distance').fill(str(p['outer_radius_m']+1))
                page.locator('#distance').dispatch_event('input')
                assert '0.0' in page.locator('#damage-result').inner_text()
            page.locator('#blast-weapon').select_option('tank_he')
            page.locator('#distance').fill('4')
            page.locator('#distance').dispatch_event('input')
            page.evaluate('document.documentElement.style.scrollBehavior="auto";scrollTo(0,0)')
            for href in page.locator('a').evaluate_all('(items)=>items.map(a=>a.getAttribute("href"))'):
                if not href.startswith(('https:', 'http:', '#')):
                    from urllib.parse import unquote
                    assert (REPORT.parent/unquote(href)).is_file(), href
            page.screenshot(path=str(LOCAL/(channel+'-桌面.png')), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), channel
            page.screenshot(path=str(LOCAL/(channel+'-窄屏.png')), full_page=True)
            page.emulate_media(media='print')
            assert page.evaluate('getComputedStyle(document.body).backgroundColor') == 'rgb(255, 255, 255)'
            result['checks'].append(channel+'：三车切换、12条关系、建议衰减、链接、390窄屏与打印')
            browser.close()
    assert not result['errors'] and not result['network_requests']
    assert tested_identity == [identity(p) for p in [REPORT, TEMPLATE, SUGGESTIONS]], '页面检查期间输入发生变化'
    result['passed'] = True
    save(LOCAL/'报告页面验证.json', result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--verify', action='store_true')
    group.add_argument('--verify-page', action='store_true')
    args = parser.parse_args()
    if args.verify_page:
        check_page()
    else:
        generate(args.verify)
