"""核验本轮 WAV 结构、帧数、幅度与文件摘要；不代替实际听审。"""

import argparse
from array import array
import json
import math
from pathlib import Path
import wave
from 探测音效 import local_batch, save, digest


def inspect(path):
    with wave.open(str(path), "rb") as stream:
        channels, width, rate, frames = stream.getnchannels(), stream.getsampwidth(), stream.getframerate(), stream.getnframes()
        pcm = stream.readframes(frames)
    if width != 2:
        raise ValueError("本轮只接受已验证的PCM16输出")
    values = array("h", pcm)
    if not values or len(values) != frames * channels:
        raise ValueError("PCM为空或帧数不符")
    peak = max(abs(value) for value in values)
    active = [index // channels for index, value in enumerate(values) if value]
    return {"sha256": digest(path), "bytes": path.stat().st_size, "format": "PCM16",
            "channels": channels, "sample_rate": rate, "frames": frames, "duration_seconds": frames / rate,
            "peak": peak / 32768, "rms": math.sqrt(sum(value * value for value in values) / len(values)) / 32768,
            "clip_samples": sum(abs(value) >= 32767 for value in values), "nonfinite_samples": 0,
            "nonzero_samples": len(active), "first_nonzero_frame": min(active) if active else None,
            "last_nonzero_frame": max(active) if active else None,
            "decode_status": "passed" if peak else "silent_requires_review",
            "loop_status": "unknown_finite_render", "listening_status": "pending"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True)
    args = parser.parse_args()
    batch = local_batch(args.batch)
    results = []
    for record in (batch / "尝试").glob("*/渲染记录.json"):
        info = json.loads(record.read_text(encoding="utf-8"))
        if info["render_status"] != "passed":
            continue
        path = batch / info["output"]
        try:
            details = inspect(path)
        except Exception as error:
            details = {"decode_status": "failed", "error": str(error)}
        check = {"path": path.relative_to(batch).as_posix(), "event_path": info["event"]["path"], **details}
        save(record.parent / "技术检查.json", check)
        results.append(check)
    save(batch / "证据/音频技术检查.json", results)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
