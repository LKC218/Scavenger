"""无需启动网页即可搜索正式模型和音频，支持Agent的JSON输出。"""
import argparse
import json
from pathlib import Path
import sys
from 资料库核心 import current, query

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", default="")
    parser.add_argument("--type", choices=("all", "model", "audio", "scene"), default="all")
    parser.add_argument("--id")
    parser.add_argument("--related-to")
    parser.add_argument("--status")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--candidate", type=Path, help="只读场景候选清单，保留当前正式版本号并单列候选摘要")
    args = parser.parse_args()
    if not 1 <= args.limit <= 200 or args.offset < 0: parser.error("limit应为1至200，offset不小于0")
    _, catalog, routes, _ = current()
    if args.candidate:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '资源库'))
        from 发布场景 import candidate_index
        catalog, routes = candidate_index(json.loads(args.candidate.read_text('utf-8')), catalog, routes)
    result = query(catalog, routes, args.query, args.type, args.id, args.related_to, args.status, args.limit, args.offset)
    if args.candidate:
        result.update(status='candidate', candidate_sha256=catalog['candidate_sha256'])
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"正式资料库 v{result['revision']}：匹配 {result['total']} 项")
        for row in result["results"]:
            print(f"{row['id']}\t{row['name']}\t{row['quality_status']}")
            for key, path in row["local_paths"].items(): print(f"  {key}: {path}")
