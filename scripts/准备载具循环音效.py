"""从三型确证机械声派生循环（旧靶场接口），并支持129车36条MoveEvent全量批量无缝循环派生与解码质检。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.request
import wave

import numpy as np
from 检查载具原型资源 import OUT, query, sha, check

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BATCH_OUT = ROOT / 'local-data/音效派生/V001-b9029b57-w2'
DEFAULT_META_DIR = ROOT / 'local-data/程序化载具/V001-b4570e71/V2-全库编译/meta'
DEFAULT_CATALOG_URL = 'http://127.0.0.1:8800/api/catalog'
FFMPEG_BIN = ROOT / 'local-data/tools/网页资源优化/1/node_modules/ffmpeg-static/ffmpeg.exe'
SEVENZ_BIN = ROOT / 'local-data/tools/网页资源优化/1/node_modules/7zip-bin/win/x64/7za.exe'

TARGET_SAMPLE_RATE = 48000
OPUS_FRAME_SAMPLES = 960  # 20ms @ 48kHz，对齐Opus帧长避免尾部填充间隙
CROSSFADE_SECONDS = 0.12
CROSSFADE_SAMPLES = int(round(CROSSFADE_SECONDS * TARGET_SAMPLE_RATE))  # 5760
TARGET_RMS = 0.12
PEAK_HARD_LIMIT = 0.85
PEAK_ENCODE_TARGET = 0.82  # 为Opus有损编码量化过冲预留安全余量

# 既有三条靶场循环ID对应关系，供Wave 2增量入库复用或衔接
LEGACY_LOOP_ID_ALIASES = {
    'audio:ff1ba8b0-db8d-4187-ba22-35dd8762b5d4:fac3530269ac8b52': 'audio:loop:tankMove',
    'audio:78be8b8a-f46a-4ffb-b5ff-bbb34d23ca7c:204aa9fa2d39cfd7': 'audio:loop:heliMove',
    'audio:71d3cf2e-0d7f-4efd-94f4-3c7014f05aab:49073e01da189783': 'audio:loop:planeMove',
}

# 缺失MoveEvent或存在ID/slug差异的5辆载具同系映射与核验规则
SIBLING_MOVE_EVENT_FALLBACKS = {
    'US_AC130J': {
        'target_source_audio_id': 'audio:fa646521-e43b-4d4d-a0c6-0e300748a5dc:49073e01da189783',
        'target_source_name': 'US_AC-130',
        'reference_models': ['model:US_AC130_Base', 'model:US_C130_SuperHercules', 'model:US_KC130_HarvestHawk', 'model:US_MC130H'],
        'reason': 'C-130J 涡桨炮艇机同系，复用正式库 US_AC-130 涡桨引擎原音',
    },
    'US_AC130U': {
        'target_source_audio_id': 'audio:fa646521-e43b-4d4d-a0c6-0e300748a5dc:49073e01da189783',
        'target_source_name': 'US_AC-130',
        'reference_models': ['model:US_AC130_Base', 'model:US_C130_SuperHercules', 'model:US_KC130_HarvestHawk', 'model:US_MC130H'],
        'reason': 'C-130U 涡桨炮艇机同系，复用正式库 US_AC-130 涡桨引擎原音',
    },
    'US_LAV_AT-M_body': {
        'target_source_audio_id': 'audio:c553c3c5-a422-4834-9b91-842a01da8c36:fac3530269ac8b52',
        'target_source_name': 'US_LAV25_Engine',
        'reference_models': ['model:US_LAV25', 'model:US_LAV_AT', 'model:US_LAV_AD', 'model:US_LAV_L', 'model:US_LAV_M', 'model:US_ACV'],
        'reason': 'LAV 八轮反坦克变体车体同底盘，复用正式库 US_LAV25_Engine 轮式装甲发动机原音',
    },
    'US_AH-1W_Super_Cobra': {
        'target_source_audio_id': 'audio:eebd3b95-b500-40f7-8433-e126ab2c6e98:204aa9fa2d39cfd7',
        'target_source_name': 'US_AH-1',
        'reference_models': ['model:US_AH-1W Super_Cobra'],
        'reason': '正式库以含空格ID model:US_AH-1W Super_Cobra 确证绑定 US_AH-1 旋翼原音，兼容slug检索兜底',
    },
    'US_AH-1Z_Viper': {
        'target_source_audio_id': 'audio:eebd3b95-b500-40f7-8433-e126ab2c6e98:204aa9fa2d39cfd7',
        'target_source_name': 'US_AH-1',
        'reference_models': ['model:US_AH-1Z Viper'],
        'reason': '正式库以含空格ID model:US_AH-1Z Viper 确证绑定 US_AH-1 旋翼原音，兼容slug检索兜底',
    },
}

SOURCES = {
    'tankMove': ('model:US_M1A2', 'audio:ff1ba8b0-db8d-4187-ba22-35dd8762b5d4:fac3530269ac8b52'),
    'heliMove': ('model:US_AH64D', 'audio:78be8b8a-f46a-4ffb-b5ff-bbb34d23ca7c:204aa9fa2d39cfd7'),
    'planeMove': ('model:US_A10', 'audio:71d3cf2e-0d7f-4efd-94f4-3c7014f05aab:49073e01da189783'),
}


def prepare():
    sys.path.insert(0, str(ROOT / 'scripts/资源库'))
    from 统一资源 import available
    if available():
        from 兼容维护 import check_runtime
        check_runtime(sync_legacy=True)
        return
    manifest = json.loads((OUT / '资源清单.json').read_text('utf-8'))
    routes = json.loads((OUT / '私有路由.json').read_text('utf-8'))
    inputs = json.loads((OUT / '输入核验.json').read_text('utf-8'))
    folder = OUT / '循环音效'
    folder.mkdir(exist_ok=True)
    records = []
    for key, (model, identity) in SOURCES.items():
        revision, row = query(identity)
        source = Path(row['local_paths']['url'])
        original = sha(source)
        if original != row['sha256'] or row['quality_status'] != 'ready':
            raise ValueError('来源身份或质量不符：' + key)
        if not any(b['model_id'] == model and b['field'] == 'MoveEvent' for b in row['bindings']):
            raise ValueError('没有确证的型号机械声关联：' + key)
        with wave.open(str(source), 'rb') as stream:
            rate = stream.getframerate()
            channels = stream.getnchannels()
            if stream.getsampwidth() != 2:
                raise ValueError('仅接受当前确证PCM16')
            pcm = np.frombuffer(stream.readframes(stream.getnframes()), '<i2').reshape(-1, channels).astype(np.float64) / 32768
        # 三份实测均在1至7.5秒持续发声；避开事件启动及停止尾部。
        segment = pcm[rate:int(rate * 7.5)].mean(axis=1)
        if len(segment) != int(rate * 6.5):
            raise ValueError('稳定段长度不足')
        n = int(rate * .12)
        t = np.linspace(0, 1, n)
        weight = t * t * (3 - 2 * t)
        loop = np.concatenate((segment[n:-n], segment[-n:] * (1 - weight) + segment[:n] * weight))
        gain = min(.12 / np.sqrt(np.mean(loop ** 2)), .85 / np.max(np.abs(loop)))
        data = np.clip(np.rint(loop * gain * 32768), -32768, 32767).astype('<i2')
        output = folder / (key + '-循环.wav')
        with wave.open(str(output), 'wb') as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(rate)
            stream.writeframes(data.tobytes())
        decoded = data.astype(np.float64) / 32768
        windows = [float(np.sqrt(np.mean(a ** 2))) for a in np.array_split(np.tile(decoded, 4), 255)]
        boundary = float(abs(decoded[-1] - decoded[0]))
        differences = np.abs(np.diff(decoded))
        if min(windows) < .01 or boundary > float(np.quantile(differences, .999)):
            raise ValueError('循环静音窗或接缝跳变超出素材局部差分：' + key)
        digest = sha(output)
        url = '/media/' + digest + '.wav'
        routes[url] = {'path': str(output.resolve()), 'sha256': digest, 'bytes': output.stat().st_size}
        # 保留原声音路由用于来源回读，运行只读取派生循环。
        source_url = '/media/' + original + '.wav'
        routes[source_url] = {'path': str(source.resolve()), 'sha256': original, 'bytes': source.stat().st_size}
        manifest['audio'][key] = {
            'audio_id': identity + ':prototype-loop-v1',
            'source_audio_id': identity,
            'url': url,
            'name': row['name'],
            'sha256': digest,
            'source_sha256': original,
            'source_url': source_url,
            'duration_seconds': len(data) / rate,
            'sample_rate': rate,
            'channels': 1,
            'loop': True,
            'derivation': '稳定段1至7.5秒，单声道，120毫秒平滑交叉淡化，RMS约0.12',
            'bindings': [b for b in row['bindings'] if b['model_id'] == model],
        }
        inputs = [r for r in inputs if r['id'] != identity]
        inputs.append({'id': identity, 'revision': revision, **routes[source_url]})
        records.append({
            'key': key,
            'model': model,
            'source_audio_id': identity,
            'source_sha256': original,
            'derived_sha256': digest,
            'sample_rate': rate,
            'channels': 1,
            'seconds': len(data) / rate,
            'source_parameters': row['render_profile']['parameters'],
            'gain': float(gain),
            'rms': float(np.sqrt(np.mean(decoded ** 2))),
            'peak': float(np.max(np.abs(decoded))),
            'minimum_100ms_rms': min(windows),
            'boundary_step': boundary,
            'local_difference_p999': float(np.quantile(differences, .999)),
            'source_unchanged': sha(source) == original,
        })
    manifest['notes'] = [s for s in manifest.get('notes', []) if '未确认原生循环' not in s]
    note = '三型机械声为独立派生循环，非原生RPM多层音效；没有独立发电机素材'
    if note not in manifest['notes']:
        manifest['notes'].append(note)
    for name, value in [('资源清单.json', manifest), ('私有路由.json', routes), ('输入核验.json', inputs), ('循环音效核验.json', records)]:
        (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    check()
    print(json.dumps(records, ensure_ascii=False, indent=2))


def _sanitize_stem(value: str) -> str:
    """将音频名清理为可读、安全的文件名片段。"""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f\s]+', '_', str(value)).strip('._')
    return cleaned or 'audio'


def _load_catalog_and_library(catalog_url: str):
    """只读访问在线 /api/catalog，并与本地统一资源库路由及原件映射关联。"""
    lib_dir = str(ROOT / 'scripts/资源库')
    if lib_dir not in sys.path:
        sys.path.insert(0, lib_dir)
    from 统一资源 import current, read, record_path

    pointer, lib_catalog, lib_routes, lib_base = current()
    orig_map = read(lib_base / '原件映射.json')
    try:
        with urllib.request.urlopen(catalog_url, timeout=8) as resp:
            catalog = json.loads(resp.read().decode('utf-8'))
    except Exception as exc:
        raise RuntimeError(f'无法只读访问在线 catalog 接口 {catalog_url}：{exc}') from exc
    return pointer, catalog, lib_routes, orig_map, record_path


def _resolve_129_vehicle_mappings(catalog: dict, meta_dir: Path):
    """核实全部129辆载具meta与MoveEvent绑定及5车同系映射。"""
    meta_files = sorted(Path(meta_dir).glob('*.json'))
    if not meta_files:
        raise FileNotFoundError(f'未找到载具元数据目录：{meta_dir}')

    models_by_id = {m['id']: m for m in catalog.get('models', [])}
    audios_by_id = {a['id']: a for a in catalog.get('audio', [])}

    vehicle_entries = []
    unique_sources = {}
    audit_five = []

    for meta_path in meta_files:
        mj = json.loads(meta_path.read_text('utf-8'))
        slug = mj.get('slug') or meta_path.stem
        model_id = mj.get('id') or ('model:' + slug)
        model_row = models_by_id.get(model_id)
        if not model_row:
            raise KeyError(f'catalog 中缺失载具模型：{slug} ({model_id})')

        direct_events = []
        for aid in model_row.get('audio_ids', []):
            arow = audios_by_id.get(aid)
            if not arow or arow.get('kind') != 'event_render':
                continue
            if any(b.get('model_id') == model_id and b.get('field') == 'MoveEvent' for b in arow.get('bindings', [])):
                direct_events.append(arow)

        if len(direct_events) > 1:
            raise ValueError(f'载具存在多个 event_render MoveEvent 绑定：{slug} -> {[a["id"] for a in direct_events]}')

        fallback_rule = SIBLING_MOVE_EVENT_FALLBACKS.get(slug)
        if direct_events:
            src_audio = direct_events[0]
            if src_audio.get('quality_status') != 'ready':
                raise ValueError(f'MoveEvent 原音质量状态非 ready：{slug} -> {src_audio["id"]} ({src_audio.get("quality_status")})')
            mismatch = model_id != f'model:{slug}'
            mode = 'direct_catalog_id_with_slug_space_mismatch' if mismatch else 'direct'
            reason = fallback_rule['reason'] if fallback_rule else ''
        elif fallback_rule:
            target_id = fallback_rule['target_source_audio_id']
            src_audio = audios_by_id.get(target_id)
            if not src_audio:
                raise KeyError(f'同系映射目标音频不存在：{slug} -> {target_id}')
            mode = 'sibling_fallback'
            reason = fallback_rule['reason']
        else:
            raise ValueError(f'载具缺失 MoveEvent 且无同系映射配置：{slug} ({model_id})')

        src_id = src_audio['id']
        unique_sources[src_id] = src_audio
        entry = {
            'slug': slug,
            'model_id': model_id,
            'name': mj.get('name') or model_row.get('name') or slug,
            'role': mj.get('role') or 'unknown',
            'category': mj.get('category') or model_row.get('category') or 'unknown',
            'mapping_mode': mode,
            'source_audio_id': src_id,
            'source_name': src_audio.get('name', ''),
            'fallback_reason': reason,
        }
        vehicle_entries.append(entry)
        if slug in SIBLING_MOVE_EVENT_FALLBACKS:
            audit_five.append({
                'slug': slug,
                'model_id': model_id,
                'role': entry['role'],
                'mapping_mode': mode,
                'resolved_source_audio_id': src_id,
                'resolved_source_name': src_audio.get('name', ''),
                'reference_models': fallback_rule['reference_models'],
                'reason': reason,
            })

    return vehicle_entries, unique_sources, audit_five


def _extract_source_wavs(unique_sources: dict, lib_routes: dict, orig_map: dict, record_path, sources_dir: Path) -> dict:
    """从只读归档批量抽取无损PCM16 WAV至本轮工作目录（不写入local-data/资源库/），缺少归档时回退解码库内OGG。"""
    sources_dir.mkdir(parents=True, exist_ok=True)
    to_extract = {}
    resolved = {}

    for src_id, arow in unique_sources.items():
        src_sha = arow.get('source_sha256')
        orig_row = orig_map.get(src_sha) if src_sha else None
        if orig_row:
            target_wav = sources_dir / orig_row['entry']
            if target_wav.is_file() and sha(target_wav) == src_sha:
                resolved[src_id] = {'kind': 'wav', 'path': target_wav, 'source_wav_sha256': src_sha}
            else:
                archive_path = str(record_path(orig_row['archive']))
                to_extract.setdefault(archive_path, []).append((src_id, orig_row['entry'], src_sha, target_wav))
        else:
            route_row = lib_routes.get(arow.get('url', ''))
            if not route_row:
                raise FileNotFoundError(f'无法定位原音或库内路径：{src_id}')
            resolved[src_id] = {'kind': 'ogg', 'path': record_path(route_row), 'source_wav_sha256': src_sha or ''}

    for archive_path, items in to_extract.items():
        if not SEVENZ_BIN.is_file():
            raise FileNotFoundError(f'缺少 7za 工具：{SEVENZ_BIN}')
        entries = [item[1] for item in items]
        proc = subprocess.run(
            [str(SEVENZ_BIN), 'x', '-aoa', f'-o{sources_dir}', archive_path, *entries],
            capture_output=True, text=True, encoding='utf-8', errors='replace', check=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f'7za 抽取失败：{proc.stderr}')
        for src_id, _entry, expected_sha, target_wav in items:
            if not target_wav.is_file():
                raise FileNotFoundError(f'归档抽取后缺少文件：{target_wav}')
            actual_sha = sha(target_wav)
            if actual_sha != expected_sha:
                raise ValueError(f'原音WAV SHA-256 校验失败：{src_id} ({actual_sha} != {expected_sha})')
            resolved[src_id] = {'kind': 'wav', 'path': target_wav, 'source_wav_sha256': actual_sha}

    return resolved


def _decode_audio_mono_48k(path: Path, kind: str) -> np.ndarray:
    """将原音WAV或OGG统一解码为48kHz单声道双精度浮点数组。"""
    if kind == 'wav':
        with wave.open(str(path), 'rb') as stream:
            sr = stream.getframerate()
            ch = stream.getnchannels()
            sw = stream.getsampwidth()
            nf = stream.getnframes()
            if sw == 2 and sr == TARGET_SAMPLE_RATE:
                pcm = np.frombuffer(stream.readframes(nf), '<i2').reshape(-1, ch).astype(np.float64) / 32768.0
                return pcm.mean(axis=1)
    if not FFMPEG_BIN.is_file():
        raise FileNotFoundError(f'缺少 ffmpeg 工具：{FFMPEG_BIN}')
    proc = subprocess.run(
        [str(FFMPEG_BIN), '-v', 'error', '-i', str(path), '-f', 'f32le', '-ar', str(TARGET_SAMPLE_RATE), '-ac', '1', '-'],
        capture_output=True, check=True,
    )
    return np.frombuffer(proc.stdout, dtype='<f4').astype(np.float64)


def _evaluate_candidate(seg: np.ndarray, sub_samples: int, fin: np.ndarray, fout: np.ndarray) -> tuple[float, float, float, float]:
    """评估候选稳态段在120ms等功率交叉淡化后的子窗平稳度与接缝能量一致性。"""
    n = len(fin)
    cross = seg[-n:] * fout + seg[:n] * fin
    body = seg[n:-n]
    usable = (len(seg) // sub_samples) * sub_samples
    chunks = seg[:usable].reshape(-1, sub_samples)
    crms = np.sqrt(np.mean(chunks ** 2, axis=1))
    body_rms = max(float(np.sqrt(np.mean(body ** 2))), 1e-9)
    cv = float(np.std(crms) / max(float(np.mean(crms)), 1e-9))

    w100 = int(round(0.10 * TARGET_SAMPLE_RATE))
    head_100 = max(float(np.sqrt(np.mean(body[:w100] ** 2))), 1e-9)
    tail_100 = max(float(np.sqrt(np.mean(cross[-w100:] ** 2))), 1e-9)
    cross_rms = max(float(np.sqrt(np.mean(cross ** 2))), 1e-9)
    seam_db = abs(20.0 * math.log10(head_100 / tail_100))
    cross_db = abs(20.0 * math.log10(cross_rms / body_rms))
    score = cv + 0.45 * (seam_db / 2.0) + 0.45 * (cross_db / 2.0)
    return score, cv, seam_db, cross_db


def select_steady_segment(mono: np.ndarray, render_profile: dict | None = None) -> tuple[np.ndarray, dict]:
    """自动跳过前导静音与尾段ALLOWFADEOUT，自适应选取4.0s~6.5s最稳态且接缝能量一致的核心段。"""
    sr = TARGET_SAMPLE_RATE
    total_samples = len(mono)
    total_sec = total_samples / sr
    step_sec = 0.05
    step_n = int(round(step_sec * sr))
    n_frames = total_samples // step_n
    if n_frames <= 0:
        raise ValueError('原音频时长过短，无法提取稳态段')

    env = np.array([float(np.sqrt(np.mean(mono[i * step_n:(i + 1) * step_n] ** 2))) for i in range(n_frames)])
    peak_env = float(np.max(env))
    active = env[env > peak_env * 0.05]
    ref_rms = float(np.median(active)) if len(active) else 0.0
    if ref_rms <= 1e-6:
        raise ValueError('原音频为静默或有效能量过低')

    active_idx = np.where(env >= 0.45 * ref_rms)[0]
    first_active_sec = int(active_idx[0]) * step_sec
    last_active_sec = (int(active_idx[-1]) + 1) * step_sec
    tail_sec = float((render_profile or {}).get('tail_seconds', 2.0) or 2.0)

    # 严格避开前导起振与尾段ALLOWFADEOUT淡出
    valid_start_sec = max(0.40, first_active_sec + 0.35)
    valid_end_sec = min(last_active_sec - 0.50, total_sec - tail_sec - 0.30)
    if valid_end_sec - valid_start_sec < 4.12:
        valid_start_sec = max(0.25, first_active_sec + 0.20)
        valid_end_sec = min(last_active_sec - 0.25, total_sec - max(0.40, tail_sec - 0.40))

    avail_sec = valid_end_sec - valid_start_sec
    if avail_sec <= CROSSFADE_SECONDS + 0.5:
        raise ValueError(f'有效非静音稳态段过短：{avail_sec:.3f}s')

    frame_sec = OPUS_FRAME_SAMPLES / sr  # 0.02s
    max_loop_frames = int(math.floor((avail_sec - CROSSFADE_SECONDS) / frame_sec))
    target_loop_frames = min(int(round(6.38 / frame_sec)), max_loop_frames)
    if target_loop_frames <= 0:
        raise ValueError('有效循环帧数不足')

    t = np.linspace(0.0, 1.0, CROSSFADE_SAMPLES, endpoint=False)
    fin = np.sin(0.5 * np.pi * t)
    fout = np.cos(0.5 * np.pi * t)
    s_min = int(round(valid_start_sec * sr))
    s_end_limit = int(round(valid_end_sec * sr))
    sub_n = int(round(0.25 * sr))

    def _search_best_for_loop(loop_frames: int, stride: int = OPUS_FRAME_SAMPLES):
        loop_n = loop_frames * OPUS_FRAME_SAMPLES
        seg_n = loop_n + CROSSFADE_SAMPLES
        s_max = max(s_min, s_end_limit - seg_n)
        best = None
        for s in range(s_min, s_max + 1, stride):
            seg = mono[s:s + seg_n]
            if len(seg) != seg_n:
                continue
            score, cv, seam_db, cross_db = _evaluate_candidate(seg, sub_n, fin, fout)
            if best is None or score < best[0]:
                best = (score, cv, seam_db, cross_db, s, seg_n, loop_n)
        return best

    chosen = _search_best_for_loop(target_loop_frames)
    # 若最长窗口内部有相位抵消导致接缝略波动，允许微调最多14个Opus帧（<=0.28s）以对齐相位
    if chosen[2] > 0.55 or chosen[3] > 0.55:
        for df in range(1, 15):
            cand_frames = target_loop_frames - df
            if cand_frames * frame_sec < 4.00 and cand_frames < int(round(2.0 / frame_sec)):
                break
            cand = _search_best_for_loop(cand_frames)
            if cand and cand[0] + 0.02 < chosen[0]:
                chosen = cand
                if chosen[2] <= 0.25 and chosen[3] <= 0.25 and chosen[1] <= 0.12:
                    break

    # 若长窗口整体能量仍有阶段爬升/波动，自适应尝试4.80s~4.00s稳态核心段
    if chosen[1] > 0.16 and target_loop_frames * frame_sec > 4.60:
        for alt_sec in (4.80, 4.20):
            alt_frames = int(round(alt_sec / frame_sec))
            if alt_frames < target_loop_frames:
                cand = _search_best_for_loop(alt_frames)
                if cand and cand[0] + 0.06 < chosen[0]:
                    chosen = cand

    score, cv, seam_db, cross_db, start_idx, seg_n, loop_n = chosen
    seg = mono[start_idx:start_idx + seg_n]
    info = {
        'source_duration_sec': round(total_sec, 4),
        'first_active_sec': round(first_active_sec, 3),
        'last_active_sec': round(last_active_sec, 3),
        'valid_range_sec': [round(valid_start_sec, 3), round(valid_end_sec, 3)],
        'start_sec': round(start_idx / sr, 3),
        'end_sec': round((start_idx + seg_n) / sr, 3),
        'segment_sec': round(seg_n / sr, 3),
        'loop_sec': round(loop_n / sr, 3),
        'stability_cv': round(cv, 4),
        'predicted_seam_db': round(seam_db, 4),
        'predicted_cross_db': round(cross_db, 4),
        'window_score': round(score, 4),
    }
    return seg, info


def build_equal_power_loop(seg: np.ndarray, extra_scale: float = 1.0) -> tuple[np.ndarray, float]:
    """执行120ms等功率交叉淡化、去直流偏置、RMS归一与峰值安全限幅。"""
    n = CROSSFADE_SAMPLES
    t = np.linspace(0.0, 1.0, n, endpoint=False)
    fade_in = np.sin(0.5 * np.pi * t)
    fade_out = np.cos(0.5 * np.pi * t)
    cross = seg[-n:] * fade_out + seg[:n] * fade_in
    loop = np.concatenate((seg[n:-n], cross))
    loop = loop - float(np.mean(loop))

    rms = float(np.sqrt(np.mean(loop ** 2)))
    peak = float(np.max(np.abs(loop)))
    if rms <= 1e-8 or peak <= 1e-8:
        raise ValueError('交叉淡化后循环波形能量过低')

    gain = min(TARGET_RMS / rms, PEAK_ENCODE_TARGET / peak) * extra_scale
    return (loop * gain).astype(np.float32), float(gain)


def encode_ogg_opus(samples_f32: np.ndarray, out_ogg: Path) -> None:
    """将48kHz单声道浮点PCM通过管道原子编码为紧凑OGG Opus文件。"""
    if not FFMPEG_BIN.is_file():
        raise FileNotFoundError(f'缺少 ffmpeg 工具：{FFMPEG_BIN}')
    out_ogg.parent.mkdir(parents=True, exist_ok=True)
    tmp_ogg = out_ogg.with_suffix('.tmp.ogg')
    cmd = [
        str(FFMPEG_BIN), '-hide_banner', '-loglevel', 'error', '-y',
        '-f', 'f32le', '-ar', str(TARGET_SAMPLE_RATE), '-ac', '1', '-i', 'pipe:0',
        '-map_metadata', '-1', '-c:a', 'libopus', '-b:a', '96k', '-vbr', 'on', str(tmp_ogg),
    ]
    subprocess.run(cmd, input=samples_f32.tobytes(), capture_output=True, check=True)
    tmp_ogg.replace(out_ogg)


def inspect_decoded_ogg(ogg_path: Path, expected_duration_sec: float | None = None) -> dict:
    """全量解码OGG并检查可解码性、NaN/Inf、削波、最小静音窗与接缝跳变。"""
    if not ogg_path.is_file() or ogg_path.stat().st_size <= 0:
        raise FileNotFoundError(f'OGG 文件不存在或为空：{ogg_path}')
    if not FFMPEG_BIN.is_file():
        raise FileNotFoundError(f'缺少 ffmpeg 工具：{FFMPEG_BIN}')

    probe = subprocess.run(
        [str(FFMPEG_BIN), '-hide_banner', '-i', str(ogg_path)],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
    )
    stream_lines = [ln.strip() for ln in probe.stderr.splitlines() if 'Audio:' in ln]
    stream_info = stream_lines[0] if stream_lines else ''
    is_mono_48k_opus = ('opus' in stream_info.lower()) and ('48000 hz' in stream_info.lower()) and ('mono' in stream_info.lower())

    dec_proc = subprocess.run(
        [str(FFMPEG_BIN), '-v', 'error', '-i', str(ogg_path), '-f', 'f32le', '-ar', str(TARGET_SAMPLE_RATE), '-ac', '1', '-'],
        capture_output=True, check=True,
    )
    decoded = np.frombuffer(dec_proc.stdout, dtype='<f4').astype(np.float64)
    if len(decoded) < TARGET_SAMPLE_RATE:
        raise ValueError(f'解码样本数过短：{ogg_path.name} ({len(decoded)})')

    finite = bool(np.isfinite(decoded).all())
    duration_sec = len(decoded) / TARGET_SAMPLE_RATE
    duration_ok = True if expected_duration_sec is None else abs(duration_sec - expected_duration_sec) <= 0.005

    rms = float(np.sqrt(np.mean(decoded ** 2)))
    peak = float(np.max(np.abs(decoded)))
    clip_samples = int(np.sum(np.abs(decoded) >= 0.9999))
    over_limit_samples = int(np.sum(np.abs(decoded) >= PEAK_HARD_LIMIT))

    tiled = np.tile(decoded, 2)
    win_n = int(round(0.10 * TARGET_SAMPLE_RATE))  # 100ms 滑动/切分窗，含跨越接缝处
    n_win = len(tiled) // win_n
    win_rms = [float(np.sqrt(np.mean(tiled[i * win_n:(i + 1) * win_n] ** 2))) for i in range(n_win)]
    min_100ms_rms = min(win_rms) if win_rms else 0.0

    differences = np.abs(np.diff(decoded))
    local_diff_p999 = float(np.quantile(differences, 0.999))
    local_diff_max = float(np.max(differences))
    boundary_step = float(abs(decoded[-1] - decoded[0]))

    w10 = int(round(0.01 * TARGET_SAMPLE_RATE))
    head_10ms_rms = float(np.sqrt(np.mean(decoded[:w10] ** 2)))
    tail_10ms_rms = float(np.sqrt(np.mean(decoded[-w10:] ** 2)))
    head_100ms_rms = float(np.sqrt(np.mean(decoded[:win_n] ** 2)))
    tail_100ms_rms = float(np.sqrt(np.mean(decoded[-win_n:] ** 2)))
    seam_rms_delta_db = float(abs(20.0 * math.log10(max(head_100ms_rms, 1e-9) / max(tail_100ms_rms, 1e-9))))
    seam_rms_diff = float(abs(head_100ms_rms - tail_100ms_rms))

    boundary_ok = boundary_step <= local_diff_p999 * 1.5
    seam_rms_ok = seam_rms_delta_db <= 1.5 and min(head_10ms_rms, tail_10ms_rms) >= 0.015
    passed = bool(
        finite
        and is_mono_48k_opus
        and duration_ok
        and (0.075 <= rms <= 0.145)
        and (peak < PEAK_HARD_LIMIT)
        and (clip_samples == 0)
        and (min_100ms_rms >= 0.01)
        and boundary_ok
        and seam_rms_ok
    )

    return {
        'passed': passed,
        'stream_info': stream_info,
        'is_mono_48k_opus': is_mono_48k_opus,
        'finite': finite,
        'samples': int(len(decoded)),
        'duration_seconds': round(duration_sec, 6),
        'duration_ok': duration_ok,
        'rms': round(rms, 6),
        'peak': round(peak, 6),
        'clip_samples': clip_samples,
        'over_085_samples': over_limit_samples,
        'min_100ms_rms': round(min_100ms_rms, 6),
        'seam_boundary_step': round(boundary_step, 6),
        'local_diff_p999': round(local_diff_p999, 6),
        'local_diff_max': round(local_diff_max, 6),
        'seam_step_to_p999_ratio': round(boundary_step / max(local_diff_p999, 1e-9), 4),
        'boundary_ok': boundary_ok,
        'head_10ms_rms': round(head_10ms_rms, 6),
        'tail_10ms_rms': round(tail_10ms_rms, 6),
        'head_100ms_rms': round(head_100ms_rms, 6),
        'tail_100ms_rms': round(tail_100ms_rms, 6),
        'seam_100ms_rms_delta_db': round(seam_rms_delta_db, 4),
        'seam_100ms_rms_diff': round(seam_rms_diff, 6),
        'seam_rms_ok': seam_rms_ok,
    }


def prepare_batch_all(out_dir: Path, meta_dir: Path, catalog_url: str, *, overwrite: bool = False) -> dict:
    """批量派生全部129辆载具对应的36条唯一MoveEvent无缝循环OGG，并输出完整质检与映射报告。"""
    out_dir = Path(out_dir).resolve()
    loops_dir = out_dir / 'loops'
    sources_dir = out_dir / 'sources'
    report_path = out_dir / '129车循环映射与质检报告.json'
    if not overwrite and (report_path.exists() or (loops_dir.is_dir() and any(loops_dir.iterdir()))):
        raise FileExistsError(f'目标批次目录已存在产物，拒绝覆写（请指定新 --out-dir 或显式传入 --overwrite）：{out_dir}')
    loops_dir.mkdir(parents=True, exist_ok=True)

    pointer, catalog, lib_routes, orig_map, record_path = _load_catalog_and_library(catalog_url)
    vehicle_entries, unique_sources, audit_five = _resolve_129_vehicle_mappings(catalog, meta_dir)
    source_files = _extract_source_wavs(unique_sources, lib_routes, orig_map, record_path, sources_dir)

    used_by_map = {}
    for ve in vehicle_entries:
        used_by_map.setdefault(ve['source_audio_id'], []).append(ve['slug'])

    loop_records = []
    loop_by_source_id = {}

    for src_id, arow in sorted(unique_sources.items(), key=lambda kv: (kv[1].get('category', ''), kv[1].get('name', ''))):
        src_info = source_files[src_id]
        mono = _decode_audio_mono_48k(src_info['path'], src_info['kind'])
        seg, steady_info = select_steady_segment(mono, arow.get('render_profile'))

        short_hash = src_id.split(':')[1].split('-')[0]
        stem = _sanitize_stem(arow.get('name', 'engine'))
        cat_stem = _sanitize_stem(arow.get('category', 'audio'))
        out_ogg = loops_dir / f'{cat_stem}-{stem}-{short_hash}-loop.ogg'

        scale = 1.0
        gain = 1.0
        qa = None
        for _attempt in range(3):
            pcm_f32, gain = build_equal_power_loop(seg, extra_scale=scale)
            encode_ogg_opus(pcm_f32, out_ogg)
            qa = inspect_decoded_ogg(out_ogg, expected_duration_sec=steady_info['loop_sec'])
            if qa['peak'] < PEAK_HARD_LIMIT and qa['clip_samples'] == 0:
                break
            # 若Opus编码量化瞬间越过峰值阈值，按实际解码峰值微降增益重新编码
            scale *= (PEAK_ENCODE_TARGET / max(qa['peak'], 1e-6))

        if not qa or not qa['passed']:
            raise RuntimeError(f'自动化解码质检未通过：{src_id} ({arow.get("name")}) -> {json.dumps(qa, ensure_ascii=False)}')

        digest = sha(out_ogg)
        size_bytes = out_ogg.stat().st_size
        rel_loop = out_ogg.relative_to(out_dir).as_posix()

        rec = {
            'source_audio_id': src_id,
            'source_name': arow.get('name', ''),
            'source_category': arow.get('category', ''),
            'source_catalog_url': arow.get('url', ''),
            'source_catalog_sha256': arow.get('sha256', ''),
            'source_wav_sha256': src_info['source_wav_sha256'],
            'source_kind': src_info['kind'],
            'legacy_loop_alias_id': LEGACY_LOOP_ID_ALIASES.get(src_id),
            'suggested_loop_audio_id': LEGACY_LOOP_ID_ALIASES.get(src_id) or f'audio:loop:{stem}_{short_hash}',
            'loop_file': rel_loop,
            'loop_abs_path': str(out_ogg),
            'sha256': digest,
            'bytes': size_bytes,
            'codec': 'libopus',
            'sample_rate': TARGET_SAMPLE_RATE,
            'channels': 1,
            'duration': qa['duration_seconds'],
            'gain_applied': round(gain, 6),
            'steady_window': steady_info,
            'rms': qa['rms'],
            'peak': qa['peak'],
            'clip_samples': qa['clip_samples'],
            'qa': qa,
            'used_by_count': len(used_by_map.get(src_id, [])),
            'used_by_slugs': sorted(used_by_map.get(src_id, [])),
        }
        loop_records.append(rec)
        loop_by_source_id[src_id] = rec

    vehicle_loop_map = {}
    for ve in sorted(vehicle_entries, key=lambda x: x['slug']):
        lrec = loop_by_source_id[ve['source_audio_id']]
        vehicle_loop_map[ve['slug']] = {
            **ve,
            'suggested_loop_audio_id': lrec['suggested_loop_audio_id'],
            'loop_file': lrec['loop_file'],
            'loop_sha256': lrec['sha256'],
            'loop_bytes': lrec['bytes'],
            'loop_duration_seconds': lrec['duration'],
        }

    report = {
        'schema_version': 1,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'catalog_revision': catalog.get('revision'),
        'asset_library_revision': pointer.get('asset_library_revision') or catalog.get('asset_library_revision'),
        'library_pointer_revision': pointer.get('revision'),
        'params': {
            'sample_rate': TARGET_SAMPLE_RATE,
            'channels': 1,
            'crossfade_ms': int(round(CROSSFADE_SECONDS * 1000)),
            'crossfade_type': 'equal_power_sin_cos',
            'target_rms': TARGET_RMS,
            'peak_hard_limit': PEAK_HARD_LIMIT,
            'codec': 'libopus',
            'bitrate': '96k',
        },
        'summary': {
            'unique_source_count': len(unique_sources),
            'generated_loops_count': len(loop_records),
            'verified_passed_count': sum(1 for r in loop_records if r['qa']['passed']),
            'total_loop_bytes': sum(r['bytes'] for r in loop_records),
            'total_vehicles': len(vehicle_loop_map),
            'mapped_vehicles': len(vehicle_loop_map),
            'direct_mapped': sum(1 for v in vehicle_loop_map.values() if v['mapping_mode'] == 'direct'),
            'slug_id_mismatch_direct': sum(1 for v in vehicle_loop_map.values() if v['mapping_mode'] == 'direct_catalog_id_with_slug_space_mismatch'),
            'sibling_fallback_mapped': sum(1 for v in vehicle_loop_map.values() if v['mapping_mode'] == 'sibling_fallback'),
            'max_peak': round(max(r['peak'] for r in loop_records), 6),
            'min_rms': round(min(r['rms'] for r in loop_records), 6),
            'max_rms': round(max(r['rms'] for r in loop_records), 6),
            'total_clip_samples': sum(r['clip_samples'] for r in loop_records),
            'all_checks_passed': all(r['qa']['passed'] for r in loop_records) and len(vehicle_loop_map) == 129,
        },
        'missing_move_event_audit': audit_five,
        'loops': loop_records,
        'vehicle_loop_map': vehicle_loop_map,
    }

    report_path = out_dir / '129车循环映射与质检报告.json'
    tmp_report = report_path.with_name(f'{report_path.name}.{os.getpid()}.tmp')
    tmp_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp_report, report_path)
    return verify_batch_all(out_dir=out_dir, meta_dir=meta_dir)


def verify_batch_all(out_dir: Path, meta_dir: Path) -> dict:
    """独立全量自动化解码复核：核对36条OGG哈希、解码波形健康、接缝平滑及129车100%映射命中。"""
    out_dir = Path(out_dir).resolve()
    report_path = out_dir / '129车循环映射与质检报告.json'
    if not report_path.is_file():
        raise FileNotFoundError(f'缺少报告文件：{report_path}')
    report = json.loads(report_path.read_text('utf-8'))
    loops = report.get('loops', [])
    vmap = report.get('vehicle_loop_map', {})

    if len(loops) != 36:
        raise ValueError(f'循环音频总数不等于 36：{len(loops)}')

    loop_by_file = {}
    recheck_results = []
    for rec in loops:
        ogg_path = out_dir / rec['loop_file']
        actual_sha = sha(ogg_path)
        if actual_sha != rec['sha256'] or ogg_path.stat().st_size != rec['bytes']:
            raise ValueError(f'OGG 文件指纹与报告不一致：{ogg_path}')
        qa = inspect_decoded_ogg(ogg_path, expected_duration_sec=rec['steady_window']['loop_sec'])
        if not qa['passed']:
            raise ValueError(f'解码复核失败：{rec["loop_file"]} -> {qa}')
        loop_by_file[rec['loop_file']] = rec
        recheck_results.append({
            'source_name': rec['source_name'],
            'loop_file': rec['loop_file'],
            'bytes': rec['bytes'],
            'duration': qa['duration_seconds'],
            'rms': qa['rms'],
            'peak': qa['peak'],
            'clip_samples': qa['clip_samples'],
            'seam_100ms_rms_delta_db': qa['seam_100ms_rms_delta_db'],
            'seam_boundary_step': qa['seam_boundary_step'],
            'local_diff_p999': qa['local_diff_p999'],
            'passed': qa['passed'],
        })

    meta_slugs = {
        (json.loads(p.read_text('utf-8')).get('slug') or p.stem)
        for p in sorted(Path(meta_dir).glob('*.json'))
    }
    if len(meta_slugs) != 129:
        raise ValueError(f'meta 载具数不等于 129：{len(meta_slugs)}')
    missing_slugs = sorted(meta_slugs - set(vmap.keys()))
    if missing_slugs:
        raise ValueError(f'映射表缺失载具：{missing_slugs}')
    for slug in meta_slugs:
        row = vmap[slug]
        lrec = loop_by_file.get(row['loop_file'])
        if not lrec or row['loop_sha256'] != lrec['sha256']:
            raise ValueError(f'载具 {slug} 指向无效循环文件：{row}')

    summary = {
        'verified_loops': len(recheck_results),
        'verified_passed': sum(1 for r in recheck_results if r['passed']),
        'verified_vehicles': len(meta_slugs),
        'total_loop_bytes': sum(r['bytes'] for r in recheck_results),
        'max_peak': max(r['peak'] for r in recheck_results),
        'min_rms': min(r['rms'] for r in recheck_results),
        'max_rms': max(r['rms'] for r in recheck_results),
        'total_clip_samples': sum(r['clip_samples'] for r in recheck_results),
        'max_seam_rms_delta_db': max(r['seam_100ms_rms_delta_db'] for r in recheck_results),
        'report_path': str(report_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', action='store_true', help='兼容旧靶场3车准备流程')
    parser.add_argument('--batch-all', action='store_true', help='批量派生全部129车对应的36条MoveEvent无缝循环OGG')
    parser.add_argument('--verify-only', action='store_true', help='仅对既有批量输出运行全量自动化解码复核')
    parser.add_argument('--out-dir', type=Path, default=DEFAULT_BATCH_OUT, help='批处理输出根目录')
    parser.add_argument('--meta-dir', type=Path, default=DEFAULT_META_DIR, help='129车meta元数据目录')
    parser.add_argument('--catalog-url', default=DEFAULT_CATALOG_URL, help='在线catalog只读接口地址')
    parser.add_argument('--overwrite', action='store_true', help='允许覆盖已存在的目标批次输出（默认拒绝覆写既有批次）')
    args = parser.parse_args()
    if args.verify_only:
        verify_batch_all(args.out_dir, args.meta_dir)
    elif args.batch_all:
        prepare_batch_all(args.out_dir, args.meta_dir, args.catalog_url, overwrite=args.overwrite)
    elif args.prepare:
        prepare()
    else:
        check()
