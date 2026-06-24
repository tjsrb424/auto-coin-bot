from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

ACTIVE_PRESET_SETTINGS_KEY = "aggression_preset"

PRESETS: dict[str, dict[str, Any]] = {
    "conservative": {
        "label": "Conservative",
        "settings": {
            "AUTO_MAX_ORDER_KRW": 20_000,
            "MAX_LIVE_ORDER_KRW": 20_000,
            "RISK_MAX_ORDER_KRW": 20_000,
            "AUTO_MAX_ORDERS_PER_DAY": 2,
            "AUTO_MAX_NEW_ENTRIES_PER_TICK": 1,
            "AUTO_SINGLE_POSITION_MAX_EXPOSURE_PCT": 25,
            "RISK_MAX_ORDERS_PER_DAY": 3,
            "RISK_MAX_ENTRY_ORDERS_PER_DAY": 1,
            "RISK_MAX_EXIT_ORDERS_PER_DAY": 10,
            "AUTO_COOLDOWN_SECONDS": 2_700,
            "RISK_MIN_COOLDOWN_SECONDS": 2_700,
            "AUTO_SCALE_IN_ENABLED": False,
            "AUTO_SCALE_IN_MAX_COUNT_PER_POSITION": 0,
            "AUTO_SCALE_IN_MIN_POSITION_PNL_PERCENT": 0.5,
            "AUTO_SCALE_IN_MAX_POSITION_EXPOSURE_PCT": 25,
            "AUTO_SCALE_IN_MIN_INTERVAL_SECONDS": 3_600,
            "AUTO_SCALE_IN_REQUIRE_BUY_SIGNAL": True,
            "AUTO_SCALE_IN_BLOCK_TREND_DOWN": True,
            "AUTO_SCALE_IN_NO_AVERAGING_DOWN": True,
            "AUTO_STOP_LOSS_PERCENT": 0.6,
            "AUTO_TAKE_PROFIT_PERCENT": 0.8,
            "AUTO_MAX_HOLD_MINUTES": 45,
            "SMART_DYNAMIC_SIZING_ENABLED": True,
            "SMART_DYNAMIC_SIZING_MODE": "shadow",
            "SMART_DYNAMIC_SIZING_MIN_MULTIPLIER": 0.5,
            "SMART_DYNAMIC_SIZING_MAX_MULTIPLIER": 1.0,
            "SMART_DYNAMIC_SIZING_MIN_CONFIDENCE": 40,
            "SMART_DYNAMIC_SIZING_REQUIRE_POSITIVE_NET_EDGE": True,
            "SMART_AGGRESSIVE_MAX_EXPOSURE_BREAKOUT": 55,
            "SMART_AGGRESSIVE_MAX_EXPOSURE_TREND_UP": 45,
            "SMART_AGGRESSIVE_MAX_EXPOSURE_RANGE": 25,
            "RISK_MAX_POSITION_RATIO_PERCENT": 15,
            "MAX_LIVE_POSITION_RATIO": 0.25,
        },
    },
    "balanced": {
        "label": "Balanced",
        "settings": {
            "AUTO_MAX_ORDER_KRW": 30_000,
            "MAX_LIVE_ORDER_KRW": 30_000,
            "RISK_MAX_ORDER_KRW": 30_000,
            "AUTO_MAX_ORDERS_PER_DAY": 3,
            "AUTO_MAX_NEW_ENTRIES_PER_TICK": 2,
            "AUTO_SINGLE_POSITION_MAX_EXPOSURE_PCT": 45,
            "RISK_MAX_ORDERS_PER_DAY": 6,
            "RISK_MAX_ENTRY_ORDERS_PER_DAY": 2,
            "RISK_MAX_EXIT_ORDERS_PER_DAY": 10,
            "AUTO_COOLDOWN_SECONDS": 1_800,
            "RISK_MIN_COOLDOWN_SECONDS": 1_800,
            "AUTO_SCALE_IN_ENABLED": True,
            "AUTO_SCALE_IN_MAX_COUNT_PER_POSITION": 2,
            "AUTO_SCALE_IN_MIN_POSITION_PNL_PERCENT": 0.0,
            "AUTO_SCALE_IN_MAX_POSITION_EXPOSURE_PCT": 45,
            "AUTO_SCALE_IN_MIN_INTERVAL_SECONDS": 900,
            "AUTO_SCALE_IN_REQUIRE_BUY_SIGNAL": True,
            "AUTO_SCALE_IN_BLOCK_TREND_DOWN": True,
            "AUTO_SCALE_IN_NO_AVERAGING_DOWN": True,
            "AUTO_STOP_LOSS_PERCENT": 0.8,
            "AUTO_TAKE_PROFIT_PERCENT": 1.2,
            "AUTO_MAX_HOLD_MINUTES": 90,
            "SMART_DYNAMIC_SIZING_ENABLED": True,
            "SMART_DYNAMIC_SIZING_MODE": "shadow",
            "SMART_DYNAMIC_SIZING_MIN_MULTIPLIER": 0.5,
            "SMART_DYNAMIC_SIZING_MAX_MULTIPLIER": 1.5,
            "SMART_DYNAMIC_SIZING_MIN_CONFIDENCE": 30,
            "SMART_DYNAMIC_SIZING_REQUIRE_POSITIVE_NET_EDGE": True,
            "SMART_AGGRESSIVE_MAX_EXPOSURE_BREAKOUT": 70,
            "SMART_AGGRESSIVE_MAX_EXPOSURE_TREND_UP": 60,
            "SMART_AGGRESSIVE_MAX_EXPOSURE_RANGE": 45,
            "RISK_MAX_POSITION_RATIO_PERCENT": 20,
            "MAX_LIVE_POSITION_RATIO": 0.5,
        },
    },
    "aggressive": {
        "label": "Aggressive",
        "settings": {
            "AUTO_MAX_ORDER_KRW": 50_000,
            "MAX_LIVE_ORDER_KRW": 50_000,
            "RISK_MAX_ORDER_KRW": 50_000,
            "AUTO_MAX_ORDERS_PER_DAY": 6,
            "AUTO_MAX_NEW_ENTRIES_PER_TICK": 3,
            "AUTO_SINGLE_POSITION_MAX_EXPOSURE_PCT": 60,
            "RISK_MAX_ORDERS_PER_DAY": 10,
            "RISK_MAX_ENTRY_ORDERS_PER_DAY": 4,
            "RISK_MAX_EXIT_ORDERS_PER_DAY": 10,
            "AUTO_COOLDOWN_SECONDS": 900,
            "RISK_MIN_COOLDOWN_SECONDS": 900,
            "AUTO_SCALE_IN_ENABLED": True,
            "AUTO_SCALE_IN_MAX_COUNT_PER_POSITION": 3,
            "AUTO_SCALE_IN_MIN_POSITION_PNL_PERCENT": 0.2,
            "AUTO_SCALE_IN_MAX_POSITION_EXPOSURE_PCT": 60,
            "AUTO_SCALE_IN_MIN_INTERVAL_SECONDS": 600,
            "AUTO_SCALE_IN_REQUIRE_BUY_SIGNAL": True,
            "AUTO_SCALE_IN_BLOCK_TREND_DOWN": True,
            "AUTO_SCALE_IN_NO_AVERAGING_DOWN": True,
            "AUTO_STOP_LOSS_PERCENT": 1.0,
            "AUTO_TAKE_PROFIT_PERCENT": 1.8,
            "AUTO_MAX_HOLD_MINUTES": 120,
            "SMART_DYNAMIC_SIZING_ENABLED": True,
            "SMART_DYNAMIC_SIZING_MODE": "apply",
            "SMART_DYNAMIC_SIZING_MIN_MULTIPLIER": 0.5,
            "SMART_DYNAMIC_SIZING_MAX_MULTIPLIER": 1.8,
            "SMART_DYNAMIC_SIZING_MIN_CONFIDENCE": 35,
            "SMART_DYNAMIC_SIZING_REQUIRE_POSITIVE_NET_EDGE": True,
            "SMART_AGGRESSIVE_MAX_EXPOSURE_BREAKOUT": 85,
            "SMART_AGGRESSIVE_MAX_EXPOSURE_TREND_UP": 75,
            "SMART_AGGRESSIVE_MAX_EXPOSURE_RANGE": 55,
            "RISK_MAX_POSITION_RATIO_PERCENT": 30,
            "MAX_LIVE_POSITION_RATIO": 0.6,
        },
    },
}

FORCED_SAFETY_SETTINGS: dict[str, Any] = {
    "AUTO_SCALE_IN_NO_AVERAGING_DOWN": True,
    "RISK_BLOCK_ON_BALANCE_MISMATCH": True,
    "RISK_BLOCK_ON_OPEN_ORDER": True,
    "RISK_BLOCK_ON_PARTIAL_FILL": True,
    "RISK_REQUIRE_ORDER_CHANCE_SUCCESS": True,
}
PROTECTED_POLICY_KEYS = {"auto_trading_enabled", "max_total_exposure_krw", "daily_loss_limit_pct"}


def list_aggression_presets(market: str = "KRW-BTC") -> dict:
    active = load_active_aggression_preset()
    return {
        "active_preset": active,
        "presets": [
            {
                "name": name,
                "label": preset["label"],
                "preview": build_aggression_preset_preview(name, market=market),
            }
            for name, preset in PRESETS.items()
        ],
        "safety_guards": _safety_guard_summary(),
    }


def build_aggression_preset_preview(preset_name: str, *, market: str = "KRW-BTC") -> dict:
    name = _normalize_preset_name(preset_name)
    preset_settings = _safe_preset_settings(name)
    return {
        "preset": name,
        "label": PRESETS[name]["label"],
        "before": _runtime_summary(market=market),
        "after": _runtime_summary(market=market, override_settings=preset_settings),
        "settings_delta": _settings_delta(preset_settings),
        "protected_policy_keys": sorted(PROTECTED_POLICY_KEYS),
        "safety_guards": _safety_guard_summary(),
        "application": {
            "requires_env_edit": False,
            "runtime_restart_required": False,
            "effective_on": "next_runtime_config_read",
        },
    }


def apply_aggression_preset(preset_name: str, *, market: str = "KRW-BTC", requested_by: str = "admin", reason: str = "") -> dict:
    from app.database import insert_aggression_preset_log, update_app_settings

    name = _normalize_preset_name(preset_name)
    previous = load_active_aggression_preset()
    preview = build_aggression_preset_preview(name, market=market)
    now_utc = _utc_now()
    payload = {
        "name": name,
        "label": PRESETS[name]["label"],
        "settings": _safe_preset_settings(name),
        "applied_at": now_utc,
        "requested_by": requested_by or "admin",
        "reason": reason or "",
    }
    update_app_settings({ACTIVE_PRESET_SETTINGS_KEY: payload})
    log = insert_aggression_preset_log(
        {
            "preset_name": name,
            "previous_preset": previous.get("name"),
            "previous_settings": previous.get("settings") or {},
            "applied_settings": payload["settings"],
            "before_summary": preview["before"],
            "after_summary": preview["after"],
            "safety_guards": preview["safety_guards"],
            "requested_by": requested_by or "admin",
            "reason": reason or "",
        }
    )
    return {**preview, "active_preset": payload, "change_log": log}


def load_active_aggression_preset() -> dict:
    try:
        from app.database import load_app_settings

        payload = load_app_settings().get(ACTIVE_PRESET_SETTINGS_KEY)
    except Exception:
        payload = None
    if not isinstance(payload, dict):
        return {"name": None, "settings": {}, "active": False}
    settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
    return {**payload, "settings": _apply_forced_safety(settings), "active": bool(payload.get("name"))}


def runtime_setting(name: str, default: Any = None) -> Any:
    settings = load_active_aggression_preset().get("settings") or {}
    if name in settings:
        return settings[name]
    return os.getenv(name, default)


def runtime_setting_bool(name: str, default: bool) -> bool:
    value = runtime_setting(name, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def runtime_setting_int(name: str, default: int) -> int:
    try:
        return int(runtime_setting(name, default))
    except (TypeError, ValueError):
        return default


def runtime_setting_float(name: str, default: float) -> float:
    try:
        return float(runtime_setting(name, default))
    except (TypeError, ValueError):
        return default


def runtime_setting_str(name: str, default: str) -> str:
    value = runtime_setting(name, default)
    return str(value if value is not None else default)


def _safe_preset_settings(name: str) -> dict:
    settings = dict(PRESETS[name]["settings"])
    return _apply_forced_safety(settings)


def _apply_forced_safety(settings: dict) -> dict:
    sanitized = {key: value for key, value in settings.items() if key not in PROTECTED_POLICY_KEYS}
    sanitized.update(FORCED_SAFETY_SETTINGS)
    return sanitized


def _normalize_preset_name(name: str) -> str:
    normalized = str(name or "").strip().lower()
    if normalized not in PRESETS:
        raise ValueError(f"Unknown aggression preset: {name}")
    return normalized


def _runtime_summary(*, market: str, override_settings: dict | None = None) -> dict:
    from app.database import load_bot_operation_policy

    policy = load_bot_operation_policy(market)
    settings = {**(load_active_aggression_preset().get("settings") or {}), **(override_settings or {})}

    def value(key: str, default: Any) -> Any:
        if key in settings:
            return settings[key]
        return os.getenv(key, default)

    max_total = _float(policy.get("max_total_exposure_krw"))
    max_exposure_pct = max(
        _float(value("SMART_AGGRESSIVE_MAX_EXPOSURE_BREAKOUT", 85)),
        _float(value("SMART_AGGRESSIVE_MAX_EXPOSURE_TREND_UP", 75)),
        _float(value("SMART_AGGRESSIVE_MAX_EXPOSURE_RANGE", 45)),
    )
    return {
        "policy": {
            "market": market,
            "auto_trading_enabled": bool(policy.get("auto_trading_enabled")),
            "max_total_exposure_krw": max_total,
            "daily_loss_limit_pct": _float(policy.get("daily_loss_limit_pct")),
            "daily_loss_limit_krw": _float(policy.get("daily_loss_limit_krw")),
            "unchanged_by_preset": sorted(PROTECTED_POLICY_KEYS),
        },
        "limits": {
            "auto_max_order_krw": _float(value("AUTO_MAX_ORDER_KRW", 30_000)),
            "max_live_order_krw": _float(value("MAX_LIVE_ORDER_KRW", 30_000)),
            "risk_max_order_krw": _float(value("RISK_MAX_ORDER_KRW", 30_000)),
            "auto_orders_per_day": _int(value("AUTO_MAX_ORDERS_PER_DAY", 3)),
            "risk_orders_per_day": _int(value("RISK_MAX_ORDERS_PER_DAY", 3)),
            "entry_orders_per_day": _int(value("RISK_MAX_ENTRY_ORDERS_PER_DAY", 2)),
            "exit_orders_per_day": _int(value("RISK_MAX_EXIT_ORDERS_PER_DAY", 3)),
            "cooldown_seconds": _int(value("AUTO_COOLDOWN_SECONDS", 1_800)),
            "risk_cooldown_seconds": _int(value("RISK_MIN_COOLDOWN_SECONDS", 1_800)),
        },
        "scale_in": {
            "enabled": _bool(value("AUTO_SCALE_IN_ENABLED", True)),
            "max_count": _int(value("AUTO_SCALE_IN_MAX_COUNT_PER_POSITION", 3)),
            "min_position_pnl_pct": _float(value("AUTO_SCALE_IN_MIN_POSITION_PNL_PERCENT", 0.0)),
            "max_position_exposure_pct": _float(value("AUTO_SCALE_IN_MAX_POSITION_EXPOSURE_PCT", 45.0)),
            "no_averaging_down": _bool(value("AUTO_SCALE_IN_NO_AVERAGING_DOWN", True)),
        },
        "exit": {
            "stop_loss_percent": _float(value("AUTO_STOP_LOSS_PERCENT", 0.8)),
            "take_profit_percent": _float(value("AUTO_TAKE_PROFIT_PERCENT", 1.2)),
            "max_hold_minutes": _int(value("AUTO_MAX_HOLD_MINUTES", 90)),
        },
        "dynamic_sizing": {
            "enabled": _bool(value("SMART_DYNAMIC_SIZING_ENABLED", False)),
            "mode": str(value("SMART_DYNAMIC_SIZING_MODE", "shadow")),
            "max_multiplier": _float(value("SMART_DYNAMIC_SIZING_MAX_MULTIPLIER", 1.5)),
            "min_confidence": _float(value("SMART_DYNAMIC_SIZING_MIN_CONFIDENCE", 30)),
            "require_positive_net_edge": _bool(value("SMART_DYNAMIC_SIZING_REQUIRE_POSITIVE_NET_EDGE", True)),
        },
        "expected_max_exposure": {
            "max_aggressive_exposure_pct": max_exposure_pct,
            "expected_max_exposure_krw": max_total * max_exposure_pct / 100 if max_total > 0 else 0.0,
            "policy_max_total_exposure_krw": max_total,
        },
    }


def _settings_delta(settings: dict) -> list[dict]:
    active = load_active_aggression_preset().get("settings") or {}
    return [
        {"key": key, "before": active.get(key, os.getenv(key)), "after": value}
        for key, value in sorted(settings.items())
        if active.get(key, os.getenv(key)) != value
    ]


def _safety_guard_summary() -> dict:
    return {
        "emergency_stop_preserved": True,
        "daily_loss_limit_preserved": True,
        "max_total_exposure_policy_preserved": True,
        "auto_trading_switch_preserved": True,
        "balance_mismatch_block_preserved": True,
        "open_order_mismatch_block_preserved": True,
        "no_averaging_down_preserved": True,
        "forced_runtime_settings": FORCED_SAFETY_SETTINGS,
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
