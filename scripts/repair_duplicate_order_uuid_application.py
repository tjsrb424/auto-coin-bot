from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import init_db
from app.order_fill_repair import repair_duplicate_order_uuid_application_sync


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair a duplicate BUY order_uuid application on a live position.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Preview the repair without changing the database.")
    mode.add_argument("--apply", action="store_true", help="Apply the repair.")
    parser.add_argument("--position-id", type=int, required=True)
    parser.add_argument("--order-uuid", required=True)
    parser.add_argument("--exchange", default="bithumb")
    args = parser.parse_args()

    init_db()
    result = repair_duplicate_order_uuid_application_sync(
        position_id=args.position_id,
        order_uuid=args.order_uuid,
        exchange=args.exchange,
        dry_run=not args.apply,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
