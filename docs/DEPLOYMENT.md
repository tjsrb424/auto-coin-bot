# Lightsail Production Deployment

This guide deploys Auto Trader to AWS Lightsail with Docker Compose. The production policy is:

- Feature flags may be `true`.
- Runtime must start as `OFF`, `STOPPED`, or `PAUSED`.
- Auto trading must not resume on server boot.
- The user must log in as admin and press ON in the UI to start runtime trading.
- Runtime source of truth is the backend server and DB, not frontend local state.

## 1. Prepare Lightsail

1. Create an AWS Lightsail Ubuntu instance.
2. Attach a Static IP to the instance.
3. Add the Static IP to the allowed IP list for Bithumb and Upbit API keys.
4. Open the Lightsail browser SSH console.
5. Update packages:

```bash
sudo apt update && sudo apt upgrade -y
```

## 2. Install Docker

```bash
sudo apt install -y ca-certificates curl gnupg git
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker
docker --version
docker compose version
```

## 3. Clone

```bash
git clone <YOUR_REPOSITORY_URL> auto-coin-bot
cd auto-coin-bot
```

## 4. Create Production Env

Never commit `.env.production`. It contains API keys and admin session secrets.

Create an admin password hash locally or on the server:

```bash
python -c "from app.auth import hash_password; print(hash_password('CHANGE_THIS_PASSWORD'))"
```

Put `ADMIN_PASSWORD_HASH=<printed hash>` in your source `.env` together with the exchange keys. Then generate production env:

```bash
npm run env:production
```

If your source env is in another location:

```bash
python scripts/create-production-env.py --source .env --output .env.production
```

The script copies required Bithumb keys, optional Upbit keys, requires `ADMIN_PASSWORD_HASH`, and generates `SESSION_SECRET` when missing. It does not print API keys.

Required production values:

```bash
APP_ENV=production
RUNTIME_INSTANCE_ID=
RUNTIME_LOCK_TTL_SECONDS=3600
SESSION_COOKIE_SECURE=false
EXCHANGE=bithumb
LIVE_TRADING_ENABLED=true
LIVE_AUTO_TRADING_ENABLED=true
AUTO_STRATEGY_PILOT_ENABLED=true
AUTO_START_ON_BOOT=false
REQUIRE_MANUAL_START=true
REQUIRE_UI_CONFIRMATION=true
AUTO_ALLOWED_EXCHANGE=bithumb
AUTO_ALLOWED_MARKET=KRW-BTC
AUTO_ALLOWED_ORDER_TYPE=limit
AUTO_EXIT_ENABLED=true
AUTO_MARKET_ORDER_ENABLED=false
PROFIT_ENGINE_ENABLED=true
ORDER_SIZING_MODE=available_balance_cap
DATABASE_URL=sqlite:////app/data/app.db
LOG_DIR=/app/logs
```

Profit Bot V1 recommended values:

```bash
PROFIT_ENGINE_MODE=aggressive
PROFIT_ENGINE_REQUIRE_AUTO_EXIT=true
PROFIT_ENGINE_BLOCK_ENTRY_WHEN_EXIT_DISABLED=true
PROFIT_ENGINE_DISABLE_PERCENT_SIZING=true
PROFIT_ENGINE_EXTRA_FEE_BUFFER_RATE=0.0002
AUTO_STOP_LOSS_PERCENT=0.8
AUTO_TAKE_PROFIT_PERCENT=1.2
AUTO_MAX_HOLD_MINUTES=90
AUTO_CANCEL_EXIT_ORDER_AFTER_SECONDS=45
AUTO_MAX_EXIT_RETRY_COUNT=2
AUTO_EXIT_REQUIRE_MANUAL_CONFIRM=false
AUTO_TRAILING_STOP_PERCENT=0.7
AUTO_PROMOTION_MIN_FORWARD_TRADES=30
AUTO_PROMOTION_MIN_FORWARD_RUNTIME_HOURS=168
AUTO_PROMOTION_MIN_FORWARD_RETURN_PERCENT=1
AUTO_PROMOTION_MAX_FORWARD_MDD=0.08
AUTO_PROMOTION_MIN_FORWARD_WIN_RATE=0.42
AUTO_PROMOTION_MIN_PROFIT_FACTOR=1.2
AUTO_PROMOTION_MIN_EXPECTANCY_AFTER_FEE=0
AUTO_PROMOTION_MAX_SINGLE_TRADE_PROFIT_SHARE=0.5
```

Use `SESSION_COOKIE_SECURE=true` only after HTTPS is configured. With plain HTTP Static IP testing, keep it `false` or browser login cookies will not persist.

## 5. Build And Run

```bash
docker compose build
docker compose up -d
docker compose ps
```

Volumes:

- `coin_bot_data` stores SQLite DB at `/app/data/app.db`.
- `coin_bot_logs` stores logs at `/app/logs`.

Rebuilding containers does not delete these volumes.

## 6. Health Checks

```bash
curl http://localhost/health
curl http://localhost/health/live
curl http://localhost/health/broker
curl http://localhost/health/risk
curl http://localhost/health/scheduler
```

Confirm:

- `database_status` is `OK`.
- `selected_exchange` is `bithumb`.
- `live_trading_enabled` is `true`.
- `auto_trading_enabled` is `true`.
- `auto_runtime_status` is `OFF` before UI start.
- `emergency_stop_status` is `OFF`.

## 7. Admin Login

1. Open `http://<STATIC_IP>/`.
2. Log in with `ADMIN_USERNAME` and the password used to generate `ADMIN_PASSWORD_HASH`.
3. Unauthenticated users can only see the login screen.
4. Use the user icon menu to log out.

## 8. Exchange Verification

After login:

1. Confirm the exchange dropdown defaults to `빗썸 (Bithumb)`.
2. Confirm API Key Loaded is `YES`.
3. Test balance fetch from the dashboard or settings.
4. Check order chance from the backend if needed:

```bash
curl -b cookies.txt http://localhost/api/live-trading/order-chance?exchange=bithumb\&market=KRW-BTC
```

## 9. Auto Trading ON Flow

Before ON:

- Runtime must be `OFF`, `STOPPED`, or `PAUSED`.
- No auto order must be created.
- Check `GET /api/runtime/status`; do not trust browser local state.

When pressing ON in the UI:

1. Admin session is required.
2. Exchange must be allowed, default `bithumb`.
3. API keys must be loaded.
4. Balance and order chance checks must succeed.
5. Emergency Stop must be inactive.
6. Risk Manager must allow the action.
7. Candidate Strategy must exist.
8. Market must be `KRW-BTC`.
9. Order type must be `limit`.
10. Enter confirmation text: `돈은 속도가 아니라 규율로 지킨다`.
11. Runtime changes to `RUNNING` only after all checks pass.
12. Runtime lock is acquired in DB. If another instance owns a RUNNING lock, start is blocked.

## 10. Auto Trading OFF Flow

When pressing OFF:

- Runtime changes to `STOPPED` or `LIVE_PAUSED`.
- New auto orders stop.
- Open orders are not force market-closed.
- Open positions are not market-sold automatically.
- Review open orders and positions before further action.

## 11. Emergency Stop Test

1. Use the UI Emergency Stop control.
2. Confirm `/health/risk` returns `emergency_stop_status=ON`.
3. Confirm ON attempts are blocked.
4. Reset only with the required reset confirmation.

## 12. Restart Test

```bash
docker compose restart backend
curl http://localhost/health/live
curl http://localhost/api/runtime/status
```

Confirm:

- Feature flags remain `true`.
- Runtime does not return to `RUNNING`.
- Prior `READY` or `RUNNING` live sessions become `LIVE_PAUSED`.
- User must log in and press ON again to restart auto trading.
- No startup order is created.

## 13. Runtime Source Of Truth

The frontend must not store or override runtime state with `localStorage`.

Allowed runtime mutation:

- User clicks Auto Trading ON, enters `돈은 속도가 아니라 규율로 지킨다`, and the frontend calls `POST /api/runtime/start`.
- User clicks Auto Trading OFF and the frontend calls `POST /api/runtime/stop`.

Forbidden runtime mutation:

- Page load calls stop.
- Browser refresh calls stop.
- `npm run dev` changes server runtime.
- Frontend default state overwrites backend runtime.
- `localStorage` overwrites backend runtime.

Runtime status endpoint:

```bash
curl http://localhost/api/runtime/status
```

Important fields:

- `runtime_status`
- `strategy_status`
- `instance_id`
- `hostname`
- `server_ip`
- `runtime_owner`
- `runtime_lock`

## 14. Local Development Safety

In development, live trading is blocked unless explicitly overridden:

```bash
APP_ENV=development
ALLOW_DEV_LIVE_TRADING=false
LIVE_TRADING_ENABLED=false
LIVE_AUTO_TRADING_ENABLED=false
AUTO_STRATEGY_PILOT_ENABLED=false
```

If a local frontend should control the production server, set:

```bash
VITE_API_BASE_URL=http://<STATIC_IP>
```

Do not run a local backend with the same exchange API keys while production auto trading is active.

## 15. Logs

```bash
docker compose logs -f backend
docker compose logs -f frontend
docker volume inspect auto-coin-bot_coin_bot_logs
```

Do not log or paste:

- API Secret Key
- JWT or session token
- Authorization header
- Raw API Key
- Raw password

## 16. Update

```bash
git pull
docker compose build
docker compose up -d
curl http://localhost/health
```

After update, run the restart test again and confirm runtime is not auto-resumed.

### 16.1 GitHub Actions Auto Deploy

The repository includes `.github/workflows/deploy-production.yml`.

On every push to `main`, GitHub Actions:

1. Installs backend dependencies and runs `python -m unittest discover -s tests`.
2. Installs frontend dependencies and runs `npm run build`.
3. SSHes into Lightsail.
4. Resets `/home/ubuntu/auto-coin-bot` to `origin/main`.
5. Rebuilds and restarts the backend and frontend containers.
6. Checks `http://43.201.162.191/health`.

Required GitHub repository secrets:

```text
LIGHTSAIL_HOST=43.201.162.191
LIGHTSAIL_USER=ubuntu
LIGHTSAIL_SSH_KEY=<private key contents>
```

If these secrets are missing, the workflow still runs tests and build checks but skips the deploy step.

Optional GitHub repository variables:

```text
LIGHTSAIL_APP_DIR=/home/ubuntu/auto-coin-bot
PRODUCTION_APP_URL=http://43.201.162.191
```

The workflow uses `docker compose --env-file .env.production`, so the server-side `.env.production` remains the production source for API keys and runtime flags. Do not commit `.env.production`.

The deploy step intentionally runs `git reset --hard origin/main` on the server. Keep production-only values in ignored files such as `.env.production` or Docker volumes, not in tracked files.

Auto deploy updates code only. Runtime safety still applies after backend restart: live sessions are paused and must be resumed from the authenticated UI after review.

## 17. Rollback

```bash
git log --oneline
git checkout <PREVIOUS_COMMIT>
docker compose build
docker compose up -d
curl http://localhost/health
```

The DB and logs remain in Docker volumes.

## 18. Stop Server

```bash
docker compose down
```

To stop and delete data volumes only when intentionally wiping state:

```bash
docker compose down -v
```

Do not run `down -v` during normal operation.

## 19. Sprint 10 Lightsail Runbook

Use this runbook for the first real Lightsail deployment and live pilot.

### 19.1 Initial Ubuntu 24.04 Setup

Connect with the Lightsail browser SSH terminal, then run:

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y git curl ca-certificates
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker
docker --version
docker compose version
```

### 19.2 Clone And Env

```bash
git clone <REPOSITORY_URL> <PROJECT_DIR>
cd <PROJECT_DIR>
```

Prepare production env with one of these methods.

Method A, generate on server:

```bash
cp .env.production.example .env
nano .env
python scripts/create-production-env.py --source .env --output .env.production
chmod 600 .env.production
```

Method B, upload from local:

```bash
scp .env.production ubuntu@<SERVER_IP>:/home/ubuntu/<PROJECT_DIR>/.env.production
ssh ubuntu@<SERVER_IP> "chmod 600 /home/ubuntu/<PROJECT_DIR>/.env.production"
```

If you store the file as `backend/.env.production`, run compose with:

```bash
PRODUCTION_ENV_FILE=backend/.env.production docker compose up -d
```

Never paste API keys into documentation, Git commits, issue comments, or screenshots.

### 19.3 Static IP Allowlist

The exchange sees the Lightsail Static IP, not your home PC IP.

- Register the Lightsail Static IP in Bithumb API allowed IP settings.
- Register the same Static IP in Upbit Open API settings if Upbit will be used.
- If balance or order chance fails with `API_IP_NOT_ALLOWED`, `BROKER_AUTH_IP_ERROR`, or `NO_AUTHORIZATION_IP`, fix the exchange allowlist first.

### 19.4 Build And Start

```bash
docker compose build
docker compose up -d
docker compose ps
docker compose logs -f backend
```

Open:

```text
http://<STATIC_IP>/
```

Then verify admin login, logout, and that unauthenticated `/api/live/status` requests are blocked.

### 19.5 Health Verification

```bash
curl http://localhost/health
curl http://localhost/health/live
curl http://localhost/health/broker
curl http://localhost/health/risk
curl http://localhost/health/scheduler
curl http://localhost/api/runtime/status
```

Expected after server start:

- `live_trading_enabled=true`
- `auto_trading_enabled=true`
- `auto_runtime_status=OFF`
- `strategy_status=STOPPED`
- `live_session_status=PAUSED`
- `emergency_stop_status=OFF`

The UI bot status panel shows server status, database status, broker status, selected exchange, scheduler status, risk status, runtime status, app env, and server start time.

### 19.6 Bithumb API Checks

After admin login:

1. Select `빗썸 (Bithumb)`.
2. Confirm API Key Loaded.
3. Fetch balances.
4. Fetch order chance.
5. Confirm no API key or secret appears in browser Network responses.

### 19.7 Manual Limit Order And Cancel Test

Run this only after health, balance, and order chance pass.

Policy:

- Exchange: Bithumb
- Market: `KRW-BTC`
- Order type: `limit`
- Amount: `5,000~30,000 KRW`
- Price: below current price
- No market orders

Flow:

1. Admin login.
2. Fetch balance.
3. Fetch order chance.
4. Create manual order preview.
5. Confirm Risk Result is `ALLOWED`.
6. Enter the required final confirmation.
7. Submit limit buy order.
8. Save order UUID.
9. Confirm order status is `WAITING`.
10. Cancel the order.
11. Confirm `CANCELED` in LiveOrderLog.

Expected log flow:

- `PREVIEWED`
- `SUBMITTED`
- `WAITING`
- `CANCELED`

### 19.8 Limited Auto Pilot Test

Run this only after the manual limit order/cancel test succeeds.

Policy:

- One Candidate Strategy only
- Exchange: Bithumb
- Market: `KRW-BTC`
- Order type: `limit`
- `AUTO_MAX_ORDERS_PER_DAY=0` means no daily auto entry order count limit.
- `AUTO_CANCEL_UNFILLED_AFTER_SECONDS=60`
- Amount must stay within `AUTO_MAX_ORDER_KRW`
- `RISK_MAX_DAILY_LOSS_PERCENT` is measured against account equity. Live order checks use the fetched exchange balance and dashboard-only checks fall back to `RISK_ACCOUNT_EQUITY_KRW`.
- `RISK_MAX_DAILY_LOSS_KRW` remains a separate absolute daily loss stop and can block before the percentage limit.
- Live entry liquidity uses the latest completed 1-minute candle and recent five completed 1-minute candles. Defaults require `RISK_MIN_CURRENT_1M_VOLUME_KRW=30000000` and `RISK_MIN_AVG_5M_VOLUME_KRW=50000000`.

Flow:

1. Select one Candidate Strategy.
2. Click Auto Trading ON.
3. Enter `돈은 속도가 아니라 규율로 지킨다`.
4. Confirm `/api/runtime/status` returns `runtime_status=RUNNING`.
5. Wait for a completed candle BUY signal.
6. Confirm Risk Manager allows it.
7. Confirm one limit order is created.
8. If unfilled, confirm it is canceled after 60 seconds.
9. Click Auto Trading OFF.
10. Confirm `runtime_status=STOPPED`.

Expected unfilled log flow:

- `SIGNAL_BUY`
- `RISK_ALLOWED`
- `SUBMITTED`
- `WAITING`
- `CANCELED`

If filled:

- `FILLED`
- LivePosition is created.
- Additional buys are blocked by open position policy.

### 19.9 Emergency Stop Test

1. Start from `runtime_status=RUNNING`.
2. Click Emergency Stop.
3. Confirm new orders are blocked with `BLOCKED_EMERGENCY_STOP`.
4. Confirm runtime is `EMERGENCY_STOPPED` or stopped.
5. If an open order exists, cancel it according to policy.
6. Do not market-sell an existing LivePosition automatically.
7. Confirm manual review state and emergency log.

### 19.10 Restart And Persistence Test

```bash
docker compose restart
curl http://localhost/health/live
curl http://localhost/api/runtime/status
```

Confirm:

- Runtime does not auto-resume to `RUNNING`.
- Prior `READY` or `RUNNING` sessions are `LIVE_PAUSED`.
- No startup order is created.
- RecoveryLog is stored.
- LiveOrderLog, RiskLog, and RecoveryLog remain after restart.

### 19.11 Update, Rollback, Stop

Update:

```bash
git pull
docker compose build
docker compose up -d
curl http://localhost/health
```

Rollback:

```bash
git log --oneline
git checkout <PREVIOUS_COMMIT>
docker compose build
docker compose up -d
```

Stop:

```bash
docker compose down
```

Do not run `docker compose down -v` unless you intentionally want to delete DB and logs.
