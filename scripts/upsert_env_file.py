from __future__ import annotations

import re
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: upsert_env_file.py <env-file> KEY=VALUE [KEY=VALUE ...]", file=sys.stderr)
        return 2

    path = Path(sys.argv[1])
    updates: dict[str, str] = {}
    for item in sys.argv[2:]:
        if "=" not in item:
            print(f"invalid env assignment: {item}", file=sys.stderr)
            return 2
        key, value = item.split("=", 1)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            print(f"invalid env key: {key}", file=sys.stderr)
            return 2
        updates[key] = value

    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            output.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            if key not in seen:
                output.append(f"{key}={updates[key]}")
                seen.add(key)
            continue
        output.append(line)

    for key, value in updates.items():
        if key not in seen:
            output.append(f"{key}={value}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(output) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
