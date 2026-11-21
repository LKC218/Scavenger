"""按空中、地面顺序构建并检查场景，保留每件独立记录。"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def run_one(scene_path, batch, blender, args):
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    category = "空中" if scene["category"] == "air" else "地面" if scene["category"] == "ground" else "外挂"
    output = batch / args.output_root / category / scene_path.parent.name
    receipt = output / "模型检查.json"
    if receipt.exists():
        previous = json.loads(receipt.read_text(encoding="utf-8"))
        if previous.get("结构检查通过"):
            return {"name": scene["name"], "category": category, "passed": True, "reused": True, "output": str(output)}
        return {"name": scene["name"], "category": category, "passed": False, "error": "该输出已有未通过记录，请使用重试批次", "output": str(output)}
    source = scene_path.with_name("source.blend")
    started = time.time()
    environment = os.environ.copy()
    environment["OMP_NUM_THREADS"] = "2"
    environment["OPENBLAS_NUM_THREADS"] = "2"
    environment["PYTHONIOENCODING"] = "utf-8"
    log_dir = batch / "日志" / category
    log_dir.mkdir(parents=True, exist_ok=True)
    try:
        if not source.exists():
            command = [str(blender), "--background", "--factory-startup", "--threads", "2", "--python-exit-code", "1", "--python", str(Path(__file__).with_name("构建场景.py")), "--", "--input", str(scene_path), "--output", str(source)]
            with (log_dir / (scene_path.parent.name + "-构建.log")).open("w", encoding="utf-8") as log:
                process = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=environment, timeout=600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if process.returncode:
                raise RuntimeError(f"构建失败，退出码{process.returncode}")
        command = [sys.executable, str(Path(__file__).with_name("检查模型.py")), "--input", str(source), "--output", str(output), "--blender", str(blender), "--collection", "模型", "--resolution", str(args.resolution), "--samples", str(args.samples), "--views", "4"]
        with (log_dir / (scene_path.parent.name + "-检查.log")).open("w", encoding="utf-8") as log:
            process = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=environment, timeout=1800, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        passed = process.returncode == 0 and receipt.exists() and json.loads(receipt.read_text(encoding="utf-8")).get("结构检查通过")
        return {"name": scene["name"], "category": category, "passed": bool(passed), "output": str(output), "seconds": round(time.time()-started, 2), "source_warnings": scene["warnings"]}
    except Exception as error:
        return {"name": scene["name"], "category": category, "passed": False, "error": repr(error), "output": str(output), "seconds": round(time.time()-started, 2)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", default="BA-26273833-001")
    parser.add_argument("--category", choices=["air", "ground", "attachment", "all"], default="all")
    parser.add_argument("--name", action="append")
    parser.add_argument("--workers", type=int, choices=[1, 2], default=2)
    parser.add_argument("--resolution", type=int, default=768)
    parser.add_argument("--samples", type=int, default=32)
    parser.add_argument("--scene-root", default="场景数据")
    parser.add_argument("--output-root", default="导出结果")
    args = parser.parse_args()
    batch = (ROOT / "local-data/断箭/批次" / args.batch).resolve()
    if not batch.is_relative_to((ROOT / "local-data/断箭/批次").resolve()):
        raise ValueError("输出必须位于本地批次目录")
    if not all((batch / p).resolve().is_relative_to(batch) for p in [args.scene_root, args.output_root]):
        raise ValueError("场景与输出必须位于同一批次")
    config = json.loads((ROOT / "local-data/断箭/本地配置.json").read_text(encoding="utf-8"))
    blender = Path(config["tools"]["Blender"])
    results = []
    for category in ("air", "ground", "attachment"):
        if args.category not in ("all", category):
            continue
        scenes = sorted((batch / args.scene_root / category).glob("*/scene.json"))
        if args.name:
            scenes = [p for p in scenes if p.parent.name in args.name]
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            jobs = {pool.submit(run_one, path, batch, blender, args): path for path in scenes}
            for job in as_completed(jobs):
                result = job.result()
                results.append(result)
                print(json.dumps(result, ensure_ascii=False), flush=True)
                name = f"批量验证-{args.output_root}-{args.category}" + ("-选择" if args.name else "") + ".json"
                (batch / name).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    if not results:
        raise ValueError("没有匹配场景")
    return 0 if all(r["passed"] for r in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
