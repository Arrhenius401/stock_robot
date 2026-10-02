"""官方指数接口探针，默认无重试；输出保留在忽略目录。"""
import argparse
import json
from datetime import date
from pathlib import Path
from uuid import uuid4

from index.source_probe import run_probe


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--source", choices=["all", "history", "factsheet", "pb"], default="all")
    parser.add_argument("--provider", choices=["csi", "cni", "sse"], default="csi")
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    parser.add_argument("--timeout", type=float, default=12)
    parser.add_argument("--budget", type=float, default=30)
    parser.add_argument("--retries", type=int, default=0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        root = Path(__file__).resolve().parents[1]
        output = args.output or root / "tmp" / f"index-probe-{uuid4().hex}"
        result = run_probe(args.symbol, args.source, args.date, output,
                           root=root, provider=args.provider,
                           timeout=args.timeout, budget=args.budget, retries=args.retries)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if any(item["status"] != "ok" for item in result["sources"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
