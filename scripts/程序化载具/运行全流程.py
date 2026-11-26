"""程序化载具优化与流式渲染（V001）离线编译与归档流水线（V0 → V3）。

浏览器全链路验收（含 129 车 4B 武器库 `/armory` 与 `/dev/battlefield`）请统一使用：
`python -X utf8 -B scripts/验证主服务全链路.py`
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

from 公共配置 import BATCH_ID, ROOT, V1_DIR, V2_DIR, V3_DIR

STAGES = ["V0", "V1", "V2", "V3"]


def run_step(title: str, cmd: list[str]) -> None:
    print(f"\n==================== {title} ====================")
    print("CMD:", " ".join(cmd))
    t0 = time.perf_counter()
    subprocess.run(cmd, cwd=str(ROOT), check=True)
    print(f"DONE {title} ({time.perf_counter() - t0:.1f}s)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-stage", choices=STAGES, default="V0")
    parser.add_argument("--to-stage", choices=STAGES, default="V3")
    args = parser.parse_args()

    i0 = STAGES.index(args.from_stage)
    i1 = STAGES.index(args.to_stage)
    selected = STAGES[i0 : i1 + 1]
    py = sys.executable

    if "V0" in selected:
        run_step("V0 基线冻结与原件盘点", [py, "-X", "utf8", "-B", "scripts/程序化载具/冻结基线与盘点.py"])
    if "V1" in selected:
        run_step("V1 七车极限样本编译", ["node", "scripts/程序化载具/编译载具.mjs", "--v1-samples"])
        run_step("V1 静态结构校验", ["node", "scripts/程序化载具/验证静态结构.mjs", "--dir", str(V1_DIR)])
        run_step("V1 六视角自动视觉测试与自修", [py, "-X", "utf8", "-B", "scripts/程序化载具/验证全库视觉.py", "--dir", str(V1_DIR), "--route-base", "/v1", "--auto-repair"])
    if "V2" in selected:
        run_step("V2 全库129车批编译", ["node", "scripts/程序化载具/编译载具.mjs", "--all", "--out", str(V2_DIR)])
        run_step("V2 全库静态结构校验", ["node", "scripts/程序化载具/验证静态结构.mjs", "--dir", str(V2_DIR)])
        run_step("V2 全库129车六视角自动视觉测试与自修", [py, "-X", "utf8", "-B", "scripts/程序化载具/验证全库视觉.py", "--dir", str(V2_DIR), "--route-base", "/v2", "--auto-repair"])
    if "V3" in selected:
        run_step("V3 分层Range归档与二进制索引打包", [py, "-X", "utf8", "-B", "scripts/程序化载具/打包载具归档.py", "--src", str(V2_DIR), "--out", str(V3_DIR)])
        run_step("V3 HTTP 206 Range逐条回读核验", [py, "-X", "utf8", "-B", "scripts/程序化载具/验证归档回读.py", "--dir", str(V3_DIR)])

    print(f"\n[运行全流程] 选定阶段 {selected} 全部执行完成！")
    print("提示：现役 129 车武器库（/armory）与混编场（/dev/battlefield）全链路验证请运行：python -X utf8 -B scripts/验证主服务全链路.py")


if __name__ == "__main__":
    main()
