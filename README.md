# Coin Bot Lab

개인용 코인 전략 실험실입니다. 기본 기능은 백테스트와 페이퍼 트레이딩이며, 자동 실거래·전략 신호 기반 자동 주문·레버리지/선물 기능은 포함하지 않습니다.

Sprint 6에서는 실거래 API 연결을 준비하지만, 실주문은 환경변수와 UI 확인 문구로 잠금 해제된 수동 소액 테스트만 허용합니다.

## Features

- Upbit public minute candles for `KRW-BTC`
- Rule-based backtesting for MA cross, RSI, and volatility breakout
- SQLite persistence for candles, signals, virtual backtest orders, paper sessions, paper orders, and equity curves
- Paper trading simulation with virtual KRW/BTC balance, fees, slippage, and risk limits
- Realtime paper trading sessions that poll public candles every 60 seconds and only process new UTC candles
- Candidate strategy forward paper tests with persistent tick, signal, order, and equity logs
- LiveBroker safety scaffold for balance lookup, order preview, risk checks, emergency stop, and manual-only small live order submission
- Dark trading-terminal dashboard with candle chart, signal markers, balance cards, PnL graph, and logs

## Live Trading Safety

- Live trading defaults to `PAPER` or `LIVE_LOCKED`.
- Server restart resets live trading to a locked state. `LIVE_MANUAL_ONLY` is not restored automatically.
- API keys are read only from backend environment variables and are never entered in the UI.
- Use an Upbit API key without withdrawal permission.
- Configure allowed IPs in Upbit before enabling private API access.
- Every preview, blocked request, failed request, and submitted live order is stored in SQLite with masked request payloads.
- Emergency Stop blocks all live order candidates and does not auto-sell or auto-liquidate.

Environment variables:

```bash
UPBIT_ACCESS_KEY=
UPBIT_SECRET_KEY=
LIVE_TRADING_ENABLED=false
MAX_LIVE_ORDER_KRW=10000
MAX_DAILY_LIVE_LOSS_PERCENT=1
MIN_LIVE_ORDER_KRW=5000
MAX_LIVE_POSITION_RATIO=0.5
LIVE_DUPLICATE_WINDOW_SECONDS=30
LIVE_FEE_RATE=0.0005
LIVE_VOLATILITY_BLOCK_RATE=0.03
LIVE_MIN_CANDLE_VOLUME=0
```

## Time Handling

- SQLite storage and internal strategy/backtest ordering use UTC candle timestamps.
- Candle uniqueness is enforced by `market + unit + candle_time_utc`.
- Dashboard chart and logs display UTC timestamps as Korea Standard Time.
- Paper trading daily loss limits are bucketed by KST date for Korean users.

## Backend

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Frontend

```bash
npm install
npm run dev
```

기본 마켓은 `KRW-BTC`입니다. 백테스트/페이퍼/Forward Paper는 업비트 공개 분봉 캔들 API를 사용하며, 실계좌 잔고 조회와 수동 실주문 테스트는 서버 환경변수로 명시적으로 활성화한 경우에만 private API를 사용합니다.
