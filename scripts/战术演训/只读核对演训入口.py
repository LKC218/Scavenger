"""核对已有8784及已知快捷入口；不执行快捷文件，不触发启动流程。"""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import 启动战术演训原型 as launcher

# 快捷文件本身没有只读开关，日常校验以已核实的文件身份确认其仍委托原启动器。
ENTRIES = {
    '打开战术演训原型.cmd': '3e8ac915f7f8db77993773126c13c6fcfd7909c885d208ed34ed6a1958853f89',
    '打开载具靶场.cmd': 'aad42e1e786396c0f571327cd4e2930ada3f705679f13ce936b84709dd29e825',
}


def check_only(entry=None):
    config, _, _ = launcher.configuration()
    if entry is not None and (entry not in ENTRIES or launcher.digest(ROOT / entry) != ENTRIES[entry]):
        raise RuntimeError('快捷入口内容变化，须先核对其委托关系')
    current = launcher.status()
    if not current:
        raise RuntimeError('8784未运行或身份接口不可用；只读核对不启动服务')
    return launcher.verify_service(current, config)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-only', action='store_true', required=True)
    parser.add_argument('--entry', choices=ENTRIES)
    args = parser.parse_args()
    identity = check_only(args.entry)
    print(f"8784只读核对通过，PID={identity['pid']}，版本={identity['revision']}", flush=True)
