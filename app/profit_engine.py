from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


BLOCKED_ENTRY_REGIMES = {"PANIC", "TREND_DOWN", "OVERHEATED", "UNKNOWN"}
ALLOWED_STRATEGIES_BY_REGIME = {
    "RANGE": {"range_reversion"},
    "TREND_UP": {"trend_pullback", "volume_breakout"},
    "BREAKOUT": {"volume_breakout", "trend_pullback"},
}
DEFAULT_STRATEGY_BY_REGIME = {
    "RANGE": "range_reversion",
    "TREND_UP": "trend_pullback",
    "BREAKOUT": "volume_breakout",
}


@dataclass(frozen=True)
class ProfitEngineConfig:
    enabled: bool
    mode: str
    order_sizing_mode: str
    require_auto_exit: bool
    block_entry_when_exit_disabled: bool
    allow_balance_cap: bool
    disable_percent_sizing: bool
    extra_fee_buffer_rate: float

    @classmethod
    def from_env(cls) -> "ProfitEngineConfig":
        return cls(
            enabled=_env_bool("PROFIT_ENGINE_ENABLED", False),
            mode=os.getenv("PROFIT_ENGINE_MODE", "aggressive").strip().lower() or "aggressive",
            order_sizing_mode=os.getenv("ORDER_SIZING_MODE", "available_balance_cap").strip().lower(),
            require_auto_exit=_env_bool("PROFIT_ENGINE_REQUIRE_AUTO_EXIT", True),
            block_entry_when_exit_disabled=_env_bool("PROFIT_ENGINE_BLOCK_ENTRY_WHEN_EXIT_DISABLED", True),
            allow_balance_cap=_env_bool("PROFIT_ENGINE_ALLOW_BALANCE_CAP", True),
            disable_percent_sizing=_env_bool("PROFIT_ENGINE_DISABLE_PERCENT_SIZING", True),
            extra_fee_buffer_rate=_float(os.getenv("PROFIT_ENGINE_EXTRA_FEE_BUFFER_RATE"), 0.0002),
        )


def profit_engine_enabled() -> bool:
    return ProfitEngineConfig.from_env().enabled


def allowed_strategy_for_regime(market_regime: str) -> str | None:
    return DEFAULT_STRATEGY_BY_REGIME.get(str(market_regime or "UNKNOWN").upper())


def profit_engine_status_payload() -> dict:
    config = ProfitEngineConfig.from_env()
    return {
        "enabled": config.enabled,
        "mode": config.mode,
        "order_sizing_mode": config.order_sizing_mode,
        "require_auto_exit": config.require_auto_exit,
        "block_entry_when_exit_disabled": config.block_entry_when_exit_disabled,
        "allow_balance_cap": config.allow_balance_cap,
        "disable_percent_sizing": config.disable_percent_sizing,
        "extra_fee_buffer_rate": config.extra_fee_buffer_rate,
        "blocked_entry_regimes": sorted(BLOCKED_ENTRY_REGIMES),
        "allowed_strategies_by_regime": {key: sorted(value) for key, value in ALLOWED_STRATEGIES_BY_REGIME.items()},
    }


def evaluate_profit_entry_gate(
    *,
    market_regime: str | None,
    strategy_name: str | None,
    side: str = "BUY",
    auto_exit_enabled: bool = True,
    config: ProfitEngineConfig | None = None,
) -> dict:
    cfg = config or ProfitEngineConfig.from_env()
    regime = str(market_regime or "UNKNOWN").upper()
    side_upper = str(side or "").upper()
    strategy = normalize_profit_strategy_name(strategy_name) or allowed_strategy_for_regime(regime)
    allowed_strategies = ALLOWED_STRATEGIES_BY_REGIME.get(regime, set())
    base = {
        "enabled": cfg.enabled,
        "market_regime": regime,
        "strategy_name": strategy,
        "allowed_strategy_types": sorted(allowed_strategies),
        "entry_allowed": True,
        "entry_block_reason": "",
        "block_code": None,
    }
    if not cfg.enabled or side_upper not in {"BUY", "BID"}:
        return base
    if cfg.require_auto_exit and cfg.block_entry_when_exit_disabled and not auto_exit_enabled:
        return _blocked(base, "BLOCKED_AUTO_EXIT_DISABLED", "Auto exit is disabled, so Profit Engine blocks new automatic entries.")
    if regime in BLOCKED_ENTRY_REGIMES:
        return _blocked(base, f"PROFIT_ENGINE_BLOCKED_{regime}", f"Profit Engine blocks new automatic entries during {regime}.")
    if allowed_strategies and strategy not in allowed_strategies:
        return _blocked(base, "PROFIT_ENGINE_STRATEGY_NOT_ALLOWED", f"{strategy} is not allowed for {regime}.")
    return base


def normalize_profit_strategy_name(value: Any) -> str:
    strategy = str(value or "").strip().lower()
    compact = strategy.replace("-", "_").replace(" ", "_")
    aliases = {
        "smart_autonomous": "",
        "smart_autonomous_engine": "",
        "ma_cross": "",
        "rsi": "",
        "volatility_breakout": "volume_breakout",
        "breakout": "volume_breakout",
        "pullback": "trend_pullback",
        "range": "range_reversion",
    }
    if strategy in aliases:
        return aliases[strategy]
    if compact in aliases:
        return aliases[compact]
    for known_strategy in ("volume_breakout", "trend_pullback", "range_reversion"):
        if known_strategy in compact:
            return known_strategy
    return strategy


def _blocked(base: dict, code: str, reason: str) -> dict:
    return {**base, "entry_allowed": False, "entry_block_reason": reason, "block_code": code}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
