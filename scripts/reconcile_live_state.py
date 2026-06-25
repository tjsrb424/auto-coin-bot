from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import init_db
from app.live_state_reconciler import reconcile_live_state


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile LIVE_ACTIVE orphan candidates and stale live session position pointers.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Show proposed updates without changing the database.")
    mode.add_argument("--apply", action="store_true", help="Apply reconciliation changes.")
    args = parser.parse_args()

    init_db()
    result = reconcile_live_state(dry_run=not args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
