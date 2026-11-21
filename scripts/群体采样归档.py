"""群体样本自动归档与Windows前台只读观察，不激活任何窗口。"""
from collections import deque
import csv
import ctypes
from ctypes import wintypes
import hashlib
import json
from pathlib import Path
import sys
import threading
import time


class ForegroundObserver:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.events = deque(maxlen=28800)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def proof(self):
        result = {'epoch': time.time(), 'iab': False, 'chrome': False, 'edge': False}
        if sys.platform != 'win32':
            return result
        u, k = ctypes.windll.user32, ctypes.windll.kernel32
        u.GetForegroundWindow.restype = wintypes.HWND
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(u.GetForegroundWindow(), ctypes.byref(pid))
        handle = k.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return result
        try:
            buffer = ctypes.create_unicode_buffer(2048)
            size = wintypes.DWORD(len(buffer))
            if k.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                name = Path(buffer.value).name.casefold()
                result.update(iab=name == 'chatgpt.exe', chrome=name == 'chrome.exe', edge=name == 'msedge.exe')
        finally:
            k.CloseHandle(handle)
        return result

    def run(self):
        start = time.monotonic()
        with (self.directory / 'Windows前台观察.jsonl').open('a', encoding='utf-8', buffering=1) as stream:
            while not self.stop.is_set():
                row = self.proof()
                self.events.append(row)
                stream.write(json.dumps(row) + '\n')
                now = time.monotonic()
                next_at = start + (int((now-start) / .5)+1) * .5
                self.stop.wait(max(0, next_at-time.monotonic()))

    def coverage(self, start, end, browser):
        rows = [r for r in list(self.events) if start-.6 <= r['epoch'] <= end+.6]
        covered = bool(rows) and rows[0]['epoch'] <= start+.6 and rows[-1]['epoch'] >= end-.6
        gaps = [b['epoch']-a['epoch'] for a, b in zip(rows, rows[1:])]
        verified = covered and max(gaps, default=0) <= 1.2 and all(r.get(browser, False) for r in rows)
        return {'nativeForegroundVerified': verified, 'nativeForegroundChecks': len(rows),
                'foregroundObservation': {'covered': covered, 'maxGapS': max(gaps, default=0),
                                          'foregroundPct': 100*sum(r.get(browser, False) for r in rows)/len(rows) if rows else 0,
                                          'scope': '预热及正式采样，Windows前台进程只读观察；不记录其他应用名称或标题'}}

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)


def qualification(result):
    config, summary = result['config'], result['summary']
    return bool(result['valid'] and result['nativeForegroundVerified'] and result['requestedDurationS'] >= 180
                and summary['elapsedS'] >= 179.5 and summary['fps'] >= 60 and summary['p95Ms'] <= 16.7
                and summary['over33Pct'] < .1 and summary['simulationDroppedS'] == 0
                and summary['minVisible'] == config['count'] and summary['minAnimated'] == config['count']
                and config['mode'] in ('open', 'channel')
                and all(m['complete'] and m['minVisible'] == m['count'] and m['minAnimated'] == m['count'] for m in result['models']))


def store_sample(result, manifest, observer, directory):
    config = result['config']
    if result.get('schema') != 2 or config['code_sha256'] != manifest['code_sha256'] or config['delivery']['sha256'] != manifest['delivery']['sha256']:
        raise ValueError('样本源码或资源身份不匹配')
    if config.get('actorSet') not in ('single', 'mixed') or config.get('mode') not in ('static', 'animated', 'open', 'channel'):
        raise ValueError('样本模式无效')
    if sum(m['count'] for m in config['models']) != config['count'] or len(result['models']) != 13:
        raise ValueError('逐款样本分母不完整')
    if result['endEpoch'] < result['startEpoch'] or not result.get('rows'):
        raise ValueError('样本时间或逐帧记录无效')
    browser = result['environment']['browserLabel']
    if browser not in ('iab', 'chrome', 'edge'):
        raise ValueError('采样浏览器未标明')
    result.update(observer.coverage(result['startEpoch'], result['endEpoch'], browser))
    result['browser'] = browser
    result['headless'] = False
    result['windowMode'] = '内置可见浏览器' if browser == 'iab' else '独立可见浏览器'
    result['stable60Passed'] = qualification(result)
    # 相同配置允许留下独立复测；按时间戳创建，绝不覆盖历史样本。
    folder = Path(directory) / '样本'
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{browser}-{config['actorSet']}-{config['count']}-{config['mode']}-{config['view']['internal'][0]}-{result['requestedDurationS']}s-{time.time_ns()}"
    encoded = json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    json_path = folder / (name+'.json')
    with json_path.open('xb') as stream:
        stream.write(encoded)
    columns = ['t', 'frame', 'simulation', 'animation', 'sync', 'submit', 'gpu', 'calls', 'triangles', 'visible', 'animated', 'moving', 'neighbors', 'dropped']
    with (folder / (name+'.csv')).open('x', encoding='utf-8-sig', newline='') as stream:
        for label, value in [('配置', config), ('环境', result['environment']), ('判定', {'valid': result['valid'], 'nativeForegroundVerified': result['nativeForegroundVerified'], 'stable60Passed': result['stable60Passed']})]:
            stream.write('# '+label+' '+json.dumps(value, ensure_ascii=False)+'\n')
        writer = csv.writer(stream)
        writer.writerow(columns + [m['id']+':'+key for m in config['models'] for key in ('count', 'visible', 'animated', 'moving')])
        for row in result['rows']:
            writer.writerow([row.get(key) for key in columns] + [m[key] for m in row['models'] for key in ('count', 'visible', 'animated', 'moving')])
    return {'file': json_path.name, 'sha256': hashlib.sha256(encoded).hexdigest(),
            'nativeForegroundVerified': result['nativeForegroundVerified'], 'stable60Passed': result['stable60Passed']}
