# Profit Bot V1 Result Report

Date: 2026-06-23 KST

## Summary

Profit Bot V1 was implemented behind `PROFIT_ENGINE_ENABLED`.

The live entry path now keeps existing safety gates for emergency stop, auto trading OFF, duplicate orders, unresolved exchange orders, balance mismatch, partial fills, API errors, and open order recovery. When Profit Engine is enabled, entry order sizing bypasses total exposure, position ratio, and max-order entry caps, then caps the requested amount by available KRW balance plus fee buffer.

## Implemented

- Added Profit Engine modules:
  - `app/profit_engine.py`
  - `app/order_sizing.py`
  - `app/profit_strategies.py`
  - `app/execution_quality.py`
  - `app/strategy_kill_switch.py`
- Added DB tables:
  - `execution_quality_logs`
  - `strategy_kill_switch_events`
- Added `GET /api/profit-engine/status`.
- Added dashboard Profit Engine status in the Operations view.
- Added V1 strategy identifiers:
  - `trend_pullback`
  - `volume_breakout`
  - `range_reversion`
  - `panic_blocker`
- Added V1 BUY candidate strategies to automatic discovery and validation defaults:
  - `trend_pullback`
  - `volume_breakout`
  - `range_reversion`
- Kept `panic_blocker` separated as a risk-off-only strategy, not a default BUY candidate for automatic discovery.
- Added `selected_strategy_type` to Smart decision snapshots so Profit Engine uses the actual strategy type before display names.
- Hardened Profit Engine strategy matching so display names such as `KRW-BTC volume_breakout 5m 82pt` resolve to the underlying Profit strategy type.
- Smart Autonomous Engine now falls back to the regime default Profit strategy, for example `TREND_UP -> trend_pullback`, while blocked regimes remain blocked.
- Strengthened promotion gates:
  - 30 forward trades
  - 168 runtime hours
  - return >= 1%
  - MDD <= 8%
  - win rate >= 42%
  - Profit Factor >= 1.2
  - positive expectancy
  - largest trade profit share <= 50%
- Strengthened auto exit defaults:
  - stop loss 0.8%
  - take profit 1.2%
  - max hold 90 minutes
  - cancel exit order after 45 seconds
  - max exit retry count 2
  - manual confirm false by default
- Added trailing stop tracking on live positions.

## Verification

Passed:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_profit_engine_gate tests.test_smart_autonomous_runtime
```

Passed:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_order_sizing tests.test_profit_engine_gate tests.test_auto_exit_flow tests.test_strategy_promotion_gate tests.test_execution_quality tests.test_strategy_promotion_pipeline
```

Passed:

```powershell
.venv\Scripts\python.exe -m py_compile app\order_sizing.py app\profit_engine.py app\profit_strategies.py app\execution_quality.py app\strategy_kill_switch.py app\live_strategy_pilot.py app\live_exit.py app\strategy_promotion_pipeline.py app\main.py app\risk_manager.py app\database.py
```

Passed:

```powershell
.venv\Scripts\python.exe -m py_compile app\strategy_discovery_scheduler.py app\main.py app\database.py app\smart_decision.py app\profit_engine.py app\live_strategy_pilot.py
```

## Remaining Risks

- Profit Engine is feature-flagged; production behavior changes only after `PROFIT_ENGINE_ENABLED=true` is deployed and runtime is restarted.
- The first live cycle after deployment should be watched for balance mismatch and open order blockers before assuming entries will fire.
- `execution_quality_logs` starts accumulating after the new code is running; old orders will not have full quality metrics.
- The dashboard block is informational. The backend remains the source of truth for blocking and order submission.

## Production Checklist

Before enabling in production:

- Confirm `.env.production` contains `PROFIT_ENGINE_ENABLED=true`.
- Confirm `AUTO_EXIT_ENABLED=true`.
- Confirm `/health` shows schema OK and broker READY.
- Confirm `/api/profit-engine/status?exchange=bithumb&market=KRW-BTC`.
- Confirm no unresolved live orders.
- Confirm no balance mismatch blocker.
- Start auto trading from the UI, not boot.
- Watch first entry and first exit attempt in system logs and live order logs.
