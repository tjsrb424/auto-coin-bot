from __future__ import annotations

import argparse
import base64
import hashlib
import secrets
import sys
from pathlib import Path


PRODUCTION_DEFAULTS = {
    "APP_ENV": "production",
    "APP_HOST": "0.0.0.0",
    "APP_PORT": "8000",
    "RUNTIME_INSTANCE_ID": "",
    "RUNTIME_LOCK_TTL_SECONDS": "3600",
    "ADMIN_USERNAME": "admin",
    "ADMIN_PASSWORD_HASH": "",
    "SESSION_SECRET": "",
    "SESSION_COOKIE_SECURE": "false",
    "EXCHANGE": "bithumb",
    "BITHUMB_ACCESS_KEY": "",
    "BITHUMB_SECRET_KEY": "",
    "UPBIT_ACCESS_KEY": "",
    "UPBIT_SECRET_KEY": "",
    "LIVE_TRADING_ENABLED": "true",
    "LIVE_AUTO_TRADING_ENABLED": "true",
    "AUTO_STRATEGY_PILOT_ENABLED": "true",
    "AUTO_START_ON_BOOT": "false",
    "REQUIRE_MANUAL_START": "true",
    "REQUIRE_UI_CONFIRMATION": "true",
    "AUTO_ALLOWED_EXCHANGE": "bithumb",
    "AUTO_ALLOWED_MARKET": "KRW-BTC",
    "AUTO_ALLOWED_ORDER_TYPE": "limit",
    "MAX_LIVE_ORDER_KRW": "30000",
    "MAX_DAILY_LIVE_LOSS_PERCENT": "1",
    "AUTO_MAX_ORDER_KRW": "30000",
    "AUTO_MAX_ORDERS_PER_DAY": "0",
    "AUTO_PILOT_MAX_ORDERS_PER_DAY": "0",
    "AUTO_MAX_OPEN_POSITION_COUNT": "1",
    "AUTO_COOLDOWN_SECONDS": "1800",
    "AUTO_REQUIRE_COMPLETED_CANDLE": "true",
    "AUTO_CANCEL_UNFILLED_AFTER_SECONDS": "60",
    "AUTO_ENTRY_PRICE_OFFSET_PERCENT": "0.3",
    "AUTO_STOP_LOSS_PERCENT": "0.7",
    "AUTO_TAKE_PROFIT_PERCENT": "1.0",
    "AUTO_MAX_HOLD_MINUTES": "60",
    "AUTO_EXIT_ENABLED": "true",
    "AUTO_MARKET_ORDER_ENABLED": "false",
    "RISK_MAX_DAILY_LOSS_PERCENT": "1",
    "RISK_MAX_DAILY_LOSS_KRW": "10000",
    "RISK_MAX_ORDERS_PER_DAY": "0",
    "RISK_MAX_ENTRY_ORDERS_PER_DAY": "0",
    "RISK_MAX_EXIT_ORDERS_PER_DAY": "3",
    "RISK_MAX_CONSECUTIVE_LOSSES": "2",
    "RISK_MIN_COOLDOWN_SECONDS": "1800",
    "RISK_BLOCK_ON_BALANCE_MISMATCH": "true",
    "RISK_BLOCK_ON_PARTIAL_FILL": "true",
    "RISK_BLOCK_ON_OPEN_ORDER": "true",
    "RISK_BLOCK_ON_OPEN_POSITION": "true",
    "RISK_MAX_POSITION_RATIO_PERCENT": "20",
    "RISK_MAX_ORDER_KRW": "30000",
    "RISK_REQUIRE_COMPLETED_CANDLE": "true",
    "RISK_REQUIRE_ORDER_CHANCE_SUCCESS": "true",
    "DATABASE_URL": "sqlite:////app/data/app.db",
    "LOG_DIR": "/app/logs",
}


SECRET_KEYS = {
    "BITHUMB_ACCESS_KEY",
    "BITHUMB_SECRET_KEY",
    "UPBIT_ACCESS_KEY",
    "UPBIT_SECRET_KEY",
    "ADMIN_PASSWORD_HASH",
    "SESSION_SECRET",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Create production env without printing secrets.")
    parser.add_argument("--source", type=Path, default=default_source_path())
    parser.add_argument("--output", type=Path, default=default_output_path())
    parser.add_argument("--yes", action="store_true", help="Overwrite output without interactive confirmation.")
    args = parser.parse_args()

    source = args.source
    output = args.output
    if not source.exists():
        print(f"Source env file not found: {source}", file=sys.stderr)
        return 1

    source_values = parse_env(source)
    values = dict(PRODUCTION_DEFAULTS)
    for key in SECRET_KEYS | {"ADMIN_USERNAME"}:
        if source_values.get(key):
            values[key] = source_values[key]

    if not values["BITHUMB_ACCESS_KEY"]:
        print("BITHUMB_ACCESS_KEY is required in source env.", file=sys.stderr)
        return 1
    if not values["BITHUMB_SECRET_KEY"]:
        print("BITHUMB_SECRET_KEY is required in source env.", file=sys.stderr)
        return 1
    if not values["ADMIN_PASSWORD_HASH"]:
        print("ADMIN_PASSWORD_HASH is required. Generate one and add it to the source env before running.", file=sys.stderr)
        print("Example: python scripts/create-production-env.py --source .env --output .env.production", file=sys.stderr)
        return 1
    values["SESSION_SECRET"] = values["SESSION_SECRET"] if _is_hex_secret(values["SESSION_SECRET"]) else secrets.token_hex(48)

    if output.exists() and not args.yes:
        answer = input(f"{output} already exists. Overwrite? Type YES: ")
        if answer != "YES":
            print("Canceled.")
            return 1

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_env(values), encoding="utf-8")
    print(f"Created {output}")
    print("Secrets were copied or generated but were not printed.")
    return 0


def default_source_path() -> Path:
    backend_env = Path("backend") / ".env"
    return backend_env if backend_env.exists() else Path(".env")


def default_output_path() -> Path:
    backend_env = Path("backend") / ".env"
    return Path("backend") / ".env.production" if backend_env.exists() else Path(".env.production")


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def render_env(values: dict[str, str]) -> str:
    return "\n".join(f"{key}={_render_env_value(key, value)}" for key, value in values.items()) + "\n"


def _render_env_value(key: str, value: str) -> str:
    if key == "ADMIN_PASSWORD_HASH" or "$" in value:
        return "'" + value.replace("'", "'\"'\"'") + "'"
    return value


def _is_hex_secret(value: str) -> bool:
    if len(value) < 64 or len(value) % 2 != 0:
        return False
    return all(char in "0123456789abcdefABCDEF" for char in value)


def hash_password(password: str, *, iterations: int = 240_000) -> str:
    salt = secrets.token_urlsafe(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return f"pbkdf2_sha256${iterations}${salt}${encoded}"


if __name__ == "__main__":
    raise SystemExit(main())
