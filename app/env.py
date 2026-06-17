from __future__ import annotations

import os
from pathlib import Path


def load_server_env() -> None:
    root = Path(__file__).resolve().parent.parent
    candidates = [root / ".env.production"] if os.getenv("APP_ENV", "").lower() == "production" else [root / ".env"]
    candidates.append(root / ".env.production")
    env_path = next((path for path in candidates if path.exists()), None)
    if env_path is None:
        return
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
