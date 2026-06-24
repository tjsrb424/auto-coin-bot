from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.scale_in_repair import repair_scale_in_duplicate_sync


def main() -> int:
    parser = argparse.ArgumentParser(description="Preview or repair duplicate scale-in live positions.")
    parser.add_argument("--exchange", default="bithumb")
    parser.add_argument("--market", default="KRW-XLM")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Show proposed repair without changing the DB.")
    mode.add_argument("--apply", action="store_true", help="Apply the repair.")
    args = parser.parse_args()
    result = repair_scale_in_duplicate_sync(exchange=args.exchange, market=args.market, dry_run=not args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
