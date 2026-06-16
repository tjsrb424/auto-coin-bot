import React from "react";
import ReactDOM from "react-dom/client";
import {
  Activity,
  BarChart3,
  PauseCircle,
  Play,
  RefreshCw,
  ShieldCheck,
  Wallet
} from "lucide-react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  IChartApi,
  ISeriesApi,
  ISeriesMarkersPluginApi,
  Time
} from "lightweight-charts";
import "./styles.css";

type Strategy = "ma_cross" | "rsi" | "volatility_breakout";
type Exchange = "upbit" | "bithumb";
type Tone = "neutral" | "green" | "red" | "amber" | "cyan";

type Candle = {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

type ChartCandleData = {
  time: Time;
  open: number;
  high: number;
  low: number;
  close: number;
};

type Signal = {
  time: string;
  signal: "BUY" | "SELL";
  price: number;
  reason: string;
};

type BacktestOrder = {
  time: string;
  market?: string;
  strategy?: Strategy;
  side: "BUY" | "SELL";
  price: number;
  quantity: number;
  volume?: number;
  amount_krw?: number;
  fee: number;
  pnl?: number | null;
  realized_pnl?: number | null;
  reason?: string;
};

type PaperOrder = {
  time: string;
  created_at?: string;
  candle_timestamp?: string;
  market: string;
  side: "BUY" | "SELL";
  strategy: Strategy;
  signal_price: number;
  execution_price: number;
  quantity: number;
  price?: number;
  volume?: number;
  amount_krw?: number;
  fee: number;
  realized_pnl?: number | null;
  reason: string;
  signal_reason?: string;
  risk_check_result?: string;
  order_source?: string;
  blocked?: boolean;
  blocked_reason?: string | null;
};

type Metrics = {
  total_return: number;
  mdd: number;
  win_rate: number;
  trade_count: number;
  average_profit: number;
  average_loss: number;
  profit_factor?: number;
  profit_loss_ratio: number;
  average_holding_time_minutes?: number;
  last_signal: string;
  final_equity: number;
  realized_pnl?: number;
  score?: number;
};

type BacktestResponse = {
  id: number;
  strategy: Strategy;
  candles: Candle[];
  signals: Signal[];
  orders: BacktestOrder[];
  metrics: Metrics;
  equity_curve?: EquityPoint[];
};

type StrategyComparisonRow = {
  strategy: Strategy;
  total_return: number;
  mdd: number;
  win_rate: number;
  trade_count: number;
  profit_factor: number;
  final_equity: number;
  score: number;
};

type BacktestCompareResponse = {
  market: string;
  unit: number;
  start_time_utc: string;
  end_time_utc: string;
  candle_count: number;
  results: BacktestResponse[];
  comparison: StrategyComparisonRow[];
};

type ValidationRow = {
  market: string;
  unit: number;
  timeframe: string;
  strategy: Strategy;
  parameters: Record<string, number>;
  period_label: string;
  start_time_utc: string;
  end_time_utc: string;
  metrics: Metrics;
  stability_score: number;
  warnings: string[];
};

type StrategyValidationResponse = {
  run_id: number;
  strategy: Strategy;
  rows: ValidationRow[];
  parameter_count: number;
};

type CandidateStrategy = {
  id: number;
  strategy: Strategy;
  parameters: Record<string, number>;
  unit: number;
  market: string;
  backtest_period: string;
  score: number;
  backtest_total_return?: number;
  backtest_mdd?: number;
  backtest_win_rate?: number;
  backtest_profit_factor?: number;
  backtest_trade_count?: number;
  backtest_average_trade_pnl?: number;
  warning?: string;
  created_at: string;
};

type ForwardTickLog = {
  tick_time_utc: string;
  session_id: number;
  market: string;
  unit: number;
  latest_candle_time_utc?: string | null;
  last_processed_candle_time_utc?: string | null;
  result: string;
  message: string;
};

type ForwardSignalLog = {
  signal_time_utc: string;
  session_id: number;
  strategy: Strategy;
  signal: "BUY" | "SELL" | "HOLD";
  confidence: number;
  reason: string;
  risk_result: string;
  candle_time_utc: string;
};

type ApiCandle = {
  candle_time_utc: string;
  opening_price: number;
  high_price: number;
  low_price: number;
  trade_price: number;
  candle_acc_trade_volume: number;
};

type CandleResponse = {
  candles: ApiCandle[];
};

type EquityPoint = {
  time: string;
  equity: number;
  cash_krw: number;
  btc_quantity: number;
  price: number;
  unrealized_pnl?: number;
  drawdown?: number;
};

type PaperResponse = {
  id?: number;
  status: "EMPTY" | "RUNNING" | "STOPPED" | "ERROR" | "COMPLETED";
  mode?: "SIMULATION" | "LIVE" | "FORWARD_PAPER";
  risk_status?: string;
  scheduler_interval_seconds?: number | null;
  market?: string;
  unit?: number;
  timeframe?: number;
  strategy?: Strategy;
  started_at?: string;
  stopped_at?: string | null;
  last_processed_candle_time_utc?: string | null;
  last_tick_time_utc?: string | null;
  last_signal?: string;
  updated_at?: string | null;
  next_check_time_utc?: string | null;
  balance?: {
    initial_cash: number;
    cash_krw: number;
    current_price: number;
    equity: number;
    realized_pnl: number;
    unrealized_pnl: number;
    total_pnl?: number;
    total_return?: number;
    mdd?: number;
  };
  position?: {
    btc_quantity: number;
    avg_buy_price: number;
    market_value: number;
    position_ratio?: number;
  };
  signals?: Signal[];
  blocked_signals?: Signal[];
  orders?: PaperOrder[];
  equity_curve?: EquityPoint[];
};

type ForwardPaperResponse = PaperResponse & {
  candidate_strategy_id?: number;
  candidate?: CandidateStrategy;
  metrics?: {
    total_return: number;
    mdd: number;
    win_rate: number;
    trade_count: number;
    profit_factor: number;
    realized_pnl: number;
    average_trade_pnl: number;
  };
  tick_logs?: ForwardTickLog[];
  signal_logs?: ForwardSignalLog[];
};

type LiveMode = "PAPER" | "LIVE_LOCKED" | "LIVE_ARMED" | "LIVE_MANUAL_ONLY" | "EMERGENCY_STOPPED";

type LiveStatus = {
  mode: LiveMode;
  exchange?: Exchange;
  live_trading_enabled: boolean;
  broker_status: string;
  api_key_loaded: boolean;
  access_key_loaded: boolean;
  secret_key_loaded: boolean;
  balance_fetch_status: string;
  order_chance_status?: string;
  risk_manager_status: string;
  emergency_stop: boolean;
  max_live_order_krw: number;
  daily_loss_limit_percent: number;
  min_order_krw: number;
  last_live_order_time?: string | null;
  api_key_policy?: string;
};

type LiveBalances = LiveStatus & {
  error_message?: string | null;
  estimated_total_equity_krw?: number;
  balances?: {
    krw?: { balance: number; locked: number };
    btc?: { balance: number; locked: number; avg_buy_price?: number };
    eth?: { balance: number; locked: number; avg_buy_price?: number };
  };
};

type LiveOrderChance = LiveStatus & {
  market: string;
  order_chance_status: string;
  order_chance_error?: string | null;
  order_chance?: Record<string, unknown>;
};

type LiveOrderPreview = {
  request_id: string;
  allowed: boolean;
  risk_result: string;
  blocked_reason?: string;
  exchange?: string;
  market: string;
  side: "BUY" | "SELL";
  order_type: "LIMIT" | "MARKET";
  price: number;
  amount_krw: number;
  volume: number;
  fee_estimate: number;
  estimated_post_krw_balance: number;
  estimated_post_asset_balance: number;
  balance_fetch_status?: string;
  balance_error?: string | null;
  order_chance_status?: string;
  order_chance_error?: string | null;
};

type LiveOrderLog = {
  id: number;
  request_id: string;
  exchange?: string;
  market: string;
  side: "BUY" | "SELL";
  order_type: string;
  price?: number | null;
  volume?: number | null;
  amount_krw?: number | null;
  fee_estimate: number;
  risk_result: string;
  status: string;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
  order_uuid?: string | null;
  strategy_name?: string | null;
  signal_reason?: string | null;
  candle_time_utc?: string | null;
};

type AutoLivePilotSession = {
  id: number;
  exchange: string;
  market: string;
  candidate_strategy_id?: number | null;
  strategy_name: string;
  status: "READY" | "RUNNING" | "STOPPED" | "ERROR" | "EMERGENCY_STOPPED";
  auto_enabled: boolean;
  order_amount_krw: number;
  max_orders_per_day: number;
  orders_created_today: number;
  last_signal?: string | null;
  last_signal_time_utc?: string | null;
  last_order_time_utc?: string | null;
  last_order_uuid?: string | null;
  last_order_status?: string | null;
  last_processed_candle_time_utc?: string | null;
};

type AutoLivePilotStatus = {
  session?: AutoLivePilotSession | null;
  exchange: string;
  market: string;
  live_trading_enabled: boolean;
  live_auto_trading_enabled: boolean;
  auto_pilot_enabled: boolean;
  emergency_stop: boolean;
  api_key_loaded: boolean;
  min_auto_order_krw: number;
  max_auto_order_krw: number;
  max_orders_per_day: number;
  auto_cancel_after_seconds: number;
  order_type: string;
  ok?: boolean;
  message?: string;
};

type LiveStrategySession = {
  id: number;
  exchange: string;
  market: string;
  candidate_strategy_id: number;
  strategy_name: string;
  strategy_parameters: Record<string, number>;
  status: "READY" | "RUNNING" | "PAUSED" | "STOPPED" | "ERROR" | "EMERGENCY_STOPPED";
  auto_enabled: boolean;
  max_order_krw: number;
  max_orders_per_day: number;
  orders_created_today: number;
  current_open_order_uuid?: string | null;
  current_position_id?: number | null;
  last_signal?: string | null;
  last_signal_time_utc?: string | null;
  last_risk_result?: string | null;
  last_order_status?: string | null;
  last_order_time_utc?: string | null;
  last_processed_candle_time_utc?: string | null;
};

type LivePosition = {
  id: number;
  status: string;
  entry_price: number;
  entry_volume: number;
  entry_amount_krw: number;
  current_price: number;
  unrealized_pnl: number;
  realized_pnl: number;
  stop_loss_price: number;
  take_profit_price: number;
  opened_at?: string | null;
};

type LiveStrategyStatus = {
  session?: LiveStrategySession | null;
  position?: LivePosition | null;
  exchange: string;
  market: string;
  current_mode: string;
  live_trading_enabled: boolean;
  live_auto_trading_enabled: boolean;
  auto_strategy_pilot_enabled: boolean;
  emergency_stop: boolean;
  api_key_loaded: boolean;
  max_order_krw: number;
  max_orders_per_day: number;
  max_open_position_count: number;
  cancel_unfilled_after_seconds: number;
  entry_price_offset_percent: number;
  exit_enabled: boolean;
  market_order_enabled: boolean;
  ok?: boolean;
  message?: string;
};

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

const STRATEGY_LABELS: Record<Strategy, string> = {
  ma_cross: "이동평균 크로스",
  rsi: "RSI",
  volatility_breakout: "변동성 돌파"
};

const STRATEGY_BADGES: Record<Strategy, string> = {
  ma_cross: "MA",
  rsi: "RSI",
  volatility_breakout: "VB"
};

const FIELD_LABELS: Record<string, string> = {
  short_window: "단기 이동평균",
  long_window: "장기 이동평균",
  rsi_period: "RSI 기간",
  buy_threshold: "매수 기준",
  sell_threshold: "매도 기준",
  exit_rule: "청산 규칙",
  period: "기간",
  oversold: "과매도 기준",
  overbought: "과매수 기준",
  k: "돌파 계수",
  exit_window: "청산 이동평균",
  initial_cash: "초기 원화 잔고",
  position_size: "진입 비중",
  fee_rate: "수수료율",
  max_order_amount: "1회 최대 주문 금액",
  daily_max_loss_rate: "하루 최대 손실률",
  max_position_ratio: "최대 보유 비중",
  consecutive_loss_limit: "연속 손실 제한",
  volatility_block_rate: "급등락 차단 기준",
  slippage_rate: "슬리피지율",
  min_volume: "최소 거래량"
};

const SIGNAL_REASON_LABELS: Record<string, string> = {
  short_ma_cross_up: "단기 이동평균이 장기 이동평균을 상향 돌파",
  short_ma_cross_down: "단기 이동평균이 장기 이동평균을 하향 돌파",
  rsi_oversold_cross: "RSI 과매도 구간 진입",
  rsi_overbought_cross: "RSI 과매수 구간 진입",
  breakout_target_hit: "변동성 돌파 목표가 도달",
  close_below_exit_ma: "종가가 청산 이동평균 아래로 하락",
  daily_loss_limit: "하루 최대 손실률 초과",
  consecutive_loss_limit: "연속 손실 제한 도달",
  volatility_block: "급등락 구간 진입 차단",
  position_or_cash_limit: "보유 비중 또는 현금 한도 초과",
  BLOCKED_BY_MAX_ORDER_AMOUNT: "1회 최대 주문 금액 조건 차단",
  BLOCKED_BY_MAX_POSITION_RATIO: "최대 보유 비중 조건 차단",
  BLOCKED_BY_DAILY_LOSS_LIMIT: "KST 기준 하루 최대 손실률 조건 차단",
  BLOCKED_BY_CONSECUTIVE_LOSS_LIMIT: "연속 손실 제한 조건 차단",
  BLOCKED_BY_INSUFFICIENT_BALANCE: "가상 원화 잔고 부족",
  BLOCKED_BY_INSUFFICIENT_POSITION: "가상 보유 수량 부족",
  BLOCKED_BY_VOLATILITY_FILTER: "급등락 구간 신규 진입 차단",
  BLOCKED_BY_LOW_VOLUME: "거래량 부족으로 신규 진입 차단",
  NO_NEW_CANDLE: "완성된 새 캔들 없음",
  ACTIVE: "리스크 감시 활성",
  STOPPED_BY_USER: "사용자 중지",
  STOPPED_ON_SERVER_RESTART: "서버 재시작으로 안전 중지"
};

const DEFAULT_SETTINGS: Record<Strategy, Record<string, number>> = {
  ma_cross: { short_window: 5, long_window: 20 },
  rsi: { rsi_period: 14, buy_threshold: 30, sell_threshold: 70 },
  volatility_breakout: { k: 0.5, exit_rule: 10 }
};

const DEFAULT_BACKTEST_RISK = {
  initial_cash: 10_000_000,
  position_size: 1,
  fee_rate: 0.0005,
  slippage_rate: 0.0005
};

const DEFAULT_PAPER_RISK = {
  initial_cash: 1_000_000,
  max_order_amount: 100_000,
  daily_max_loss_rate: 0.03,
  max_position_ratio: 0.5,
  consecutive_loss_limit: 3,
  volatility_block_rate: 0.03,
  min_volume: 0,
  fee_rate: 0.0005,
  slippage_rate: 0.0005
};

const KST_OFFSET_MS = 9 * 60 * 60 * 1000;

function parseUtcDate(value: string) {
  const trimmed = value.trim();
  const normalized = trimmed.includes("T") ? trimmed : trimmed.replace(" ", "T");
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(normalized);
  return new Date(hasTimezone ? normalized : `${normalized}Z`);
}

function pad(value: number) {
  return String(value).padStart(2, "0");
}

function toChartTime(value: string): Time {
  return Math.floor(parseUtcDate(value).getTime() / 1000) as Time;
}

function formatKstDateTime(value?: string | null) {
  if (!value) return "-";
  const date = parseUtcDate(value);
  if (Number.isNaN(date.getTime())) return "-";
  const kstDate = new Date(date.getTime() + KST_OFFSET_MS);
  const year = kstDate.getUTCFullYear();
  const month = pad(kstDate.getUTCMonth() + 1);
  const day = pad(kstDate.getUTCDate());
  const hour = pad(kstDate.getUTCHours());
  const minute = pad(kstDate.getUTCMinutes());
  return `${year}-${month}-${day} ${hour}:${minute} KST`;
}

function formatKstShort(value?: string | null) {
  if (!value) return "-";
  const date = parseUtcDate(value);
  if (Number.isNaN(date.getTime())) return "-";
  const kstDate = new Date(date.getTime() + KST_OFFSET_MS);
  const month = pad(kstDate.getUTCMonth() + 1);
  const day = pad(kstDate.getUTCDate());
  const hour = pad(kstDate.getUTCHours());
  const minute = pad(kstDate.getUTCMinutes());
  return `${month}-${day} ${hour}:${minute} KST`;
}

function formatKstChartTime(time: Time) {
  if (typeof time !== "number") return "";
  const kstDate = new Date(time * 1000 + KST_OFFSET_MS);
  const month = pad(kstDate.getUTCMonth() + 1);
  const day = pad(kstDate.getUTCDate());
  const hour = pad(kstDate.getUTCHours());
  const minute = pad(kstDate.getUTCMinutes());
  return `${month}-${day} ${hour}:${minute}`;
}

function formatPercent(value?: number) {
  if (value == null || !Number.isFinite(value)) return "-";
  return `${(value * 100).toFixed(2)}%`;
}

function formatKrw(value?: number) {
  if (value == null || !Number.isFinite(value)) return "-";
  return new Intl.NumberFormat("ko-KR", {
    style: "currency",
    currency: "KRW",
    maximumFractionDigits: 0
  }).format(value);
}

function formatNumber(value?: number, digits = 8) {
  if (value == null || !Number.isFinite(value)) return "-";
  return value.toFixed(digits);
}

function formatDecimal(value?: number, digits = 2) {
  if (value === Infinity) return "∞";
  if (value == null || !Number.isFinite(value)) return "-";
  return value.toFixed(digits);
}

function formatTimeframe(unit?: number) {
  if (!unit) return "-";
  return unit === 60 ? "1h" : `${unit}m`;
}

function toKstDateTimeInput(date: Date) {
  const kstDate = new Date(date.getTime() + KST_OFFSET_MS);
  const year = kstDate.getUTCFullYear();
  const month = pad(kstDate.getUTCMonth() + 1);
  const day = pad(kstDate.getUTCDate());
  const hour = pad(kstDate.getUTCHours());
  const minute = pad(kstDate.getUTCMinutes());
  return `${year}-${month}-${day}T${hour}:${minute}`;
}

function kstInputToUtcIso(value: string) {
  const [datePart, timePart] = value.split("T");
  const [year, month, day] = datePart.split("-").map(Number);
  const [hour, minute] = timePart.split(":").map(Number);
  return new Date(Date.UTC(year, month - 1, day, hour - 9, minute, 0)).toISOString();
}

function formatHoldingTime(minutes?: number) {
  if (minutes == null || !Number.isFinite(minutes)) return "-";
  if (minutes < 60) return `${minutes.toFixed(1)}분`;
  return `${(minutes / 60).toFixed(2)}시간`;
}

function normalizeApiCandles(response: CandleResponse): Candle[] {
  return response.candles.map((candle) => ({
    time: candle.candle_time_utc,
    open: candle.opening_price,
    high: candle.high_price,
    low: candle.low_price,
    close: candle.trade_price,
    volume: candle.candle_acc_trade_volume
  }));
}

function toneClass(tone: Tone) {
  return {
    neutral: "text-slate-100",
    green: "text-terminal-green",
    red: "text-terminal-red",
    amber: "text-terminal-amber",
    cyan: "text-terminal-cyan"
  }[tone];
}

function pnlTone(value?: number | null): Tone {
  if (value == null || value === 0) return "neutral";
  return value > 0 ? "green" : "red";
}

function statusTone(status?: string): Tone {
  if (status === "RUNNING") return "green";
  if (status === "ERROR") return "red";
  if (status === "STOPPED") return "amber";
  if (status === "EMERGENCY_STOPPED") return "red";
  if (status === "LIVE_MANUAL_ONLY") return "red";
  if (status === "LIVE_LOCKED") return "amber";
  return "neutral";
}

function liveModeLabel(mode?: string) {
  return {
    PAPER: "PAPER MODE",
    LIVE_LOCKED: "LIVE LOCKED",
    LIVE_ARMED: "LIVE ARMED",
    LIVE_MANUAL_ONLY: "LIVE MANUAL ONLY",
    EMERGENCY_STOPPED: "EMERGENCY STOPPED"
  }[mode ?? ""] ?? "PAPER MODE";
}

function liveModeBannerClass(mode?: string) {
  if (mode === "LIVE_MANUAL_ONLY" || mode === "LIVE_ARMED") return "border-terminal-red bg-[#2a0810] text-terminal-red";
  if (mode === "EMERGENCY_STOPPED") return "border-terminal-red bg-[#3a0712] text-terminal-red";
  if (mode === "LIVE_LOCKED") return "border-terminal-amber bg-[#211a09] text-terminal-amber";
  return "border-terminal-cyan bg-[#071b22] text-terminal-cyan";
}

function formatSessionStatus(status?: string) {
  return {
    EMPTY: "대기",
    RUNNING: "실행 중",
    STOPPED: "중지됨",
    ERROR: "오류",
    COMPLETED: "완료"
  }[status ?? ""] ?? "-";
}

function formatRiskStatus(status?: string) {
  if (!status) return "-";
  if (status === "ACTIVE") return "활성";
  if (status === "INACTIVE") return "비활성";
  if (status === "PASS") return "통과";
  return SIGNAL_REASON_LABELS[status] ?? status;
}

function formatLiveOrderStatus(status?: string) {
  return {
    PREVIEWED: "미리보기",
    SUBMITTED: "제출됨",
    FILLED: "체결",
    PARTIALLY_FILLED: "부분 체결",
    CANCELED: "취소",
    FAILED: "실패",
    BLOCKED: "차단"
  }[status ?? ""] ?? status ?? "-";
}

function formatFieldLabel(key: string) {
  return FIELD_LABELS[key] ?? key;
}

function formatSignalReason(reason?: string | null) {
  if (!reason) return "-";
  return SIGNAL_REASON_LABELS[reason] ?? reason;
}

function formatParameters(parameters: Record<string, number>) {
  return Object.entries(parameters)
    .map(([key, value]) => `${formatFieldLabel(key)}=${value}`)
    .join(", ");
}

function formatPeriodLabel(period: string) {
  return {
    "7d": "최근 7일",
    "30d": "최근 30일",
    "90d": "최근 90일",
    "180d": "최근 180일",
    custom: "사용자 지정"
  }[period] ?? period;
}

function formatRuntimeDuration(startedAt?: string | null, stoppedAt?: string | null) {
  if (!startedAt) return "-";
  const start = parseUtcDate(startedAt);
  const end = stoppedAt ? parseUtcDate(stoppedAt) : new Date();
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return "-";
  const seconds = Math.max(Math.floor((end.getTime() - start.getTime()) / 1000), 0);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours > 0) return `${hours}시간 ${minutes}분`;
  return `${minutes}분`;
}

function MetricCard({
  label,
  value,
  tone = "neutral",
  title
}: {
  label: string;
  value: React.ReactNode;
  tone?: Tone;
  title?: string;
}) {
  return (
    <div className="border border-terminal-line bg-terminal-panel2 px-3 py-2" title={title}>
      <div className="text-[11px] uppercase text-slate-500">{label}</div>
      <div className={`mt-1 min-h-7 truncate text-lg font-semibold ${toneClass(tone)}`}>{value}</div>
    </div>
  );
}

function StatusBadge({ value, tone = "neutral" }: { value: string; tone?: Tone }) {
  return <span className={`badge ${toneClass(tone)}`}>{value}</span>;
}

function SideBadge({ side }: { side: "BUY" | "SELL" }) {
  return (
    <span className={`badge ${side === "BUY" ? "border-terminal-green text-terminal-green" : "border-terminal-red text-terminal-red"}`}>
      {side}
    </span>
  );
}

function NumberField({
  label,
  value,
  step,
  onChange
}: {
  label: string;
  value: number;
  step: string;
  onChange: (value: number) => void;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input type="number" step={step} value={value} onChange={(event) => onChange(Number(event.target.value))} />
    </label>
  );
}

function ConfirmationField({
  label,
  value,
  phrase,
  onChange
}: {
  label: string;
  value: string;
  phrase: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="control w-full">
      <span>{label}</span>
      <div className="confirm-input-shell">
        <span className="confirm-input-ghost">{phrase}</span>
        <input
          className="confirm-input"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          aria-label={label}
          autoComplete="off"
          spellCheck={false}
        />
      </div>
    </label>
  );
}

function ChartPanel({ candles, signals }: { candles: Candle[]; signals: Signal[] }) {
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const chartRef = React.useRef<IChartApi | null>(null);
  const seriesRef = React.useRef<ISeriesApi<"Candlestick"> | null>(null);
  const markersRef = React.useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const previousDataRef = React.useRef<ChartCandleData[]>([]);
  const didFitContentRef = React.useRef(false);

  React.useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#05070b" },
        textColor: "#8fa3bd"
      },
      grid: {
        vertLines: { color: "#101827" },
        horzLines: { color: "#101827" }
      },
      rightPriceScale: { borderColor: "#1d2a3d" },
      localization: {
        locale: "ko-KR",
        timeFormatter: (time: Time) => `${formatKstChartTime(time)} KST`
      },
      timeScale: {
        borderColor: "#1d2a3d",
        timeVisible: true,
        tickMarkFormatter: (time: Time) => formatKstChartTime(time)
      },
      height: 470
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#23d18b",
      downColor: "#ff5c7a",
      borderUpColor: "#23d18b",
      borderDownColor: "#ff5c7a",
      wickUpColor: "#23d18b",
      wickDownColor: "#ff5c7a"
    });
    chartRef.current = chart;
    seriesRef.current = series;
    markersRef.current = createSeriesMarkers(series, []);
    const resize = () => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
      }
    };
    resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.remove();
    };
  }, []);

  React.useEffect(() => {
    if (!seriesRef.current || !chartRef.current || candles.length === 0) return;
    const nextData = candles.map((candle) => ({
      time: toChartTime(candle.time),
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close
    }));
    const previousData = previousDataRef.current;
    const timeScale = chartRef.current.timeScale();
    const visibleRange = didFitContentRef.current ? timeScale.getVisibleLogicalRange() : null;
    const addedBars = Math.max(nextData.length - previousData.length, 0);
    const wasAtRightEdge = Boolean(
      visibleRange && previousData.length > 0 && visibleRange.to >= previousData.length - 1.5
    );
    const hasSameTimelinePrefix =
      previousData.length > 0 &&
      nextData.length >= previousData.length &&
      previousData.every((item, index) => nextData[index]?.time === item.time);

    if (previousData.length === 0) {
      seriesRef.current.setData(nextData);
      timeScale.fitContent();
      didFitContentRef.current = true;
    } else if (hasSameTimelinePrefix) {
      const firstChangedIndex = previousData.findIndex((item, index) => {
        const next = nextData[index];
        return (
          !next ||
          item.open !== next.open ||
          item.high !== next.high ||
          item.low !== next.low ||
          item.close !== next.close
        );
      });
      const startIndex = firstChangedIndex === -1 ? previousData.length : firstChangedIndex;
      for (let index = startIndex; index < nextData.length; index += 1) {
        seriesRef.current.update(nextData[index], index < nextData.length - 1);
      }
    } else {
      seriesRef.current.setData(nextData);
    }

    if (visibleRange) {
      const range = wasAtRightEdge && addedBars > 0
        ? { from: visibleRange.from + addedBars, to: visibleRange.to + addedBars }
        : visibleRange;
      timeScale.setVisibleLogicalRange(range);
    }
    previousDataRef.current = nextData;
    markersRef.current?.setMarkers(
      signals.map((signal) => ({
        time: toChartTime(signal.time),
        position: signal.signal === "BUY" ? "belowBar" : "aboveBar",
        color: signal.signal === "BUY" ? "#23d18b" : "#ff5c7a",
        shape: signal.signal === "BUY" ? "arrowUp" : "arrowDown",
        text: signal.signal
      }))
    );
  }, [candles, signals]);

  return <div ref={containerRef} className="h-[470px] w-full" />;
}

function PnlGraph({ points, initialCash }: { points: EquityPoint[]; initialCash: number }) {
  const width = 520;
  const height = 150;
  if (points.length < 2) {
    return <div className="flex h-[150px] items-center justify-center text-sm text-slate-500">페이퍼 트레이딩 평가 자산 기록을 기다리는 중입니다</div>;
  }
  const values = points.map((point) => point.equity - initialCash);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(max - min, 1);
  const path = values
    .map((value, index) => {
      const x = (index / (values.length - 1)) * width;
      const y = height - ((value - min) / span) * (height - 18) - 9;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const last = values[values.length - 1];
  return (
    <div className="h-[150px] w-full">
      <svg viewBox={`0 0 ${width} ${height}`} className="h-full w-full" role="img" aria-label="페이퍼 트레이딩 손익 그래프">
        <line x1="0" y1={height / 2} x2={width} y2={height / 2} stroke="#1d2a3d" strokeDasharray="4 4" />
        <polyline points={path} fill="none" stroke={last >= 0 ? "#23d18b" : "#ff5c7a"} strokeWidth="3" />
      </svg>
    </div>
  );
}

function BacktestEquityGraph({ points }: { points: EquityPoint[] }) {
  const width = 620;
  const height = 190;
  if (points.length < 2) {
    return <div className="flex h-[190px] items-center justify-center text-sm text-slate-500">백테스트 equity curve를 기다리는 중입니다</div>;
  }
  const equities = points.map((point) => point.equity);
  const drawdowns = points.map((point) => point.drawdown ?? 0);
  const minEquity = Math.min(...equities);
  const maxEquity = Math.max(...equities);
  const equitySpan = Math.max(maxEquity - minEquity, 1);
  const minDrawdown = Math.min(...drawdowns, 0);
  const equityPath = equities
    .map((value, index) => {
      const x = (index / (equities.length - 1)) * width;
      const y = height * 0.58 - ((value - minEquity) / equitySpan) * (height * 0.48);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const drawdownPath = drawdowns
    .map((value, index) => {
      const x = (index / (drawdowns.length - 1)) * width;
      const y = height * 0.7 + (Math.abs(value) / Math.max(Math.abs(minDrawdown), 0.001)) * (height * 0.24);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <div className="h-[190px] w-full">
      <svg viewBox={`0 0 ${width} ${height}`} className="h-full w-full" role="img" aria-label="백테스트 평가자산과 낙폭 그래프">
        <line x1="0" y1={height * 0.64} x2={width} y2={height * 0.64} stroke="#1d2a3d" />
        <polyline points={equityPath} fill="none" stroke="#18e0c8" strokeWidth="3" />
        <polyline points={drawdownPath} fill="none" stroke="#ff5c7a" strokeWidth="2" />
      </svg>
      <div className="mt-1 flex gap-4 text-xs text-slate-500">
        <span className="text-terminal-cyan">평가자산</span>
        <span className="text-terminal-red">Drawdown</span>
      </div>
    </div>
  );
}

function App() {
  const [market] = React.useState("KRW-BTC");
  const [unit, setUnit] = React.useState(1);
  const [strategy, setStrategy] = React.useState<Strategy>("ma_cross");
  const [settings, setSettings] = React.useState(DEFAULT_SETTINGS.ma_cross);
  const [strategySettings, setStrategySettings] = React.useState<Record<Strategy, Record<string, number>>>(DEFAULT_SETTINGS);
  const [backtestRisk, setBacktestRisk] = React.useState(DEFAULT_BACKTEST_RISK);
  const [paperRisk, setPaperRisk] = React.useState(DEFAULT_PAPER_RISK);
  const [result, setResult] = React.useState<BacktestResponse | null>(null);
  const [comparisonRows, setComparisonRows] = React.useState<StrategyComparisonRow[]>([]);
  const [comparisonResults, setComparisonResults] = React.useState<BacktestResponse[]>([]);
  const [backtestCandleCount, setBacktestCandleCount] = React.useState(0);
  const [startDateKst, setStartDateKst] = React.useState(() => toKstDateTimeInput(new Date(Date.now() - 6 * 60 * 60 * 1000)));
  const [endDateKst, setEndDateKst] = React.useState(() => toKstDateTimeInput(new Date()));
  const [validationStrategy, setValidationStrategy] = React.useState<Strategy>("ma_cross");
  const [validationRows, setValidationRows] = React.useState<ValidationRow[]>([]);
  const [validationLoading, setValidationLoading] = React.useState(false);
  const [validationError, setValidationError] = React.useState<string | null>(null);
  const [candidateStrategies, setCandidateStrategies] = React.useState<CandidateStrategy[]>([]);
  const [candidateError, setCandidateError] = React.useState<string | null>(null);
  const [chartCandles, setChartCandles] = React.useState<Candle[]>([]);
  const [chartUpdatedAt, setChartUpdatedAt] = React.useState<string | null>(null);
  const [paper, setPaper] = React.useState<PaperResponse>({ status: "EMPTY" });
  const [forwardPaper, setForwardPaper] = React.useState<ForwardPaperResponse>({ status: "EMPTY", mode: "FORWARD_PAPER" });
  const [liveExchange, setLiveExchange] = React.useState<Exchange>("upbit");
  const [liveStatus, setLiveStatus] = React.useState<LiveStatus | null>(null);
  const [liveBalances, setLiveBalances] = React.useState<LiveBalances | null>(null);
  const [liveOrderChance, setLiveOrderChance] = React.useState<LiveOrderChance | null>(null);
  const [liveOrders, setLiveOrders] = React.useState<LiveOrderLog[]>([]);
  const [livePreview, setLivePreview] = React.useState<LiveOrderPreview | null>(null);
  const [autoPilot, setAutoPilot] = React.useState<AutoLivePilotStatus | null>(null);
  const [autoPilotCandidateId, setAutoPilotCandidateId] = React.useState<number | "">("");
  const [autoPilotAmount, setAutoPilotAmount] = React.useState(10000);
  const [liveStrategy, setLiveStrategy] = React.useState<LiveStrategyStatus | null>(null);
  const [liveStrategyCandidateId, setLiveStrategyCandidateId] = React.useState<number | "">("");
  const [liveOrderForm, setLiveOrderForm] = React.useState({
    exchange: "upbit" as Exchange,
    market: "KRW-BTC",
    side: "BUY" as "BUY" | "SELL",
    order_type: "LIMIT" as "LIMIT" | "MARKET",
    price: 0,
    amount_krw: 5000,
    volume: 0
  });
  const [liveArmAcknowledged, setLiveArmAcknowledged] = React.useState(false);
  const [liveArmConfirmation, setLiveArmConfirmation] = React.useState("");
  const [livePlaceConfirmation, setLivePlaceConfirmation] = React.useState("");
  const [liveEmergencyResetConfirmation, setLiveEmergencyResetConfirmation] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [paperLoading, setPaperLoading] = React.useState(false);
  const [forwardLoading, setForwardLoading] = React.useState(false);
  const [liveLoading, setLiveLoading] = React.useState(false);
  const [autoPilotLoading, setAutoPilotLoading] = React.useState(false);
  const [liveStrategyLoading, setLiveStrategyLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [paperError, setPaperError] = React.useState<string | null>(null);
  const [forwardError, setForwardError] = React.useState<string | null>(null);
  const [liveError, setLiveError] = React.useState<string | null>(null);
  const [autoPilotError, setAutoPilotError] = React.useState<string | null>(null);
  const [liveStrategyError, setLiveStrategyError] = React.useState<string | null>(null);

  React.useEffect(() => {
    setSettings(strategySettings[strategy]);
  }, [strategy, strategySettings]);

  React.useEffect(() => {
    if (!autoPilotCandidateId && candidateStrategies.length > 0) {
      const firstBtc = candidateStrategies.find((candidate) => candidate.market === "KRW-BTC") ?? candidateStrategies[0];
      setAutoPilotCandidateId(firstBtc.id);
    }
    if (!liveStrategyCandidateId && candidateStrategies.length > 0) {
      const firstBtc = candidateStrategies.find((candidate) => candidate.market === "KRW-BTC") ?? candidateStrategies[0];
      setLiveStrategyCandidateId(firstBtc.id);
    }
  }, [autoPilotCandidateId, candidateStrategies, liveStrategyCandidateId]);

  const fetchLatestPaper = React.useCallback(async () => {
    const response = await fetch(`${API_BASE}/api/paper-trading/live/latest`);
    if (response.ok) {
      setPaper(await response.json());
    }
  }, []);

  const fetchChartCandles = React.useCallback(async () => {
    const params = new URLSearchParams({
      market,
      unit: String(unit),
      count: "300"
    });
    const response = await fetch(`${API_BASE}/api/candles?${params.toString()}`);
    if (response.ok) {
      const body = (await response.json()) as CandleResponse;
      setChartCandles(normalizeApiCandles(body));
      setChartUpdatedAt(new Date().toISOString());
    }
  }, [market, unit]);

  const fetchCandidates = React.useCallback(async () => {
    const response = await fetch(`${API_BASE}/api/candidate-strategies`);
    if (response.ok) {
      const body = (await response.json()) as { candidates: CandidateStrategy[] };
      setCandidateStrategies(body.candidates);
    }
  }, []);

  const fetchForwardPaper = React.useCallback(async () => {
    const response = await fetch(`${API_BASE}/api/forward-paper/latest`);
    if (response.ok) {
      setForwardPaper(await response.json());
    }
  }, []);

  const fetchLiveStatus = React.useCallback(async (exchange?: Exchange) => {
    const params = exchange ? `?${new URLSearchParams({ exchange }).toString()}` : "";
    const response = await fetch(`${API_BASE}/api/live/status${params}`);
    if (response.ok) {
      const body = (await response.json()) as LiveStatus;
      setLiveStatus(body);
      if (!exchange && body.exchange) {
        setLiveExchange(body.exchange);
        setLiveOrderForm((prev) => ({ ...prev, exchange: body.exchange as Exchange }));
      }
    }
  }, []);

  const fetchLiveBalances = React.useCallback(async () => {
    setLiveLoading(true);
    setLiveError(null);
    try {
      const params = new URLSearchParams({ exchange: liveExchange });
      const response = await fetch(`${API_BASE}/api/live/balances?${params.toString()}`);
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail ?? "실계좌 잔고 조회에 실패했습니다.");
      }
      const body = (await response.json()) as LiveBalances;
      setLiveBalances(body);
      setLiveStatus(body);
    } catch (err) {
      setLiveError(err instanceof Error ? err.message : "알 수 없는 잔고 조회 오류가 발생했습니다.");
    } finally {
      setLiveLoading(false);
    }
  }, [liveExchange]);

  const fetchLiveOrderChance = React.useCallback(async () => {
    setLiveLoading(true);
    setLiveError(null);
    try {
      const params = new URLSearchParams({
        market: liveOrderForm.market,
        exchange: liveExchange
      });
      const response = await fetch(`${API_BASE}/api/live-trading/order-chance?${params.toString()}`);
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail ?? "주문 가능 정보 조회에 실패했습니다.");
      }
      const body = (await response.json()) as LiveOrderChance;
      setLiveOrderChance(body);
      setLiveStatus(body);
      if (body.order_chance_error) setLiveError(body.order_chance_error);
    } catch (err) {
      setLiveError(err instanceof Error ? err.message : "알 수 없는 주문 가능 정보 조회 오류가 발생했습니다.");
    } finally {
      setLiveLoading(false);
    }
  }, [liveExchange, liveOrderForm.market]);

  const fetchLiveOrders = React.useCallback(async () => {
    const response = await fetch(`${API_BASE}/api/live-orders`);
    if (response.ok) {
      const body = (await response.json()) as { orders: LiveOrderLog[] } & LiveStatus;
      setLiveOrders(body.orders);
      if (body.exchange === liveExchange) setLiveStatus(body);
    }
  }, [liveExchange]);

  const fetchAutoPilotStatus = React.useCallback(async () => {
    const response = await fetch(`${API_BASE}/api/auto-live-pilot/status`);
    if (response.ok) {
      setAutoPilot(await response.json());
    }
  }, []);

  const fetchLiveStrategyStatus = React.useCallback(async () => {
    const response = await fetch(`${API_BASE}/api/live-strategy-pilot/status`);
    if (response.ok) {
      setLiveStrategy(await response.json());
    }
  }, []);

  const startAutoPilot = React.useCallback(async () => {
    if (!autoPilotCandidateId) return;
    setAutoPilotLoading(true);
    setAutoPilotError(null);
    try {
      const response = await fetch(`${API_BASE}/api/auto-live-pilot/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          candidate_strategy_id: autoPilotCandidateId,
          order_amount_krw: Math.min(Math.max(autoPilotAmount, autoPilot?.min_auto_order_krw ?? 10000), Math.max(autoPilot?.max_auto_order_krw ?? 10000, autoPilot?.min_auto_order_krw ?? 10000)),
          confirmation: "AUTO PILOT ENABLE",
          order_confirmation: "PLACE AUTO LIVE ORDER"
        })
      });
      const body = (await response.json()) as AutoLivePilotStatus;
      setAutoPilot(body);
      if (!body.ok) setAutoPilotError(body.message ?? "Auto Pilot 시작이 차단되었습니다.");
      await fetchLiveOrders();
    } catch (err) {
      setAutoPilotError(err instanceof Error ? err.message : "Auto Pilot 시작 오류가 발생했습니다.");
    } finally {
      setAutoPilotLoading(false);
    }
  }, [autoPilot?.max_auto_order_krw, autoPilot?.min_auto_order_krw, autoPilotAmount, autoPilotCandidateId, fetchLiveOrders]);

  const stopAutoPilot = React.useCallback(async () => {
    setAutoPilotLoading(true);
    try {
      const response = await fetch(`${API_BASE}/api/auto-live-pilot/stop`, { method: "POST" });
      if (response.ok) setAutoPilot(await response.json());
    } finally {
      setAutoPilotLoading(false);
    }
  }, []);

  const cancelAutoPilotOpenOrder = React.useCallback(async () => {
    setAutoPilotLoading(true);
    setAutoPilotError(null);
    try {
      const response = await fetch(`${API_BASE}/api/auto-live-pilot/cancel-open-order`, { method: "POST" });
      const body = (await response.json()) as AutoLivePilotStatus;
      setAutoPilot(body);
      if (!body.ok) setAutoPilotError(body.message ?? "오픈 주문 취소에 실패했습니다.");
      await fetchLiveOrders();
    } finally {
      setAutoPilotLoading(false);
    }
  }, [fetchLiveOrders]);

  const toggleAutoPilot = React.useCallback(async () => {
    if (autoPilot?.session?.status === "RUNNING" || autoPilot?.session?.status === "READY") {
      await stopAutoPilot();
      return;
    }
    await startAutoPilot();
  }, [autoPilot?.session?.status, startAutoPilot, stopAutoPilot]);

  const startLiveStrategy = React.useCallback(async () => {
    if (!liveStrategyCandidateId) return;
    setLiveStrategyLoading(true);
    setLiveStrategyError(null);
    try {
      const response = await fetch(`${API_BASE}/api/live-strategy-pilot/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          candidate_strategy_id: liveStrategyCandidateId,
          confirmation: "AUTO STRATEGY ENABLE",
          order_confirmation: "PLACE AUTO LIVE ORDER"
        })
      });
      const body = (await response.json()) as LiveStrategyStatus;
      setLiveStrategy(body);
      if (!body.ok) setLiveStrategyError(body.message ?? "Auto Strategy start blocked.");
      await fetchLiveOrders();
    } catch (err) {
      setLiveStrategyError(err instanceof Error ? err.message : "Auto Strategy start failed.");
    } finally {
      setLiveStrategyLoading(false);
    }
  }, [fetchLiveOrders, liveStrategyCandidateId]);

  const stopLiveStrategy = React.useCallback(async () => {
    setLiveStrategyLoading(true);
    try {
      const response = await fetch(`${API_BASE}/api/live-strategy-pilot/stop`, { method: "POST" });
      if (response.ok) setLiveStrategy(await response.json());
    } finally {
      setLiveStrategyLoading(false);
    }
  }, []);

  const cancelLiveStrategyOpenOrder = React.useCallback(async () => {
    setLiveStrategyLoading(true);
    setLiveStrategyError(null);
    try {
      const response = await fetch(`${API_BASE}/api/live-strategy-pilot/cancel-open-order`, { method: "POST" });
      const body = (await response.json()) as LiveStrategyStatus;
      setLiveStrategy(body);
      if (!body.ok) setLiveStrategyError(body.message ?? "Cancel open Auto Strategy order failed.");
      await fetchLiveOrders();
    } finally {
      setLiveStrategyLoading(false);
    }
  }, [fetchLiveOrders]);

  const toggleLiveStrategy = React.useCallback(async () => {
    const status = liveStrategy?.session?.status;
    if (status === "RUNNING" || status === "READY") {
      await stopLiveStrategy();
      return;
    }
    await startLiveStrategy();
  }, [liveStrategy?.session?.status, startLiveStrategy, stopLiveStrategy]);

  React.useEffect(() => {
    setLiveOrderForm((prev) => ({ ...prev, exchange: liveExchange }));
    setLiveBalances(null);
    setLiveOrderChance(null);
    setLivePreview(null);
    setLiveError(null);
    void fetchLiveStatus(liveExchange);
    void fetchAutoPilotStatus();
    void fetchLiveStrategyStatus();
  }, [fetchAutoPilotStatus, fetchLiveStatus, fetchLiveStrategyStatus, liveExchange]);

  const armLiveTrading = React.useCallback(async () => {
    setLiveLoading(true);
    setLiveError(null);
    try {
      const response = await fetch(`${API_BASE}/api/live-trading/arm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ acknowledged: liveArmAcknowledged, confirmation: liveArmConfirmation })
      });
      const body = await response.json();
      setLiveStatus(body);
      if (!body.ok) setLiveError(body.message ?? "실거래 잠금 해제에 실패했습니다.");
    } catch (err) {
      setLiveError(err instanceof Error ? err.message : "알 수 없는 실거래 잠금 해제 오류가 발생했습니다.");
    } finally {
      setLiveLoading(false);
    }
  }, [liveArmAcknowledged, liveArmConfirmation]);

  const lockLiveTradingMode = React.useCallback(async () => {
    const response = await fetch(`${API_BASE}/api/live-trading/lock`, { method: "POST" });
    if (response.ok) setLiveStatus(await response.json());
  }, []);

  const emergencyStopLiveTrading = React.useCallback(async () => {
    const response = await fetch(`${API_BASE}/api/live-trading/emergency-stop`, { method: "POST" });
    if (response.ok) setLiveStatus(await response.json());
    await fetchLiveOrders();
  }, [fetchLiveOrders]);

  const resetEmergencyStop = React.useCallback(async () => {
    setLiveLoading(true);
    setLiveError(null);
    try {
      const response = await fetch(`${API_BASE}/api/live-trading/reset-emergency`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmation: liveEmergencyResetConfirmation })
      });
      const body = await response.json();
      setLiveStatus(body);
      if (!body.ok) {
        setLiveError(body.message ?? "Emergency Stop 해제에 실패했습니다.");
      } else {
        setLiveEmergencyResetConfirmation("");
      }
      await fetchLiveOrders();
    } catch (err) {
      setLiveError(err instanceof Error ? err.message : "알 수 없는 Emergency Stop 해제 오류가 발생했습니다.");
    } finally {
      setLiveLoading(false);
    }
  }, [fetchLiveOrders, liveEmergencyResetConfirmation]);

  const previewLiveOrder = React.useCallback(async () => {
    setLiveLoading(true);
    setLiveError(null);
    setLivePreview(null);
    try {
      const response = await fetch(`${API_BASE}/api/live-orders/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...liveOrderForm, exchange: liveExchange })
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "주문 미리보기에 실패했습니다.");
      setLiveStatus(body);
      setLivePreview(body.preview);
      await fetchLiveOrders();
    } catch (err) {
      setLiveError(err instanceof Error ? err.message : "알 수 없는 주문 미리보기 오류가 발생했습니다.");
    } finally {
      setLiveLoading(false);
    }
  }, [fetchLiveOrders, liveExchange, liveOrderForm]);

  const placeLiveOrder = React.useCallback(async () => {
    if (!livePreview) return;
    setLiveLoading(true);
    setLiveError(null);
    try {
      const response = await fetch(`${API_BASE}/api/live-orders/place`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ request_id: livePreview.request_id, final_confirmation: livePlaceConfirmation })
      });
      const body = await response.json();
      setLiveStatus(body);
      if (!response.ok || body.status === "FAILED" || body.status === "BLOCKED") {
        setLiveError(body.error_message ?? body.message ?? "실주문이 차단되었거나 실패했습니다.");
      }
      await fetchLiveOrders();
      setLivePreview(null);
      setLivePlaceConfirmation("");
    } catch (err) {
      setLiveError(err instanceof Error ? err.message : "알 수 없는 실주문 제출 오류가 발생했습니다.");
    } finally {
      setLiveLoading(false);
    }
  }, [fetchLiveOrders, livePlaceConfirmation, livePreview]);

  const runBacktestSet = React.useCallback(async (strategies: Strategy[]) => {
    setLoading(true);
    setError(null);
    try {
      const settingsByStrategy = {
        ...strategySettings,
        [strategy]: settings
      };
      const response = await fetch(`${API_BASE}/api/backtests/compare`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          market,
          unit,
          start_time_utc: kstInputToUtcIso(startDateKst),
          end_time_utc: kstInputToUtcIso(endDateKst),
          strategies,
          settings_by_strategy: settingsByStrategy,
          risk: backtestRisk
        })
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail ?? "백테스트 실행에 실패했습니다.");
      }
      const body = (await response.json()) as BacktestCompareResponse;
      const preferred = body.results.find((item) => item.strategy === strategies[0]) ?? body.results[0] ?? null;
      setResult(preferred);
      setComparisonRows(body.comparison);
      setComparisonResults(body.results);
      setBacktestCandleCount(body.candle_count);
      setChartCandles(preferred?.candles ?? []);
      setChartUpdatedAt(new Date().toISOString());
    } catch (err) {
      setError(err instanceof Error ? err.message : "알 수 없는 백테스트 오류가 발생했습니다.");
    } finally {
      setLoading(false);
    }
  }, [backtestRisk, endDateKst, market, settings, startDateKst, strategy, strategySettings, unit]);

  const runBacktest = React.useCallback(async () => {
    await runBacktestSet([strategy]);
  }, [runBacktestSet, strategy]);

  const runAllStrategyComparison = React.useCallback(async () => {
    await runBacktestSet(["ma_cross", "rsi", "volatility_breakout"]);
  }, [runBacktestSet]);

  const runStrategyValidation = React.useCallback(async () => {
    setValidationLoading(true);
    setValidationError(null);
    try {
      const response = await fetch(`${API_BASE}/api/strategy-validation/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          market,
          strategy: validationStrategy,
          timeframes: [1, 5, 15, 60],
          periods: ["7d", "30d", "90d", "180d", "custom"],
          custom_start_time_utc: kstInputToUtcIso(startDateKst),
          custom_end_time_utc: kstInputToUtcIso(endDateKst),
          settings: strategySettings[validationStrategy],
          risk: backtestRisk
        })
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail ?? "전략 검증 실행에 실패했습니다.");
      }
      const body = (await response.json()) as StrategyValidationResponse;
      setValidationRows(body.rows);
    } catch (err) {
      setValidationError(err instanceof Error ? err.message : "알 수 없는 전략 검증 오류가 발생했습니다.");
    } finally {
      setValidationLoading(false);
    }
  }, [backtestRisk, endDateKst, market, startDateKst, strategySettings, validationStrategy]);

  const saveCandidate = React.useCallback(async (row: ValidationRow) => {
    setCandidateError(null);
    try {
      const response = await fetch(`${API_BASE}/api/candidate-strategies`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          strategy: row.strategy,
          parameters: row.parameters,
          unit: row.unit,
          market: row.market,
          backtest_period: row.period_label,
          score: row.stability_score,
          backtest_total_return: row.metrics.total_return,
          backtest_mdd: row.metrics.mdd,
          backtest_win_rate: row.metrics.win_rate,
          backtest_profit_factor: row.metrics.profit_factor ?? 0,
          backtest_trade_count: row.metrics.trade_count,
          backtest_average_trade_pnl: row.metrics.trade_count > 0
            ? (row.metrics.realized_pnl ?? 0) / row.metrics.trade_count
            : 0,
          warning: row.warnings.join(", ")
        })
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail ?? "후보 전략 저장에 실패했습니다.");
      }
      await fetchCandidates();
    } catch (err) {
      setCandidateError(err instanceof Error ? err.message : "알 수 없는 후보 저장 오류가 발생했습니다.");
    }
  }, [fetchCandidates]);

  const startForwardPaper = React.useCallback(async (candidate: CandidateStrategy) => {
    setForwardLoading(true);
    setForwardError(null);
    try {
      setUnit(candidate.unit);
      const response = await fetch(`${API_BASE}/api/forward-paper/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          candidate_strategy_id: candidate.id,
          initial_balance_krw: paperRisk.initial_cash,
          risk: paperRisk
        })
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail ?? "Forward Paper 시작에 실패했습니다.");
      }
      setForwardPaper(await response.json());
      await fetchChartCandles();
    } catch (err) {
      setForwardError(err instanceof Error ? err.message : "알 수 없는 Forward Paper 시작 오류가 발생했습니다.");
    } finally {
      setForwardLoading(false);
    }
  }, [fetchChartCandles, paperRisk]);

  const stopForwardPaper = React.useCallback(async (sessionId?: number) => {
    setForwardLoading(true);
    setForwardError(null);
    try {
      const response = await fetch(`${API_BASE}/api/forward-paper/stop`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId ?? forwardPaper.id ?? null })
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail ?? "Forward Paper 중지에 실패했습니다.");
      }
      setForwardPaper(await response.json());
    } catch (err) {
      setForwardError(err instanceof Error ? err.message : "알 수 없는 Forward Paper 중지 오류가 발생했습니다.");
    } finally {
      setForwardLoading(false);
    }
  }, [forwardPaper.id]);

  const simulatePaperTrading = async () => {
    setPaperLoading(true);
    setPaperError(null);
    try {
      const response = await fetch(`${API_BASE}/api/paper-trading/simulate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ market, unit, count: 300, strategy, settings, risk: paperRisk })
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail ?? "최근 캔들 시뮬레이션 실행에 실패했습니다.");
      }
      setPaper(await response.json());
    } catch (err) {
      setPaperError(err instanceof Error ? err.message : "알 수 없는 페이퍼 시뮬레이션 오류가 발생했습니다.");
    } finally {
      setPaperLoading(false);
    }
  };

  const startLivePaperTrading = async () => {
    setPaperLoading(true);
    setPaperError(null);
    try {
      const response = await fetch(`${API_BASE}/api/paper-trading/live/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ market, unit, count: 300, strategy, settings, risk: paperRisk })
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail ?? "실시간 페이퍼 시작에 실패했습니다.");
      }
      setPaper(await response.json());
      await fetchChartCandles();
    } catch (err) {
      setPaperError(err instanceof Error ? err.message : "알 수 없는 실시간 페이퍼 시작 오류가 발생했습니다.");
    } finally {
      setPaperLoading(false);
    }
  };

  const stopLivePaperTrading = async () => {
    setPaperLoading(true);
    setPaperError(null);
    try {
      const response = await fetch(`${API_BASE}/api/paper-trading/live/stop`, { method: "POST" });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail ?? "실시간 페이퍼 중지에 실패했습니다.");
      }
      setPaper(await response.json());
    } catch (err) {
      setPaperError(err instanceof Error ? err.message : "알 수 없는 실시간 페이퍼 중지 오류가 발생했습니다.");
    } finally {
      setPaperLoading(false);
    }
  };

  React.useEffect(() => {
    void runBacktest();
    void fetchLatestPaper();
    void fetchCandidates();
    void fetchForwardPaper();
    void fetchLiveStatus();
    void fetchLiveOrders();
    void fetchAutoPilotStatus();
    void fetchLiveStrategyStatus();
  }, []);

  React.useEffect(() => {
    const intervalId = window.setInterval(() => {
      void fetchLiveStatus(liveExchange);
      void fetchLiveOrders();
      void fetchAutoPilotStatus();
      void fetchLiveStrategyStatus();
    }, 15000);
    return () => window.clearInterval(intervalId);
  }, [fetchAutoPilotStatus, fetchLiveOrders, fetchLiveStatus, fetchLiveStrategyStatus, liveExchange]);

  React.useEffect(() => {
    void fetchChartCandles();
  }, [fetchChartCandles]);

  React.useEffect(() => {
    if (forwardPaper.status !== "RUNNING") return;
    const intervalId = window.setInterval(() => {
      void fetchForwardPaper();
    }, 10000);
    return () => window.clearInterval(intervalId);
  }, [fetchForwardPaper, forwardPaper.status]);

  React.useEffect(() => {
    if (forwardPaper.status !== "RUNNING") return;
    const intervalId = window.setInterval(() => {
      void fetchChartCandles();
    }, 60000);
    return () => window.clearInterval(intervalId);
  }, [fetchChartCandles, forwardPaper.status]);

  React.useEffect(() => {
    if (paper.status !== "RUNNING" || paper.mode !== "LIVE") return;
    const intervalId = window.setInterval(() => {
      void fetchLatestPaper();
    }, 10000);
    return () => window.clearInterval(intervalId);
  }, [fetchLatestPaper, paper.mode, paper.status]);

  React.useEffect(() => {
    if (paper.status !== "RUNNING" || paper.mode !== "LIVE") return;
    const intervalId = window.setInterval(() => {
      void fetchChartCandles();
    }, 60000);
    return () => window.clearInterval(intervalId);
  }, [fetchChartCandles, paper.mode, paper.status]);

  const metrics = result?.metrics;
  const balance = paper.balance;
  const position = paper.position;
  const paperOrders = paper.orders ?? [];
  const equityPoints = paper.equity_curve ?? [];
  const forwardOrders = forwardPaper.orders ?? [];
  const forwardEquityPoints = forwardPaper.equity_curve ?? [];
  const forwardMetrics = forwardPaper.metrics;
  const forwardCandidate = forwardPaper.candidate;
  const forwardBalance = forwardPaper.balance;
  const forwardPosition = forwardPaper.position;
  const paperSignals = paper.signals ?? [];
  const forwardChartSignals: Signal[] = (forwardPaper.signal_logs ?? [])
    .filter((item) => item.signal === "BUY" || item.signal === "SELL")
    .map((item) => ({
      time: item.candle_time_utc,
      signal: item.signal as "BUY" | "SELL",
      price: 0,
      reason: item.reason
    }));
  const chartSignals = forwardPaper.status === "RUNNING" && forwardChartSignals.length > 0
    ? forwardChartSignals
    : paper.status === "RUNNING" && paper.mode === "LIVE" && paperSignals.length > 0
      ? paperSignals
      : result?.signals ?? [];
  const displayCandles = chartCandles.length > 0 ? chartCandles : result?.candles ?? [];
  const paperMode = "PAPER";
  const sessionStatus = paper.status;
  const displayedStrategy = paper.strategy ?? strategy;
  const totalPnl = balance?.total_pnl ?? ((balance?.equity ?? 0) - (balance?.initial_cash ?? 0));
  const riskStatus = paper.risk_status ?? (paper.status === "RUNNING" ? "ACTIVE" : "INACTIVE");
  const currentLiveMode = liveStatus?.mode ?? "PAPER";
  const liveKrw = liveBalances?.balances?.krw;
  const liveBtc = liveBalances?.balances?.btc;
  const liveEth = liveBalances?.balances?.eth;
  const activeExchange = liveExchange;
  const orderChanceStatus = liveOrderChance?.order_chance_status ?? liveStatus?.order_chance_status ?? "NOT_REQUESTED";
  const autoSession = autoPilot?.session;
  const autoPilotCandidate = candidateStrategies.find((candidate) => candidate.id === autoPilotCandidateId);
  const isAutoPilotOn = autoSession?.status === "RUNNING" || autoSession?.status === "READY";
  const autoPilotMinOrder = autoPilot?.min_auto_order_krw ?? 10000;
  const autoPilotMaxOrder = Math.max(autoPilot?.max_auto_order_krw ?? 10000, autoPilotMinOrder);
  const autoPilotFlow =
    autoSession?.last_order_status === "CANCELED" ? "자동취소 완료" :
    autoSession?.last_order_status === "WAITING" ? "취소 대기" :
    autoSession?.last_order_status === "SUBMITTED" ? "주문 접수" :
    autoSession?.last_order_status === "FILLED" ? "체결 완료" :
    autoSession?.last_order_status === "FAILED" ? "주문 실패" :
    autoSession?.last_order_status === "BLOCKED" ? "차단/재시도 대기" :
    isAutoPilotOn ? "신호 감시 중" : "정지";

  const liveStrategySession = liveStrategy?.session;
  const liveStrategyPosition = liveStrategy?.position;
  const liveStrategyCandidate = candidateStrategies.find((candidate) => candidate.id === liveStrategyCandidateId);
  const isLiveStrategyOn = liveStrategySession?.status === "RUNNING" || liveStrategySession?.status === "READY";
  const liveStrategyFlow =
    liveStrategyPosition?.status === "OPEN" ? "OPEN_POSITION" :
    liveStrategySession?.last_order_status === "CANCELED" ? "AUTO_CANCELED" :
    liveStrategySession?.last_order_status === "WAITING" ? "WAITING" :
    liveStrategySession?.last_order_status === "SUBMITTED" ? "SUBMITTED" :
    liveStrategySession?.last_order_status === "FILLED" ? "FILLED_POSITION_CREATED" :
    liveStrategySession?.last_order_status === "BLOCKED" ? "BLOCKED" :
    isLiveStrategyOn ? "WATCHING" : "STOPPED";

  return (
    <main className="min-h-screen bg-terminal-bg text-slate-100">
      <div className="flex min-h-screen flex-col">
        <div className={`border-b px-5 py-3 ${liveModeBannerClass(currentLiveMode)}`}>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-xl font-black tracking-wide">{liveModeLabel(currentLiveMode)}</div>
              <div className="text-xs opacity-90">
                전략 신호는 실주문 후보까지만 생성합니다. 실제 주문은 미리보기, Risk Manager, 최종 확인 문구를 모두 통과한 수동 요청만 허용됩니다.
              </div>
            </div>
            <button
              onClick={() => void emergencyStopLiveTrading()}
              className="inline-flex h-11 items-center gap-2 border border-terminal-red bg-terminal-red px-4 text-sm font-black text-black hover:bg-[#ff8ba0]"
            >
              <PauseCircle className="h-4 w-4" />
              EMERGENCY STOP
            </button>
          </div>
        </div>
        <header className="border-b border-terminal-line bg-[#070b12] px-5 py-3">
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2 pr-4">
              <Activity className="h-5 w-5 text-terminal-cyan" />
              <div>
                <h1 className="text-base font-semibold tracking-wide">Coin Bot Lab</h1>
                <p className="text-xs text-slate-500">페이퍼/백테스트 우선. Sprint 6 실거래는 수동 소액 주문만 잠금 해제 후 가능합니다.</p>
              </div>
            </div>
            <label className="control">
              <span>마켓</span>
              <select value={market} disabled>
                <option>KRW-BTC</option>
              </select>
            </label>
            <label className="control">
              <span>타임프레임</span>
              <select value={unit} onChange={(event) => setUnit(Number(event.target.value))}>
                {[1, 3, 5, 10, 15, 30, 60, 240].map((item) => (
                  <option key={item} value={item}>{`${item}m`}</option>
                ))}
              </select>
            </label>
            <label className="control min-w-[190px]">
              <span>전략</span>
              <select value={strategy} onChange={(event) => setStrategy(event.target.value as Strategy)}>
                {Object.entries(STRATEGY_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
            </label>
            <button onClick={simulatePaperTrading} disabled={paperLoading} className="ml-auto inline-flex h-10 items-center gap-2 border border-terminal-cyan bg-transparent px-4 text-sm font-semibold text-terminal-cyan transition hover:bg-[#0d2d33] disabled:cursor-not-allowed disabled:opacity-60">
              <RefreshCw className="h-4 w-4" />
              최근 캔들 시뮬레이션 실행
            </button>
            <button onClick={startLivePaperTrading} disabled={paperLoading || (paper.status === "RUNNING" && paper.mode === "LIVE")} className="inline-flex h-10 items-center gap-2 border border-terminal-green bg-terminal-green px-4 text-sm font-semibold text-black transition hover:bg-[#4ff0ad] disabled:cursor-not-allowed disabled:opacity-60">
              <Play className="h-4 w-4" />
              실시간 페이퍼 시작
            </button>
            <button onClick={stopLivePaperTrading} disabled={paperLoading || paper.status !== "RUNNING" || paper.mode !== "LIVE"} className="inline-flex h-10 items-center gap-2 border border-terminal-red bg-transparent px-4 text-sm font-semibold text-terminal-red transition hover:bg-[#31131d] disabled:cursor-not-allowed disabled:opacity-60">
              <PauseCircle className="h-4 w-4" />
              실시간 페이퍼 중지
            </button>
          </div>
        </header>

        <section className="border-b border-terminal-line bg-[#080d14] px-4 py-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-slate-100">Auto Live Pilot / 빗썸 소액 자동주문 파일럿</h2>
              <p className="text-xs text-slate-500">Bithumb KRW-BTC 지정가 매수 1회만 허용하며, 시장가/자동매도/출금 기능은 없습니다.</p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button onClick={() => void fetchAutoPilotStatus()} className="inline-flex h-9 items-center gap-2 border border-terminal-cyan px-3 text-xs font-semibold text-terminal-cyan hover:bg-[#0d2d33]">
                상태 새로고침
              </button>
              <button onClick={() => void cancelAutoPilotOpenOrder()} disabled={autoPilotLoading || !autoSession?.last_order_uuid} className="inline-flex h-9 items-center gap-2 border border-terminal-red px-3 text-xs font-semibold text-terminal-red hover:bg-[#331018] disabled:opacity-50">
                Cancel Open Order
              </button>
            </div>
          </div>
          {autoPilotError && <div className="mb-3 border border-terminal-red px-3 py-2 text-sm text-terminal-red">{autoPilotError}</div>}
          <section className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-8">
            <MetricCard label="Exchange" value={autoPilot?.exchange?.toUpperCase() ?? "BITHUMB"} tone="cyan" />
            <MetricCard label="Market" value={autoPilot?.market ?? "KRW-BTC"} />
            <MetricCard label="Auto Pilot Status" value={autoSession?.status ?? "STOPPED"} tone={autoSession?.status === "RUNNING" ? "green" : autoSession?.status === "ERROR" ? "red" : "amber"} />
            <MetricCard label="Live Trading" value={autoPilot?.live_trading_enabled ? "TRUE" : "FALSE"} tone={autoPilot?.live_trading_enabled ? "amber" : "neutral"} />
            <MetricCard label="Auto Trading" value={autoPilot?.live_auto_trading_enabled ? "TRUE" : "FALSE"} tone={autoPilot?.live_auto_trading_enabled ? "amber" : "neutral"} />
            <MetricCard label="Pilot Env" value={autoPilot?.auto_pilot_enabled ? "TRUE" : "FALSE"} tone={autoPilot?.auto_pilot_enabled ? "amber" : "neutral"} />
            <MetricCard label="Emergency Stop" value={autoPilot?.emergency_stop ? "ACTIVE" : "INACTIVE"} tone={autoPilot?.emergency_stop ? "red" : "green"} />
            <MetricCard label="API Key Loaded" value={autoPilot?.api_key_loaded ? "YES" : "NO"} tone={autoPilot?.api_key_loaded ? "green" : "red"} />
            <MetricCard label="Max Auto Order" value={formatKrw(autoPilot?.max_auto_order_krw)} tone="amber" />
            <MetricCard label="Min Test Order" value={formatKrw(autoPilotMinOrder)} tone="amber" />
            <MetricCard label="Orders Today" value={`${autoSession?.orders_created_today ?? 0}/${autoPilot?.max_orders_per_day ?? 1}`} />
            <MetricCard label="Test Flow" value={autoPilotFlow} tone={autoPilotFlow.includes("완료") ? "green" : autoPilotFlow.includes("실패") || autoPilotFlow.includes("차단") ? "red" : "cyan"} />
            <MetricCard label="Last Signal" value={autoSession?.last_signal ?? "-"} />
            <MetricCard label="Last Candle" value={formatKstShort(autoSession?.last_processed_candle_time_utc ?? undefined)} title={formatKstDateTime(autoSession?.last_processed_candle_time_utc ?? undefined)} />
            <MetricCard label="Last Order Status" value={autoSession?.last_order_status ?? "-"} />
            <MetricCard label="Last Order Time" value={formatKstShort(autoSession?.last_order_time_utc ?? undefined)} title={formatKstDateTime(autoSession?.last_order_time_utc ?? undefined)} />
            <MetricCard label="Last Order UUID" value={autoSession?.last_order_uuid ? `${autoSession.last_order_uuid.slice(0, 8)}...` : "-"} title={autoSession?.last_order_uuid ?? undefined} />
            <MetricCard label="Auto Cancel" value={`${autoPilot?.auto_cancel_after_seconds ?? 60}s`} />
          </section>
          <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,180px)_auto]">
            <label className="control">
              <span>Candidate Strategy</span>
              <select value={autoPilotCandidateId} onChange={(event) => setAutoPilotCandidateId(Number(event.target.value))}>
                <option value="">후보 전략 선택</option>
                {candidateStrategies.filter((candidate) => candidate.market === "KRW-BTC").map((candidate) => (
                  <option key={candidate.id} value={candidate.id}>
                    {`${STRATEGY_BADGES[candidate.strategy]} · ${formatTimeframe(candidate.unit)} · ${formatDecimal(candidate.score)}점`}
                  </option>
                ))}
              </select>
            </label>
            <label className="control">
              <span>Order KRW</span>
              <input type="number" min={autoPilotMinOrder} max={autoPilotMaxOrder} value={autoPilotAmount} onChange={(event) => setAutoPilotAmount(Math.min(Math.max(Number(event.target.value), autoPilotMinOrder), autoPilotMaxOrder))} />
            </label>
            <button
              onClick={() => void toggleAutoPilot()}
              disabled={
                autoPilotLoading ||
                (!isAutoPilotOn && !autoPilotCandidate)
              }
              className={`inline-flex h-10 min-w-[150px] self-end items-center justify-center border px-4 text-xs font-black disabled:cursor-not-allowed disabled:opacity-50 ${
                isAutoPilotOn
                  ? "border-terminal-amber text-terminal-amber hover:bg-[#2c2412]"
                  : "border-terminal-green bg-terminal-green text-black hover:bg-[#4ff0ad]"
              }`}
            >
              {isAutoPilotOn ? "1회 테스트 중지" : "1회 테스트 주문 실행"}
            </button>
          </div>
        </section>

        <section className="border-b border-terminal-line bg-[#071016] px-4 py-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-slate-100">Auto Live Strategy / Controlled Strategy Pilot</h2>
              <p className="text-xs text-slate-500">Bithumb KRW-BTC limit BUY only. Market orders, auto sell, withdrawals, leverage are disabled.</p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button onClick={() => void fetchLiveStrategyStatus()} className="inline-flex h-9 items-center gap-2 border border-terminal-cyan px-3 text-xs font-semibold text-terminal-cyan hover:bg-[#0d2d33]">Refresh Status</button>
              <button onClick={() => void cancelLiveStrategyOpenOrder()} disabled={liveStrategyLoading || !liveStrategySession?.current_open_order_uuid} className="inline-flex h-9 items-center gap-2 border border-terminal-red px-3 text-xs font-semibold text-terminal-red hover:bg-[#331018] disabled:opacity-50">Cancel Open Order</button>
            </div>
          </div>
          {liveStrategyError && <div className="mb-3 border border-terminal-red px-3 py-2 text-sm text-terminal-red">{liveStrategyError}</div>}
          <section className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-8">
            <MetricCard label="Exchange" value={liveStrategy?.exchange?.toUpperCase() ?? "BITHUMB"} tone="cyan" />
            <MetricCard label="Market" value={liveStrategy?.market ?? "KRW-BTC"} />
            <MetricCard label="Current Mode" value={liveStrategy?.current_mode ?? currentLiveMode} tone={liveStrategy?.current_mode === "AUTO_STRATEGY_RUNNING" ? "green" : liveStrategy?.current_mode === "EMERGENCY_STOPPED" ? "red" : "amber"} />
            <MetricCard label="Strategy Status" value={liveStrategySession?.status ?? "STOPPED"} tone={liveStrategySession?.status === "RUNNING" ? "green" : liveStrategySession?.status === "ERROR" ? "red" : "amber"} />
            <MetricCard label="Live Trading" value={liveStrategy?.live_trading_enabled ? "TRUE" : "FALSE"} tone={liveStrategy?.live_trading_enabled ? "amber" : "neutral"} />
            <MetricCard label="Auto Trading" value={liveStrategy?.live_auto_trading_enabled ? "TRUE" : "FALSE"} tone={liveStrategy?.live_auto_trading_enabled ? "amber" : "neutral"} />
            <MetricCard label="Strategy Env" value={liveStrategy?.auto_strategy_pilot_enabled ? "TRUE" : "FALSE"} tone={liveStrategy?.auto_strategy_pilot_enabled ? "amber" : "neutral"} />
            <MetricCard label="Emergency Stop" value={liveStrategy?.emergency_stop ? "ACTIVE" : "INACTIVE"} tone={liveStrategy?.emergency_stop ? "red" : "green"} />
            <MetricCard label="API Key Loaded" value={liveStrategy?.api_key_loaded ? "YES" : "NO"} tone={liveStrategy?.api_key_loaded ? "green" : "red"} />
            <MetricCard label="Max Order KRW" value={formatKrw(liveStrategy?.max_order_krw)} tone="amber" />
            <MetricCard label="Orders Today" value={`${liveStrategySession?.orders_created_today ?? 0}/${liveStrategy?.max_orders_per_day ?? 3}`} />
            <MetricCard label="Open Order" value={liveStrategySession?.current_open_order_uuid ? `${liveStrategySession.current_open_order_uuid.slice(0, 8)}...` : "-"} title={liveStrategySession?.current_open_order_uuid ?? undefined} />
            <MetricCard label="Current Position" value={liveStrategyPosition ? `${liveStrategyPosition.status} #${liveStrategyPosition.id}` : "-"} tone={liveStrategyPosition?.status === "OPEN" ? "green" : "neutral"} />
            <MetricCard label="Last Signal" value={liveStrategySession?.last_signal ?? "-"} />
            <MetricCard label="Last Risk Result" value={liveStrategySession?.last_risk_result ?? "-"} tone={liveStrategySession?.last_risk_result?.startsWith("BLOCKED") ? "red" : "neutral"} />
            <MetricCard label="Last Order Status" value={liveStrategySession?.last_order_status ?? "-"} />
            <MetricCard label="Last Candle" value={formatKstShort(liveStrategySession?.last_processed_candle_time_utc ?? undefined)} title={formatKstDateTime(liveStrategySession?.last_processed_candle_time_utc ?? undefined)} />
            <MetricCard label="Flow" value={liveStrategyFlow} tone={liveStrategyFlow === "BLOCKED" ? "red" : liveStrategyFlow.includes("CANCELED") || liveStrategyFlow.includes("POSITION") ? "green" : "cyan"} />
          </section>

          <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_auto]">
            <label className="control">
              <span>Candidate Strategy</span>
              <select value={liveStrategyCandidateId} onChange={(event) => setLiveStrategyCandidateId(Number(event.target.value))}>
                <option value="">Select candidate</option>
                {candidateStrategies.filter((candidate) => candidate.market === "KRW-BTC").map((candidate) => (
                  <option key={candidate.id} value={candidate.id}>
                    {`${STRATEGY_BADGES[candidate.strategy]} ? ${formatTimeframe(candidate.unit)} ? ${formatDecimal(candidate.score)}pt`}
                  </option>
                ))}
              </select>
            </label>
            <button
              onClick={() => void toggleLiveStrategy()}
              disabled={liveStrategyLoading || (!isLiveStrategyOn && !liveStrategyCandidate)}
              className={`inline-flex h-10 min-w-[180px] self-end items-center justify-center border px-4 text-xs font-black disabled:cursor-not-allowed disabled:opacity-50 ${
                isLiveStrategyOn
                  ? "border-terminal-amber text-terminal-amber hover:bg-[#2c2412]"
                  : "border-terminal-green bg-terminal-green text-black hover:bg-[#4ff0ad]"
              }`}
            >
              {isLiveStrategyOn ? "Auto Strategy OFF" : "Auto Strategy ON"}
            </button>
          </div>

          <section className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
            <MetricCard label="Position Status" value={liveStrategyPosition?.status ?? "-"} />
            <MetricCard label="Entry Price" value={formatKrw(liveStrategyPosition?.entry_price)} />
            <MetricCard label="Current Price" value={formatKrw(liveStrategyPosition?.current_price)} />
            <MetricCard label="Volume" value={formatNumber(liveStrategyPosition?.entry_volume)} tone="cyan" />
            <MetricCard label="Entry Amount" value={formatKrw(liveStrategyPosition?.entry_amount_krw)} />
            <MetricCard label="Unrealized PnL" value={formatKrw(liveStrategyPosition?.unrealized_pnl)} tone={pnlTone(liveStrategyPosition?.unrealized_pnl ?? 0)} />
            <MetricCard label="Stop Loss" value={formatKrw(liveStrategyPosition?.stop_loss_price)} tone="red" />
            <MetricCard label="Take Profit" value={formatKrw(liveStrategyPosition?.take_profit_price)} tone="green" />
          </section>
        </section>

        <section className="border-b border-terminal-line bg-terminal-bg px-4 py-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-slate-100">Live Safety Panel / 실거래 안전장치</h2>
              <p className="text-xs text-slate-500">API Key는 백엔드 환경변수에서만 읽습니다. 출금 권한 없는 API Key와 허용 IP 설정을 전제로 합니다.</p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <label className="control min-w-[150px]">
                <span>Exchange</span>
                <select value={liveExchange} onChange={(event) => setLiveExchange(event.target.value as Exchange)}>
                  <option value="upbit">Upbit</option>
                  <option value="bithumb">Bithumb</option>
                </select>
              </label>
              <button onClick={() => void fetchLiveBalances()} disabled={liveLoading} className="inline-flex h-9 items-center gap-2 border border-terminal-cyan px-3 text-xs font-semibold text-terminal-cyan hover:bg-[#0d2d33] disabled:opacity-60">
                <RefreshCw className="h-4 w-4" />
                실계좌 잔고 조회
              </button>
              <button onClick={() => void fetchLiveOrderChance()} disabled={liveLoading} className="inline-flex h-9 items-center gap-2 border border-terminal-cyan px-3 text-xs font-semibold text-terminal-cyan hover:bg-[#0d2d33] disabled:opacity-60">
                주문 가능 정보 조회
              </button>
              <button onClick={() => void lockLiveTradingMode()} className="inline-flex h-9 items-center gap-2 border border-terminal-amber px-3 text-xs font-semibold text-terminal-amber hover:bg-[#2c2412]">
                실거래 잠금
              </button>
            </div>
          </div>
          {liveError && <div className="mb-3 border border-terminal-red px-3 py-2 text-sm text-terminal-red">{liveError}</div>}

          <section className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-8">
            <MetricCard label="Exchange" value={activeExchange === "bithumb" ? "Bithumb" : "Upbit"} tone="cyan" />
            <MetricCard label="Live Trading Enabled" value={liveStatus?.live_trading_enabled ? "TRUE" : "FALSE"} tone={liveStatus?.live_trading_enabled ? "amber" : "neutral"} />
            <MetricCard label="Broker Status" value={liveStatus?.broker_status ?? "-"} tone={liveStatus?.broker_status === "READY" ? "green" : liveStatus?.broker_status === "EMERGENCY_STOPPED" ? "red" : "amber"} />
            <MetricCard label="API Key Loaded" value={liveStatus?.api_key_loaded ? "YES" : "NO"} tone={liveStatus?.api_key_loaded ? "green" : "amber"} />
            <MetricCard label="Balance Fetch" value={liveBalances?.balance_fetch_status ?? liveStatus?.balance_fetch_status ?? "-"} tone={liveBalances?.balance_fetch_status === "SUCCESS" ? "green" : "amber"} />
            <MetricCard label="Order Chance" value={orderChanceStatus} tone={orderChanceStatus === "SUCCESS" ? "green" : orderChanceStatus === "FAILED" ? "red" : "amber"} />
            <MetricCard label="Risk Manager" value={liveStatus?.risk_manager_status ?? "-"} tone={liveStatus?.risk_manager_status === "ACTIVE" ? "green" : "amber"} />
            <MetricCard label="Emergency Stop" value={liveStatus?.emergency_stop ? "ACTIVE" : "INACTIVE"} tone={liveStatus?.emergency_stop ? "red" : "green"} />
            <MetricCard label="Max Live Order" value={formatKrw(liveStatus?.max_live_order_krw)} tone="amber" />
            <MetricCard label="Daily Loss Limit" value={`${formatDecimal(liveStatus?.daily_loss_limit_percent)}%`} tone="amber" />
            <MetricCard label="Last Live Order" value={formatKstShort(liveStatus?.last_live_order_time)} title={formatKstDateTime(liveStatus?.last_live_order_time)} />
          </section>

          <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
            <div className="space-y-4">
              <div className="border border-terminal-red bg-[#140910]">
                <div className="border-b border-terminal-red/60 px-4 py-3 text-sm font-semibold text-terminal-red">Emergency Stop 해제</div>
                <div className="space-y-3 p-4 text-sm">
                  <p className="text-xs text-slate-400">
                    긴급정지는 모든 실거래 주문 후보를 차단합니다. 해제해도 실거래는 자동으로 켜지지 않고 잠금 상태로 돌아갑니다.
                  </p>
                  <ConfirmationField
                    label="해제 확인 문구"
                    value={liveEmergencyResetConfirmation}
                    phrase="RESET EMERGENCY"
                    onChange={setLiveEmergencyResetConfirmation}
                  />
                  <button
                    onClick={() => void resetEmergencyStop()}
                    disabled={liveLoading || liveEmergencyResetConfirmation !== "RESET EMERGENCY"}
                    className="inline-flex h-9 items-center gap-2 border border-terminal-amber px-3 text-xs font-semibold text-terminal-amber hover:bg-[#2c2412] disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    Emergency Stop 해제
                  </button>
                </div>
              </div>

              <div className="border border-terminal-line bg-terminal-panel">
                <div className="border-b border-terminal-line px-4 py-3 text-sm font-semibold">실거래 잠금 해제</div>
                <div className="space-y-3 p-4 text-sm">
                  <label className="flex items-center gap-2 text-slate-300">
                    <input
                      type="checkbox"
                      checked={liveArmAcknowledged}
                      onChange={(event) => setLiveArmAcknowledged(event.target.checked)}
                    />
                    <span>실주문은 손실이 발생할 수 있으며, 자동매매가 아니라 수동 소액 테스트임을 확인합니다.</span>
                  </label>
                  <ConfirmationField
                    label="확인 문구"
                    value={liveArmConfirmation}
                    phrase="LIVE ENABLE"
                    onChange={setLiveArmConfirmation}
                  />
                  <button
                    onClick={() => void armLiveTrading()}
                    disabled={liveLoading}
                    className="inline-flex h-9 items-center gap-2 border border-terminal-red px-3 text-xs font-semibold text-terminal-red hover:bg-[#331018] disabled:opacity-60"
                  >
                    LIVE MANUAL ONLY 활성화
                  </button>
                </div>
              </div>

              <div className="border border-terminal-line bg-terminal-panel">
                <div className="border-b border-terminal-line px-4 py-3 text-sm font-semibold">실계좌 잔고</div>
                <div className="grid grid-cols-2 gap-3 p-4">
                  <MetricCard label="KRW 사용 가능" value={formatKrw(liveKrw?.balance)} />
                  <MetricCard label="KRW 잠김" value={formatKrw(liveKrw?.locked)} />
                  <MetricCard label="BTC 보유" value={formatNumber(liveBtc?.balance)} tone="cyan" />
                  <MetricCard label="BTC 잠김" value={formatNumber(liveBtc?.locked)} />
                  <MetricCard label="BTC 평균가" value={formatKrw(liveBtc?.avg_buy_price)} />
                  <MetricCard label="ETH 보유" value={formatNumber(liveEth?.balance)} tone="cyan" />
                  <MetricCard label="ETH 잠김" value={formatNumber(liveEth?.locked)} />
                  <MetricCard label="ETH 평균가" value={formatKrw(liveEth?.avg_buy_price)} />
                  <MetricCard label="총 자산 추정" value={formatKrw(liveBalances?.estimated_total_equity_krw)} tone="amber" />
                </div>
              </div>
            </div>

            <div className="space-y-4">
              <div className="border border-terminal-line bg-terminal-panel">
                <div className="flex items-center justify-between border-b border-terminal-line px-4 py-3">
                  <span className="text-sm font-semibold">Order Preview / 수동 실주문 미리보기</span>
                  <span className="text-xs text-slate-500">미리보기는 실제 주문을 만들지 않습니다</span>
                </div>
                <div className="grid grid-cols-1 gap-3 p-4 md:grid-cols-3 xl:grid-cols-6">
                  <label className="control">
                    <span>거래소</span>
                    <select value={liveExchange} onChange={(event) => setLiveExchange(event.target.value as Exchange)}>
                      <option value="upbit">Upbit</option>
                      <option value="bithumb">Bithumb</option>
                    </select>
                  </label>
                  <label className="control">
                    <span>마켓</span>
                    <select value={liveOrderForm.market} onChange={(event) => setLiveOrderForm((prev) => ({ ...prev, market: event.target.value }))}>
                      <option value="KRW-BTC">KRW-BTC</option>
                    </select>
                  </label>
                  <label className="control">
                    <span>방향</span>
                    <select value={liveOrderForm.side} onChange={(event) => setLiveOrderForm((prev) => ({ ...prev, side: event.target.value as "BUY" | "SELL" }))}>
                      <option value="BUY">BUY</option>
                      <option value="SELL">SELL</option>
                    </select>
                  </label>
                  <label className="control">
                    <span>주문 타입</span>
                    <select value={liveOrderForm.order_type} onChange={(event) => setLiveOrderForm((prev) => ({ ...prev, order_type: event.target.value as "LIMIT" | "MARKET" }))}>
                      <option value="LIMIT">LIMIT</option>
                      <option value="MARKET">MARKET</option>
                    </select>
                  </label>
                  <label className="control">
                    <span>가격</span>
                    <input type="number" value={liveOrderForm.price} onChange={(event) => setLiveOrderForm((prev) => ({ ...prev, price: Number(event.target.value) }))} />
                  </label>
                  <label className="control">
                    <span>주문 금액 KRW</span>
                    <input type="number" value={liveOrderForm.amount_krw} onChange={(event) => setLiveOrderForm((prev) => ({ ...prev, amount_krw: Number(event.target.value) }))} />
                  </label>
                  <label className="control">
                    <span>수량</span>
                    <input type="number" step="0.00000001" value={liveOrderForm.volume} onChange={(event) => setLiveOrderForm((prev) => ({ ...prev, volume: Number(event.target.value) }))} />
                  </label>
                </div>
                <div className="flex flex-wrap items-center gap-2 border-t border-terminal-line px-4 py-3">
                  <button onClick={() => void previewLiveOrder()} disabled={liveLoading} className="inline-flex h-9 items-center gap-2 border border-terminal-cyan px-3 text-xs font-semibold text-terminal-cyan hover:bg-[#0d2d33] disabled:opacity-60">
                    주문 미리보기
                  </button>
                  {livePreview && (
                    <span className="text-xs text-slate-400">
                      Request ID {livePreview.request_id} · Risk {formatRiskStatus(livePreview.risk_result)}
                    </span>
                  )}
                </div>
              </div>

              <div className="border border-terminal-line bg-terminal-panel">
                <div className="flex items-center justify-between border-b border-terminal-line px-4 py-3">
                  <span className="text-sm font-semibold">Live Order Log</span>
                  <span className="text-xs text-slate-500">페이퍼 로그와 분리 저장</span>
                </div>
                <div className="table-scroll max-h-72 overflow-auto">
                  <table className="ops-table min-w-[1380px] w-full text-left text-sm">
                    <thead className="sticky top-0 z-10 bg-terminal-panel2 text-xs text-slate-500">
                      <tr>
                        <th className="px-3 py-2">Time, KST</th>
                        <th className="px-3 py-2">Exchange</th>
                        <th className="px-3 py-2">Market</th>
                        <th className="px-3 py-2">Side</th>
                        <th className="px-3 py-2">Order Type</th>
                        <th className="px-3 py-2">Strategy</th>
                        <th className="px-3 py-2">Order UUID</th>
                        <th className="px-3 py-2">Candle</th>
                        <th className="px-3 py-2 text-right">Price</th>
                        <th className="px-3 py-2 text-right">Amount KRW</th>
                        <th className="px-3 py-2">Status</th>
                        <th className="px-3 py-2">Risk Result</th>
                        <th className="px-3 py-2">Error Message</th>
                      </tr>
                    </thead>
                    <tbody>
                      {liveOrders.map((order) => (
                        <tr key={order.request_id} className="border-t border-terminal-line">
                          <td className="nowrap px-3 py-2 text-slate-400" title={formatKstDateTime(order.created_at)}>{formatKstShort(order.created_at)}</td>
                          <td className="nowrap px-3 py-2 uppercase text-terminal-cyan">{order.exchange ?? "upbit"}</td>
                          <td className="nowrap px-3 py-2 font-semibold">{order.market}</td>
                          <td className="px-3 py-2"><SideBadge side={order.side} /></td>
                          <td className="nowrap px-3 py-2">{order.order_type}</td>
                          <td className="nowrap px-3 py-2">{order.strategy_name ? <StatusBadge value={STRATEGY_BADGES[order.strategy_name as Strategy] ?? order.strategy_name} tone="cyan" /> : "-"}</td>
                          <td className="max-w-[120px] truncate px-3 py-2 text-slate-300" title={order.order_uuid ?? ""}>{order.order_uuid ?? "-"}</td>
                          <td className="nowrap px-3 py-2 text-slate-400" title={formatKstDateTime(order.candle_time_utc ?? undefined)}>{formatKstShort(order.candle_time_utc ?? undefined)}</td>
                          <td className="mono-num px-3 py-2 text-right">{formatKrw(order.price ?? undefined)}</td>
                          <td className="mono-num px-3 py-2 text-right">{formatKrw(order.amount_krw ?? undefined)}</td>
                          <td className="nowrap px-3 py-2">
                            <StatusBadge value={formatLiveOrderStatus(order.status)} tone={order.status === "BLOCKED" || order.status === "FAILED" ? "red" : order.status === "SUBMITTED" || order.status === "FILLED" ? "green" : "amber"} />
                          </td>
                          <td className="nowrap px-3 py-2">
                            <StatusBadge value={formatRiskStatus(order.risk_result)} tone={order.risk_result === "ALLOWED" ? "green" : "red"} />
                          </td>
                          <td className="max-w-[260px] truncate px-3 py-2 text-slate-300" title={order.error_message ?? ""}>{order.error_message ?? "-"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </div>
        </section>

        {livePreview && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
            <div className="w-full max-w-2xl border border-terminal-line bg-terminal-panel shadow-2xl">
              <div className="flex items-center justify-between border-b border-terminal-line px-4 py-3">
                <span className="text-sm font-semibold">Order Preview Modal</span>
                <button onClick={() => setLivePreview(null)} className="border border-terminal-line px-2 py-1 text-xs text-slate-300">닫기</button>
              </div>
              <div className="grid grid-cols-2 gap-3 p-4">
                <MetricCard label="거래소" value={(livePreview.exchange ?? activeExchange).toUpperCase()} tone="cyan" />
                <MetricCard label="방향" value={<SideBadge side={livePreview.side} />} />
                <MetricCard label="마켓" value={livePreview.market} />
                <MetricCard label="주문 가격" value={formatKrw(livePreview.price)} />
                <MetricCard label="주문 금액" value={formatKrw(livePreview.amount_krw)} />
                <MetricCard label="예상 수량" value={formatNumber(livePreview.volume)} tone="cyan" />
                <MetricCard label="예상 수수료" value={formatKrw(livePreview.fee_estimate)} />
                <MetricCard label="주문 후 KRW" value={formatKrw(livePreview.estimated_post_krw_balance)} />
                <MetricCard label="주문 후 코인" value={formatNumber(livePreview.estimated_post_asset_balance)} />
                <MetricCard label="Risk Result" value={formatRiskStatus(livePreview.risk_result)} tone={livePreview.allowed ? "green" : "red"} />
                <MetricCard label="Order Chance" value={livePreview.order_chance_status ?? "-"} tone={livePreview.order_chance_status === "SUCCESS" ? "green" : livePreview.order_chance_status === "FAILED" ? "red" : "amber"} title={livePreview.order_chance_error ?? undefined} />
                <MetricCard label="차단 사유" value={formatRiskStatus(livePreview.blocked_reason)} tone={livePreview.allowed ? "neutral" : "red"} />
              </div>
              <div className="space-y-3 border-t border-terminal-line p-4">
                <ConfirmationField
                  label="최종 확인 문구"
                  value={livePlaceConfirmation}
                  phrase="PLACE LIVE ORDER"
                  onChange={setLivePlaceConfirmation}
                />
                <button
                  onClick={() => void placeLiveOrder()}
                  disabled={!livePreview.allowed || livePlaceConfirmation !== "PLACE LIVE ORDER" || liveLoading}
                  className="inline-flex h-10 items-center gap-2 border border-terminal-red px-4 text-sm font-bold text-terminal-red hover:bg-[#331018] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  수동 소액 실주문 제출
                </button>
              </div>
            </div>
          </div>
        )}

        <section className="border-b border-terminal-line bg-[#070b12] px-4 py-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-slate-100">백테스트 실험실</h2>
              <p className="text-xs text-slate-500">기간 고정 백테스트와 3개 전략 비교 결과입니다. 실시간 페이퍼 세션과 별도로 계산됩니다.</p>
            </div>
            <div className="flex gap-2">
              <button onClick={runBacktest} disabled={loading} className="inline-flex h-9 items-center gap-2 border border-terminal-cyan bg-transparent px-3 text-xs font-semibold text-terminal-cyan transition hover:bg-[#0d2d33] disabled:opacity-60">
                <RefreshCw className="h-4 w-4" />
                단일 전략 실행
              </button>
              <button onClick={runAllStrategyComparison} disabled={loading} className="inline-flex h-9 items-center gap-2 border border-terminal-green bg-terminal-green px-3 text-xs font-semibold text-black transition hover:bg-[#4ff0ad] disabled:opacity-60">
                <BarChart3 className="h-4 w-4" />
                전체 전략 비교
              </button>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-3 lg:grid-cols-4 2xl:grid-cols-8">
            <label className="control">
              <span>마켓</span>
              <select value={market} disabled>
                <option>KRW-BTC</option>
              </select>
            </label>
            <label className="control">
              <span>타임프레임</span>
              <select value={unit} onChange={(event) => setUnit(Number(event.target.value))}>
                {[1, 5, 15, 60].map((item) => (
                  <option key={item} value={item}>{formatTimeframe(item)}</option>
                ))}
              </select>
            </label>
            <label className="control">
              <span>시작 일시 (KST)</span>
              <input type="datetime-local" value={startDateKst} onChange={(event) => setStartDateKst(event.target.value)} />
            </label>
            <label className="control">
              <span>종료 일시 (KST)</span>
              <input type="datetime-local" value={endDateKst} onChange={(event) => setEndDateKst(event.target.value)} />
            </label>
            <NumberField label="초기 원화 잔고" value={backtestRisk.initial_cash} step="100000" onChange={(next) => setBacktestRisk((prev) => ({ ...prev, initial_cash: next }))} />
            <NumberField label="수수료율" value={backtestRisk.fee_rate} step="0.0001" onChange={(next) => setBacktestRisk((prev) => ({ ...prev, fee_rate: next }))} />
            <NumberField label="슬리피지율" value={backtestRisk.slippage_rate} step="0.0001" onChange={(next) => setBacktestRisk((prev) => ({ ...prev, slippage_rate: next }))} />
            <label className="control">
              <span>단일 실행 전략</span>
              <select value={strategy} onChange={(event) => setStrategy(event.target.value as Strategy)}>
                {Object.entries(STRATEGY_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
            </label>
          </div>

          <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-3">
            {(Object.keys(DEFAULT_SETTINGS) as Strategy[]).map((item) => (
              <div key={item} className="border border-terminal-line bg-terminal-panel p-3">
                <div className="mb-3 flex items-center justify-between">
                  <span className="text-sm font-semibold">{STRATEGY_LABELS[item]}</span>
                  <StatusBadge value={STRATEGY_BADGES[item]} tone="cyan" />
                </div>
                <div className="space-y-2">
                  {Object.entries(strategySettings[item]).map(([key, value]) => (
                    <NumberField
                      key={key}
                      label={formatFieldLabel(key)}
                      value={value}
                      step={key === "k" || key.includes("threshold") ? "0.1" : "1"}
                      onChange={(next) =>
                        setStrategySettings((prev) => ({
                          ...prev,
                          [item]: { ...prev[item], [key]: next }
                        }))
                      }
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="border-b border-terminal-line bg-terminal-bg px-4 py-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-slate-100">Parameter Sweep / 전략 검증</h2>
              <p className="text-xs text-slate-500">여러 기간, 타임프레임, 파라미터 조합에서 안정성 점수와 과최적화 경고를 계산합니다.</p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <label className="control min-w-[180px]">
                <span>검증 전략</span>
                <select value={validationStrategy} onChange={(event) => setValidationStrategy(event.target.value as Strategy)}>
                  {Object.entries(STRATEGY_LABELS).map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </select>
              </label>
              <button onClick={runStrategyValidation} disabled={validationLoading} className="inline-flex h-10 items-center gap-2 border border-terminal-amber bg-transparent px-4 text-sm font-semibold text-terminal-amber transition hover:bg-[#2c2412] disabled:opacity-60">
                <RefreshCw className="h-4 w-4" />
                {validationLoading ? "검증 중" : "스윕 실행"}
              </button>
            </div>
          </div>
          {validationError && <div className="mb-3 border border-terminal-red px-3 py-2 text-sm text-terminal-red">{validationError}</div>}
          {candidateError && <div className="mb-3 border border-terminal-red px-3 py-2 text-sm text-terminal-red">{candidateError}</div>}
          {forwardError && <div className="mb-3 border border-terminal-red px-3 py-2 text-sm text-terminal-red">{forwardError}</div>}

          <div className="grid grid-cols-1 gap-4 2xl:grid-cols-[minmax(0,1fr)_420px]">
            <div className="border border-terminal-line bg-terminal-panel">
              <div className="flex items-center justify-between border-b border-terminal-line px-4 py-3">
                <span className="text-sm font-semibold">전략 랭킹 UI</span>
                <span className="text-xs text-slate-500">{validationRows.length}개 조합</span>
              </div>
              <div className="table-scroll max-h-96 overflow-auto">
                <table className="ops-table min-w-[1320px] w-full text-left text-sm">
                  <thead className="sticky top-0 z-10 bg-terminal-panel2 text-xs text-slate-500">
                    <tr>
                      <th className="px-3 py-2">Rank</th>
                      <th className="px-3 py-2">Strategy</th>
                      <th className="px-3 py-2">Timeframe</th>
                      <th className="px-3 py-2">Parameters</th>
                      <th className="px-3 py-2">Period</th>
                      <th className="px-3 py-2 text-right">Total Return</th>
                      <th className="px-3 py-2 text-right">MDD</th>
                      <th className="px-3 py-2 text-right">Win Rate</th>
                      <th className="px-3 py-2 text-right">Profit Factor</th>
                      <th className="px-3 py-2 text-right">Trades</th>
                      <th className="px-3 py-2 text-right">Stability Score</th>
                      <th className="px-3 py-2">Warning</th>
                      <th className="px-3 py-2">Candidate</th>
                    </tr>
                  </thead>
                  <tbody>
                    {validationRows.map((row, index) => (
                      <tr key={`${row.strategy}-${row.unit}-${row.period_label}-${index}`} className="border-t border-terminal-line">
                        <td className="mono-num px-3 py-2 text-slate-400">{index + 1}</td>
                        <td className="nowrap px-3 py-2"><StatusBadge value={STRATEGY_BADGES[row.strategy]} tone="cyan" /></td>
                        <td className="nowrap px-3 py-2">{row.timeframe}</td>
                        <td className="max-w-[260px] truncate px-3 py-2" title={formatParameters(row.parameters)}>{formatParameters(row.parameters)}</td>
                        <td className="nowrap px-3 py-2">{formatPeriodLabel(row.period_label)}</td>
                        <td className={`mono-num px-3 py-2 text-right ${toneClass(pnlTone(row.metrics.total_return))}`}>{formatPercent(row.metrics.total_return)}</td>
                        <td className="mono-num px-3 py-2 text-right text-terminal-red">{formatPercent(row.metrics.mdd)}</td>
                        <td className="mono-num px-3 py-2 text-right">{formatPercent(row.metrics.win_rate)}</td>
                        <td className="mono-num px-3 py-2 text-right">{formatDecimal(row.metrics.profit_factor)}</td>
                        <td className="mono-num px-3 py-2 text-right">{row.metrics.trade_count}</td>
                        <td className="mono-num px-3 py-2 text-right text-terminal-amber">{formatDecimal(row.stability_score)}</td>
                        <td className="max-w-[260px] truncate px-3 py-2 text-slate-300" title={row.warnings.join(", ")}>
                          {row.warnings.length ? row.warnings.join(", ") : "없음"}
                        </td>
                        <td className="px-3 py-2">
                          <button onClick={() => void saveCandidate(row)} className="nowrap border border-terminal-cyan px-2 py-1 text-xs font-semibold text-terminal-cyan hover:bg-[#0d2d33]">
                            후보 저장
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="border border-terminal-line bg-terminal-panel">
              <div className="flex items-center justify-between border-b border-terminal-line px-4 py-3">
                <span className="text-sm font-semibold">Candidate Strategies</span>
                <span className="text-xs text-slate-500">Forward Paper 연결 후보</span>
              </div>
              <div className="max-h-96 overflow-auto">
                <table className="ops-table min-w-[980px] w-full text-left text-sm">
                  <thead className="sticky top-0 bg-terminal-panel2 text-xs text-slate-500">
                    <tr>
                      <th className="px-3 py-2">Strategy</th>
                      <th className="px-3 py-2">Market</th>
                      <th className="px-3 py-2">TF</th>
                      <th className="px-3 py-2">Parameters</th>
                      <th className="px-3 py-2">Period</th>
                      <th className="px-3 py-2 text-right">Return</th>
                      <th className="px-3 py-2 text-right">MDD</th>
                      <th className="px-3 py-2 text-right">PF</th>
                      <th className="px-3 py-2 text-right">Score</th>
                      <th className="px-3 py-2">Warning</th>
                      <th className="px-3 py-2">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {candidateStrategies.map((candidate) => (
                      <tr key={candidate.id} className="border-t border-terminal-line" title={formatParameters(candidate.parameters)}>
                        <td className="nowrap px-3 py-2"><StatusBadge value={STRATEGY_BADGES[candidate.strategy]} tone="cyan" /></td>
                        <td className="nowrap px-3 py-2 font-semibold">{candidate.market}</td>
                        <td className="nowrap px-3 py-2">{formatTimeframe(candidate.unit)}</td>
                        <td className="max-w-[220px] truncate px-3 py-2" title={formatParameters(candidate.parameters)}>{formatParameters(candidate.parameters)}</td>
                        <td className="nowrap px-3 py-2">{formatPeriodLabel(candidate.backtest_period)}</td>
                        <td className={`mono-num px-3 py-2 text-right ${toneClass(pnlTone(candidate.backtest_total_return))}`}>{formatPercent(candidate.backtest_total_return)}</td>
                        <td className="mono-num px-3 py-2 text-right text-terminal-red">{formatPercent(candidate.backtest_mdd)}</td>
                        <td className="mono-num px-3 py-2 text-right">{formatDecimal(candidate.backtest_profit_factor)}</td>
                        <td className="mono-num px-3 py-2 text-right text-terminal-amber">{formatDecimal(candidate.score)}</td>
                        <td className="max-w-[180px] truncate px-3 py-2 text-slate-300" title={candidate.warning || ""}>{candidate.warning || "없음"}</td>
                        <td className="nowrap px-3 py-2">
                          <button
                            onClick={() => void startForwardPaper(candidate)}
                            disabled={forwardLoading}
                            className="border border-terminal-green px-2 py-1 text-xs font-semibold text-terminal-green hover:bg-[#0c2b1f] disabled:opacity-60"
                          >
                            Forward 시작
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </section>

        <section className="border-b border-terminal-line bg-terminal-bg px-4 py-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-slate-100">Forward Paper Test / 후보 전략 실시간 검증</h2>
              <p className="text-xs text-slate-500">저장된 후보 전략을 완성된 새 캔들 기준으로만 평가합니다. 실제 주문/API Key/실잔고 조회는 없습니다.</p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button
                onClick={() => void fetchForwardPaper()}
                className="inline-flex h-10 items-center gap-2 border border-terminal-cyan bg-transparent px-4 text-sm font-semibold text-terminal-cyan transition hover:bg-[#0d2d33]"
              >
                <RefreshCw className="h-4 w-4" />
                Forward 새로고침
              </button>
              <button
                onClick={() => void stopForwardPaper()}
                disabled={forwardLoading || forwardPaper.status !== "RUNNING"}
                className="inline-flex h-10 items-center gap-2 border border-terminal-red bg-transparent px-4 text-sm font-semibold text-terminal-red transition hover:bg-[#331018] disabled:opacity-50"
              >
                <PauseCircle className="h-4 w-4" />
                Forward 중지
              </button>
            </div>
          </div>

          <section className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-8">
            <MetricCard label="모드" value="PAPER / FORWARD" tone="cyan" />
            <MetricCard label="세션 상태" value={<StatusBadge value={formatSessionStatus(forwardPaper.status)} tone={statusTone(forwardPaper.status)} />} tone={statusTone(forwardPaper.status)} title={forwardPaper.status} />
            <MetricCard label="후보 전략" value={forwardCandidate ? <StatusBadge value={STRATEGY_BADGES[forwardCandidate.strategy]} tone="cyan" /> : "-"} title={forwardCandidate ? STRATEGY_LABELS[forwardCandidate.strategy] : "-"} />
            <MetricCard label="마켓" value={forwardPaper.market ?? "-"} />
            <MetricCard label="타임프레임" value={formatTimeframe(forwardPaper.unit)} />
            <MetricCard label="마지막 처리 캔들" value={formatKstShort(forwardPaper.last_processed_candle_time_utc)} title={formatKstDateTime(forwardPaper.last_processed_candle_time_utc)} tone="amber" />
            <MetricCard label="마지막 Tick" value={formatKstShort(forwardPaper.last_tick_time_utc ?? forwardPaper.updated_at)} title={formatKstDateTime(forwardPaper.last_tick_time_utc ?? forwardPaper.updated_at)} tone={forwardPaper.status === "RUNNING" ? "green" : "neutral"} />
            <MetricCard label="다음 체크" value={formatKstShort(forwardPaper.next_check_time_utc)} title={formatKstDateTime(forwardPaper.next_check_time_utc)} tone={forwardPaper.status === "RUNNING" ? "green" : "neutral"} />
            <MetricCard label="마지막 신호" value={forwardPaper.last_signal ?? "-"} tone={forwardPaper.last_signal === "BUY" ? "green" : forwardPaper.last_signal === "SELL" ? "red" : "neutral"} />
            <MetricCard label="리스크 상태" value={<StatusBadge value={formatRiskStatus(forwardPaper.risk_status)} tone={forwardPaper.risk_status?.startsWith("BLOCKED") ? "red" : forwardPaper.status === "RUNNING" ? "green" : "amber"} />} title={forwardPaper.risk_status} />
            <MetricCard label="실행 시간" value={formatRuntimeDuration(forwardPaper.started_at, forwardPaper.stopped_at)} />
          </section>

          <section className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-8">
            <MetricCard label="초기 원화 잔고" value={formatKrw(forwardBalance?.initial_cash)} />
            <MetricCard label="현재 원화 잔고" value={formatKrw(forwardBalance?.cash_krw)} />
            <MetricCard label="현재 보유 수량" value={formatNumber(forwardPosition?.btc_quantity)} tone="cyan" />
            <MetricCard label="평균 매수가" value={formatKrw(forwardPosition?.avg_buy_price)} />
            <MetricCard label="실현 손익" value={formatKrw(forwardBalance?.realized_pnl)} tone={pnlTone(forwardBalance?.realized_pnl)} />
            <MetricCard label="평가 손익" value={formatKrw(forwardBalance?.unrealized_pnl)} tone={pnlTone(forwardBalance?.unrealized_pnl)} />
            <MetricCard label="총 평가자산" value={formatKrw(forwardBalance?.equity)} tone={pnlTone(forwardBalance?.total_pnl)} />
            <MetricCard label="총 수익률" value={formatPercent(forwardBalance?.total_return)} tone={pnlTone(forwardBalance?.total_return)} />
            <MetricCard label="Max Drawdown" value={formatPercent(forwardMetrics?.mdd)} tone="red" />
          </section>

          <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
            <div className="space-y-4">
              <div className="border border-terminal-line bg-terminal-panel">
                <div className="flex items-center justify-between border-b border-terminal-line px-4 py-3">
                  <span className="text-sm font-semibold">Backtest vs Forward Paper 비교</span>
                  <span className="text-xs text-slate-500">{forwardCandidate ? `${formatPeriodLabel(forwardCandidate.backtest_period)} 백테스트 기준` : "후보 전략 대기"}</span>
                </div>
                <div className="table-scroll overflow-auto">
                  <table className="ops-table min-w-[760px] w-full text-left text-sm">
                    <thead className="sticky top-0 bg-terminal-panel2 text-xs text-slate-500">
                      <tr>
                        <th className="px-3 py-2">Metric</th>
                        <th className="px-3 py-2 text-right">Backtest</th>
                        <th className="px-3 py-2 text-right">Forward Paper</th>
                        <th className="px-3 py-2 text-right">차이</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[
                        ["Total Return", forwardCandidate?.backtest_total_return, forwardMetrics?.total_return, "percent"],
                        ["MDD", forwardCandidate?.backtest_mdd, forwardMetrics?.mdd, "percent"],
                        ["Win Rate", forwardCandidate?.backtest_win_rate, forwardMetrics?.win_rate, "percent"],
                        ["Profit Factor", forwardCandidate?.backtest_profit_factor, forwardMetrics?.profit_factor, "decimal"],
                        ["Trade Count", forwardCandidate?.backtest_trade_count, forwardMetrics?.trade_count, "count"],
                        ["Average Trade PnL", forwardCandidate?.backtest_average_trade_pnl, forwardMetrics?.average_trade_pnl, "krw"]
                      ].map(([label, backtestValue, forwardValue, kind]) => {
                        const backtestNumber = typeof backtestValue === "number" ? backtestValue : undefined;
                        const forwardNumber = typeof forwardValue === "number" ? forwardValue : undefined;
                        const diff = backtestNumber != null && forwardNumber != null ? forwardNumber - backtestNumber : undefined;
                        const render = (value?: number) => {
                          if (kind === "percent") return formatPercent(value);
                          if (kind === "krw") return formatKrw(value);
                          if (kind === "count") return value == null ? "-" : String(value);
                          return formatDecimal(value);
                        };
                        return (
                          <tr key={String(label)} className="border-t border-terminal-line">
                            <td className="px-3 py-2 text-slate-300">{label}</td>
                            <td className="mono-num px-3 py-2 text-right">{render(backtestNumber)}</td>
                            <td className={`mono-num px-3 py-2 text-right ${label === "MDD" ? "text-terminal-red" : toneClass(pnlTone(forwardNumber))}`}>{render(forwardNumber)}</td>
                            <td className={`mono-num px-3 py-2 text-right ${toneClass(pnlTone(diff))}`}>{render(diff)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>

              <div className="border border-terminal-line bg-terminal-panel">
                <div className="flex items-center justify-between border-b border-terminal-line px-4 py-3">
                  <span className="text-sm font-semibold">Forward Paper 주문 로그</span>
                  <span className="text-xs text-slate-500">{forwardOrders.length}건, 주문 소스: PaperBroker</span>
                </div>
                <div className="table-scroll max-h-80 overflow-auto">
                  <table className="ops-table min-w-[1260px] w-full text-left text-sm">
                    <thead className="sticky top-0 z-10 bg-terminal-panel2 text-xs text-slate-500">
                      <tr>
                        <th className="px-3 py-2">시간 (KST)</th>
                        <th className="px-3 py-2">캔들 (KST)</th>
                        <th className="px-3 py-2">마켓</th>
                        <th className="px-3 py-2">TF</th>
                        <th className="px-3 py-2">전략</th>
                        <th className="px-3 py-2">방향</th>
                        <th className="px-3 py-2 text-right">가격</th>
                        <th className="px-3 py-2 text-right">수량</th>
                        <th className="px-3 py-2 text-right">금액</th>
                        <th className="px-3 py-2 text-right">수수료</th>
                        <th className="px-3 py-2 text-right">실현 손익</th>
                        <th className="px-3 py-2">Risk Result</th>
                        <th className="px-3 py-2">Reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {forwardOrders.slice().reverse().map((order, index) => (
                        <tr key={`${order.time}-${index}`} className="border-t border-terminal-line">
                          <td className="nowrap px-3 py-2 text-slate-400" title={formatKstDateTime(order.created_at ?? order.time)}>{formatKstShort(order.created_at ?? order.time)}</td>
                          <td className="nowrap px-3 py-2 text-slate-400" title={formatKstDateTime(order.candle_timestamp ?? order.time)}>{formatKstShort(order.candle_timestamp ?? order.time)}</td>
                          <td className="nowrap px-3 py-2 font-semibold">{order.market}</td>
                          <td className="nowrap px-3 py-2">{formatTimeframe(forwardPaper.unit)}</td>
                          <td className="nowrap px-3 py-2"><StatusBadge value={STRATEGY_BADGES[order.strategy]} tone="cyan" /></td>
                          <td className="px-3 py-2"><SideBadge side={order.side} /></td>
                          <td className="mono-num px-3 py-2 text-right">{formatKrw(order.execution_price)}</td>
                          <td className="mono-num px-3 py-2 text-right">{formatNumber(order.quantity)}</td>
                          <td className="mono-num px-3 py-2 text-right">{formatKrw(order.amount_krw)}</td>
                          <td className="mono-num px-3 py-2 text-right">{formatKrw(order.fee)}</td>
                          <td className={`mono-num px-3 py-2 text-right ${toneClass(pnlTone(order.realized_pnl))}`}>
                            {order.realized_pnl == null ? "-" : formatKrw(order.realized_pnl)}
                          </td>
                          <td className="nowrap px-3 py-2"><StatusBadge value={formatRiskStatus(order.risk_check_result)} tone={order.risk_check_result === "PASS" ? "green" : "red"} /></td>
                          <td className="max-w-[260px] truncate px-3 py-2 text-slate-300" title={formatSignalReason(order.reason)}>{formatSignalReason(order.reason)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>

            <div className="space-y-4">
              <div className="border border-terminal-line bg-terminal-panel">
                <div className="flex items-center justify-between border-b border-terminal-line px-4 py-3">
                  <span className="text-sm font-semibold">Forward Equity / Drawdown</span>
                  <span className="text-xs text-slate-500">{forwardEquityPoints.length}개 포인트</span>
                </div>
                <div className="p-4">
                  <BacktestEquityGraph points={forwardEquityPoints} />
                </div>
              </div>

              <div className="border border-terminal-line bg-terminal-panel">
                <div className="flex items-center justify-between border-b border-terminal-line px-4 py-3">
                  <span className="text-sm font-semibold">Forward Tick 로그</span>
                  <span className="text-xs text-slate-500">최근 50건</span>
                </div>
                <div className="table-scroll max-h-80 overflow-auto">
                  <table className="ops-table min-w-[720px] w-full text-left text-sm">
                    <thead className="sticky top-0 bg-terminal-panel2 text-xs text-slate-500">
                      <tr>
                        <th className="px-3 py-2">Tick</th>
                        <th className="px-3 py-2">결과</th>
                        <th className="px-3 py-2">최신 캔들</th>
                        <th className="px-3 py-2">마지막 처리</th>
                        <th className="px-3 py-2">메시지</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(forwardPaper.tick_logs ?? []).map((log, index) => (
                        <tr key={`${log.tick_time_utc}-${index}`} className="border-t border-terminal-line">
                          <td className="nowrap px-3 py-2 text-slate-400" title={formatKstDateTime(log.tick_time_utc)}>{formatKstShort(log.tick_time_utc)}</td>
                          <td className="nowrap px-3 py-2"><StatusBadge value={formatRiskStatus(log.result)} tone={log.result === "ERROR" || log.result === "BLOCKED_BY_RISK" ? "red" : log.result === "NO_NEW_CANDLE" ? "amber" : "green"} /></td>
                          <td className="nowrap px-3 py-2 text-slate-400" title={formatKstDateTime(log.latest_candle_time_utc)}>{formatKstShort(log.latest_candle_time_utc)}</td>
                          <td className="nowrap px-3 py-2 text-slate-400" title={formatKstDateTime(log.last_processed_candle_time_utc)}>{formatKstShort(log.last_processed_candle_time_utc)}</td>
                          <td className="max-w-[260px] truncate px-3 py-2 text-slate-300" title={log.message}>{log.message}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section className="grid grid-cols-1 gap-3 px-4 pt-4 md:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-8">
          <MetricCard label="모드" value={paperMode} tone="cyan" />
          <MetricCard label="세션 상태" value={<StatusBadge value={formatSessionStatus(sessionStatus)} tone={statusTone(sessionStatus)} />} tone={statusTone(sessionStatus)} title={sessionStatus} />
          <MetricCard label="마켓" value={paper.market ?? market} />
          <MetricCard label="타임프레임" value={formatTimeframe(paper.unit ?? unit)} />
          <MetricCard label="전략" value={<StatusBadge value={STRATEGY_BADGES[displayedStrategy]} tone="cyan" />} title={STRATEGY_LABELS[displayedStrategy]} />
          <MetricCard label="마지막 처리 캔들 (KST)" value={formatKstShort(paper.last_processed_candle_time_utc)} title={formatKstDateTime(paper.last_processed_candle_time_utc)} tone="amber" />
          <MetricCard label="마지막 신호" value={paper.last_signal ?? "-"} tone={paper.last_signal === "BUY" ? "green" : paper.last_signal === "SELL" ? "red" : "neutral"} />
          <MetricCard
            label={paper.next_check_time_utc ? "다음 확인 시간 (KST)" : "마지막 갱신 (KST)"}
            value={formatKstShort(paper.next_check_time_utc ?? paper.updated_at)}
            title={formatKstDateTime(paper.next_check_time_utc ?? paper.updated_at)}
            tone={paper.status === "RUNNING" ? "green" : "neutral"}
          />
          <MetricCard label="리스크 상태" value={<StatusBadge value={formatRiskStatus(riskStatus)} tone={riskStatus === "ACTIVE" ? "green" : "amber"} />} tone={riskStatus === "ACTIVE" ? "green" : "amber"} title={riskStatus} />
        </section>

        <section className="grid grid-cols-1 gap-3 px-4 pt-4 md:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-7">
          <MetricCard label="가상 원화 잔고" value={formatKrw(balance?.cash_krw)} />
          <MetricCard label="현재 보유 수량" value={formatNumber(position?.btc_quantity)} tone="cyan" />
          <MetricCard label="평균 매수가" value={formatKrw(position?.avg_buy_price)} />
          <MetricCard label="실현 손익" value={formatKrw(balance?.realized_pnl)} tone={pnlTone(balance?.realized_pnl)} />
          <MetricCard label="평가 손익" value={formatKrw(balance?.unrealized_pnl)} tone={pnlTone(balance?.unrealized_pnl)} />
          <MetricCard label="총 평가자산" value={formatKrw(balance?.equity)} tone={pnlTone(totalPnl)} />
          <MetricCard label="총 수익률" value={formatPercent(balance?.total_return)} tone={pnlTone(balance?.total_return)} />
        </section>

        <section className="grid flex-1 grid-cols-1 gap-4 p-4 xl:grid-cols-[minmax(0,1fr)_340px]">
          <div className="border border-terminal-line bg-terminal-panel">
            <div className="flex items-center justify-between border-b border-terminal-line px-4 py-3">
              <div className="flex items-center gap-2">
                <BarChart3 className="h-4 w-4 text-terminal-cyan" />
                <span className="text-sm font-semibold">캔들 차트</span>
              </div>
              <span className="text-xs text-slate-500">
                {displayCandles.length}개 캔들, 축 시간 KST, 차트 갱신 {formatKstShort(chartUpdatedAt)}
              </span>
            </div>
            <ChartPanel candles={displayCandles} signals={chartSignals} />
            {(error || paperError) && <div className="border-t border-terminal-line px-4 py-3 text-sm text-terminal-red">{error ?? paperError}</div>}
          </div>

          <aside className="space-y-4">
            <div className="border border-terminal-line bg-terminal-panel">
              <div className="border-b border-terminal-line px-4 py-3 text-sm font-semibold">전략 설정값</div>
              <div className="space-y-3 p-4">
                {Object.entries(settings).map(([key, value]) => (
                  <NumberField
                    key={key}
                    label={formatFieldLabel(key)}
                    value={value}
                    step={key === "k" ? "0.1" : "1"}
                    onChange={(next) => {
                      setSettings((prev) => ({ ...prev, [key]: next }));
                      setStrategySettings((prev) => ({
                        ...prev,
                        [strategy]: { ...prev[strategy], [key]: next }
                      }));
                    }}
                  />
                ))}
              </div>
            </div>

            <div className="border border-terminal-line bg-terminal-panel">
              <div className="flex items-center gap-2 border-b border-terminal-line px-4 py-3 text-sm font-semibold">
                <ShieldCheck className="h-4 w-4 text-terminal-amber" />
                백테스트 리스크
              </div>
              <div className="space-y-3 p-4">
                {Object.entries(backtestRisk).map(([key, value]) => (
                  <NumberField
                    key={key}
                    label={formatFieldLabel(key)}
                    value={value}
                    step={key === "fee_rate" || key === "position_size" ? "0.0001" : "100000"}
                    onChange={(next) => setBacktestRisk((prev) => ({ ...prev, [key]: next }))}
                  />
                ))}
              </div>
            </div>

            <div className="border border-terminal-line bg-terminal-panel">
              <div className="flex items-center gap-2 border-b border-terminal-line px-4 py-3 text-sm font-semibold">
                <Wallet className="h-4 w-4 text-terminal-green" />
                페이퍼 리스크
              </div>
              <div className="space-y-3 p-4">
                {Object.entries(paperRisk).map(([key, value]) => (
                  <NumberField
                    key={key}
                    label={formatFieldLabel(key)}
                    value={value}
                    step={key.includes("rate") || key.includes("ratio") ? "0.0001" : "10000"}
                    onChange={(next) => setPaperRisk((prev) => ({ ...prev, [key]: next }))}
                  />
                ))}
              </div>
            </div>
          </aside>
        </section>

        <section className="grid grid-cols-1 gap-4 px-4 pb-4 xl:grid-cols-[minmax(0,1fr)_520px]">
          <div className="space-y-4">
            <div className="border border-terminal-line bg-terminal-panel">
              <div className="flex items-center justify-between border-b border-terminal-line px-4 py-3">
                <span className="text-sm font-semibold">페이퍼 손익 그래프</span>
                <span className="text-xs text-slate-500">{equityPoints.length}개 포인트</span>
              </div>
              <div className="p-4">
                <PnlGraph points={equityPoints} initialCash={balance?.initial_cash ?? DEFAULT_PAPER_RISK.initial_cash} />
              </div>
            </div>

            <div className="border border-terminal-line bg-terminal-panel">
              <div className="flex items-center justify-between border-b border-terminal-line px-4 py-3">
                <span className="text-sm font-semibold">페이퍼 주문 로그</span>
                <span className="text-xs text-slate-500">{paperOrders.length}건, 주문 소스: PaperBroker</span>
              </div>
              <div className="table-scroll max-h-80 overflow-auto">
                <table className="ops-table min-w-[1320px] w-full text-left text-sm">
                  <thead className="sticky top-0 z-10 bg-terminal-panel2 text-xs text-slate-500">
                    <tr>
                      <th className="px-3 py-2">생성 시각</th>
                      <th className="px-3 py-2">캔들 시각</th>
                      <th className="px-3 py-2">마켓</th>
                      <th className="px-3 py-2">방향</th>
                      <th className="px-3 py-2 text-right">체결가</th>
                      <th className="px-3 py-2 text-right">수량</th>
                      <th className="px-3 py-2 text-right">주문 금액</th>
                      <th className="px-3 py-2 text-right">수수료</th>
                      <th className="px-3 py-2 text-right">실현 손익</th>
                      <th className="px-3 py-2">전략</th>
                      <th className="px-3 py-2">리스크</th>
                      <th className="px-3 py-2">소스</th>
                      <th className="px-3 py-2">차단</th>
                      <th className="px-3 py-2">신호 사유</th>
                    </tr>
                  </thead>
                  <tbody>
                    {paperOrders.slice().reverse().map((order, index) => {
                      const realizedPnl = order.realized_pnl ?? 0;
                      const candleTimestamp = order.candle_timestamp ?? order.time;
                      const createdAt = order.created_at ?? order.time;
                      const orderReason = formatSignalReason(order.blocked_reason || order.signal_reason || order.reason);
                      return (
                        <tr key={`${order.time}-${index}`} className="border-t border-terminal-line">
                          <td className="nowrap px-3 py-2 text-slate-400" title={formatKstDateTime(createdAt)}>{formatKstShort(createdAt)}</td>
                          <td className="nowrap px-3 py-2 text-slate-400" title={formatKstDateTime(candleTimestamp)}>{formatKstShort(candleTimestamp)}</td>
                          <td className="nowrap px-3 py-2 font-semibold">{order.market}</td>
                          <td className="px-3 py-2"><SideBadge side={order.side} /></td>
                          <td className="mono-num px-3 py-2 text-right">{formatKrw(order.execution_price)}</td>
                          <td className="mono-num px-3 py-2 text-right">{formatNumber(order.quantity)}</td>
                          <td className="mono-num px-3 py-2 text-right">{formatKrw(order.amount_krw ?? order.execution_price * order.quantity)}</td>
                          <td className="mono-num px-3 py-2 text-right">{formatKrw(order.fee)}</td>
                          <td className={`mono-num px-3 py-2 text-right ${toneClass(pnlTone(realizedPnl))}`}>
                            {order.realized_pnl == null ? "-" : formatKrw(order.realized_pnl)}
                          </td>
                          <td className="nowrap px-3 py-2" title={STRATEGY_LABELS[order.strategy]}>
                            <StatusBadge value={STRATEGY_BADGES[order.strategy]} tone="cyan" />
                          </td>
                          <td className="nowrap px-3 py-2">
                            <StatusBadge value={(order.risk_check_result ?? "PASS") === "PASS" ? "통과" : order.risk_check_result ?? "-"} tone={(order.risk_check_result ?? "PASS") === "PASS" ? "green" : "red"} />
                          </td>
                          <td className="nowrap px-3 py-2 text-slate-300">{order.order_source ?? "PaperBroker"}</td>
                          <td className="nowrap px-3 py-2" title={order.blocked_reason ?? ""}>
                            <StatusBadge value={order.blocked ? "차단" : "정상"} tone={order.blocked ? "red" : "green"} />
                          </td>
                          <td className="max-w-[280px] truncate px-3 py-2 text-slate-300" title={orderReason}>
                            {orderReason}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <div className="space-y-4">
            <div className="border border-terminal-line bg-terminal-panel">
              <div className="flex items-center justify-between border-b border-terminal-line px-4 py-3">
                <span className="text-sm font-semibold">전략 비교 테이블</span>
                <span className="text-xs text-slate-500">{backtestCandleCount}개 캔들 기준</span>
              </div>
              <div className="table-scroll max-h-64 overflow-auto">
                <table className="ops-table min-w-[760px] w-full text-left text-sm">
                  <thead className="sticky top-0 bg-terminal-panel2 text-xs text-slate-500">
                    <tr>
                      <th className="px-3 py-2">전략</th>
                      <th className="px-3 py-2 text-right">총 수익률</th>
                      <th className="px-3 py-2 text-right">MDD</th>
                      <th className="px-3 py-2 text-right">승률</th>
                      <th className="px-3 py-2 text-right">거래</th>
                      <th className="px-3 py-2 text-right">Profit Factor</th>
                      <th className="px-3 py-2 text-right">최종 자산</th>
                      <th className="px-3 py-2 text-right">Score</th>
                    </tr>
                  </thead>
                  <tbody>
                    {comparisonRows.map((row) => {
                      const rowResult = comparisonResults.find((item) => item.strategy === row.strategy);
                      return (
                        <tr
                          key={row.strategy}
                          className={`cursor-pointer border-t border-terminal-line ${result?.strategy === row.strategy ? "bg-[#10202a]" : ""}`}
                          onClick={() => rowResult && setResult(rowResult)}
                        >
                          <td className="nowrap px-3 py-2" title={STRATEGY_LABELS[row.strategy]}>
                            <StatusBadge value={STRATEGY_BADGES[row.strategy]} tone="cyan" />
                          </td>
                          <td className={`mono-num px-3 py-2 text-right ${toneClass(pnlTone(row.total_return))}`}>{formatPercent(row.total_return)}</td>
                          <td className="mono-num px-3 py-2 text-right text-terminal-red">{formatPercent(row.mdd)}</td>
                          <td className="mono-num px-3 py-2 text-right">{formatPercent(row.win_rate)}</td>
                          <td className="mono-num px-3 py-2 text-right">{row.trade_count}</td>
                          <td className="mono-num px-3 py-2 text-right">{formatDecimal(row.profit_factor)}</td>
                          <td className="mono-num px-3 py-2 text-right">{formatKrw(row.final_equity)}</td>
                          <td className="mono-num px-3 py-2 text-right text-terminal-amber">{formatDecimal(row.score)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="border border-terminal-line bg-terminal-panel">
              <div className="flex items-center justify-between border-b border-terminal-line px-4 py-3">
                <span className="text-sm font-semibold">백테스트 Equity / Drawdown</span>
                <span className="text-xs text-slate-500">{result ? STRATEGY_LABELS[result.strategy] : "-"}</span>
              </div>
              <div className="p-4">
                <BacktestEquityGraph points={result?.equity_curve ?? []} />
              </div>
            </div>

            <div className="border border-terminal-line bg-terminal-panel">
              <div className="border-b border-terminal-line px-4 py-3 text-sm font-semibold">백테스트 성과 요약</div>
              <div className="grid grid-cols-2 gap-3 p-4">
                <MetricCard label="총 수익률" value={formatPercent(metrics?.total_return)} tone={pnlTone(metrics?.total_return)} />
                <MetricCard label="최종 평가자산" value={formatKrw(metrics?.final_equity)} tone={pnlTone(metrics?.total_return)} />
                <MetricCard label="실현 손익" value={formatKrw(metrics?.realized_pnl)} tone={pnlTone(metrics?.realized_pnl)} />
                <MetricCard label="MDD" value={formatPercent(metrics?.mdd)} tone="red" />
                <MetricCard label="승률" value={formatPercent(metrics?.win_rate)} />
                <MetricCard label="거래 횟수" value={String(metrics?.trade_count ?? "-")} />
                <MetricCard label="평균 수익" value={formatPercent(metrics?.average_profit)} tone="green" />
                <MetricCard label="평균 손실" value={formatPercent(metrics?.average_loss)} tone="red" />
                <MetricCard label="Profit Factor" value={formatDecimal(metrics?.profit_factor)} tone="amber" />
                <MetricCard label="평균 보유 시간" value={formatHoldingTime(metrics?.average_holding_time_minutes)} />
                <MetricCard label="Score" value={formatDecimal(metrics?.score)} tone="amber" />
                <MetricCard label="마지막 신호" value={metrics?.last_signal ?? "-"} tone={metrics?.last_signal === "BUY" ? "green" : metrics?.last_signal === "SELL" ? "red" : "neutral"} />
              </div>
            </div>

            <div className="border border-terminal-line bg-terminal-panel">
              <div className="border-b border-terminal-line px-4 py-3 text-sm font-semibold">백테스트 거래 로그</div>
              <div className="max-h-64 overflow-auto">
                <table className="ops-table min-w-[1120px] w-full text-left text-sm">
                  <thead className="sticky top-0 bg-terminal-panel2 text-xs text-slate-500">
                    <tr>
                      <th className="px-3 py-2">시간 (KST)</th>
                      <th className="px-3 py-2">마켓</th>
                      <th className="px-3 py-2">전략</th>
                      <th className="px-3 py-2">방향</th>
                      <th className="px-3 py-2 text-right">가격</th>
                      <th className="px-3 py-2 text-right">수량</th>
                      <th className="px-3 py-2 text-right">금액</th>
                      <th className="px-3 py-2 text-right">수수료</th>
                      <th className="px-3 py-2 text-right">실현 손익</th>
                      <th className="px-3 py-2">사유</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(result?.orders ?? []).slice().reverse().map((order, index) => (
                      <tr key={`${order.time}-${index}`} className="border-t border-terminal-line">
                        <td className="nowrap px-3 py-2 text-slate-400" title={formatKstDateTime(order.time)}>{formatKstShort(order.time)}</td>
                        <td className="nowrap px-3 py-2 font-semibold">{order.market ?? market}</td>
                        <td className="nowrap px-3 py-2" title={STRATEGY_LABELS[(order.strategy ?? result?.strategy ?? strategy) as Strategy]}>
                          <StatusBadge value={STRATEGY_BADGES[(order.strategy ?? result?.strategy ?? strategy) as Strategy]} tone="cyan" />
                        </td>
                        <td className="px-3 py-2"><SideBadge side={order.side} /></td>
                        <td className="mono-num px-3 py-2 text-right">{formatKrw(order.price)}</td>
                        <td className="mono-num px-3 py-2 text-right">{formatNumber(order.quantity)}</td>
                        <td className="mono-num px-3 py-2 text-right">{formatKrw(order.amount_krw ?? order.price * order.quantity)}</td>
                        <td className="mono-num px-3 py-2 text-right">{formatKrw(order.fee)}</td>
                        <td className={`mono-num px-3 py-2 text-right ${toneClass(pnlTone(order.realized_pnl ?? order.pnl))}`}>
                          {order.realized_pnl == null && order.pnl == null ? "-" : formatKrw(order.realized_pnl ?? order.pnl ?? 0)}
                        </td>
                        <td className="max-w-[260px] truncate px-3 py-2 text-slate-300" title={formatSignalReason(order.reason)}>
                          {formatSignalReason(order.reason)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
