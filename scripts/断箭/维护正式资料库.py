"""初始化正式资料库，或校验、显式发布增量入库清单。"""
import argparse
import json
from 资料库核心 import bootstrap, import_manifest

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init", "validate", "publish"))
    parser.add_argument("--manifest")
    parser.add_argument("--expected-revision", type=int)
    args = parser.parse_args()
    if args.action != "init" and (not args.manifest or args.expected_revision is None): parser.error("增量入库需要manifest及expected-revision")
    result = bootstrap() if args.action == "init" else import_manifest(args.manifest, args.action == "publish", args.expected_revision)
    print(json.dumps(result, ensure_ascii=False, indent=2))
