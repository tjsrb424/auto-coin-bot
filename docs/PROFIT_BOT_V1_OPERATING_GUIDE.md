# Profit Bot V1 Operating Guide

## Feature Flag

Profit Bot V1 is controlled by:

```env
PROFIT_ENGINE_ENABLED=true
PROFIT_ENGINE_MODE=aggressive
ORDER_SIZING_MODE=available_balance_cap
```

When disabled, the existing live strategy flow remains the default.

## Entry Behavior

When enabled, automatic BUY entries use:

```text
actual_order_krw = min(requested_order_krw, available_krw / (1 + fee_buffer_rate))
```

Profit Engine entry bypasses only these entry caps:

- max total exposure cap
- max position ratio cap
- max order amount cap

These blockers remain active:

- emergency stop
- auto trading OFF
- auto exit disabled
- duplicate candle/order
- unresolved exchange order
- DB/exchange balance mismatch
- partial fill recovery
- exchange API/order chance failure
- stale capital snapshot
- daily loss and consecutive loss guards

## Market Regime Gate

BUY is blocked in:

- `PANIC`
- `TREND_DOWN`
- `OVERHEATED`
- `UNKNOWN`

BUY can pass in:

- `RANGE` with `range_reversion`
- `TREND_UP` with `trend_pullback` or `volume_breakout`
- `BREAKOUT` with `volume_breakout` or `trend_pullback`

## Auto Exit

V1 assumes auto exit is part of the safety system.

Recommended production values:

```env
AUTO_EXIT_ENABLED=true
AUTO_EXIT_REQUIRE_MANUAL_CONFIRM=false
AUTO_STOP_LOSS_PERCENT=0.8
AUTO_TAKE_PROFIT_PERCENT=1.2
AUTO_MAX_HOLD_MINUTES=90
AUTO_TRAILING_STOP_PERCENT=0.7
AUTO_CANCEL_EXIT_ORDER_AFTER_SECONDS=45
AUTO_MAX_EXIT_RETRY_COUNT=2
```

If `AUTO_EXIT_ENABLED=false`, Profit Engine blocks new automatic entries with `BLOCKED_AUTO_EXIT_DISABLED`.

## Promotion Gate

Forward candidates become `LIVE_ELIGIBLE` only after all V1 gates pass:

- at least 30 trades
- at least 168 runtime hours
- return >= 1%
- MDD <= 8%
- win rate >= 42%
- Profit Factor >= 1.2
- expectancy after fee > 0
- largest single winning trade share <= 50%

Kill switch pauses a strategy on repeated losses, negative expectancy, repeated stop loss exits, poor execution quality, exit failure, or balance mismatch.

## Dashboard

Operations view shows:

- Profit Engine mode
- market regime
- entry gate result
- strategy name
- requested order amount
- available KRW
- actual order amount
- sizing reason
- fill rate
- kill switch status

The API source is:

```http
GET /api/profit-engine/status?exchange=bithumb&market=KRW-BTC
```
