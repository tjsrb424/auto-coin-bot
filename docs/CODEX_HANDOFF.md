# Codex Handoff Notes

Last updated: 2026-06-22 KST

This document is the first handoff file for a new Codex session. Read it before changing the trading runtime.

## Current Product Direction

The bot is moving from a BTC-only auto trader to an autonomous multi-market strategy system.

The intended user model is:

- The user controls only auto trading ON/OFF and operating limits.
- The bot researches markets, validates strategies, saves candidates, promotes them through paper/shadow gates, and selects the active strategy.
- Real orders are allowed only when auto trading is ON and all runtime/risk gates pass.
- The bot must never change `auto_trading_enabled`, `max_total_exposure_krw`, `daily_loss_limit_pct`, emergency stop, or runtime locks.

## What Was Built Today

### Multi-Market Strategy Discovery

- Added the Backtest/Strategy Validation Center UI.
- Added KRW market universe scanning and multi-market validation.
- Added candidate status progression:
  `DISCOVERED`, `BACKTEST_RUNNING`, `BACKTEST_PASSED`, `BACKTEST_FAILED`, `SHADOW_RUNNING`, `SHADOW_PASSED`, `LIVE_ELIGIBLE`, `LIVE_ACTIVE`, `PAUSED`, `REJECTED`.
- Added staged scheduler/orchestrator flow:
  - market scan
  - fast validation
  - deep validation
  - promotion/selector
  - autonomous orchestrator run-now/status
- Added schema self-healing and `/health` DB schema reporting.
- Added selector status and switch logs in the UI.
- Added Korean UI labels for system/risk/blocker messages.

### Five-Slot Capital Allocator

- Added central capital allocation tables and APIs.
- Production defaults were changed to 5 slots:
  - `AUTO_MAX_OPEN_POSITION_COUNT=5`
  - `AUTO_SELECTOR_MAX_OPEN_POSITIONS=5`
  - `AUTO_CAPITAL_ALLOCATOR_ENABLED=true`
  - `AUTO_MAX_NEW_ENTRIES_PER_TICK=2`
- Fixed `/api/capital-allocator/status?exchange=bithumb` so it does not fail with open positions.

### Real-Time Capital Snapshot

- Added `app/capital_snapshot.py`.
- Before entry orders, the bot now checks:
  - actual KRW balance
  - DB open positions
  - exchange coin balances
  - DB unresolved orders
  - exchange open orders
  - active reservations
  - available budget after reserve/exposure limits
- Before sell orders, the bot now limits sell volume to the smaller of:
  - DB position volume
  - actual exchange sellable balance
- Added APIs:
  - `GET /api/capital-snapshot`
  - `POST /api/capital-snapshot/reconcile`
- Added `AUTO_CAPITAL_SNAPSHOT_MAX_AGE_SECONDS=10`.

### Recovery Log Deduplication

- The system was repeatedly logging `BALANCE_MISMATCH` every tick.
- Added `LIVE_RECOVERY_EVENT_DEDUPE_SECONDS=300`.
- Safety blocking remains unchanged, but identical balance mismatch logs are suppressed for 5 minutes.

## Important Commits From Today

- `363b0d5` Add multi-market strategy selection
- `c994788` Add autonomous strategy orchestrator
- `31b6c92` Add staged strategy discovery schedulers
- `9bbc07a` Harden strategy discovery schema recovery
- `22fad68` Add five-slot capital allocator
- `ecf914b` Fix capital allocator status with open positions
- `f7cc388` Add real-time capital snapshot checks
- `196e839` Fix live exit capital snapshot test
- `2fb12c2` Deduplicate balance mismatch recovery logs

## Current Production Snapshot

Production URL: `http://43.201.162.191`

Latest checked state after `2fb12c2` deployment:

- `/health`: OK
- DB schema: OK
- broker: READY
- scheduler: RUNNING
- emergency stop: OFF
- `live_trading_enabled`: true
- `auto_trading_enabled`: true
- `auto_strategy_enabled`: true
- runtime/session state at last check:
  - `auto_runtime_status=PAUSED`
  - `auto_strategy_status=LIVE_PAUSED`
  - `live_session_status=LIVE_PAUSED`

Interpretation:

Auto trading policy is ON, but the live session can still be paused or blocked by safety gates. Do not treat ON as proof that live orders are being submitted.

## Current Known Blocker

The UI showed repeated Korean system logs meaning:

> Bot records and exchange balance differ, so orders are being held.

This maps to `BALANCE_MISMATCH` / `BLOCKED_BALANCE_MISMATCH`.

Code paths:

- `app/live_recovery.py::reconcile_balances`
- `app/capital_snapshot.py::build_capital_snapshot_async`
- `app/live_strategy_pilot.py::_submit_entry_order`
- `app/live_exit.py::evaluate_exit_order`

Current behavior is intentional:

- If DB open position volume and actual exchange coin balance do not match, new orders are blocked.
- The log is now deduplicated, but the safety block remains.

Next debugging step for this blocker:

- Use authenticated UI/API session to inspect:
  - `/api/live-orders`
  - `/api/live-recovery/status?exchange=bithumb`
  - `/api/capital-snapshot?exchange=bithumb`
  - `/api/capital-allocator/status?exchange=bithumb`
- Determine whether:
  - DB has an open position that exchange balance does not have, or
  - exchange has coin balance that DB has not imported/adopted.
- Do not auto-import or clear positions without explicit user confirmation.

## Safety Rules For The Next Agent

Do not weaken these unless the user explicitly asks and the risk is explained:

- Do not let the bot mutate `auto_trading_enabled`.
- Do not let the bot mutate `max_total_exposure_krw`.
- Do not let the bot mutate `daily_loss_limit_pct`.
- Do not bypass emergency stop.
- Do not submit live orders when `BALANCE_MISMATCH`, unresolved orders, stale snapshot, open-order mismatch, cooldown, daily switch limit, or exposure limit blocks.
- Do not treat `BACKTEST_PASSED` as live-ready.
- Only `LIVE_ELIGIBLE` or `LIVE_ACTIVE` candidates can be used by the live selector/runtime.
- Auto trading OFF may still allow scan/validation/promotion up to `LIVE_ELIGIBLE`, but must not apply `LIVE_ACTIVE` or submit real orders.

## Verification Commands Used

Common checks:

```powershell
.venv\Scripts\python.exe -m py_compile (Get-ChildItem app -Filter *.py).FullName
.venv\Scripts\python.exe -m unittest discover -s tests
.venv\Scripts\python.exe -m pytest
npm run build
```

Production checks:

```powershell
Invoke-RestMethod -Uri 'http://43.201.162.191/health' | ConvertTo-Json -Depth 5
```

Frontend build hash check:

```powershell
$html=Invoke-WebRequest -UseBasicParsing -Uri 'http://43.201.162.191/'
$asset=($html.Content | Select-String -Pattern 'assets/[^"'']+\.js' -AllMatches).Matches.Value | Select-Object -First 1
$js=Invoke-WebRequest -UseBasicParsing -Uri "http://43.201.162.191/$asset"
$js.Content -match '<short_commit_hash>'
```

## Recommended Next Work

1. Diagnose and resolve the current production `BALANCE_MISMATCH`.
2. Improve the UI so the mismatch card shows exact DB volume, exchange volume, and the action needed.
3. Make the balance reconciliation flow multi-market aware instead of BTC-only.
4. Add a safe admin-only review flow for importing/adopting exchange balances into DB positions.
5. Keep testing the autonomous pipeline:
   scan -> validation -> candidate save -> forward/shadow -> `LIVE_ELIGIBLE` -> selector apply -> runtime entry.

## Handoff Advice

For another Codex session, start with:

1. Read `AGENTS.md`.
2. Read this file.
3. Run `git status --short --branch`.
4. Check production `/health`.
5. Inspect the exact blocker before changing runtime logic.
