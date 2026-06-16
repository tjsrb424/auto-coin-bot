import React from "react";
import ReactDOM from "react-dom/client";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Bell,
  Bot,
  CheckCircle2,
  ClipboardList,
  Copy,
  DollarSign,
  Download,
  FileText,
  Filter,
  Gauge,
  History,
  Home,
  LineChart,
  Link,
  Lock,
  Menu,
  PauseCircle,
  PieChart,
  Play,
  Power,
  Plus,
  RefreshCw,
  Save,
  Search,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Target,
  TestTube2,
  TrendingUp,
  UserCircle,
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
  risk_level?: string;
  block_code?: string | null;
  block_reason?: string | null;
  warnings?: string[];
  max_allowed_order_krw?: number;
  checks?: Record<string, { allowed?: boolean; code?: string; reason?: string; detail?: unknown }>;
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
  executed_volume?: number;
  remaining_volume?: number;
  filled_amount_krw?: number;
  paid_fee?: number;
  strategy_name?: string | null;
  signal_reason?: string | null;
  candle_time_utc?: string | null;
};

type RiskState = {
  status: "OK" | "WARNING" | "BLOCKED" | "EMERGENCY_STOPPED" | "MANUAL_REVIEW_REQUIRED";
  daily_realized_pnl: number;
  daily_unrealized_pnl: number;
  daily_total_pnl: number;
  daily_loss_percent: number;
  daily_order_count: number;
  daily_entry_count: number;
  daily_exit_count: number;
  consecutive_loss_count: number;
  open_order_count: number;
  open_position_count: number;
  last_order_time_utc?: string | null;
  emergency_stop_enabled: boolean;
  balance_mismatch_detected: boolean;
  partial_fill_detected: boolean;
  volatility_block_enabled: boolean;
  low_volume_block_enabled: boolean;
};

type RiskDashboard = {
  risk_state: RiskState;
  risk_logs: Array<{ id: number; risk_level: string; allowed: boolean; block_code?: string | null; block_reason?: string | null; created_at: string }>;
  config: Record<string, unknown>;
};

type RecoveryEvent = {
  id: number;
  event_type: string;
  severity: string;
  exchange: string;
  market: string;
  session_id?: number | null;
  request_id?: string | null;
  order_uuid?: string | null;
  message: string;
  created_at: string;
};

type AutoLivePilotSession = {
  id: number;
  exchange: string;
  market: string;
  candidate_strategy_id?: number | null;
  strategy_name: string;
  status: "READY" | "RUNNING" | "LIVE_PAUSED" | "STOPPED" | "ERROR" | "EMERGENCY_STOPPED";
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
  partial_fill_policy?: string;
  restart_policy?: string;
  recent_recovery_events?: RecoveryEvent[];
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
  status: "READY" | "RUNNING" | "LIVE_PAUSED" | "PAUSED" | "STOPPED" | "ERROR" | "EMERGENCY_STOPPED";
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
  session_id?: number;
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

type ExitCandidate = {
  id: number;
  position_id: number;
  session_id: number;
  exchange: string;
  market: string;
  reason: string;
  status: string;
  entry_price: number;
  current_price: number;
  target_exit_price: number;
  volume: number;
  expected_amount_krw: number;
  expected_fee: number;
  expected_pnl: number;
  risk_result: string;
  created_at: string;
  updated_at: string;
};

type LiveStrategyStatus = {
  session?: LiveStrategySession | null;
  position?: LivePosition | null;
  exit_candidate?: ExitCandidate | null;
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
  auto_exit_enabled?: boolean;
  exit_order_type?: string;
  exit_price_offset_percent?: number;
  cancel_exit_order_after_seconds?: number;
  max_exit_retry_count?: number;
  manual_confirm_required?: boolean;
  partial_fill_policy?: string;
  restart_policy?: string;
  recent_recovery_events?: RecoveryEvent[];
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
  title,
  icon
}: {
  label: string;
  value: React.ReactNode;
  tone?: Tone;
  title?: string;
  icon?: React.ReactNode;
}) {
  return (
    <div className="metric-card border border-terminal-line bg-terminal-panel2 px-3 py-2" title={title}>
      {icon && <div className={`metric-icon ${toneClass(tone)}`}>{icon}</div>}
      <div className="min-w-0">
        <div className="text-[11px] uppercase text-slate-500">{label}</div>
        <div className={`mt-1 min-h-7 truncate text-lg font-semibold ${toneClass(tone)}`}>{value}</div>
      </div>
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
  const [riskDashboard, setRiskDashboard] = React.useState<RiskDashboard | null>(null);
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
  const [exitPlaceConfirmation, setExitPlaceConfirmation] = React.useState("");
  const [exitPreviewRequestId, setExitPreviewRequestId] = React.useState<string | null>(null);
  const [liveEmergencyResetConfirmation, setLiveEmergencyResetConfirmation] = React.useState("");
  const [sidebarCollapsed, setSidebarCollapsed] = React.useState(false);
  const [activeView, setActiveView] = React.useState("dashboard");
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
  const balanceRefreshEventsRef = React.useRef<Set<string>>(new Set());

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

  const refreshLiveBalancesSilently = React.useCallback(async () => {
    try {
      const params = new URLSearchParams({ exchange: liveExchange });
      const response = await fetch(`${API_BASE}/api/live/balances?${params.toString()}`);
      if (!response.ok) return;
      const body = (await response.json()) as LiveBalances;
      setLiveBalances(body);
      setLiveStatus(body);
    } catch {
      // Silent refresh should not interrupt the trading dashboard.
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

  const fetchRiskDashboard = React.useCallback(async () => {
    const response = await fetch(`${API_BASE}/api/risk/status?${new URLSearchParams({ exchange: "bithumb" }).toString()}`);
    if (response.ok) {
      setRiskDashboard(await response.json());
    }
  }, []);

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
      await fetchRiskDashboard();
      await refreshLiveBalancesSilently();
    } catch (err) {
      setAutoPilotError(err instanceof Error ? err.message : "Auto Pilot 시작 오류가 발생했습니다.");
    } finally {
      setAutoPilotLoading(false);
    }
  }, [autoPilot?.max_auto_order_krw, autoPilot?.min_auto_order_krw, autoPilotAmount, autoPilotCandidateId, fetchLiveOrders, fetchRiskDashboard, refreshLiveBalancesSilently]);

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
      await fetchRiskDashboard();
      await refreshLiveBalancesSilently();
    } finally {
      setAutoPilotLoading(false);
    }
  }, [fetchLiveOrders, refreshLiveBalancesSilently]);

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
      await fetchRiskDashboard();
      await refreshLiveBalancesSilently();
    } catch (err) {
      setLiveStrategyError(err instanceof Error ? err.message : "Auto Strategy start failed.");
    } finally {
      setLiveStrategyLoading(false);
    }
  }, [fetchLiveOrders, fetchRiskDashboard, liveStrategyCandidateId, refreshLiveBalancesSilently]);

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
      await fetchRiskDashboard();
      await refreshLiveBalancesSilently();
    } finally {
      setLiveStrategyLoading(false);
    }
  }, [fetchLiveOrders, fetchRiskDashboard, refreshLiveBalancesSilently]);

  const approveExitCandidate = React.useCallback(async () => {
    const candidate = liveStrategy?.exit_candidate;
    if (!candidate) return;
    setLiveStrategyLoading(true);
    setLiveStrategyError(null);
    try {
      const response = await fetch(`${API_BASE}/api/live-exit-candidates/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ candidate_id: candidate.id })
      });
      const body = await response.json();
      if (!body.ok) setLiveStrategyError(body.message ?? "Exit candidate approval failed.");
      await fetchLiveStrategyStatus();
    } finally {
      setLiveStrategyLoading(false);
    }
  }, [fetchLiveStrategyStatus, liveStrategy?.exit_candidate]);

  const rejectExitCandidate = React.useCallback(async () => {
    const candidate = liveStrategy?.exit_candidate;
    if (!candidate) return;
    setLiveStrategyLoading(true);
    setLiveStrategyError(null);
    try {
      const response = await fetch(`${API_BASE}/api/live-exit-candidates/reject`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ candidate_id: candidate.id })
      });
      const body = await response.json();
      if (!body.ok) setLiveStrategyError(body.message ?? "Exit candidate reject failed.");
      setExitPreviewRequestId(null);
      await fetchLiveStrategyStatus();
    } finally {
      setLiveStrategyLoading(false);
    }
  }, [fetchLiveStrategyStatus, liveStrategy?.exit_candidate]);

  const createExitPreview = React.useCallback(async () => {
    const candidate = liveStrategy?.exit_candidate;
    if (!candidate) return;
    setLiveStrategyLoading(true);
    setLiveStrategyError(null);
    try {
      const response = await fetch(`${API_BASE}/api/live-exit-orders/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ exit_candidate_id: candidate.id, manual_confirmed: true })
      });
      const body = await response.json();
      if (body.request_id) setExitPreviewRequestId(body.request_id);
      if (!body.ok) setLiveStrategyError(body.preview?.blocked_reason ?? body.risk_result ?? "Exit preview blocked.");
      await fetchLiveOrders();
      await fetchLiveStrategyStatus();
    } finally {
      setLiveStrategyLoading(false);
    }
  }, [fetchLiveOrders, fetchLiveStrategyStatus, liveStrategy?.exit_candidate]);

  const submitExitOrder = React.useCallback(async () => {
    if (!exitPreviewRequestId) return;
    setLiveStrategyLoading(true);
    setLiveStrategyError(null);
    try {
      const response = await fetch(`${API_BASE}/api/live-exit-orders/submit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ request_id: exitPreviewRequestId, final_confirmation: exitPlaceConfirmation })
      });
      const body = await response.json();
      if (!body.ok) setLiveStrategyError(body.message ?? body.risk_result ?? "Exit order submit failed.");
      await fetchLiveOrders();
      await fetchLiveStrategyStatus();
      await refreshLiveBalancesSilently();
    } finally {
      setLiveStrategyLoading(false);
    }
  }, [exitPlaceConfirmation, exitPreviewRequestId, fetchLiveOrders, fetchLiveStrategyStatus, refreshLiveBalancesSilently]);

  const cancelExitOrder = React.useCallback(async () => {
    if (!exitPreviewRequestId) return;
    setLiveStrategyLoading(true);
    setLiveStrategyError(null);
    try {
      const response = await fetch(`${API_BASE}/api/live-exit-orders/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ request_id: exitPreviewRequestId })
      });
      const body = await response.json();
      if (!body.ok) setLiveStrategyError(body.message ?? "Exit order cancel failed.");
      await fetchLiveOrders();
      await fetchLiveStrategyStatus();
    } finally {
      setLiveStrategyLoading(false);
    }
  }, [exitPreviewRequestId, fetchLiveOrders, fetchLiveStrategyStatus]);

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
    void fetchRiskDashboard();
    void fetchAutoPilotStatus();
    void fetchLiveStrategyStatus();
  }, [fetchAutoPilotStatus, fetchLiveStatus, fetchLiveStrategyStatus, fetchRiskDashboard, liveExchange]);

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
    await fetchRiskDashboard();
    await refreshLiveBalancesSilently();
  }, [fetchLiveOrders, fetchRiskDashboard, refreshLiveBalancesSilently]);

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
      await fetchRiskDashboard();
      await refreshLiveBalancesSilently();
    } catch (err) {
      setLiveError(err instanceof Error ? err.message : "알 수 없는 Emergency Stop 해제 오류가 발생했습니다.");
    } finally {
      setLiveLoading(false);
    }
  }, [fetchLiveOrders, fetchRiskDashboard, liveEmergencyResetConfirmation, refreshLiveBalancesSilently]);

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
      await fetchRiskDashboard();
    } catch (err) {
      setLiveError(err instanceof Error ? err.message : "알 수 없는 주문 미리보기 오류가 발생했습니다.");
    } finally {
      setLiveLoading(false);
    }
  }, [fetchLiveOrders, fetchRiskDashboard, liveExchange, liveOrderForm]);

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
      await fetchRiskDashboard();
      await refreshLiveBalancesSilently();
      setLivePreview(null);
      setLivePlaceConfirmation("");
    } catch (err) {
      setLiveError(err instanceof Error ? err.message : "알 수 없는 실주문 제출 오류가 발생했습니다.");
    } finally {
      setLiveLoading(false);
    }
  }, [fetchLiveOrders, fetchRiskDashboard, livePlaceConfirmation, livePreview, refreshLiveBalancesSilently]);

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
    void fetchRiskDashboard();
    void fetchAutoPilotStatus();
    void fetchLiveStrategyStatus();
  }, []);

  React.useEffect(() => {
    const intervalId = window.setInterval(() => {
      void fetchLiveStatus(liveExchange);
      void fetchLiveOrders();
      void fetchRiskDashboard();
      void fetchAutoPilotStatus();
      void fetchLiveStrategyStatus();
    }, 15000);
    return () => window.clearInterval(intervalId);
  }, [fetchAutoPilotStatus, fetchLiveOrders, fetchLiveStatus, fetchLiveStrategyStatus, fetchRiskDashboard, liveExchange]);

  React.useEffect(() => {
    const balanceImpactingStatuses = new Set(["SUBMITTED", "WAITING", "PARTIALLY_FILLED", "FILLED", "CANCELED"]);
    const events = [
      {
        source: "auto",
        sessionId: autoPilot?.session?.id,
        uuid: autoPilot?.session?.last_order_uuid,
        status: autoPilot?.session?.last_order_status,
        time: autoPilot?.session?.last_order_time_utc,
      },
      {
        source: "strategy",
        sessionId: liveStrategy?.session?.id,
        uuid: liveStrategy?.session?.current_open_order_uuid,
        status: liveStrategy?.session?.last_order_status,
        time: liveStrategy?.session?.last_order_time_utc,
      },
    ];
    for (const event of events) {
      if (!event.status || !balanceImpactingStatuses.has(event.status)) continue;
      const key = `${event.source}:${event.sessionId ?? "none"}:${event.uuid ?? "none"}:${event.status}:${event.time ?? ""}`;
      if (balanceRefreshEventsRef.current.has(key)) continue;
      balanceRefreshEventsRef.current.add(key);
      void refreshLiveBalancesSilently();
    }
  }, [
    autoPilot?.session?.id,
    autoPilot?.session?.last_order_status,
    autoPilot?.session?.last_order_time_utc,
    autoPilot?.session?.last_order_uuid,
    liveStrategy?.session?.current_open_order_uuid,
    liveStrategy?.session?.id,
    liveStrategy?.session?.last_order_status,
    liveStrategy?.session?.last_order_time_utc,
    refreshLiveBalancesSilently,
  ]);

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
  const riskState = riskDashboard?.risk_state;
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
  const liveExitCandidate = liveStrategy?.exit_candidate;
  const liveStrategyCandidate = candidateStrategies.find((candidate) => candidate.id === liveStrategyCandidateId);
  const isLiveStrategyOn = liveStrategySession?.status === "RUNNING" || liveStrategySession?.status === "READY";
  const liveStrategyFlow =
    liveExitCandidate?.status === "PENDING" ? "EXIT_CANDIDATE" :
    liveExitCandidate?.status === "APPROVED" ? "EXIT_APPROVED" :
    liveStrategyPosition?.status === "OPEN" ? "OPEN_POSITION" :
    liveStrategySession?.last_order_status === "CANCELED" ? "AUTO_CANCELED" :
    liveStrategySession?.last_order_status === "WAITING" ? "WAITING" :
    liveStrategySession?.last_order_status === "SUBMITTED" ? "SUBMITTED" :
    liveStrategySession?.last_order_status === "FILLED" ? "FILLED_POSITION_CREATED" :
    liveStrategySession?.last_order_status === "BLOCKED" ? "BLOCKED" :
    isLiveStrategyOn ? "WATCHING" : "STOPPED";
  const topStatusTone = liveStatus?.emergency_stop || riskState?.status === "EMERGENCY_STOPPED"
    ? "red"
    : riskState?.status === "BLOCKED" || currentLiveMode === "LIVE_MANUAL_ONLY" || currentLiveMode === "LIVE_ARMED"
      ? "amber"
      : "green";
  const navItems = [
    { id: "dashboard", label: "대시보드", icon: Home },
    { id: "auto-trade", label: "자동매매", icon: Bot },
    { id: "strategies", label: "전략관리", icon: ClipboardList },
    { id: "portfolio", label: "포트폴리오", icon: PieChart },
    { id: "trades", label: "거래내역", icon: History },
    { id: "backtest", label: "백테스트", icon: LineChart },
    { id: "alerts", label: "알림로그", icon: Bell },
    { id: "settings", label: "설정", icon: Settings }
  ];
  const showView = (id: string) => {
    setActiveView(id);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  const viewSection = (id: string, className: string) => `${className} ${activeView === id ? "" : "hidden"}`;
  const latestRiskLog = riskDashboard?.risk_logs?.[0];
  const latestLiveOrder = liveOrders[0];
  const estimatedPortfolioTotal = liveBalances?.estimated_total_equity_krw ?? balance?.equity ?? forwardBalance?.equity;
  const openLiveOrders = liveOrders.filter((order) => ["SUBMITTED", "WAITING", "PARTIALLY_FILLED"].includes(order.status));
  const filledLiveOrders = liveOrders.filter((order) => order.status === "FILLED");
  const latestDisplayCandle = displayCandles.length > 0 ? displayCandles[displayCandles.length - 1] : undefined;
  const strategyCardRows = candidateStrategies.length > 0
    ? candidateStrategies.slice(0, 6).map((candidate) => ({
      key: `candidate-${candidate.id}`,
      strategy: candidate.strategy,
      unit: candidate.unit,
      returnValue: candidate.backtest_total_return
    }))
    : comparisonRows.slice(0, 6).map((row) => ({
      key: `comparison-${row.strategy}`,
      strategy: row.strategy,
      unit,
      returnValue: row.total_return
    }));

  return (
    <main className="app-root min-h-screen bg-terminal-bg text-slate-100">
      <aside className={`app-sidebar ${sidebarCollapsed ? "is-collapsed" : ""}`}>
        <div className="app-brand">
          <div className="brand-mark">Q</div>
          {!sidebarCollapsed && <span>Auto Trader</span>}
        </div>
        <nav className="app-nav">
          {navItems.map((item, index) => {
            const Icon = item.icon;
            return (
              <button key={item.id} onClick={() => showView(item.id)} className={`app-nav-item ${activeView === item.id ? "is-active" : ""}`} title={item.label}>
                <Icon className="h-4 w-4" />
                {!sidebarCollapsed && <span>{item.label}</span>}
              </button>
            );
          })}
        </nav>
        <button className="app-collapse" onClick={() => setSidebarCollapsed((value) => !value)}>
          <Menu className="h-4 w-4" />
          {!sidebarCollapsed && <span>메뉴 접기</span>}
        </button>
      </aside>

      <div className="app-frame">
        <header className="app-topbar">
          <div className="topbar-left">
            <button className="icon-button" onClick={() => setSidebarCollapsed((value) => !value)} title="메뉴">
              <Menu className="h-5 w-5" />
            </button>
            <div>
              <h1>Auto Trader</h1>
              <p>개인용 코인 전략 실험실 · 제한적 자동매매 대시보드</p>
            </div>
          </div>
          <div className="topbar-center">
            <span className={`status-pill ${topStatusTone}`}>계정 상태 {riskState?.status ?? "대기"}</span>
            <label className="topbar-select">
              <span>거래소</span>
              <select value={liveExchange} onChange={(event) => setLiveExchange(event.target.value as Exchange)}>
                <option value="upbit">업비트 (Upbit)</option>
                <option value="bithumb">빗썸 (Bithumb)</option>
              </select>
            </label>
            <div className="search-box">
              <Search className="h-4 w-4" />
              <input placeholder="코인 심볼 또는 전략 검색" />
            </div>
          </div>
          <div className="topbar-actions">
            <button onClick={simulatePaperTrading} disabled={paperLoading} className="ghost-button">
              <RefreshCw className="h-4 w-4" />
              시뮬레이션
            </button>
            <button onClick={startLivePaperTrading} disabled={paperLoading || (paper.status === "RUNNING" && paper.mode === "LIVE")} className="success-button">
              <Play className="h-4 w-4" />
              페이퍼 시작
            </button>
            <button onClick={stopLivePaperTrading} disabled={paperLoading || paper.status !== "RUNNING" || paper.mode !== "LIVE"} className="danger-ghost-button">
              <PauseCircle className="h-4 w-4" />
              중지
            </button>
            <button className="icon-button has-alert" title="알림" onClick={() => showView("alerts")}>
              <Bell className="h-5 w-5" />
            </button>
            <div className="user-chip">
              <UserCircle className="h-5 w-5" />
              <span>Trader</span>
            </div>
          </div>
        </header>
        <div className={`mode-strip ${topStatusTone}`}>
          <div>
            <strong>{liveModeLabel(currentLiveMode)}</strong>
            <span>실주문은 미리보기, Risk Manager, 최종 확인 문구를 통과한 수동 요청만 허용됩니다.</span>
          </div>
          <button onClick={() => void emergencyStopLiveTrading()} className="emergency-button">
            <Power className="h-4 w-4" />
            긴급 정지
          </button>
        </div>
        <div className="app-content">

        {activeView === "dashboard" && (
          <section className="screen-grid dashboard-screen">
            <div className="screen-kpis">
              <MetricCard icon={<Wallet className="h-5 w-5" />} label="총자산 (KRW)" value={formatKrw(estimatedPortfolioTotal)} tone="cyan" />
              <MetricCard icon={<TrendingUp className="h-5 w-5" />} label="오늘 수익 (KRW)" value={formatKrw(riskState?.daily_total_pnl ?? totalPnl)} tone={pnlTone(riskState?.daily_total_pnl ?? totalPnl)} />
              <MetricCard icon={<PieChart className="h-5 w-5" />} label="누적 수익률" value={formatPercent(balance?.total_return ?? forwardBalance?.total_return)} tone={pnlTone(balance?.total_return ?? forwardBalance?.total_return)} />
              <MetricCard icon={<Bot className="h-5 w-5" />} label="활성 전략 수" value={`${candidateStrategies.length || 0}개`} tone="amber" />
              <MetricCard icon={<Target className="h-5 w-5" />} label="승률 (7D)" value={formatPercent(metrics?.win_rate ?? forwardMetrics?.win_rate)} />
            </div>
            <div className="dashboard-main">
              <div className="terminal-panel chart-panel-large">
                <div className="market-strip">
                  <div>
                    <span className="coin-dot">₿</span>
                    <strong>BTC/KRW</strong>
                    <b className="mono-num text-terminal-green">{latestDisplayCandle ? formatKrw(latestDisplayCandle.close) : "-"}</b>
                  </div>
                  <div className="segmented">
                    {[1, 15, 60, 240].map((item) => (
                      <button key={item} onClick={() => setUnit(item)} className={unit === item ? "is-active" : ""}>{formatTimeframe(item)}</button>
                    ))}
                  </div>
                </div>
                <ChartPanel candles={displayCandles} signals={chartSignals} />
              </div>
              <aside className="right-stack">
                <div className="terminal-panel bot-card">
                  <div className="panel-card-header">
                    <span className="panel-title"><Bot className="h-4 w-4" />봇 상태</span>
                    <StatusBadge value={isLiveStrategyOn || isAutoPilotOn ? "자동매매 ON" : "대기"} tone={isLiveStrategyOn || isAutoPilotOn ? "green" : "amber"} />
                  </div>
                  <div className="bot-avatar"><Bot className="h-10 w-10" /></div>
                  <div className="metric-list">
                    <span>리스크 레벨 <b>{riskState?.status ?? "-"}</b></span>
                    <span>오늘 PnL <b className={toneClass(pnlTone(riskState?.daily_total_pnl ?? 0))}>{formatKrw(riskState?.daily_total_pnl)}</b></span>
                    <span>승률 <b>{formatPercent(metrics?.win_rate ?? forwardMetrics?.win_rate)}</b></span>
                    <span>현재 전략 <b>{liveStrategyCandidate ? STRATEGY_BADGES[liveStrategyCandidate.strategy] : STRATEGY_BADGES[displayedStrategy]}</b></span>
                  </div>
                </div>
                <div className="terminal-panel danger-panel">
                  <div className="panel-card-header">
                    <span className="panel-title"><Wallet className="h-4 w-4" />포지션 / 주문 현황</span>
                    <StatusBadge value={liveStrategyPosition?.status ?? "NO POSITION"} tone={liveStrategyPosition?.status === "OPEN" ? "green" : "neutral"} />
                  </div>
                  <div className="metric-list">
                    <span>포지션 <b>BTC/KRW</b></span>
                    <span>진입가 <b>{formatKrw(liveStrategyPosition?.entry_price)}</b></span>
                    <span>현재가 <b>{formatKrw(liveStrategyPosition?.current_price)}</b></span>
                    <span>평가손익 <b className={toneClass(pnlTone(liveStrategyPosition?.unrealized_pnl ?? 0))}>{formatKrw(liveStrategyPosition?.unrealized_pnl)}</b></span>
                    <span>Stop Loss <b className="text-terminal-red">{formatKrw(liveStrategyPosition?.stop_loss_price)}</b></span>
                    <span>Take Profit <b className="text-terminal-green">{formatKrw(liveStrategyPosition?.take_profit_price)}</b></span>
                  </div>
                  <button onClick={() => void emergencyStopLiveTrading()} className="mt-4 w-full emergency-button justify-center">긴급 정지 실행</button>
                </div>
                <div className="terminal-panel">
                  <div className="panel-card-header"><span className="panel-title"><Gauge className="h-4 w-4" />신호 분석 (BTC/KRW)</span><span className="panel-subtitle">업데이트 {formatKstShort(chartUpdatedAt)}</span></div>
                  <div className="signal-grid">
                    <MetricCard label="RSI (14)" value={metrics?.last_signal ?? "-"} tone="cyan" />
                    <MetricCard label="MACD" value={paper.last_signal ?? "-"} tone={paper.last_signal === "BUY" ? "green" : paper.last_signal === "SELL" ? "red" : "neutral"} />
                    <MetricCard label="거래량" value={latestDisplayCandle ? formatKrw(latestDisplayCandle.close * latestDisplayCandle.volume) : "-"} />
                    <MetricCard label="변동성" value={`${formatDecimal(riskState?.daily_loss_percent)}%`} tone="amber" />
                  </div>
                </div>
              </aside>
            </div>
            <div className="dashboard-bottom">
              <div className="terminal-panel">
                <div className="panel-card-header"><span className="panel-title"><History className="h-4 w-4" />최근 거래 내역</span><button onClick={() => setActiveView("trades")} className="tiny-link">전체 보기</button></div>
                <div className="table-scroll max-h-64 overflow-auto">
                  <table className="ops-table w-full min-w-[780px] text-left text-sm">
                    <thead><tr><th className="px-3 py-2">시간</th><th className="px-3 py-2">종목</th><th className="px-3 py-2">방향</th><th className="px-3 py-2 text-right">가격</th><th className="px-3 py-2">상태</th></tr></thead>
                    <tbody>{liveOrders.slice(0, 6).map((order) => (
                      <tr key={order.request_id} className="border-t border-terminal-line">
                        <td className="px-3 py-2 text-slate-400">{formatKstShort(order.created_at)}</td><td className="px-3 py-2 font-semibold">{order.market}</td><td className="px-3 py-2"><SideBadge side={order.side} /></td><td className="mono-num px-3 py-2 text-right">{formatKrw(order.price ?? undefined)}</td><td className="px-3 py-2"><StatusBadge value={formatLiveOrderStatus(order.status)} tone={order.status === "FILLED" ? "green" : order.status === "FAILED" || order.status === "BLOCKED" ? "red" : "amber"} /></td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              </div>
              <div className="terminal-panel">
                <div className="panel-card-header"><span className="panel-title"><Bell className="h-4 w-4" />시스템 로그</span><button onClick={() => setActiveView("alerts")} className="tiny-link">알림로그</button></div>
                <div className="log-list">
                  {(riskDashboard?.risk_logs ?? []).slice(0, 6).map((log) => (
                    <span key={log.id}><i className={log.allowed ? "ok" : "warn"} />{formatKstShort(log.created_at)} · {log.block_code ?? "RISK_CHECK"} · {log.block_reason ?? "정상"}</span>
                  ))}
                  {(riskDashboard?.risk_logs ?? []).length === 0 && <span><i className="ok" />최근 리스크 로그 없음</span>}
                </div>
              </div>
            </div>
          </section>
        )}

        {activeView === "auto-trade" && (
          <section className="screen-grid auto-screen">
            <div className="auto-status terminal-panel">
              <div className="status-orb"><Bot className="h-12 w-12" /></div>
              <div>
                <h2>{isLiveStrategyOn || isAutoPilotOn ? "자동매매 실행 중" : "자동매매 대기 중"}</h2>
                <p>봇이 KRW-BTC 기준 전략과 주문 안전장치를 모니터링합니다.</p>
                <StatusBadge value={riskState?.status ?? "대기"} tone={riskState?.status === "OK" ? "green" : "amber"} />
              </div>
              <div className="auto-switch">
                <span>자동매매 전체 제어</span>
                <button onClick={() => void toggleLiveStrategy()} disabled={liveStrategyLoading || (!isLiveStrategyOn && !liveStrategyCandidate)} className={isLiveStrategyOn ? "danger-ghost-button" : "success-button"}>{isLiveStrategyOn ? "OFF" : "ON"}</button>
              </div>
              <div className="mode-toggle"><button>모의매매</button><button className="is-live">실거래</button></div>
              <MetricCard icon={<TrendingUp className="h-5 w-5" />} label="일 손익 (KRW)" value={formatKrw(riskState?.daily_total_pnl)} tone={pnlTone(riskState?.daily_total_pnl)} />
              <MetricCard icon={<DollarSign className="h-5 w-5" />} label="누적 손익 (KRW)" value={formatKrw(totalPnl)} tone={pnlTone(totalPnl)} />
            </div>
            <div className="strategy-run-list">
              {[liveStrategyCandidate, autoPilotCandidate, candidateStrategies[0], candidateStrategies[1]].filter(Boolean).map((candidate, index) => (
                <div key={`${candidate?.id}-${index}`} className="strategy-run-card terminal-panel">
                  <div><strong>{candidate ? STRATEGY_LABELS[candidate.strategy] : "KRW 모멘텀 전략"}</strong><span>{candidate?.market ?? "KRW-BTC"} · {formatTimeframe(candidate?.unit)}</span></div>
                  <StatusBadge value={index < 2 && (isLiveStrategyOn || isAutoPilotOn) ? "실행 중" : "대기"} tone={index < 2 && (isLiveStrategyOn || isAutoPilotOn) ? "green" : "amber"} />
                  <b className={toneClass(pnlTone(candidate?.backtest_total_return ?? 0))}>{formatPercent(candidate?.backtest_total_return)}</b>
                </div>
              ))}
            </div>
            <div className="auto-grid">
              <div className="terminal-panel">
                <div className="panel-card-header"><span className="panel-title"><Activity className="h-4 w-4" />활성 심볼 모니터링</span><span className="panel-subtitle">KRW 마켓</span></div>
                <table className="ops-table w-full min-w-[720px] text-left text-sm"><thead><tr><th className="px-3 py-2">심볼</th><th className="px-3 py-2">전략</th><th className="px-3 py-2">상태</th><th className="px-3 py-2 text-right">가격</th><th className="px-3 py-2">신호</th></tr></thead><tbody>
                  {["BTC/KRW", "ETH/KRW", "SOL/KRW", "XRP/KRW", "ADA/KRW"].map((symbol, index) => (
                    <tr key={symbol} className="border-t border-terminal-line"><td className="px-3 py-2 font-semibold">{symbol}</td><td className="px-3 py-2">{index % 2 ? "AI 추세추종" : "KRW 모멘텀"}</td><td className="px-3 py-2"><StatusBadge value={index === 4 ? "대기" : "모니터링"} tone={index === 4 ? "amber" : "cyan"} /></td><td className="mono-num px-3 py-2 text-right">{index === 0 && latestDisplayCandle ? formatKrw(latestDisplayCandle.close) : "-"}</td><td className="px-3 py-2"><StatusBadge value={index % 3 === 0 ? "매수" : "관망"} tone={index % 3 === 0 ? "green" : "neutral"} /></td></tr>
                  ))}
                </tbody></table>
              </div>
              <div className="terminal-panel">
                <div className="panel-card-header"><span className="panel-title"><BarChart3 className="h-4 w-4" />BTC/KRW 차트</span><span className="panel-subtitle">{formatTimeframe(unit)}</span></div>
                <ChartPanel candles={displayCandles} signals={chartSignals} />
              </div>
              <div className="terminal-panel danger-panel">
                <div className="panel-card-header"><span className="panel-title"><AlertTriangle className="h-4 w-4" />긴급 정지</span><StatusBadge value={liveStatus?.emergency_stop ? "ACTIVE" : "READY"} tone={liveStatus?.emergency_stop ? "red" : "green"} /></div>
                <p className="mb-4 text-sm text-slate-400">모든 전략과 주문 후보를 즉시 중단합니다.</p>
                <button onClick={() => void emergencyStopLiveTrading()} className="w-full emergency-button justify-center">긴급 정지 실행</button>
              </div>
            </div>
          </section>
        )}

        {activeView === "strategies" && (
          <section className="strategy-screen screen-grid">
            <aside className="terminal-panel strategy-list-panel">
              <div className="panel-card-header"><span className="panel-title"><ClipboardList className="h-4 w-4" />전략 목록</span><button onClick={runStrategyValidation} disabled={validationLoading} className="ghost-button"><Plus className="h-4 w-4" />새 전략</button></div>
              <div className="search-box compact"><Search className="h-4 w-4" /><input placeholder="전략 검색" /></div>
              <div className="segmented w-full"><button className="is-active">전체</button><button>활성</button><button>비활성</button></div>
              <div className="strategy-cards">
                {strategyCardRows.map((item, index) => {
                  const strategyName = item.strategy;
                  return (
                    <button key={item.key} className={`strategy-list-card ${index === 0 ? "is-selected" : ""}`}>
                      <div><strong>{STRATEGY_LABELS[strategyName]}</strong><span>BTC · {formatTimeframe(item.unit)} · {STRATEGY_BADGES[strategyName]}</span></div>
                      <StatusBadge value={index < 2 ? "활성" : "비활성"} tone={index < 2 ? "green" : "neutral"} />
                      <b className={toneClass(pnlTone(item.returnValue))}>{formatPercent(item.returnValue)}</b>
                    </button>
                  );
                })}
              </div>
            </aside>
            <main className="terminal-panel strategy-editor-panel">
              <div className="panel-card-header"><span className="panel-title"><SlidersHorizontal className="h-4 w-4" />전략 편집</span><StatusBadge value="활성" tone="green" /></div>
              <div className="form-grid">
                <label className="control"><span>전략 이름</span><input value={`${STRATEGY_LABELS[strategy]} v2.1`} readOnly /></label>
                <label className="control"><span>타임프레임</span><select value={unit} onChange={(event) => setUnit(Number(event.target.value))}>{[1, 5, 15, 60].map((item) => <option key={item} value={item}>{formatTimeframe(item)}</option>)}</select></label>
                <label className="control"><span>전략 유형</span><select value={strategy} onChange={(event) => setStrategy(event.target.value as Strategy)}>{Object.entries(STRATEGY_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
              </div>
              <div className="editor-grid">
                <div className="rule-box"><h3>진입 조건</h3>{Object.entries(settings).slice(0, 3).map(([key, value]) => <NumberField key={key} label={formatFieldLabel(key)} value={value} step={key === "k" ? "0.1" : "1"} onChange={(next) => setSettings((prev) => ({ ...prev, [key]: next }))} />)}</div>
                <div className="rule-box"><h3>필터 조건</h3><MetricCard label="거래량 (24h)" value="> 1,000,000,000" /><MetricCard label="ATR(14)" value="현재가의 1.5%" /><MetricCard label="변동성" value="< 5%" /></div>
                <div className="rule-box"><h3>리스크 관리</h3>{Object.entries(backtestRisk).slice(0, 4).map(([key, value]) => <NumberField key={key} label={formatFieldLabel(key)} value={value} step={key.includes("rate") ? "0.0001" : "100000"} onChange={(next) => setBacktestRisk((prev) => ({ ...prev, [key]: next }))} />)}</div>
                <div className="rule-box"><h3>자본 할당</h3>{Object.entries(paperRisk).slice(0, 4).map(([key, value]) => <NumberField key={key} label={formatFieldLabel(key)} value={value} step={key.includes("rate") ? "0.0001" : "10000"} onChange={(next) => setPaperRisk((prev) => ({ ...prev, [key]: next }))} />)}</div>
              </div>
            </main>
            <aside className="strategy-side">
              <div className="terminal-panel"><div className="panel-card-header"><span className="panel-title"><TrendingUp className="h-4 w-4" />전략 성과 (실거래)</span></div><div className="metric-grid-2"><MetricCard icon={<TrendingUp className="h-5 w-5" />} label="총 수익률" value={formatPercent(metrics?.total_return)} tone={pnlTone(metrics?.total_return)} /><MetricCard icon={<Target className="h-5 w-5" />} label="승률" value={formatPercent(metrics?.win_rate)} /><MetricCard icon={<History className="h-5 w-5" />} label="총 거래 수" value={`${metrics?.trade_count ?? 0}건`} /><MetricCard icon={<AlertTriangle className="h-5 w-5" />} label="MDD" value={formatPercent(metrics?.mdd)} tone="red" /></div><BacktestEquityGraph points={result?.equity_curve ?? []} /></div>
              <div className="terminal-panel"><div className="panel-card-header"><span className="panel-title"><TestTube2 className="h-4 w-4" />백테스트 요약</span></div><div className="metric-grid-2"><MetricCard label="CAGR" value={formatPercent(metrics?.total_return)} tone="green" /><MetricCard label="Profit Factor" value={formatDecimal(metrics?.profit_factor)} /><MetricCard label="Sharpe" value={formatDecimal(metrics?.score)} /><MetricCard label="평균 보유" value={formatHoldingTime(metrics?.average_holding_time_minutes)} /></div></div>
              <div className="action-row"><button className="success-button"><Save className="h-4 w-4" />저장</button><button className="ghost-button"><Copy className="h-4 w-4" />복제</button><button className="danger-ghost-button"><AlertTriangle className="h-4 w-4" />비활성화</button><button onClick={runBacktest} className="ghost-button"><TestTube2 className="h-4 w-4" />실행 테스트</button></div>
            </aside>
          </section>
        )}

        {activeView === "portfolio" && (
          <section className="portfolio-screen screen-grid">
            <div className="screen-kpis"><MetricCard icon={<Wallet className="h-5 w-5" />} label="총 자산" value={formatKrw(estimatedPortfolioTotal)} tone="cyan" /><MetricCard icon={<TrendingUp className="h-5 w-5" />} label="일간 손익" value={formatKrw(riskState?.daily_total_pnl)} tone={pnlTone(riskState?.daily_total_pnl)} /><MetricCard icon={<PieChart className="h-5 w-5" />} label="총 수익률" value={formatPercent(balance?.total_return ?? forwardBalance?.total_return)} tone={pnlTone(balance?.total_return ?? forwardBalance?.total_return)} /><MetricCard icon={<DollarSign className="h-5 w-5" />} label="현금 비중" value={estimatedPortfolioTotal ? `${formatDecimal(((liveKrw?.balance ?? balance?.cash_krw ?? 0) / estimatedPortfolioTotal) * 100)}%` : "-"} /><MetricCard icon={<ShieldCheck className="h-5 w-5" />} label="분산 점수" value={riskState?.balance_mismatch_detected ? "검토" : "양호"} tone={riskState?.balance_mismatch_detected ? "red" : "green"} /></div>
            <div className="portfolio-layout">
              <div className="terminal-panel"><div className="panel-card-header"><span className="panel-title"><PieChart className="h-4 w-4" />포트폴리오 구성</span></div><div className="donut-wrap"><div className="donut-chart" /><div className="donut-center"><span>총 자산</span><b>{formatKrw(estimatedPortfolioTotal)}</b></div></div><div className="legend-list"><span><i className="bg-[#f59e0b]" />BTC <b>{formatNumber(liveBtc?.balance ?? position?.btc_quantity)}</b></span><span><i className="bg-[#6366f1]" />ETH <b>{formatNumber(liveEth?.balance)}</b></span><span><i className="bg-[#38bdf8]" />KRW <b>{formatKrw(liveKrw?.balance ?? balance?.cash_krw)}</b></span></div></div>
              <div className="terminal-panel"><div className="panel-card-header"><span className="panel-title"><LineChart className="h-4 w-4" />포트폴리오 자산 추이</span><div className="segmented"><button>1일</button><button>7일</button><button className="is-active">30일</button><button>전체</button></div></div><PnlGraph points={equityPoints.length ? equityPoints : forwardEquityPoints} initialCash={balance?.initial_cash ?? DEFAULT_PAPER_RISK.initial_cash} /><div className="summary-strip"><MetricCard label="기간 시작" value={formatKrw(balance?.initial_cash ?? forwardBalance?.initial_cash)} /><MetricCard label="기간 종료" value={formatKrw(estimatedPortfolioTotal)} /><MetricCard label="변동액" value={formatKrw(totalPnl)} tone={pnlTone(totalPnl)} /><MetricCard label="수익률" value={formatPercent(balance?.total_return ?? forwardBalance?.total_return)} tone={pnlTone(balance?.total_return ?? forwardBalance?.total_return)} /></div></div>
              <aside className="right-stack"><div className="terminal-panel"><div className="panel-card-header"><span className="panel-title"><RefreshCw className="h-4 w-4" />리밸런싱 제안</span><span className="panel-subtitle">업데이트 {formatKstShort(chartUpdatedAt)}</span></div><table className="ops-table w-full text-sm"><tbody>{["BTC", "ETH", "KRW", "기타"].map((asset, idx) => <tr key={asset} className="border-t border-terminal-line"><td className="px-2 py-2">{asset}</td><td className="px-2 py-2 text-right">{[56, 20, 13, 11][idx]}%</td><td className={`px-2 py-2 text-right ${idx === 1 ? "text-terminal-green" : "text-terminal-red"}`}>{idx === 1 ? "+조정" : "-조정"}</td></tr>)}</tbody></table><button className="mt-3 w-full ghost-button justify-center"><TestTube2 className="h-4 w-4" />시뮬레이션 실행</button></div><div className="terminal-panel"><div className="panel-card-header"><span className="panel-title"><ShieldCheck className="h-4 w-4" />위험 노출 현황</span></div><div className="metric-grid-2"><MetricCard label="VaR" value={`${formatDecimal(riskState?.daily_loss_percent)}%`} /><MetricCard label="최대 낙폭" value={formatPercent(metrics?.mdd)} tone="red" /><MetricCard label="샤프" value={formatDecimal(metrics?.score)} /><MetricCard label="변동성" value={riskState?.volatility_block_enabled ? "ON" : "OFF"} tone="amber" /></div></div></aside>
            </div>
          </section>
        )}

        {activeView === "trades" && (
          <section className="trades-screen screen-grid">
            <div className="screen-kpis"><MetricCard icon={<History className="h-5 w-5" />} label="전체 거래" value={`${liveOrders.length + paperOrders.length}건`} /><MetricCard icon={<Target className="h-5 w-5" />} label="승률" value={formatPercent(metrics?.win_rate ?? forwardMetrics?.win_rate)} /><MetricCard icon={<TrendingUp className="h-5 w-5" />} label="실현 손익" value={formatKrw(balance?.realized_pnl ?? riskState?.daily_realized_pnl)} tone={pnlTone(balance?.realized_pnl ?? riskState?.daily_realized_pnl)} /><MetricCard icon={<Gauge className="h-5 w-5" />} label="평균 보유 시간" value={formatHoldingTime(metrics?.average_holding_time_minutes)} /><MetricCard icon={<DollarSign className="h-5 w-5" />} label="총 수수료" value={formatKrw((result?.orders ?? []).reduce((sum, order) => sum + (order.fee ?? 0), 0))} /></div>
            <div className="trades-layout"><div className="terminal-panel"><div className="filter-bar"><button className="ghost-button"><Filter className="h-4 w-4" />코인 전체</button><button className="ghost-button"><Filter className="h-4 w-4" />전략 전체</button><button className="ghost-button"><Filter className="h-4 w-4" />상태 전체</button><div className="search-box compact"><Search className="h-4 w-4" /><input placeholder="종목/전략명 검색" /></div><button className="ghost-button"><Download className="h-4 w-4" />CSV 내보내기</button></div><div className="table-scroll max-h-[620px] overflow-auto"><table className="ops-table w-full min-w-[1080px] text-left text-sm"><thead><tr><th className="px-3 py-2">종목</th><th className="px-3 py-2">전략명</th><th className="px-3 py-2">진입시간</th><th className="px-3 py-2 text-right">진입가</th><th className="px-3 py-2 text-right">수량</th><th className="px-3 py-2 text-right">손익금</th><th className="px-3 py-2">상태</th></tr></thead><tbody>{liveOrders.slice(0, 16).map((order) => <tr key={order.request_id} className="border-t border-terminal-line"><td className="px-3 py-2 font-semibold">{order.market}</td><td className="px-3 py-2">{order.strategy_name ?? "-"}</td><td className="px-3 py-2 text-slate-400">{formatKstShort(order.created_at)}</td><td className="mono-num px-3 py-2 text-right">{formatKrw(order.price ?? undefined)}</td><td className="mono-num px-3 py-2 text-right">{formatNumber(order.executed_volume)}</td><td className="mono-num px-3 py-2 text-right">{formatKrw(order.filled_amount_krw)}</td><td className="px-3 py-2"><StatusBadge value={formatLiveOrderStatus(order.status)} tone={order.status === "FILLED" ? "green" : order.status === "FAILED" || order.status === "BLOCKED" ? "red" : "amber"} /></td></tr>)}</tbody></table></div></div><aside className="terminal-panel trade-detail-panel"><div className="panel-card-header"><span className="panel-title"><FileText className="h-4 w-4" />거래 상세</span><StatusBadge value={latestLiveOrder ? formatLiveOrderStatus(latestLiveOrder.status) : "-"} tone={latestLiveOrder?.status === "FILLED" ? "green" : latestLiveOrder ? "amber" : "neutral"} /></div><div className="metric-list"><span>종목 <b>{latestLiveOrder?.market ?? "-"}</b></span><span>주문유형 <b>{latestLiveOrder?.order_type ?? "-"}</b></span><span>주문 UUID <b>{latestLiveOrder?.order_uuid ? `${latestLiveOrder.order_uuid.slice(0, 12)}...` : "-"}</b></span><span>체결 금액 <b>{formatKrw(latestLiveOrder?.filled_amount_krw)}</b></span><span>Risk <b>{formatRiskStatus(latestLiveOrder?.risk_result)}</b></span></div><div className="timeline-list"><span>신호 발생</span><span>주문 제출</span><span>체결/대기</span><span>정산 기록</span></div></aside></div>
          </section>
        )}

        {activeView === "backtest" && (
          <section className="backtest-screen screen-grid">
            <div className="terminal-panel"><div className="panel-card-header"><span className="panel-title"><TestTube2 className="h-4 w-4" />백테스트 설정</span><div className="action-row"><button className="ghost-button"><Download className="h-4 w-4" />설정 불러오기</button><button className="ghost-button"><Save className="h-4 w-4" />설정 저장</button><button onClick={runBacktest} disabled={loading} className="success-button"><Play className="h-4 w-4" />백테스트 실행</button></div></div><div className="form-grid dense"><label className="control"><span>전략 선택</span><select value={strategy} onChange={(event) => setStrategy(event.target.value as Strategy)}>{Object.entries(STRATEGY_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label className="control"><span>코인/마켓</span><select value={market} disabled><option>KRW-BTC</option></select></label><label className="control"><span>시간 프레임</span><select value={unit} onChange={(event) => setUnit(Number(event.target.value))}>{[1, 5, 15, 60].map((item) => <option key={item} value={item}>{formatTimeframe(item)}</option>)}</select></label><label className="control"><span>기간 시작</span><input type="datetime-local" value={startDateKst} onChange={(event) => setStartDateKst(event.target.value)} /></label><label className="control"><span>기간 종료</span><input type="datetime-local" value={endDateKst} onChange={(event) => setEndDateKst(event.target.value)} /></label></div></div>
            <div className="screen-kpis"><MetricCard icon={<TrendingUp className="h-5 w-5" />} label="누적 수익률" value={formatPercent(metrics?.total_return)} tone={pnlTone(metrics?.total_return)} /><MetricCard icon={<AlertTriangle className="h-5 w-5" />} label="최대 낙폭" value={formatPercent(metrics?.mdd)} tone="red" /><MetricCard icon={<Gauge className="h-5 w-5" />} label="샤프 비율" value={formatDecimal(metrics?.score)} /><MetricCard icon={<Target className="h-5 w-5" />} label="승률" value={formatPercent(metrics?.win_rate)} /><MetricCard icon={<DollarSign className="h-5 w-5" />} label="수익 팩터" value={formatDecimal(metrics?.profit_factor)} /><MetricCard icon={<History className="h-5 w-5" />} label="총 거래 수" value={`${metrics?.trade_count ?? 0}`} /></div>
            <div className="backtest-layout"><div className="terminal-panel"><div className="panel-card-header"><span className="panel-title"><LineChart className="h-4 w-4" />자산 곡선 & Drawdown</span><div className="segmented"><button>1개월</button><button>3개월</button><button>1년</button><button className="is-active">전체</button></div></div><BacktestEquityGraph points={result?.equity_curve ?? []} /></div><aside className="right-stack"><div className="terminal-panel"><div className="panel-card-header"><span className="panel-title"><SlidersHorizontal className="h-4 w-4" />전략 파라미터 요약</span></div><div className="metric-list">{Object.entries(settings).map(([key, value]) => <span key={key}>{formatFieldLabel(key)} <b>{String(value)}</b></span>)}</div></div><div className="terminal-panel"><div className="panel-card-header"><span className="panel-title"><CheckCircle2 className="h-4 w-4" />최근 실행 정보</span></div><div className="metric-list"><span>최근 실행 <b>{formatKstShort(chartUpdatedAt)}</b></span><span>데이터 범위 <b>{startDateKst} ~ {endDateKst}</b></span><span>데이터 수 <b>{displayCandles.length}개</b></span><span>상태 <b className="text-terminal-green">완료</b></span></div></div></aside></div>
          </section>
        )}

        {activeView === "alerts" && (
          <section className="alerts-screen screen-grid">
            <div className="screen-kpis"><MetricCard icon={<Bell className="h-5 w-5" />} label="오늘 전체 알림" value={`${riskDashboard?.risk_logs?.length ?? 0}`} /><MetricCard icon={<FileText className="h-5 w-5" />} label="안 읽은 알림" value={`${(riskDashboard?.risk_logs ?? []).filter((log) => !log.allowed).length}`} tone="amber" /><MetricCard icon={<AlertTriangle className="h-5 w-5" />} label="치명적 이슈" value={`${(riskDashboard?.risk_logs ?? []).filter((log) => !log.allowed).length}`} tone="red" /><MetricCard icon={<Activity className="h-5 w-5" />} label="API 상태" value={liveStatus?.broker_status ?? "-"} tone={liveStatus?.broker_status === "READY_READ_ONLY" || liveStatus?.broker_status === "READY" ? "green" : "amber"} /></div>
            <div className="alerts-layout"><div className="terminal-panel"><div className="filter-bar"><button className="ghost-button"><Filter className="h-4 w-4" />로그 유형 전체</button><button className="ghost-button"><Filter className="h-4 w-4" />심각도 전체</button><button className="ghost-button"><Filter className="h-4 w-4" />상태 전체</button><div className="search-box compact"><Search className="h-4 w-4" /><input placeholder="메시지 또는 주문ID 검색" /></div><button onClick={() => void fetchRiskDashboard()} className="ghost-button"><RefreshCw className="h-4 w-4" />새로고침</button></div><table className="ops-table w-full min-w-[900px] text-left text-sm"><thead><tr><th className="px-3 py-2">시간</th><th className="px-3 py-2">심각도</th><th className="px-3 py-2">유형</th><th className="px-3 py-2">출처</th><th className="px-3 py-2">메시지</th><th className="px-3 py-2">상태</th></tr></thead><tbody>{(riskDashboard?.risk_logs ?? []).slice(0, 20).map((log) => <tr key={log.id} className={`border-t border-terminal-line ${!log.allowed ? "is-alert-row" : ""}`}><td className="px-3 py-2 text-slate-400">{formatKstShort(log.created_at)}</td><td className="px-3 py-2"><StatusBadge value={log.risk_level} tone={log.allowed ? "green" : "red"} /></td><td className="px-3 py-2">{log.block_code ?? "RISK_CHECK"}</td><td className="px-3 py-2">Risk Manager</td><td className="px-3 py-2">{log.block_reason ?? "정상"}</td><td className="px-3 py-2">{log.allowed ? "읽음" : "미해결"}</td></tr>)}</tbody></table></div><aside className="terminal-panel alert-detail-panel"><div className="panel-card-header"><span className="panel-title"><AlertTriangle className="h-4 w-4" />{latestRiskLog?.block_code ?? "선택 알림"}</span><StatusBadge value={latestRiskLog?.allowed ? "읽음" : "미해결"} tone={latestRiskLog?.allowed ? "green" : "red"} /></div><div className="metric-list"><span>발생 시간 <b>{formatKstShort(latestRiskLog?.created_at)}</b></span><span>상세 메시지 <b>{latestRiskLog?.block_reason ?? "-"}</b></span><span>영향 범위 <b>주문 생성, 자동매매</b></span><span>최근 API 상태 <b>{liveStatus?.broker_status ?? "-"}</b></span></div><div className="action-row"><button className="ghost-button"><CheckCircle2 className="h-4 w-4" />읽음 처리</button><button className="ghost-button"><RefreshCw className="h-4 w-4" />재시도</button><button className="danger-ghost-button"><AlertTriangle className="h-4 w-4" />알림 무시</button></div></aside></div>
          </section>
        )}

        {activeView === "settings" && (
          <section className="settings-screen screen-grid">
            <aside className="terminal-panel settings-nav"><button className="is-selected"><Settings className="h-4 w-4" />일반</button><button><Link className="h-4 w-4" />거래소 API</button><button><ShieldCheck className="h-4 w-4" />리스크 관리</button><button><Bell className="h-4 w-4" />알림</button><button><Lock className="h-4 w-4" />보안</button></aside>
            <main className="terminal-panel settings-form"><div className="panel-card-header"><span className="panel-title"><Settings className="h-4 w-4" />환경설정</span><button className="success-button"><Save className="h-4 w-4" />저장</button></div><div className="settings-row"><span><ShieldCheck className="h-4 w-4" />실거래 허용</span><StatusBadge value={liveStatus?.live_trading_enabled ? "활성" : "비활성"} tone={liveStatus?.live_trading_enabled ? "amber" : "neutral"} /></div><div className="settings-row"><span><Bot className="h-4 w-4" />자동매매 기본 모드</span><div className="segmented"><button>모니터링</button><button>페이퍼</button><button className="is-active">실거래</button></div></div><div className="settings-row"><span><Target className="h-4 w-4" />기본 거래쌍</span><select value={market} disabled><option>KRW-BTC</option></select></div><div className="settings-row"><span><DollarSign className="h-4 w-4" />표시 통화</span><select><option>KRW (원)</option></select></div><div className="settings-row"><span><Settings className="h-4 w-4" />테마 모드</span><div className="segmented"><button>시스템</button><button>라이트</button><button className="is-active">다크</button></div></div><div className="settings-row"><span><Gauge className="h-4 w-4" />시간대</span><select><option>(UTC+09:00) 서울</option></select></div><div className="settings-row"><span><Bell className="h-4 w-4" />앱 내 알림</span><StatusBadge value="활성" tone="green" /></div><div className="settings-row"><span><FileText className="h-4 w-4" />주문 유형 기본값</span><select><option>지정가 (Limit)</option></select></div></main>
            <aside className="right-stack"><div className="terminal-panel"><div className="panel-card-header"><span className="panel-title"><Activity className="h-4 w-4" />시스템 상태</span><StatusBadge value="정상 운영 중" tone="green" /></div><div className="metric-list"><span>서버 시간 <b>{formatKstShort(new Date().toISOString())}</b></span><span>API Key Loaded <b>{liveStatus?.api_key_loaded ? "YES" : "NO"}</b></span><span>Risk Manager <b>{liveStatus?.risk_manager_status ?? "-"}</b></span></div></div><div className="terminal-panel"><div className="panel-card-header"><span className="panel-title"><Link className="h-4 w-4" />연결 상태</span></div><div className="metric-list"><span>업비트 <b>{liveExchange === "upbit" ? liveStatus?.broker_status ?? "-" : "대기"}</b></span><span>빗썸 <b>{liveExchange === "bithumb" ? liveStatus?.broker_status ?? "-" : "대기"}</b></span><span>Telegram <b>미연결</b></span><span>Discord <b>미연결</b></span></div></div><div className="terminal-panel danger-panel"><button onClick={() => void fetchLiveBalances()} className="w-full ghost-button justify-center"><RefreshCw className="h-4 w-4" />테스트 연결</button><button onClick={() => void emergencyStopLiveTrading()} className="mt-3 w-full emergency-button justify-center"><Power className="h-4 w-4" />긴급 정지</button></div></aside>
          </section>
        )}

        <section id="dashboard" className={viewSection("__legacy__", "dashboard-hero")}>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-5">
            <MetricCard label="총자산 (KRW)" value={formatKrw(estimatedPortfolioTotal)} tone="cyan" />
            <MetricCard label="오늘 손익" value={formatKrw(riskState?.daily_total_pnl ?? totalPnl)} tone={pnlTone(riskState?.daily_total_pnl ?? totalPnl)} />
            <MetricCard label="누적 수익률" value={formatPercent(balance?.total_return ?? forwardBalance?.total_return)} tone={pnlTone(balance?.total_return ?? forwardBalance?.total_return)} />
            <MetricCard label="활성 전략 수" value={`${candidateStrategies.length || 0}개`} tone="amber" />
            <MetricCard label="승률" value={formatPercent(metrics?.win_rate ?? forwardMetrics?.win_rate)} />
          </div>
          <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
            <div className="panel-card">
              <div className="panel-card-header">
                <div>
                  <span className="panel-title">BTC/KRW 차트</span>
                  <span className="panel-subtitle">{displayCandles.length}개 캔들 · {formatTimeframe(unit)}</span>
                </div>
                <div className="segmented">
                  {[1, 15, 60, 240].map((item) => (
                    <button key={item} onClick={() => setUnit(item)} className={unit === item ? "is-active" : ""}>{formatTimeframe(item)}</button>
                  ))}
                </div>
              </div>
              <ChartPanel candles={displayCandles} signals={chartSignals} />
            </div>
            <aside className="space-y-4">
              <div className="panel-card">
                <div className="panel-card-header">
                  <span className="panel-title">봇 상태</span>
                  <StatusBadge value={isLiveStrategyOn || isAutoPilotOn ? "자동매매 ON" : "대기"} tone={isLiveStrategyOn || isAutoPilotOn ? "green" : "amber"} />
                </div>
                <div className="metric-list">
                  <span>리스크 레벨 <b className={toneClass(topStatusTone)}>{riskState?.status ?? "-"}</b></span>
                  <span>오늘 PnL <b className={toneClass(pnlTone(riskState?.daily_total_pnl ?? 0))}>{formatKrw(riskState?.daily_total_pnl)}</b></span>
                  <span>현재 전략 <b>{liveStrategyCandidate ? STRATEGY_LABELS[liveStrategyCandidate.strategy] : STRATEGY_LABELS[displayedStrategy]}</b></span>
                  <span>오픈 주문 <b>{openLiveOrders.length}</b></span>
                </div>
              </div>
              <div className="panel-card danger-panel">
                <div className="panel-card-header">
                  <span className="panel-title">포지션 / 주문 현황</span>
                  <StatusBadge value={liveStrategyPosition?.status ?? "NO POSITION"} tone={liveStrategyPosition?.status === "OPEN" ? "green" : "neutral"} />
                </div>
                <div className="metric-list">
                  <span>진입가 <b>{formatKrw(liveStrategyPosition?.entry_price)}</b></span>
                  <span>현재가 <b>{formatKrw(liveStrategyPosition?.current_price)}</b></span>
                  <span>평가손익 <b className={toneClass(pnlTone(liveStrategyPosition?.unrealized_pnl ?? 0))}>{formatKrw(liveStrategyPosition?.unrealized_pnl)}</b></span>
                  <span>Stop Loss <b className="text-terminal-red">{formatKrw(liveStrategyPosition?.stop_loss_price)}</b></span>
                  <span>Take Profit <b className="text-terminal-green">{formatKrw(liveStrategyPosition?.take_profit_price)}</b></span>
                </div>
                <button onClick={() => void emergencyStopLiveTrading()} className="mt-4 w-full emergency-button justify-center">긴급 정지 실행</button>
              </div>
            </aside>
          </div>
        </section>

        <section id="auto-trade" className={viewSection("__legacy__", "border-b border-terminal-line bg-[#080d14] px-4 py-4")}>
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
            <MetricCard label="Partial Fill Policy" value={autoPilot?.partial_fill_policy ?? "-"} tone="amber" />
            <MetricCard label="Restart Policy" value={autoPilot?.restart_policy ?? "-"} tone="amber" />
            <MetricCard label="Recovery Event" value={autoPilot?.recent_recovery_events?.[0]?.event_type ?? "-"} title={autoPilot?.recent_recovery_events?.[0]?.message} tone={autoPilot?.recent_recovery_events?.[0]?.severity === "ERROR" ? "red" : autoPilot?.recent_recovery_events?.[0]?.severity === "WARNING" ? "amber" : "neutral"} />
          </section>
          <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,180px)_auto]">
            <label className="control">
              <span>Candidate Strategy</span>
              <select value={autoPilotCandidateId} onChange={(event) => setAutoPilotCandidateId(Number(event.target.value))}>
                <option value="">후보 전략 선택</option>
                {candidateStrategies.filter((candidate) => candidate.market === "KRW-BTC").map((candidate) => (
                  <option key={candidate.id} value={candidate.id}>
                    {`${STRATEGY_BADGES[candidate.strategy]} | ${formatTimeframe(candidate.unit)} | ${formatDecimal(candidate.score)}점`}
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

        <section className={viewSection("__legacy__", "border-b border-terminal-line bg-[#071016] px-4 py-4")}>
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
            <MetricCard label="Partial Fill Policy" value={liveStrategy?.partial_fill_policy ?? "-"} tone="amber" />
            <MetricCard label="Restart Policy" value={liveStrategy?.restart_policy ?? "-"} tone="amber" />
            <MetricCard label="Recovery Event" value={liveStrategy?.recent_recovery_events?.[0]?.event_type ?? "-"} title={liveStrategy?.recent_recovery_events?.[0]?.message} tone={liveStrategy?.recent_recovery_events?.[0]?.severity === "ERROR" ? "red" : liveStrategy?.recent_recovery_events?.[0]?.severity === "WARNING" ? "amber" : "neutral"} />
          </section>

          <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_auto]">
            <label className="control">
              <span>Candidate Strategy</span>
              <select value={liveStrategyCandidateId} onChange={(event) => setLiveStrategyCandidateId(Number(event.target.value))}>
                <option value="">Select candidate</option>
                {candidateStrategies.filter((candidate) => candidate.market === "KRW-BTC").map((candidate) => (
                  <option key={candidate.id} value={candidate.id}>
                    {`${STRATEGY_BADGES[candidate.strategy]} | ${formatTimeframe(candidate.unit)} | ${formatDecimal(candidate.score)}pt`}
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
            <MetricCard label="Holding Since" value={formatKstShort(liveStrategyPosition?.opened_at)} title={formatKstDateTime(liveStrategyPosition?.opened_at)} />
            <MetricCard label="Exit Reason" value={liveExitCandidate?.reason ?? "-"} tone={liveExitCandidate ? "amber" : "neutral"} />
            <MetricCard label="Exit Status" value={liveExitCandidate?.status ?? "-"} tone={liveExitCandidate?.status === "COMPLETED" ? "green" : liveExitCandidate?.status === "BLOCKED" ? "red" : liveExitCandidate ? "amber" : "neutral"} />
            <MetricCard label="Expected Exit Price" value={formatKrw(liveExitCandidate?.target_exit_price)} />
            <MetricCard label="Expected PnL" value={formatKrw(liveExitCandidate?.expected_pnl)} tone={pnlTone(liveExitCandidate?.expected_pnl ?? 0)} />
            <MetricCard label="Exit Risk Result" value={liveExitCandidate?.risk_result ?? "-"} tone={liveExitCandidate?.risk_result?.startsWith("BLOCKED") ? "red" : "neutral"} />
          </section>
          <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-[repeat(5,minmax(0,auto))_minmax(0,1fr)]">
            <button onClick={() => void approveExitCandidate()} disabled={liveStrategyLoading || !liveExitCandidate || liveExitCandidate.status !== "PENDING"} className="inline-flex h-9 items-center justify-center border border-terminal-green px-3 text-xs font-semibold text-terminal-green hover:bg-[#10331f] disabled:opacity-50">Approve Exit Candidate</button>
            <button onClick={() => void rejectExitCandidate()} disabled={liveStrategyLoading || !liveExitCandidate || !["PENDING", "APPROVED"].includes(liveExitCandidate.status)} className="inline-flex h-9 items-center justify-center border border-terminal-amber px-3 text-xs font-semibold text-terminal-amber hover:bg-[#2c2412] disabled:opacity-50">Reject Exit Candidate</button>
            <button onClick={() => void createExitPreview()} disabled={liveStrategyLoading || !liveExitCandidate || liveExitCandidate.status !== "APPROVED"} className="inline-flex h-9 items-center justify-center border border-terminal-cyan px-3 text-xs font-semibold text-terminal-cyan hover:bg-[#0d2d33] disabled:opacity-50">Create Exit Preview</button>
            <button onClick={() => void submitExitOrder()} disabled={liveStrategyLoading || !exitPreviewRequestId || exitPlaceConfirmation !== "SUBMIT LIMIT EXIT ORDER"} className="inline-flex h-9 items-center justify-center border border-terminal-red px-3 text-xs font-semibold text-terminal-red hover:bg-[#331018] disabled:opacity-50">Submit Limit Exit Order</button>
            <button onClick={() => void cancelExitOrder()} disabled={liveStrategyLoading || !exitPreviewRequestId} className="inline-flex h-9 items-center justify-center border border-terminal-amber px-3 text-xs font-semibold text-terminal-amber hover:bg-[#2c2412] disabled:opacity-50">Cancel Exit Order</button>
            <label className="control min-w-[250px]">
              <span>Exit Submit Confirmation</span>
              <input value={exitPlaceConfirmation} onChange={(event) => setExitPlaceConfirmation(event.target.value)} placeholder="SUBMIT LIMIT EXIT ORDER" />
            </label>
          </div>
        </section>

        <section id="settings" className={viewSection("__legacy__", "border-b border-terminal-line bg-terminal-bg px-4 py-4")}>
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
            <MetricCard label="Risk Status" value={riskState?.status ?? "-"} tone={riskState?.status === "OK" ? "green" : riskState?.status === "BLOCKED" || riskState?.status === "EMERGENCY_STOPPED" ? "red" : "amber"} />
            <MetricCard label="Today Realized PnL" value={formatKrw(riskState?.daily_realized_pnl)} tone={pnlTone(riskState?.daily_realized_pnl ?? 0)} />
            <MetricCard label="Today Unrealized PnL" value={formatKrw(riskState?.daily_unrealized_pnl)} tone={pnlTone(riskState?.daily_unrealized_pnl ?? 0)} />
            <MetricCard label="Daily Loss %" value={`${formatDecimal(riskState?.daily_loss_percent)}%`} tone={(riskState?.daily_loss_percent ?? 0) > 0 ? "amber" : "neutral"} />
            <MetricCard label="Daily Orders" value={`${riskState?.daily_order_count ?? 0}`} />
            <MetricCard label="Entry / Exit Today" value={`${riskState?.daily_entry_count ?? 0}/${riskState?.daily_exit_count ?? 0}`} />
            <MetricCard label="Consecutive Losses" value={`${riskState?.consecutive_loss_count ?? 0}`} tone={(riskState?.consecutive_loss_count ?? 0) > 0 ? "amber" : "neutral"} />
            <MetricCard label="Open Orders" value={`${riskState?.open_order_count ?? 0}`} tone={(riskState?.open_order_count ?? 0) > 0 ? "amber" : "neutral"} />
            <MetricCard label="Open Positions" value={`${riskState?.open_position_count ?? 0}`} tone={(riskState?.open_position_count ?? 0) > 0 ? "amber" : "neutral"} />
            <MetricCard label="Cooldown Remaining" value={`${riskDashboard?.risk_logs?.[0]?.block_code === "BLOCKED_COOLDOWN" ? "ACTIVE" : "0s"}`} tone={riskDashboard?.risk_logs?.[0]?.block_code === "BLOCKED_COOLDOWN" ? "amber" : "neutral"} />
            <MetricCard label="Balance Mismatch" value={riskState?.balance_mismatch_detected ? "YES" : "NO"} tone={riskState?.balance_mismatch_detected ? "red" : "green"} />
            <MetricCard label="Partial Fill" value={riskState?.partial_fill_detected ? "YES" : "NO"} tone={riskState?.partial_fill_detected ? "red" : "green"} />
            <MetricCard label="Volatility Filter" value={riskState?.volatility_block_enabled ? "ON" : "OFF"} />
            <MetricCard label="Low Volume Filter" value={riskState?.low_volume_block_enabled ? "ON" : "OFF"} />
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
                <MetricCard label="Risk Level" value={livePreview.risk_level ?? "-"} tone={livePreview.risk_level === "BLOCKED" ? "red" : livePreview.risk_level === "MEDIUM" || livePreview.risk_level === "HIGH" ? "amber" : "green"} />
                <MetricCard label="Block Code" value={livePreview.block_code ?? "-"} tone={livePreview.block_code ? "red" : "neutral"} />
                <MetricCard label="Max Allowed KRW" value={formatKrw(livePreview.max_allowed_order_krw)} tone="amber" />
                <MetricCard label="Order Chance" value={livePreview.order_chance_status ?? "-"} tone={livePreview.order_chance_status === "SUCCESS" ? "green" : livePreview.order_chance_status === "FAILED" ? "red" : "amber"} title={livePreview.order_chance_error ?? undefined} />
                <MetricCard label="차단 사유" value={formatRiskStatus(livePreview.block_reason ?? livePreview.blocked_reason)} tone={livePreview.allowed ? "neutral" : "red"} />
                <MetricCard label="Risk Checks" value={livePreview.checks ? `${Object.values(livePreview.checks).filter((check) => check.allowed === false).length} blocked` : "-"} title={livePreview.checks ? Object.entries(livePreview.checks).map(([key, check]) => `${key}:${check.allowed === false ? check.code ?? "BLOCKED" : "OK"}`).join(" | ") : undefined} />
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

        <section id="backtest" className={viewSection("__legacy__", "border-b border-terminal-line bg-[#070b12] px-4 py-4")}>
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

        <section id="strategies" className={viewSection("__legacy__", "border-b border-terminal-line bg-terminal-bg px-4 py-4")}>
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

        <section className={viewSection("__legacy__", "border-b border-terminal-line bg-terminal-bg px-4 py-4")}>
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

        <section className={viewSection("__legacy__", "grid grid-cols-1 gap-3 px-4 pt-4 md:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-8")}>
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

        <section className={viewSection("__legacy__", "grid grid-cols-1 gap-3 px-4 pt-4 md:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-7")}>
          <MetricCard label="가상 원화 잔고" value={formatKrw(balance?.cash_krw)} />
          <MetricCard label="현재 보유 수량" value={formatNumber(position?.btc_quantity)} tone="cyan" />
          <MetricCard label="평균 매수가" value={formatKrw(position?.avg_buy_price)} />
          <MetricCard label="실현 손익" value={formatKrw(balance?.realized_pnl)} tone={pnlTone(balance?.realized_pnl)} />
          <MetricCard label="평가 손익" value={formatKrw(balance?.unrealized_pnl)} tone={pnlTone(balance?.unrealized_pnl)} />
          <MetricCard label="총 평가자산" value={formatKrw(balance?.equity)} tone={pnlTone(totalPnl)} />
          <MetricCard label="총 수익률" value={formatPercent(balance?.total_return)} tone={pnlTone(balance?.total_return)} />
        </section>

        <section className={viewSection("__legacy__", "grid flex-1 grid-cols-1 gap-4 p-4 xl:grid-cols-[minmax(0,1fr)_340px]")}>
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

        <section className={viewSection("__legacy__", "grid grid-cols-1 gap-4 px-4 pb-4 xl:grid-cols-[minmax(0,1fr)_520px]")}>
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

        <section id="portfolio" className={viewSection("__legacy__", "border-b border-terminal-line bg-terminal-bg px-4 py-4")}>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-slate-100">포트폴리오</h2>
              <p className="text-xs text-slate-500">실계좌 잔고가 있으면 거래소 기준으로, 없으면 페이퍼 평가자산 기준으로 표시합니다.</p>
            </div>
            <button onClick={() => void fetchLiveBalances()} disabled={liveLoading} className="ghost-button">
              <RefreshCw className="h-4 w-4" />
              잔고 새로고침
            </button>
          </div>
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-[420px_minmax(0,1fr)_420px]">
            <div className="panel-card">
              <div className="panel-card-header">
                <span className="panel-title">자산 구성</span>
                <StatusBadge value={activeExchange.toUpperCase()} tone="cyan" />
              </div>
              <div className="donut-wrap">
                <div className="donut-chart" />
                <div className="donut-center">
                  <span>총 자산</span>
                  <b>{formatKrw(estimatedPortfolioTotal)}</b>
                </div>
              </div>
              <div className="legend-list">
                <span><i className="bg-[#f59e0b]" />BTC <b>{formatNumber(liveBtc?.balance ?? position?.btc_quantity)}</b></span>
                <span><i className="bg-[#6366f1]" />ETH <b>{formatNumber(liveEth?.balance)}</b></span>
                <span><i className="bg-[#38bdf8]" />KRW <b>{formatKrw(liveKrw?.balance ?? balance?.cash_krw)}</b></span>
              </div>
            </div>
            <div className="panel-card">
              <div className="panel-card-header">
                <span className="panel-title">포트폴리오 자산 추이</span>
                <div className="segmented"><button className="is-active">30일</button><button>90일</button><button>전체</button></div>
              </div>
              <PnlGraph points={equityPoints.length ? equityPoints : forwardEquityPoints} initialCash={balance?.initial_cash ?? DEFAULT_PAPER_RISK.initial_cash} />
              <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
                <MetricCard label="기간 시작" value={formatKrw(balance?.initial_cash ?? forwardBalance?.initial_cash)} />
                <MetricCard label="기간 종료" value={formatKrw(estimatedPortfolioTotal)} />
                <MetricCard label="변동액" value={formatKrw(totalPnl)} tone={pnlTone(totalPnl)} />
                <MetricCard label="수익률" value={formatPercent(balance?.total_return ?? forwardBalance?.total_return)} tone={pnlTone(balance?.total_return ?? forwardBalance?.total_return)} />
              </div>
            </div>
            <div className="panel-card">
              <div className="panel-card-header">
                <span className="panel-title">위험 노출 현황</span>
                <StatusBadge value={riskState?.balance_mismatch_detected ? "BALANCE MISMATCH" : "정상"} tone={riskState?.balance_mismatch_detected ? "red" : "green"} />
              </div>
              <div className="metric-list">
                <span>일 손실률 <b className="text-terminal-amber">{formatDecimal(riskState?.daily_loss_percent)}%</b></span>
                <span>연속 손실 <b>{riskState?.consecutive_loss_count ?? 0}</b></span>
                <span>열린 주문 <b>{riskState?.open_order_count ?? 0}</b></span>
                <span>부분 체결 <b className={riskState?.partial_fill_detected ? "text-terminal-red" : "text-terminal-green"}>{riskState?.partial_fill_detected ? "감지" : "없음"}</b></span>
              </div>
            </div>
          </div>
        </section>

        <section id="trades" className={viewSection("__legacy__", "border-b border-terminal-line bg-[#070b12] px-4 py-4")}>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-slate-100">거래내역</h2>
              <p className="text-xs text-slate-500">LiveOrderLog와 PaperBroker 로그를 분리해 조회합니다.</p>
            </div>
            <button onClick={() => void fetchLiveOrders()} className="ghost-button">
              <RefreshCw className="h-4 w-4" />
              주문 새로고침
            </button>
          </div>
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
            <div className="panel-card">
              <div className="grid grid-cols-1 gap-3 md:grid-cols-5">
                <MetricCard label="전체 거래" value={`${liveOrders.length + paperOrders.length}건`} />
                <MetricCard label="실거래 체결" value={`${filledLiveOrders.length}건`} tone="green" />
                <MetricCard label="오픈 주문" value={`${openLiveOrders.length}건`} tone={openLiveOrders.length ? "amber" : "neutral"} />
                <MetricCard label="페이퍼 주문" value={`${paperOrders.length}건`} tone="cyan" />
                <MetricCard label="실현 손익" value={formatKrw(balance?.realized_pnl ?? riskState?.daily_realized_pnl)} tone={pnlTone(balance?.realized_pnl ?? riskState?.daily_realized_pnl)} />
              </div>
              <div className="mt-4 table-scroll max-h-80 overflow-auto">
                <table className="ops-table min-w-[920px] w-full text-left text-sm">
                  <thead className="sticky top-0 z-10 bg-terminal-panel2 text-xs text-slate-500">
                    <tr><th className="px-3 py-2">시간</th><th className="px-3 py-2">종목</th><th className="px-3 py-2">방향</th><th className="px-3 py-2 text-right">가격</th><th className="px-3 py-2 text-right">금액</th><th className="px-3 py-2">상태</th><th className="px-3 py-2">리스크</th></tr>
                  </thead>
                  <tbody>
                    {liveOrders.slice(0, 12).map((order) => (
                      <tr key={`trade-${order.request_id}`} className="border-t border-terminal-line">
                        <td className="nowrap px-3 py-2 text-slate-400">{formatKstShort(order.created_at)}</td>
                        <td className="px-3 py-2 font-semibold">{order.market}</td>
                        <td className="px-3 py-2"><SideBadge side={order.side} /></td>
                        <td className="mono-num px-3 py-2 text-right">{formatKrw(order.price ?? undefined)}</td>
                        <td className="mono-num px-3 py-2 text-right">{formatKrw(order.amount_krw ?? undefined)}</td>
                        <td className="px-3 py-2"><StatusBadge value={formatLiveOrderStatus(order.status)} tone={order.status === "FAILED" || order.status === "BLOCKED" ? "red" : order.status === "FILLED" ? "green" : "amber"} /></td>
                        <td className="px-3 py-2"><StatusBadge value={formatRiskStatus(order.risk_result)} tone={order.risk_result === "ALLOWED" ? "green" : "red"} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <div className="panel-card">
              <div className="panel-card-header"><span className="panel-title">거래 상세</span><StatusBadge value={latestLiveOrder ? formatLiveOrderStatus(latestLiveOrder.status) : "-"} tone={latestLiveOrder?.status === "FILLED" ? "green" : latestLiveOrder ? "amber" : "neutral"} /></div>
              <div className="metric-list">
                <span>거래소 <b>{latestLiveOrder?.exchange?.toUpperCase() ?? "-"}</b></span>
                <span>주문 UUID <b>{latestLiveOrder?.order_uuid ? `${latestLiveOrder.order_uuid.slice(0, 12)}...` : "-"}</b></span>
                <span>체결 수량 <b>{formatNumber(latestLiveOrder?.executed_volume)}</b></span>
                <span>체결 금액 <b>{formatKrw(latestLiveOrder?.filled_amount_krw)}</b></span>
                <span>오류 <b className="text-terminal-red">{latestLiveOrder?.error_message ?? "-"}</b></span>
              </div>
            </div>
          </div>
        </section>

        <section id="alerts" className={viewSection("__legacy__", "border-b border-terminal-line bg-terminal-bg px-4 py-4")}>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-slate-100">알림로그</h2>
              <p className="text-xs text-slate-500">API 오류, 주문 실패, Risk Block, 복구 이벤트를 운영 로그로 확인합니다.</p>
            </div>
            <button onClick={() => void fetchRiskDashboard()} className="ghost-button">
              <RefreshCw className="h-4 w-4" />
              새로고침
            </button>
          </div>
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
            <div className="panel-card">
              <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
                <MetricCard label="Risk 로그" value={`${riskDashboard?.risk_logs?.length ?? 0}건`} tone="amber" />
                <MetricCard label="차단 상태" value={riskState?.status ?? "-"} tone={riskState?.status === "OK" ? "green" : "red"} />
                <MetricCard label="잔고 불일치" value={riskState?.balance_mismatch_detected ? "감지" : "없음"} tone={riskState?.balance_mismatch_detected ? "red" : "green"} />
                <MetricCard label="최근 API 상태" value={liveStatus?.broker_status ?? "-"} tone={liveStatus?.broker_status === "READY" ? "green" : "amber"} />
              </div>
              <div className="mt-4 table-scroll max-h-80 overflow-auto">
                <table className="ops-table min-w-[840px] w-full text-left text-sm">
                  <thead className="sticky top-0 z-10 bg-terminal-panel2 text-xs text-slate-500">
                    <tr><th className="px-3 py-2">시간</th><th className="px-3 py-2">심각도</th><th className="px-3 py-2">유형</th><th className="px-3 py-2">메시지</th><th className="px-3 py-2">상태</th></tr>
                  </thead>
                  <tbody>
                    {(riskDashboard?.risk_logs ?? []).slice(0, 20).map((log) => (
                      <tr key={log.id} className="border-t border-terminal-line">
                        <td className="nowrap px-3 py-2 text-slate-400">{formatKstShort(log.created_at)}</td>
                        <td className="px-3 py-2"><StatusBadge value={log.risk_level} tone={log.allowed ? "green" : "red"} /></td>
                        <td className="px-3 py-2">{log.block_code ?? "RISK_CHECK"}</td>
                        <td className="max-w-[420px] truncate px-3 py-2 text-slate-300" title={log.block_reason ?? ""}>{log.block_reason ?? "-"}</td>
                        <td className="px-3 py-2">{log.allowed ? "읽음" : "미해결"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <div className="panel-card danger-panel">
              <div className="panel-card-header"><span className="panel-title">선택 알림 상세</span><StatusBadge value={latestRiskLog?.allowed ? "해결" : "미해결"} tone={latestRiskLog?.allowed ? "green" : "red"} /></div>
              <div className="metric-list">
                <span>발생 시간 <b>{formatKstShort(latestRiskLog?.created_at)}</b></span>
                <span>차단 코드 <b>{latestRiskLog?.block_code ?? "-"}</b></span>
                <span>상세 메시지 <b className="text-terminal-amber">{latestRiskLog?.block_reason ?? "최근 리스크 로그가 없습니다"}</b></span>
                <span>영향 범위 <b>주문 생성 / 자동매매 재개</b></span>
              </div>
            </div>
          </div>
        </section>
        </div>
      </div>
    </main>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
