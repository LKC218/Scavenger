"""从三型确证机械声派生循环，仅改独立靶场清单，不改正式库和模型。"""
import argparse
import json
import wave
from pathlib import Path

import numpy as np
from 检查载具原型资源 import OUT, query, sha, check

SOURCES = {
    'tankMove': ('model:US_M1A2', 'audio:ff1ba8b0-db8d-4187-ba22-35dd8762b5d4:fac3530269ac8b52'),
    'heliMove': ('model:US_AH64D', 'audio:78be8b8a-f46a-4ffb-b5ff-bbb34d23ca7c:204aa9fa2d39cfd7'),
    'planeMove': ('model:US_A10', 'audio:71d3cf2e-0d7f-4efd-94f4-3c7014f05aab:49073e01da189783'),
}


def prepare():
    import sys
    sys.path.insert(0,str(ROOT/'scripts/资源库'))
    from 统一资源 import available
    if available():
        from 兼容维护 import check_runtime
        check_runtime(sync_legacy=True)
        return
    manifest = json.loads((OUT / '资源清单.json').read_text('utf-8'))
    routes = json.loads((OUT / '私有路由.json').read_text('utf-8'))
    inputs = json.loads((OUT / '输入核验.json').read_text('utf-8'))
    folder = OUT / '循环音效'; folder.mkdir(exist_ok=True)
    records = []
    for key, (model, identity) in SOURCES.items():
        revision, row = query(identity)
        source = Path(row['local_paths']['url']); original = sha(source)
        if original != row['sha256'] or row['quality_status'] != 'ready':
            raise ValueError('来源身份或质量不符：' + key)
        if not any(b['model_id'] == model and b['field'] == 'MoveEvent' for b in row['bindings']):
            raise ValueError('没有确证的型号机械声关联：' + key)
        with wave.open(str(source), 'rb') as stream:
            rate = stream.getframerate(); channels = stream.getnchannels()
            if stream.getsampwidth() != 2: raise ValueError('仅接受当前确证PCM16')
            pcm = np.frombuffer(stream.readframes(stream.getnframes()), '<i2').reshape(-1, channels).astype(np.float64) / 32768
        # 三份实测均在1至7.5秒持续发声；避开事件启动及停止尾部。
        segment = pcm[rate:int(rate * 7.5)].mean(axis=1)
        if len(segment) != int(rate * 6.5): raise ValueError('稳定段长度不足')
        n = int(rate * .12); t = np.linspace(0, 1, n); weight = t * t * (3 - 2 * t)
        loop = np.concatenate((segment[n:-n], segment[-n:] * (1 - weight) + segment[:n] * weight))
        gain = min(.12 / np.sqrt(np.mean(loop ** 2)), .85 / np.max(np.abs(loop)))
        data = np.clip(np.rint(loop * gain * 32768), -32768, 32767).astype('<i2')
        output = folder / (key + '-循环.wav')
        with wave.open(str(output), 'wb') as stream:
            stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(rate); stream.writeframes(data.tobytes())
        decoded = data.astype(np.float64) / 32768
        windows = [float(np.sqrt(np.mean(a ** 2))) for a in np.array_split(np.tile(decoded, 4), 255)]
        boundary = float(abs(decoded[-1] - decoded[0])); differences = np.abs(np.diff(decoded))
        if min(windows) < .01 or boundary > float(np.quantile(differences, .999)):
            raise ValueError('循环静音窗或接缝跳变超出素材局部差分：' + key)
        digest = sha(output); url = '/media/' + digest + '.wav'
        routes[url] = {'path': str(output.resolve()), 'sha256': digest, 'bytes': output.stat().st_size}
        # 保留原声音路由用于来源回读，运行只读取派生循环。
        source_url = '/media/' + original + '.wav'
        routes[source_url] = {'path': str(source.resolve()), 'sha256': original, 'bytes': source.stat().st_size}
        manifest['audio'][key] = {
            'audio_id': identity + ':prototype-loop-v1', 'source_audio_id': identity, 'url': url,
            'name': row['name'], 'sha256': digest, 'source_sha256': original, 'source_url': source_url,
            'duration_seconds': len(data) / rate, 'sample_rate': rate, 'channels': 1,
            'loop': True, 'derivation': '稳定段1至7.5秒，单声道，120毫秒平滑交叉淡化，RMS约0.12',
            'bindings': [b for b in row['bindings'] if b['model_id'] == model],
        }
        inputs = [r for r in inputs if r['id'] != identity]
        inputs.append({'id': identity, 'revision': revision, **routes[source_url]})
        records.append({'key': key, 'model': model, 'source_audio_id': identity, 'source_sha256': original,
                        'derived_sha256': digest, 'sample_rate': rate, 'channels': 1, 'seconds': len(data)/rate,
                        'source_parameters': row['render_profile']['parameters'], 'gain': float(gain),
                        'rms': float(np.sqrt(np.mean(decoded**2))), 'peak': float(np.max(np.abs(decoded))),
                        'minimum_100ms_rms': min(windows), 'boundary_step': boundary,
                        'local_difference_p999': float(np.quantile(differences, .999)), 'source_unchanged': sha(source) == original})
    manifest['notes'] = [s for s in manifest.get('notes', []) if '未确认原生循环' not in s]
    note = '三型机械声为独立派生循环，非原生RPM多层音效；没有独立发电机素材'
    if note not in manifest['notes']: manifest['notes'].append(note)
    for name, value in [('资源清单.json', manifest), ('私有路由.json', routes), ('输入核验.json', inputs), ('循环音效核验.json', records)]:
        (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    check()
    print(json.dumps(records, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--prepare', action='store_true')
    if parser.parse_args().prepare: prepare()
    else: check()
