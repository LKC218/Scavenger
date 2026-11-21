"""前瞻性登记、工程师验收和自动退役；不扫描推断旧存量可删除。"""
import json
import os
from pathlib import Path
import time
import uuid

class Automatic:
    def __init__(self, cleaner, registry):
        self.c, self.registry = cleaner, registry
        self.policy = registry.get('automation', {})
        self._baseline = None

    def require(self, ok, reason):
        if not ok:
            raise ValueError(reason)

    def enabled(self):
        return self.policy.get('enabled', False)

    def baseline(self):
        if self._baseline is None:
            path = self.policy['baseline']
            self.require(self.c.digest(self.c.checked(path)) == self.policy['baseline_sha256'], '存量边界摘要变化')
            self._baseline = set(self.c.read(path)['paths'])
        return self._baseline

    def future_task(self, task, paths):
        record = self.registry['tasks'].get(task, {})
        self.require(self.enabled() and task not in self.policy['preexisting_tasks'] and record.get('created_at', 0) >= self.policy['enabled_at'], '仅允许启用后新登记的任务，不接管旧任务存量')
        self.require(all(any(self.c.overlap(p, scope) and (p == scope or p.startswith(scope.rstrip('/')+'/'))
                             for scope in record['paths']) for p in paths), '文件不在所属任务已登记范围')

    def protected(self, path):
        values = list(self.registry.get('retained_evidence', []))
        for head in self.registry.get('accepted_assets', {}).values():
            values.extend(head['files'])
        return any(self.c.overlap(path, row['path']) for row in values)

    def blockers(self, item, path):
        conflicts = self.c.blockers(self.registry, path)
        if item.get('automatic_policy') != self.policy.get('id'):
            return conflicts
        # 只细化启用时的未知目录保护，精确文件须另经前瞻登记或具体新版验收。
        initial = self.policy['initial_unknown']
        return [name for name in conflicts if self.registry['tasks'][name] != initial.get(name)]

    def plan(self, acceptance_id):
        entry = self.registry['acceptances'][acceptance_id]
        self.require(self.c.digest(self.c.checked(entry['plan'])) == entry['sha256'], '验收清单已变化')
        plan = self.c.read(entry['plan'])
        self.require(entry['confirmed_by'] == '工程师' and entry['confirmation'], '缺少工程师确认依据')
        return plan

    def reference(self, item):
        keys = ('id','path','kind','task','automatic_policy','release_bundle')
        return {key:item[key] for key in keys if key in item}

    def registered(self, item):
        record = next((x for x in self.registry['items'] if x.get('id') == item.get('id')), None)
        self.require(record and (record == item or self.reference(record) == item), '条目身份或冻结集合变化')
        return record

    def validate(self, item):
        item = self.registered(item)
        self.require(item.get('automatic_policy') == self.policy.get('id') and self.enabled(), '条目未获当前自动策略授权')
        self.require(not item.get('suspended') and item.get('files'), '条目已暂停或没有冻结文件')
        self.future_task(item['task'], [r['path'] for r in item['files']])
        for row in item['files']:
            self.c.allowed(
                row['path'],
                item['kind'],
                bool(item.get('release_bundle')),
            )
            self.require(not self.protected(row['path']), '当前通过版或验收凭证继续保护：'+row['path'])
        if item['kind'] == 'cache':
            self.require(all(r['path'].casefold() not in self.baseline() for r in item['files']), '缓存包含启用前存量，须单独审核')
        else:
            plan = self.plan(item['acceptance'])
            self.require(plan['files'] == item['files'] and plan['task'] == item['task'], '旧版集合与验收范围不一致')
            head = self.registry['accepted_assets'][plan['asset']]
            node = head['acceptance']
            seen = set()
            while node != item['acceptance']:
                self.require(node and node not in seen, '当前正式版不是已确认版本的后继')
                seen.add(node)
                successor = self.plan(node)
                self.c.same(successor['evidence'])
                node = successor['parent']
            self.c.same(plan['evidence'])
            for row in head['files']:
                self.c.same(row)

    def enable(self, approved_sha, note):
        self.require(not self.enabled(), '自动维护已经启用，不重复建立边界')
        self.require(note and approved_sha == self.c.digest(self.c.checked(self.c.STATE+'/登记.json')), '启用需要当前登记摘要和工程师授权依据')
        started = time.time()
        paths = []
        for parent, dirs, files in os.walk(self.c.ROOT, followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(parent)/d).is_symlink()
                       and not getattr((Path(parent)/d).lstat(), 'st_file_attributes', 0) & 0x400]
            paths.extend((Path(parent)/name).relative_to(self.c.ROOT).as_posix().casefold() for name in files)
        baseline = self.c.STATE+'/自动维护/启用存量边界.json'
        self.c.save(baseline, {'created':started, 'paths':sorted(set(paths))}, exclusive=True)
        self.registry['automation'] = {'id':uuid.uuid4().hex, 'enabled':True, 'enabled_at':started,
            'authorization':note, 'scope':'仅启用后新任务的冻结缓存及工程师逐版确认的退役条目',
            'baseline':baseline, 'baseline_sha256':self.c.digest(self.c.checked(baseline)),
            'preexisting_tasks':sorted(self.registry['tasks']),
            'initial_unknown':{k:v for k,v in self.registry['tasks'].items() if v['status']=='unknown'},
            'cache_warning_bytes':5*2**30, 'recycle_warning_bytes':10*2**30}
        self.registry.update(automatic_cache=True)
        self.registry.setdefault('acceptances', {})
        self.registry.setdefault('accepted_assets', {})
        self.registry.setdefault('retained_evidence', [])
        # first_review保持原值，启用不代表未审核存量获得批准。
        self.c.save(self.c.STATE+'/登记.json', self.registry)
        print('自动维护已启用；已冻结存量边界，既有条目和回执不接管')

    def enroll_cache(self, task, paths, note):
        self.require(note, '缓存登记须说明如何重新生成，不能仅凭目录名判断无用')
        self.require(len(paths) == 1, '每次登记一个具体缓存范围')
        self.c.allowed(paths[0], 'cache')
        rows = [self.c.fingerprint(p) for p in self.c.files_under(paths[0])]
        self.require(rows, '空缓存无需登记；生成后登记准确文件集合')
        self.future_task(task, [r['path'] for r in rows])
        self.require(all(r['path'].casefold() not in self.baseline() for r in rows), '拒绝启用前的缓存存量')
        self.require(not any(self.protected(r['path']) for r in rows), '缓存与当前通过版或凭证重叠')
        item = {'id':uuid.uuid4().hex, 'path':paths[0], 'kind':'cache', 'task':task,
                'files':rows, 'registered_at':time.time(), 'regeneration_note':note, 'automatic_policy':self.policy['id']}
        self.add_item(item)

    def add_item(self, item):
        occupied = {r['path'].casefold() for x in self.registry['items'] if not x.get('suspended') for r in x.get('files', [])}
        self.require(not any(r['path'].casefold() in occupied for r in item['files']), '文件已登记，禁止重复授权或自动改写快照')
        self.registry['items'].append(item)
        self.c.save(self.c.STATE+'/登记.json', self.registry)
        print('已登记冻结条目：'+item['id'])

    def stage(self, args):
        self.require(args.asset and args.version and args.evidence and args.replacement and args.note and args.path, '需提供资源ID、版本、旧文件、新文件、验收凭证和依赖核查说明')
        self.future_task(args.task, args.path)
        rows = []
        for path in args.path:
            self.c.allowed(
                path,
                'history',
                bool(getattr(args, 'release_bundle', False)),
            )
            self.require(self.c.checked(path).is_file(), '历史版须逐文件指定，不接受整目录')
            rows.append(self.c.fingerprint(path))
        replacements = [self.c.fingerprint(p) for p in args.replacement]
        evidence = self.c.fingerprint(args.evidence)
        self.future_task(args.task, [r['path'] for r in rows+replacements+[evidence]])
        self.require(len({r['path'].casefold() for r in rows}) == len(rows), '旧文件重复')
        self.require(not any(self.c.overlap(old['path'], keep['path']) for old in rows for keep in replacements+[evidence]), '新旧版本或凭证重叠')
        self.require(not any(self.c.overlap(old['path'], r['path']) for old in rows for r in self.registry['retained_evidence']), '不能退役已有验收凭证')
        head = self.registry['accepted_assets'].get(args.asset)
        plan = {'created':time.time(), 'automatic_policy':self.policy['id'], 'asset':args.asset, 'version':args.version,
                'task':args.task, 'files':rows, 'replacement':replacements, 'evidence':evidence,
                'dependency_review':args.note, 'parent':head['acceptance'] if head else None,
                'status':'等待工程师确认',
                'release_bundle':bool(getattr(args, 'release_bundle', False))}
        path = self.c.STATE+'/验收待确认/'+uuid.uuid4().hex+'.json'
        self.c.save(path, plan, exclusive=True)
        print(json.dumps({'manifest':path, 'sha256':self.c.digest(self.c.checked(path)), 'status':plan['status'], 'files':len(rows)}, ensure_ascii=False))

    def accept(self, path, approved_sha, note):
        self.require(path and path.startswith(self.c.STATE+'/验收待确认/') and note, '需指定待确认清单及工程师实际确认依据')
        self.require(self.c.digest(self.c.checked(path)) == approved_sha, '验收清单摘要不匹配')
        plan = self.c.read(path)
        self.require(plan['automatic_policy']==self.policy['id'] and plan['created']>=self.policy['enabled_at'], '不能追认其他策略或启用前清单')
        self.future_task(plan['task'], [r['path'] for r in plan['files']])
        head = self.registry['accepted_assets'].get(plan['asset'])
        self.require(plan['parent'] == (head['acceptance'] if head else None), '当前正式版已变化，重新准备验收清单')
        for row in plan['files']+plan['replacement']+[plan['evidence']]:
            self.c.same(row)
        acceptance = uuid.uuid4().hex
        self.registry['acceptances'][acceptance] = {'plan':path, 'sha256':approved_sha, 'confirmed_by':'工程师', 'confirmation':note, 'confirmed_at':time.time()}
        retired = {r['path'].casefold() for r in plan['files']}
        files = {r['path']:r for r in (head['files'] if head else []) if r['path'].casefold() not in retired}
        files.update({r['path']:r for r in plan['replacement']})
        self.registry['accepted_assets'][plan['asset']] = {'acceptance':acceptance, 'version':plan['version'], 'files':list(files.values())}
        if plan['evidence'] not in self.registry['retained_evidence']:
            self.registry['retained_evidence'].append(plan['evidence'])
        item = {'id':uuid.uuid4().hex, 'path':plan['files'][0]['path'], 'kind':'history', 'task':plan['task'],
                'files':plan['files'], 'acceptance':acceptance, 'automatic_policy':self.policy['id'],
                'registered_at':time.time(), 'release_bundle':plan.get('release_bundle', False)}
        self.add_item(item)
        print('工程师验收已登记；旧版仍有任务依赖时暂缓，定时任务不会自行确认验收')

    def status(self, task_name=None):
        """只汇总登记，不推断未登记文件、真实依赖或工程师验收。"""
        tasks = self.registry['tasks']
        self.require(not task_name or task_name in tasks, '任务未登记；先protect建立本轮独立任务')
        selected = {task_name:tasks[task_name]} if task_name else tasks
        confirmed = {x['plan'] for x in self.registry.get('acceptances', {}).values()}
        waiting = {}
        for path in self.c.checked(self.c.STATE+'/验收待确认').glob('*.json'):
            relative = path.relative_to(self.c.ROOT).as_posix()
            if relative in confirmed:
                continue
            plan = self.c.read(relative)
            if plan.get('automatic_policy') == self.policy.get('id'):
                waiting.setdefault(plan['task'], []).append({'manifest':relative, 'asset':plan['asset'], 'version':plan['version']})
        rows = []
        for name, task in selected.items():
            eligible = self.enabled() and name not in self.policy.get('preexisting_tasks', []) and task.get('created_at', 0) >= self.policy.get('enabled_at', float('inf'))
            items = [x for x in self.registry['items'] if x['task']==name and x.get('automatic_policy')==self.policy.get('id')]
            rows.append({'task':name, 'status':task['status'], 'protected_scopes':len(task['paths']),
                         'future_registration_eligible':eligible,
                         'cache_items':sum(x['kind']=='cache' for x in items),
                         'accepted_retirement_items':sum(x['kind']=='history' for x in items),
                         'suspended_items':sum(bool(x.get('suspended')) for x in items),
                         'waiting_acceptance':waiting.get(name, []),
                         'note':'仅汇总登记；未登记产物不会自动发现或清理' if eligible else '既有任务继续保护；后续新制作轮次另建独立任务，不能改名或关闭旧保护来绕过边界'})
        result = {'mode':'只读登记核对', 'automatic_enabled':self.enabled(), 'tasks':rows,
                  'accepted_assets':len(self.registry.get('accepted_assets', {})),
                  'unreviewed_stock_adopted':False,
                  'limitation':'不能据此断言没有漏登记的文件；实际产物、服务引用和验收仍由制作任务核对'}
        print(json.dumps(result, ensure_ascii=False))
        return result

    def tick(self):
        c = self.c
        skipped = []
        purged = moved = 0
        for path in c.checked(c.STATE+'/回收').glob('*/回执.json'):
            relative = path.relative_to(c.ROOT).as_posix()
            receipt = c.read(relative)
            if receipt.get('automatic_policy') != self.policy['id'] or receipt['status'] != '完成':
                continue
            stored = [r for r in receipt['files'] if r['state']=='stored']
            if not stored or any(time.time()-r['moved_at'] < 7*c.DAY for r in stored):
                continue
            try:
                c.recover_or_purge(self.registry, relative, purge=True)
                purged += len(stored)
            except (ValueError, OSError, KeyError) as error:
                if c.read(relative)['status'] == '中断待核对':
                    raise ValueError('永久清除中断，保留回执并停止本轮：'+str(error)) from error
                skipped.append({'path':relative, 'reason':str(error)})
        manifest, report = c.preview(self.registry, scheduled=True)
        if report['files']:
            c.quarantine(self.registry, manifest, c.digest(c.checked(manifest)), automatic_only=True)
            moved = len(report['files'])
        cache_bytes = sum(r['bytes'] for x in self.registry['items'] if x.get('automatic_policy')==self.policy['id'] and x['kind']=='cache'
                          for r in x['files'] if c.checked(r['path']).exists())
        recycle_bytes = 0
        for path in c.checked(c.STATE+'/回收').glob('*/回执.json'):
            receipt = c.read(path.relative_to(c.ROOT).as_posix())
            if receipt.get('automatic_policy')==self.policy['id']:
                recycle_bytes += sum(r['bytes'] for r in receipt['files'] if r['state']=='stored')
        warnings = []
        if cache_bytes > self.policy['cache_warning_bytes']:
            warnings.append('已登记缓存超过5GiB警戒线；未满足准入条件的文件不提前删除')
        if recycle_bytes > self.policy['recycle_warning_bytes']:
            warnings.append('本策略回收载荷超过10GiB警戒线；未满七天不提前清除')
        result = {'moved_files':moved, 'purged_files':purged, 'skipped':report['skipped']+skipped,
                  'cache_bytes':cache_bytes, 'recycle_bytes':recycle_bytes, 'warnings':warnings}
        output = c.STATE+'/自动维护/最近执行结果.json'
        if not c.checked(output).exists() or c.read(output) != result:
            c.save(output, result)
        print(json.dumps(result, ensure_ascii=False))
