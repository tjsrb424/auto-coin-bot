import React from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  HistogramSeries,
  LineSeries,
  Time
} from "lightweight-charts";
import {
  BarChart3,
  Bell,
  Bitcoin,
  Bot,
  Camera,
  ChevronRight,
  CircleUserRound,
  ClipboardList,
  Copy,
  Crosshair,
  Download,
  DollarSign,
  History,
  Home,
  LineChart,
  Maximize,
  Menu,
  PieChart,
  Play,
  Plus,
  Power,
  PowerOff,
  RefreshCw,
  RotateCw,
  Save,
  Search,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Target,
  Trash2,
  TrendingUp,
  Wallet
} from "lucide-react";
import { BacktestValidationView } from "./views/BacktestValidationView";

const STAGE_WIDTH = 1672;
const STAGE_HEIGHT = 941;
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";
const MARKET = "KRW-BTC";
const CHART_UNIT = 15;
const APP_BUILD_LABEL = `v${__APP_VERSION__} · ${__APP_COMMIT__}`;
const AUTO_TRADING_CONFIRMATION = "돈은 속도가 아니라 규율로 지킨다";
const CHART_TIMEFRAMES = [
  { label: "1m", unit: 1, disabled: false },
  { label: "15m", unit: 15, disabled: false },
  { label: "1h", unit: 60, disabled: false },
  { label: "4h", unit: 240, disabled: false },
  { label: "1D", unit: 1440, disabled: false }
] as const;

type Tone = "purple" | "cyan" | "green" | "amber" | "red";
type DashboardExchange = "upbit" | "bithumb";

type AuthStatus = {
  auth_required: boolean;
  auth_configured: boolean;
  authenticated: boolean;
  username?: string | null;
  app_env?: string;
};

type Candle = {
  candle_time_utc: string;
  candle_time_kst?: string;
  opening_price: number;
  high_price: number;
  low_price: number;
  trade_price: number;
  candle_acc_trade_volume: number;
};

type LiveStatus = {
  mode?: string;
  exchange?: string;
  live_trading_enabled?: boolean;
  broker_status?: string;
  emergency_stop?: boolean;
};

type BalanceEntry = {
  currency?: string;
  balance?: number;
  locked?: number;
  avg_buy_price?: number;
  unit_currency?: string;
};

type MarketPrice = {
  price?: number;
  signed_change_rate?: number;
  change_rate?: number;
  range_rate?: number;
  acc_trade_price_24h?: number;
  candle_time_utc?: string | number | null;
};

type LiveBalances = LiveStatus & {
  balance_fetch_status?: string;
  error_message?: string | null;
  estimated_total_equity_krw?: number;
  balances?: {
    krw?: BalanceEntry;
    btc?: BalanceEntry;
    eth?: BalanceEntry;
    by_currency?: Record<string, BalanceEntry>;
  };
  prices?: Record<string, MarketPrice | null>;
};

type EquityPoint = {
  time?: string;
  candle_time_utc?: string;
  equity?: number;
  cash_krw?: number;
  btc_quantity?: number;
  drawdown?: number;
};

type PaperSession = {
  status?: string;
  mode?: string;
  market?: string;
  unit?: number;
  strategy?: string;
  last_signal?: string;
  last_processed_candle_time_utc?: string | null;
  balance?: {
    initial_cash?: number;
    equity?: number;
    cash_krw?: number;
    current_price?: number;
    realized_pnl?: number;
    unrealized_pnl?: number;
    total_pnl?: number;
    total_return?: number;
    mdd?: number;
  };
  position?: {
    btc_quantity?: number;
    current_position_volume?: number;
    avg_buy_price?: number;
    average_entry_price?: number;
    market_value?: number;
    position_ratio?: number;
  };
  orders?: Array<Record<string, unknown>>;
  equity_curve?: EquityPoint[];
};

type Candidate = {
  id: number;
  name?: string;
  description?: string;
  strategy?: string;
  status?: string;
  unit?: number;
  market?: string;
  parameters?: Record<string, number>;
  score?: number;
  backtest_period?: string;
  backtest_total_return?: number;
  backtest_mdd?: number;
  backtest_win_rate?: number;
  backtest_profit_factor?: number;
  backtest_trade_count?: number;
  backtest_average_trade_pnl?: number;
  warning?: string;
  updated_at?: string;
};

type LiveOrder = {
  id?: number;
  request_id?: string;
  exchange?: string;
  market?: string;
  side?: string;
  order_type?: string;
  price?: number;
  volume?: number;
  amount_krw?: number;
  status?: string;
  risk_result?: string;
  created_at?: string;
  updated_at?: string;
  strategy_name?: string | null;
  executed_volume?: number;
  filled_amount_krw?: number;
  actual_pnl?: number | null;
  expected_pnl?: number | null;
  paid_fee?: number;
};

type RecoveryEvent = {
  event_type?: string;
  severity?: string;
  message?: string;
  created_at?: string;
  payload?: {
    status?: string;
    internal_btc_position?: number;
    exchange_btc_total?: number;
    difference_btc?: number;
    tolerance_btc?: number;
  };
};

type PolicyBlockDetail = {
  code?: string;
  summary?: string;
  reason?: string;
  auto_trading_enabled?: boolean;
  requested_order_krw?: number;
  max_total_exposure_krw?: number;
  current_bot_position_value_krw?: number;
  projected_bot_position_value_krw?: number;
  remaining_exposure_krw?: number;
  available_krw_balance?: number | null;
  daily_loss_krw?: number;
  daily_loss_limit_pct?: number;
  daily_loss_limit_krw?: number;
  daily_loss_usage_pct?: number;
  exceeded_by_krw?: number;
  krw_shortfall_krw?: number;
  next_action?: string;
};

type RiskLog = {
  id?: number;
  risk_level?: string;
  allowed?: boolean;
  block_code?: string | null;
  block_reason?: string | null;
  read_status?: string | null;
  created_at?: string;
  policy_block_detail?: PolicyBlockDetail | null;
  policy_block_summary?: string | null;
};

type RiskDashboard = {
  risk_state?: {
    status?: string;
    daily_total_pnl?: number;
    daily_realized_pnl?: number;
    daily_unrealized_pnl?: number;
    daily_loss_percent?: number;
    daily_order_count?: number;
    consecutive_loss_count?: number;
    open_position_count?: number;
    balance_mismatch_detected?: boolean;
  };
  config?: {
    max_daily_loss_percent?: number;
    max_daily_loss_krw?: number;
    account_equity_krw?: number;
  };
  risk_logs?: RiskLog[];
  latest_policy_block?: RiskLog | null;
  policy_block_logs?: RiskLog[];
};

type AutoPilotStatus = {
  session?: {
    created_at?: string;
    stopped_at?: string | null;
    status?: string;
    strategy_name?: string;
    candidate_strategy_id?: number;
    orders_created_today?: number;
    max_orders_per_day?: number;
    last_signal?: string | null;
  } | null;
  auto_pilot_enabled?: boolean;
  live_auto_trading_enabled?: boolean;
};

type LiveStrategyStatus = {
  session?: {
    created_at?: string;
    stopped_at?: string | null;
    status?: string;
    strategy_name?: string;
    candidate_strategy_id?: number;
    last_signal?: string | null;
    last_risk_result?: string | null;
    orders_created_today?: number;
    max_orders_per_day?: number;
  } | null;
  position?: {
    status?: string;
    entry_price?: number;
    entry_volume?: number;
    current_price?: number;
    unrealized_pnl?: number;
    stop_loss_price?: number;
    take_profit_price?: number;
  } | null;
  current_mode?: string;
  max_order_krw?: number;
  auto_exit_enabled?: boolean;
};

type RuntimeStatus = {
  app_env?: string;
  exchange?: DashboardExchange | string;
  live_trading_enabled?: boolean;
  live_auto_trading_enabled?: boolean;
  auto_strategy_pilot_enabled?: boolean;
  smart_autonomous_trading_enabled?: boolean;
  runtime_status?: "OFF" | "RUNNING" | "PAUSED" | "STOPPED" | "EMERGENCY_STOPPED" | string;
  strategy_status?: string;
  emergency_stop?: boolean;
  selected_strategy_id?: number | null;
  selected_market?: string;
  last_tick_time_utc?: string | null;
  last_order_time_utc?: string | null;
  server_started_at?: string | null;
  instance_id?: string;
  hostname?: string;
  server_ip?: string;
  runtime_owner?: string | null;
};

type OrderIntent = {
  id?: number;
  side?: string;
  action_hint?: string;
  current_value_krw?: number;
  target_value_krw?: number;
  delta_value_krw?: number;
  target_qty?: number | null;
  order_type?: string;
  limit_price?: number | null;
  urgency?: string;
  status?: string;
  blockers?: string[];
  risk_preview?: Record<string, unknown>;
  policy_preview?: Record<string, unknown>;
  pilot_order_cap_krw?: number;
  promotion_blockers?: string[];
  promotion_status?: string;
  attack_score?: number;
  attack_mode?: string;
  target_source?: string;
  pyramiding_allowed?: boolean;
  no_averaging_down_blocked?: boolean;
  partial_take_profit_pct?: number;
  trailing_stop_price?: number | null;
  position_pnl_pct?: number;
  created_at?: string;
};

type AnalysisDecision = {
  id?: number;
  one_line_summary?: string;
  action_hint?: string;
  market_regime?: string;
  legacy_signal?: string;
  current_bot_position_qty?: number;
  current_bot_position_value_krw?: number;
  available_krw_balance?: number | null;
  max_total_exposure_krw?: number;
  current_exposure_pct?: number;
  target_exposure_pct?: number;
  attack_score?: number;
  attack_mode?: string;
  attack_score_breakdown?: Record<string, unknown>;
  aggressive_target_exposure_pct?: number;
  conservative_target_exposure_pct?: number;
  final_target_exposure_source?: string;
  current_position_pnl_pct?: number;
  core_exposure_pct?: number;
  core_exposure_applied?: boolean;
  core_exposure_broken_by_panic?: boolean;
  highest_price_since_entry?: number | null;
  trailing_stop_price?: number | null;
  partial_take_profit_triggered?: boolean;
  pyramiding_allowed?: boolean;
  aggressive_blockers?: string[];
  aggressive_buy_blockers?: string[];
  aggressive_warnings?: string[];
  confidence_score?: number;
  risk_score?: number;
  positive_reasons?: string[];
  negative_reasons?: string[];
  blockers?: string[];
  raw_features?: Record<string, unknown>;
  external_factors?: Record<string, unknown>;
  internal_signals?: Record<string, unknown>;
  order_intents?: OrderIntent[];
  created_at?: string;
  decided_at?: string;
};

type ShadowReportRow = {
  decision_id?: number;
  decided_at?: string;
  market_regime?: string;
  action_hint?: string;
  direction?: string;
  outcome?: string;
  markout_pct?: number | null;
  promotion_status?: string;
  promotion_blockers?: string[];
  pilot_order_cap_krw?: number;
  confidence_score?: number;
  risk_score?: number;
  blockers?: string[];
  hard_blockers?: string[];
};

type ShadowReport = {
  summary?: {
    decision_count?: number;
    intent_count?: number;
    actionable_count?: number;
    evaluated_count?: number;
    favorable_count?: number;
    directional_win_rate?: number;
    average_confidence_score?: number;
    average_risk_score?: number;
    average_markout_pct?: number | null;
    hard_block_count?: number;
    hard_block_rate?: number;
    policy_block_count?: number;
    policy_block_rate?: number;
    readiness_score?: number;
    recommendation?: string;
  };
  action_counts?: Record<string, number>;
  direction_counts?: Record<string, number>;
  market_regime_counts?: Record<string, number>;
  blocker_counts?: Record<string, number>;
  recent_rows?: ShadowReportRow[];
};

type BotPolicy = {
  id?: number;
  market?: string;
  auto_trading_enabled?: boolean;
  max_total_exposure_krw?: number;
  daily_loss_limit_pct?: number;
  daily_loss_limit_krw?: number;
  current_bot_position_value_krw?: number;
  available_krw_balance?: number | null;
  exposure_usage_pct?: number;
  balance_fetch_status?: string;
  balance_error?: string | null;
  updated_at?: string;
};

type SmartReadinessCheck = {
  id?: string;
  label?: string;
  status?: "pass" | "block" | "warn" | string;
  required?: boolean;
  detail?: string;
};

type SmartRehearsalReview = {
  request_id?: string;
  exchange?: string;
  market?: string;
  decision?: "APPROVED" | "REJECTED" | string;
  note?: string;
  reviewed_by?: string;
  reviewed_at?: string;
  expires_at?: string | null;
  is_active?: boolean;
};

type SmartRehearsalOrder = {
  request_id?: string;
  exchange?: string;
  status?: string;
  risk_result?: string;
  side?: string;
  amount_krw?: number;
  price?: number;
  volume?: number;
  error_message?: string | null;
  created_at?: string;
  review?: SmartRehearsalReview | null;
  review_status?: string | null;
  review_active?: boolean;
  review_expires_at?: string | null;
};

type SmartLimitedReadiness = {
  status?: string;
  can_enable_limited?: boolean;
  live_mode?: string;
  checked_at?: string;
  recommended_next_action?: string;
  next_required_operator_action?: string;
  can_run_rehearsal?: boolean;
  rehearsal_blockers?: string[];
  checks?: SmartReadinessCheck[];
  external_provider_health?: Record<string, { stale?: boolean; severity?: string | null; source?: string | null; reason?: string | null }>;
  latest_rehearsal_order?: SmartRehearsalOrder | null;
  rehearsal?: {
    allowed?: boolean;
    blockers?: string[];
    daily_smart_order_count?: number;
    risk_score?: number;
    rules?: Record<string, number>;
  };
  latest_intent_summary?: {
    side?: string;
    status?: string;
    promotion_status?: string;
    delta_value_krw?: number;
    pilot_order_cap_krw?: number;
    promotion_blockers?: string[];
  } | null;
};

type ProfitEngineStatus = {
  config?: {
    enabled?: boolean;
    mode?: string;
    order_sizing_mode?: string;
    blocked_entry_regimes?: string[];
    allowed_strategies_by_regime?: Record<string, string[]>;
    extra_fee_buffer_rate?: number;
  };
  latest_order_sizing?: {
    requested_order_krw?: number | null;
    available_krw?: number | null;
    actual_order_krw?: number | null;
    fee_buffer_rate?: number | null;
    sizing_mode?: string | null;
    sizing_reason?: string | null;
    block_code?: string | null;
  };
  entry_gate?: {
    market_regime?: string | null;
    strategy_name?: string | null;
    entry_allowed?: boolean | null;
    entry_block_reason?: string | null;
    block_code?: string | null;
  };
  execution_quality?: {
    summary?: {
      order_count?: number;
      fill_rate?: number;
      cancel_rate?: number;
      average_slippage_pct?: number;
      average_fill_time_seconds?: number;
    };
  };
  kill_switch?: {
    status?: string;
    latest_events?: Array<{ action?: string; reason?: string; created_at?: string; blockers?: string[] }>;
  };
};

type HealthStatus = {
  server_status?: string;
  database_status?: string;
  broker_status?: string;
  selected_exchange?: string;
  scheduler_status?: string;
  risk_manager_status?: string;
  emergency_stop_status?: string;
  live_trading_enabled?: boolean;
  auto_trading_enabled?: boolean;
  auto_runtime_status?: string;
  latest_balance_sync_time?: string | null;
  latest_order_sync_time?: string | null;
};

type DashboardData = {
  candles: Candle[];
  chartUnit: number;
  liveStatus: LiveStatus | null;
  liveBalances: LiveBalances | null;
  liveOrders: LiveOrder[];
  risk: RiskDashboard | null;
  paper: PaperSession | null;
  forward: PaperSession | null;
  candidates: Candidate[];
  autoPilot: AutoPilotStatus | null;
  liveStrategy: LiveStrategyStatus | null;
  runtimeStatus: RuntimeStatus | null;
  analysisLatest: AnalysisDecision | null;
  analysisHistory: AnalysisDecision[];
  shadowReport: ShadowReport | null;
  smartEngineStatus: {
    live_mode?: string;
    decision?: AnalysisDecision | null;
    latest_intent?: OrderIntent | null;
    promotion_status?: string;
    promotion_blockers?: string[];
    readiness?: ShadowReport["summary"];
    limited_readiness?: SmartLimitedReadiness;
    latest_rehearsal_order?: SmartRehearsalOrder | null;
    rehearsal_review?: SmartRehearsalReview | null;
    rehearsal_review_status?: string | null;
    rehearsal_review_active?: boolean;
    rehearsal_review_expires_at?: string | null;
    remaining_rehearsal_blockers?: string[];
  } | null;
  profitEngineStatus: ProfitEngineStatus | null;
  botPolicy: BotPolicy | null;
  health: HealthStatus | null;
  recoveryEvents: RecoveryEvent[];
  errors: string[];
  updatedAt: string | null;
};

type ReferenceView = "dashboard" | "auto-trade" | "analysis" | "operations" | "portfolio" | "trades" | "backtest" | "alerts";

const navItems = [
  { id: "dashboard", label: "대시보드", icon: Home },
  { id: "auto-trade", label: "자동매매", icon: Bot },
  { id: "analysis", label: "분석근거", icon: ShieldCheck },
  { id: "operations", label: "운용설정", icon: SlidersHorizontal },
  { id: "portfolio", label: "포트폴리오", icon: PieChart },
  { id: "trades", label: "거래내역", icon: History },
  { id: "backtest", label: "백테스트", icon: BarChart3 },
  { id: "alerts", label: "알림로그", icon: Bell },
  { id: "settings", label: "설정", icon: Settings }
];

function useStageScale() {
  const [scale, setScale] = React.useState(1);

  React.useEffect(() => {
    const update = () => {
      const next = Math.min(window.innerWidth / STAGE_WIDTH, window.innerHeight / STAGE_HEIGHT);
      setScale(Number(Math.min(next, 1.4).toFixed(4)));
    };
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);

  return scale;
}

async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { credentials: "include" });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `${path} ${response.status}`);
  }
  return response.json() as Promise<T>;
}

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    credentials: "include",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail ?? payload.message ?? `${path} ${response.status}`);
  }
  return payload as T;
}

async function patchJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail ?? payload.message ?? `${path} ${response.status}`);
  }
  return payload as T;
}

async function deleteJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "DELETE",
    credentials: "include"
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail ?? payload.message ?? `${path} ${response.status}`);
  }
  return payload as T;
}

function useDashboardData(chartUnit: number, selectedExchange: DashboardExchange) {
  const [data, setData] = React.useState<DashboardData>({
    candles: [],
    chartUnit,
    liveStatus: null,
    liveBalances: null,
    liveOrders: [],
    risk: null,
    paper: null,
    forward: null,
    candidates: [],
    autoPilot: null,
    liveStrategy: null,
    runtimeStatus: null,
    analysisLatest: null,
    analysisHistory: [],
    shadowReport: null,
    smartEngineStatus: null,
    profitEngineStatus: null,
    botPolicy: null,
    health: null,
    recoveryEvents: [],
    errors: [],
    updatedAt: null
  });

  const refresh = React.useCallback(async () => {
      const errors: string[] = [];
      const settle = async <T,>(label: string, task: Promise<T>): Promise<T | null> => {
        try {
          return await task;
        } catch (err) {
          errors.push(`${label}: ${err instanceof Error ? err.message : "조회 실패"}`);
          return null;
        }
      };

      const [candlesResult, status, ordersResult, paper, forward, candidatesResult, autoPilot, liveStrategy, runtimeStatus, analysisLatestResult, analysisHistoryResult, shadowReportResult, smartEngineStatusResult, profitEngineStatusResult, botPolicyResult, health] = await Promise.all([
        settle("캔들", fetchJson<{ candles?: Candle[]; unit?: number }>(`/api/candles?market=${MARKET}&unit=${chartUnit}&count=120`)),
        settle("실거래 상태", fetchJson<LiveStatus>(`/api/live/status?exchange=${selectedExchange}`)),
        settle("주문", fetchJson<{ orders?: LiveOrder[]; recovery_events?: RecoveryEvent[] }>("/api/live-orders")),
        settle("실시간 페이퍼", fetchJson<PaperSession>("/api/paper-trading/live/latest")),
        settle("Forward Paper", fetchJson<PaperSession>("/api/forward-paper/latest")),
        settle("전략", fetchJson<{ candidates?: Candidate[] }>("/api/candidate-strategies")),
        settle("자동매매", fetchJson<AutoPilotStatus>("/api/auto-live-pilot/status")),
        settle("전략 파일럿", fetchJson<LiveStrategyStatus>("/api/live-strategy-pilot/status")),
        settle("Runtime", fetchJson<RuntimeStatus>("/api/runtime/status")),
        settle("분석근거", fetchJson<{ decision?: AnalysisDecision | null }>(`/api/analysis/latest?market=${MARKET}`)),
        settle("분석 히스토리", fetchJson<{ decisions?: AnalysisDecision[] }>(`/api/analysis/history?market=${MARKET}&limit=50`)),
        settle("Shadow 리포트", fetchJson<{ report?: ShadowReport }>(`/api/analysis/shadow-report?market=${MARKET}&limit=100&horizon_candles=3`)),
        settle("Smart Engine", fetchJson<DashboardData["smartEngineStatus"]>(`/api/smart-engine/status?market=${MARKET}`)),
        settle("Profit Engine", fetchJson<ProfitEngineStatus>(`/api/profit-engine/status?market=${MARKET}&exchange=${selectedExchange}`)),
        settle("운용정책", fetchJson<{ policy?: BotPolicy }>(`/api/bot/policy?market=${MARKET}&exchange=${selectedExchange}`)),
        settle("Health", fetchJson<HealthStatus>("/health"))
      ]);

      const exchange = (status?.exchange === "upbit" || status?.exchange === "bithumb") ? status.exchange : selectedExchange;
      const [balances, risk] = await Promise.all([
        settle("잔고", fetchJson<LiveBalances>(`/api/live/balances?exchange=${exchange}`)),
        settle("리스크", fetchJson<RiskDashboard>(`/api/risk/status?exchange=${exchange}`))
      ]);

      setData({
        candles: candlesResult?.candles ?? [],
        chartUnit: candlesResult?.unit ?? chartUnit,
        liveStatus: status,
        liveBalances: balances,
        liveOrders: ordersResult?.orders ?? [],
        risk,
        paper,
        forward,
        candidates: candidatesResult?.candidates ?? [],
        autoPilot,
        liveStrategy,
        runtimeStatus,
        analysisLatest: analysisLatestResult?.decision ?? null,
        analysisHistory: analysisHistoryResult?.decisions ?? [],
        shadowReport: shadowReportResult?.report ?? null,
        smartEngineStatus: smartEngineStatusResult,
        profitEngineStatus: profitEngineStatusResult,
        botPolicy: botPolicyResult?.policy ?? null,
        health,
        recoveryEvents: ordersResult?.recovery_events ?? [],
        errors,
        updatedAt: new Date().toISOString()
      });
  }, [chartUnit, selectedExchange]);

  React.useEffect(() => {
    let cancelled = false;

    const guardedRefresh = async () => {
      if (!cancelled) await refresh();
    };

    void guardedRefresh();
    const intervalId = window.setInterval(() => void guardedRefresh(), 10_000);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [refresh]);

  return { data, refresh };
}

function RefPanel({ className = "", children }: React.PropsWithChildren<{ className?: string }>) {
  return <section className={`ref-panel ${className}`}>{children}</section>;
}

function formatKrw(value?: number | null) {
  if (value == null || !Number.isFinite(value)) return "-";
  return new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 }).format(value);
}

function formatOrderLimit(value?: number | null) {
  if (value == null || !Number.isFinite(value)) return "-";
  return value >= 999_999_999 ? "제한없음" : formatKrw(value);
}

function formatSignedKrw(value?: number | null) {
  if (value == null || !Number.isFinite(value)) return "-";
  const sign = value > 0 ? "+" : "";
  return `${sign}${formatKrw(value)}`;
}

function formatPercent(value?: number | null, digits = 2) {
  if (value == null || !Number.isFinite(value)) return "-";
  const normalized = Math.abs(value) > 1 ? value : value * 100;
  const sign = normalized > 0 ? "+" : "";
  return `${sign}${normalized.toFixed(digits)}%`;
}

function valueToneClass(value?: number | null) {
  if (value == null || !Number.isFinite(value) || value === 0) return "ref-neutral";
  return value > 0 ? "ref-positive" : "ref-negative";
}

function lossToneClass(value?: number | null) {
  if (value == null || !Number.isFinite(value) || value === 0) return "ref-neutral";
  return value > 0 ? "ref-negative" : "ref-positive";
}

function formatRatioPercent(value?: number | null, digits = 1) {
  if (value == null || !Number.isFinite(value)) return "-";
  const normalized = Math.abs(value) > 1 ? value : value * 100;
  return `${normalized.toFixed(digits)}%`;
}

function formatNumber(value?: number | null, digits = 4) {
  if (value == null || !Number.isFinite(value)) return "-";
  return new Intl.NumberFormat("ko-KR", { maximumFractionDigits: digits }).format(value);
}

function parseDisplayNumber(value: string) {
  const normalized = value.replace(/,/g, "").match(/[+-]?\d+(?:\.\d+)?/);
  if (!normalized) return null;
  const parsed = Number(normalized[0]);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatAssetSub(value?: number | null) {
  if (value == null || !Number.isFinite(value)) return "-";
  if (value >= 1_000_000) return `≈ ${(value / 1_000_000).toFixed(2)}백만원`;
  if (value >= 10_000) return `≈ ${(value / 10_000).toFixed(1)}만원`;
  return `≈ ${formatKrw(value)}원`;
}

function formatAnalysisValue(value: unknown) {
  if (value == null) return "-";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return "-";
    return Math.abs(value) >= 1000 ? formatKrw(value) : formatNumber(value, 4);
  }
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function compactEntries(data?: Record<string, unknown> | null, limit = 12) {
  return Object.entries(data ?? {})
    .filter(([, value]) => value !== undefined)
    .slice(0, limit);
}

function balanceAmount(entry?: BalanceEntry | null) {
  return (entry?.balance ?? 0) + (entry?.locked ?? 0);
}

function liveBtcStats(data: DashboardData) {
  if (data.liveBalances?.balance_fetch_status !== "SUCCESS") return null;
  const btc = data.liveBalances.balances?.btc ?? data.liveBalances.balances?.by_currency?.BTC;
  const quantity = balanceAmount(btc);
  const averageEntry = btc?.avg_buy_price ?? 0;
  if (quantity <= 0 || averageEntry <= 0) return null;

  const currentPrice = data.liveBalances.prices?.[MARKET]?.price
    ?? latestCandle(data.candles)?.trade_price
    ?? data.paper?.balance?.current_price
    ?? null;
  if (currentPrice == null || currentPrice <= 0) return null;

  const costBasis = quantity * averageEntry;
  const marketValue = quantity * currentPrice;
  const unrealizedPnl = marketValue - costBasis;
  const realizedPnl = data.liveOrders
    .filter((order) => order.market === MARKET && order.actual_pnl != null)
    .reduce((sum, order) => sum + (order.actual_pnl ?? 0), 0);
  const totalPnl = realizedPnl + unrealizedPnl;

  return {
    quantity,
    averageEntry,
    currentPrice,
    costBasis,
    marketValue,
    unrealizedPnl,
    realizedPnl,
    totalPnl,
    totalReturn: costBasis > 0 ? totalPnl / costBasis : null
  };
}

function liveAccountPerformance(data: DashboardData) {
  if (data.liveBalances?.balance_fetch_status !== "SUCCESS") return null;
  const equity = data.liveBalances?.estimated_total_equity_krw;
  const basis = data.botPolicy?.max_total_exposure_krw;
  if (equity == null || !Number.isFinite(equity) || basis == null || !Number.isFinite(basis) || basis <= 0) return null;
  const totalPnl = equity - basis;
  return {
    basis,
    equity,
    totalPnl,
    totalReturn: totalPnl / basis
  };
}

function parseDate(value?: string | null) {
  if (!value) return null;
  const normalized = value.includes("T") ? value : value.replace(" ", "T");
  const withZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(normalized) ? normalized : `${normalized}Z`;
  const date = new Date(withZone);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatKstTime(value?: string | null) {
  const date = parseDate(value);
  if (!date) return "-";
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false
  }).format(date);
}

function formatKstShort(value?: string | null) {
  const date = parseDate(value);
  if (!date) return "-";
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  }).format(date).replace(". ", "-").replace(".", "");
}

function formatChartCandleTime(value: string | null | undefined, unit: number) {
  const date = parseDate(value);
  if (!date) return "-";
  if (unit >= 1440) {
    return new Intl.DateTimeFormat("ko-KR", {
      timeZone: "Asia/Seoul",
      year: "numeric",
      month: "2-digit",
      day: "2-digit"
    }).format(date).replace(/\. /g, "-").replace(".", "");
  }
  return formatKstTime(value);
}

function formatRuntimeDuration(ms?: number | null) {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return "-";
  const totalSeconds = Math.floor(ms / 1000);
  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (days > 0) return `${days}일 ${hours}시간 ${minutes}분 ${seconds}초`;
  if (hours > 0) return `${hours}시간 ${minutes}분 ${seconds}초`;
  if (minutes > 0) return `${minutes}분 ${seconds}초`;
  return `${seconds}초`;
}

function marketDisplay(market?: string) {
  return (market ?? MARKET).replace("KRW-", "") + "/KRW";
}

function strategyLabel(value?: string | null) {
  if (!value) return "-";
  const labels: Record<string, string> = {
    ma_cross: "이동평균 교차",
    rsi: "RSI",
    volatility_breakout: "변동성 돌파"
  };
  return labels[value] ?? value;
}

const policyBlockReasonLabels: Record<string, string> = {
  BLOCKED_POLICY_AUTO_TRADING_DISABLED: "운용정책 OFF로 신규매수 차단",
  BLOCKED_POLICY_MAX_EXPOSURE_INVALID: "최대 투입 금액 설정 오류",
  BLOCKED_POLICY_MAX_TOTAL_EXPOSURE: "최대 투입 금액 한도 초과",
  BLOCKED_POLICY_KRW_BALANCE_INSUFFICIENT: "거래소 KRW 잔고 부족",
  BLOCKED_POLICY_DAILY_LOSS_LIMIT: "일 손실 한도 도달",
  SMART_POLICY_AUTO_TRADING_DISABLED: "운용정책 OFF",
  SMART_MAX_TOTAL_EXPOSURE_REACHED: "최대 투입 금액 도달",
  SMART_DAILY_LOSS_LIMIT_REACHED: "일 손실 한도 도달",
  SMART_ORDER_DELTA_CAPPED_BY_MAX_TOTAL_EXPOSURE: "최대 투입 금액 기준 주문 후보 축소",
  POLICY_BLOCKED: "정책차단"
};

const systemLogLabels: Record<string, string> = {
  SERVER_START: "서버가 시작되어 실거래 모드를 안전 잠금으로 초기화했습니다.",
  SERVER_RESTART_LIVE_PAUSED: "서버 재시작으로 실거래 세션을 일시정지했습니다.",
  AUTO_TRADING_STOPPED_BY_USER: "사용자가 자동매매 Runtime을 중지했습니다.",
  ARM: "실거래 모드 대기 상태로 전환했습니다.",
  ARM_BLOCKED: "실거래 모드 전환이 차단되었습니다.",
  LOCK: "실거래 모드를 잠금 처리했습니다.",
  EMERGENCY_STOP: "긴급 정지가 활성화되어 모든 실거래 주문을 차단합니다.",
  RESET_EMERGENCY: "긴급 정지를 해제했습니다.",
  RESET_EMERGENCY_BLOCKED: "긴급 정지 해제가 차단되었습니다.",
  BLOCKED_MIN_ORDER_AMOUNT: "최소 주문 금액보다 작아 주문을 차단했습니다.",
  BLOCKED_OPEN_ORDER_EXISTS: "기존 주문이 체결 또는 취소될 때까지 신규 주문을 보류합니다.",
  BLOCKED_OPEN_POSITION_EXISTS: "이미 열린 포지션이 있어 신규 진입을 보류합니다.",
  BLOCKED_DUPLICATE_SIGNAL: "같은 신호가 반복되어 중복 주문을 막았습니다.",
  BLOCKED_DUPLICATE_CANDLE: "이미 처리한 캔들이라 주문을 건너뜁니다.",
  BLOCKED_INSUFFICIENT_BALANCE: "거래소 잔고가 부족해 주문을 차단했습니다.",
  BLOCKED_ORDER_CHANCE_FAILED: "거래소 주문 가능 여부 확인에 실패했습니다.",
  BLOCKED_API_RESPONSE_ERROR: "거래소 API 응답 오류로 주문을 차단했습니다.",
  BLOCKED_RISK_LIMIT: "리스크 한도에 걸려 주문을 차단했습니다.",
  BLOCKED_CONSECUTIVE_LOSS_LIMIT: "연속 손실 제한에 걸려 신규 진입을 보류합니다.",
  BLOCKED_DAILY_LOSS_LIMIT: "일 손실 한도에 도달해 신규 주문을 보류합니다.",
  BLOCKED_COOLDOWN: "주문 대기 시간이 남아 있어 신규 주문을 보류합니다.",
  BLOCKED_DAILY_ORDER_COUNT: "하루 주문 횟수 한도에 도달했습니다.",
  BLOCKED_MAX_ORDERS_PER_DAY: "하루 전체 주문 횟수 한도에 도달했습니다.",
  BLOCKED_MAX_ENTRY_ORDERS_PER_DAY: "하루 진입 주문 횟수 한도에 도달했습니다.",
  BLOCKED_MAX_EXIT_ORDERS_PER_DAY: "하루 청산 주문 횟수 한도에 도달했습니다.",
  BLOCKED_MAX_ORDER_AMOUNT: "1회 최대 주문 금액을 초과했습니다.",
  BLOCKED_MAX_POSITION_RATIO: "최대 보유 비중을 초과할 수 있어 주문을 보류합니다.",
  BLOCKED_VOLATILITY_FILTER: "급등락 구간이라 신규 진입을 보류합니다.",
  BLOCKED_LOW_VOLUME: "거래량이 부족해 주문을 보류합니다.",
  BLOCKED_LOW_1M_VOLUME: "최근 1분 거래량이 부족해 주문을 보류합니다.",
  BLOCKED_LOW_5M_AVG_VOLUME: "최근 5분 평균 거래량이 부족해 주문을 보류합니다.",
  BLOCKED_VOLUME_DATA_UNAVAILABLE: "거래량 데이터를 확인할 수 없어 주문을 보류합니다.",
  BLOCKED_INCOMPLETE_CANDLE: "아직 완성되지 않은 캔들이라 주문을 보류합니다.",
  BLOCKED_BALANCE_MISMATCH: "봇 기록과 거래소 잔고가 달라 주문을 보류합니다.",
  BLOCKED_BALANCE_RECONCILIATION_FAILED: "잔고 대조에 실패해 주문을 보류합니다.",
  BLOCKED_PARTIAL_FILL_UNSUPPORTED: "부분 체결 상태라 자동 처리를 보류합니다.",
  BLOCKED_PARTIAL_FILL_REQUIRES_RECOVERY: "부분 체결 복구가 필요해 주문을 보류합니다.",
  BLOCKED_UNRESOLVED_LIVE_ORDER: "해결되지 않은 실거래 주문이 있어 진행을 보류합니다.",
  BLOCKED_LIVE_DISABLED: "실거래 기능이 꺼져 있어 주문을 차단했습니다.",
  BLOCKED_LIVE_LOCKED: "실거래 모드가 잠겨 있어 주문을 차단했습니다.",
  BLOCKED_EMERGENCY_STOP: "긴급 정지 상태라 주문을 차단했습니다.",
  BLOCKED_DUPLICATE_ORDER: "중복 주문으로 판단해 주문을 막았습니다.",
  BLOCKED_DUPLICATE_REQUEST: "중복 요청이라 주문을 막았습니다.",
  BLOCKED_INVALID_MODE: "현재 실행 모드에서는 주문할 수 없습니다.",
  BLOCKED_AUTO_DISABLED: "자동매매가 꺼져 있어 주문을 보류합니다.",
  BLOCKED_AUTO_STRATEGY_DISABLED: "자동 전략 실행이 꺼져 있어 주문을 보류합니다.",
  BLOCKED_EXCHANGE_NOT_ALLOWED: "허용되지 않은 거래소라 주문을 보류합니다.",
  BLOCKED_MARKET_NOT_ALLOWED: "허용되지 않은 마켓이라 주문을 보류합니다.",
  BLOCKED_ORDER_TYPE_NOT_ALLOWED: "허용되지 않은 주문 방식이라 보류합니다.",
  BLOCKED_MARKET_ORDER_DISABLED: "시장가 주문이 꺼져 있어 주문을 보류합니다.",
  BLOCKED_EXIT_DISABLED: "자동 청산이 꺼져 있어 청산 주문을 보류합니다.",
  BLOCKED_POSITION_NOT_OPEN: "열린 포지션이 없어 청산 주문을 보류합니다.",
  BLOCKED_MAX_EXIT_RETRY: "청산 재시도 한도에 도달했습니다.",
  BLOCKED_MANUAL_CONFIRM_REQUIRED: "수동 확인이 필요해 주문을 보류합니다.",
  AUTO_CANCEL_UNFILLED_TIMEOUT: "미체결 시간이 지나 주문 취소를 시도했습니다.",
  AUTO_CANCELED_UNFILLED: "미체결 주문을 자동 취소했습니다.",
  AUTO_EXIT_ALREADY_SUBMITTED: "이미 청산 주문이 제출되어 추가 청산을 보류합니다.",
  AUTO_EXIT_BLOCKED: "자동 청산 주문이 차단되었습니다.",
  AUTO_EXIT_FAILED: "자동 청산 주문에 실패했습니다.",
  AUTO_EXIT_SUBMITTED: "자동 청산 주문을 제출했습니다.",
  POLICY_AUTO_TRADING_DISABLED: "운용정책에서 자동매매가 꺼져 있습니다.",
  NO_LIVE_ELIGIBLE_CANDIDATE: "실거래 적용 가능한 후보 전략이 없습니다.",
  MARKET_NOT_AUTO_SELECTABLE: "자동 선택 가능한 마켓이 아닙니다.",
  MARKET_NOT_LIVE_ALLOWED: "실거래 허용 마켓이 아닙니다.",
  RISK_STATE_BLOCKED: "리스크 상태가 차단되어 전략 적용을 보류합니다.",
  UNRESOLVED_OPEN_ORDER: "미해결 주문이 있어 전략 적용을 보류합니다.",
  OPEN_POSITION_LIMIT: "열린 포지션 한도 때문에 전략 적용을 보류합니다.",
  SWITCH_COOLDOWN_ACTIVE: "전략 교체 대기 시간이 남아 있습니다.",
  SCORE_DELTA_TOO_SMALL: "현재 전략 대비 점수 차이가 충분하지 않습니다.",
  DAILY_SWITCH_LIMIT: "하루 전략 교체 한도에 도달했습니다.",
  BEST_CANDIDATE_ALREADY_ACTIVE: "최고 후보 전략이 이미 적용 중입니다.",
  AUTO_TRADING_DISABLED_SELECTOR_NOT_APPLIED: "자동매매 OFF라 실거래 전략 적용을 보류했습니다.",
  CANDIDATE_POOL_LIMIT: "후보 풀이 가득 차 저장을 보류했습니다.",
  DAILY_CANDIDATE_SAVE_LIMIT: "하루 후보 자동 저장 한도에 도달했습니다.",
  DUPLICATE_CANDIDATE: "중복 후보라 저장하지 않았습니다.",
  AUTO_SAVE_DISABLED: "후보 자동 저장이 꺼져 있습니다.",
  AUTO_SAVE_GATE_FAILED: "후보 저장 기준을 통과하지 못했습니다.",
  ORDER_NOT_FOUND_STALE_CANCELED: "거래소에서 찾을 수 없는 오래된 주문을 취소 처리했습니다.",
  ORDER_STATUS_UNKNOWN_TIMEOUT: "주문 상태 확인 시간이 초과되었습니다.",
  EXIT_ORDER_STATUS_UNKNOWN_TIMEOUT: "청산 주문 상태 확인 시간이 초과되었습니다.",
  ORDER_CREATED: "주문 기록을 생성했습니다.",
  ORDER_CANCELED_SYNCED: "주문 취소 상태를 동기화했습니다.",
  ORDER_CANCEL_FAILED: "주문 취소에 실패했습니다.",
  CANCEL_FAILED: "취소에 실패했습니다.",
  EXIT_ORDER_CANCEL_FAILED: "청산 주문 취소에 실패했습니다.",
  ACTIVE_SELECTOR_SYNCED: "활성 전략 선택 상태를 동기화했습니다.",
  OPEN_ORDER_SYNC: "거래소 미체결 주문 상태를 동기화했습니다.",
  OPEN_ORDER_DETAIL_RECONCILED: "거래소 개별 주문 조회로 상태를 확인했습니다.",
  OPEN_ORDER_SYNC_MISSING: "거래소 개별 주문 조회로 상태를 확인했습니다.",
  EXCHANGE_OPEN_ORDER_NOT_IN_DB: "거래소에는 있지만 봇 기록에는 없는 미체결 주문이 있습니다.",
  EXCHANGE_FILLED_SYNCED: "거래소 체결 내역을 봇 기록에 반영했습니다.",
  EXCHANGE_CANCELED_SYNCED: "거래소 취소 내역을 봇 기록에 반영했습니다.",
  PARTIAL_FILL_REMAINDER_CANCELED: "부분 체결 후 남은 주문을 취소했습니다.",
  PARTIAL_FILL_CANCEL_FAILED: "부분 체결 잔량 취소에 실패했습니다.",
  POSITION_ATTACHED_TO_FILLED_ORDER: "체결 주문에 포지션 기록을 연결했습니다.",
  POSITION_OPEN_SYNCED: "열린 포지션을 동기화했습니다.",
  POSITION_IMPORTED: "거래소 잔고를 봇 포지션으로 가져왔습니다.",
  EXCHANGE_BALANCE_IMPORTED: "거래소 잔고를 봇 관리 포지션으로 편입했습니다.",
  POSITION_ADOPTED_BY_SMART_ENGINE: "스마트 엔진이 기존 포지션을 관리 대상으로 편입했습니다.",
  POSITION_ADOPTED_FROM_STRATEGY: "전략 세션의 포지션을 관리 대상으로 편입했습니다.",
  FILLED_ENTRY_POSITION_RECOVERY: "체결된 진입 주문의 포지션을 복구했습니다.",
  BALANCE_MISMATCH: "봇 기록과 거래소 잔고가 달라 확인이 필요합니다.",
  API_ERROR: "거래소 API 오류가 발생했습니다.",
  BROKER_AUTH_ERROR: "거래소 인증 오류가 발생했습니다.",
  RECONCILIATION_FAILED: "거래소 상태 대조에 실패했습니다.",
  NO_BALANCE_MISMATCH: "편입할 잔고 불일치가 없습니다.",
  NO_EXCHANGE_BTC: "거래소 BTC 잔고가 없습니다.",
  INTERNAL_POSITION_EXISTS: "이미 내부 포지션이 있어 자동 편입하지 않습니다.",
  SESSION_MARKET_MISMATCH: "현재 세션의 거래소/마켓과 일치하지 않습니다.",
  NO_LIVE_STRATEGY_SESSION: "편입할 자동매매 전략 세션이 없습니다.",
  PREVIEW_NOT_FOUND: "주문 미리보기 정보를 찾을 수 없습니다.",
  ORDERBOOK_UNAVAILABLE: "호가 정보를 확인할 수 없습니다.",
  NO_CURRENT_PRICE: "현재가를 확인할 수 없습니다.",
  NO_NEW_CANDLE: "새로 처리할 캔들이 없습니다.",
  NO_FORWARD_SESSION: "Forward Paper 세션이 없습니다.",
  SMART_SHADOW_MODE: "스마트 엔진 Shadow 모드라 실주문을 내지 않습니다.",
  SMART_MIN_REBALANCE_DELTA: "최소 리밸런싱 차이보다 작아 주문을 보류합니다.",
  SMART_ORDER_DELTA_CAPPED_BY_MAX_TOTAL_EXPOSURE: "최대 투입 한도에 맞춰 주문 후보를 줄였습니다.",
  SMART_POLICY_AUTO_TRADING_DISABLED: "운용정책에서 자동매매가 꺼져 있습니다.",
  SMART_MAX_TOTAL_EXPOSURE_REACHED: "최대 투입 금액에 도달했습니다.",
  SMART_DAILY_LOSS_LIMIT_REACHED: "일 손실 한도에 도달했습니다.",
  SMART_INSUFFICIENT_KRW_BALANCE: "KRW 잔고가 부족합니다.",
  SMART_ORDER_CHANCE_FAILED: "거래소 주문 가능 여부 확인에 실패했습니다.",
  SMART_ORDER_AMOUNT_BELOW_MIN: "주문 금액이 최소 주문 금액보다 작습니다.",
  SMART_SELL_AMOUNT_BELOW_MIN: "매도 금액이 최소 주문 금액보다 작습니다.",
  SMART_SELL_POSITION_MISSING: "매도할 포지션을 찾지 못했습니다.",
  SMART_SELL_QTY_EXCEEDS_POSITION: "매도 수량이 보유 수량을 초과합니다.",
  SMART_REHEARSAL_REVIEW_REQUIRED: "실주문 리허설 검토가 필요합니다.",
  REHEARSAL_REVIEW_REQUIRED: "실주문 리허설 검토가 필요합니다.",
  SMART_REHEARSAL_ORDER_TOO_SMALL: "리허설 주문 금액이 너무 작습니다.",
  SMART_REHEARSAL_RISK_SCORE_TOO_HIGH: "리허설 리스크 점수가 너무 높습니다.",
  SMART_REHEARSAL_TIME_WINDOW_CLOSED: "리허설 주문 허용 시간이 아닙니다.",
  SMART_REHEARSAL_DAILY_ORDER_LIMIT: "리허설 하루 주문 한도에 도달했습니다.",
  SMART_SHADOW_REPORT_NOT_READY: "Shadow 리포트가 아직 준비되지 않았습니다.",
  SMART_RISK_PREVIEW_BLOCKED: "스마트 엔진 리스크 미리보기에서 차단되었습니다.",
  SMART_RISK_PREVIEW_MISSING: "스마트 엔진 리스크 미리보기 결과가 없습니다.",
  SMART_AGGRESSIVE_MODE_DISABLED: "공격 모드가 꺼져 있습니다.",
  SMART_AGGRESSIVE_RISK_BLOCKED: "공격 모드 리스크 조건에 걸렸습니다.",
  SMART_AGGRESSIVE_OVERHEATED_BLOCKED: "과열 구간이라 공격 진입을 보류합니다.",
  SMART_AGGRESSIVE_PANIC_BLOCKED: "급락/패닉 구간이라 공격 진입을 보류합니다.",
  SMART_AGGRESSIVE_TREND_DOWN_BLOCKED: "하락 추세라 공격 진입을 보류합니다.",
  SMART_AGGRESSIVE_NO_AVERAGING_DOWN: "물타기 금지 조건으로 추가 매수를 보류합니다.",
  SMART_EXCHANGE_NOTICE_RISK_BLOCK: "거래소 공지 리스크로 주문을 차단했습니다.",
  SMART_EXCHANGE_NOTICE_RISK_WARNING: "거래소 공지 리스크 주의가 필요합니다.",
  SMART_NEWS_SENTIMENT_NEGATIVE: "뉴스 심리가 부정적입니다.",
  SMART_NEWS_SENTIMENT_WEAK: "뉴스 심리가 약합니다.",
  SMART_FEAR_GREED_OVERHEATED: "공포·탐욕 지수가 과열 상태입니다.",
  SMART_SUBMIT_FAILED: "스마트 엔진 주문 제출에 실패했습니다.",
  SMART_SELL_SUBMITTED: "스마트 엔진 매도 주문을 제출했습니다.",
  MORE_SHADOW_DATA_REQUIRED: "Shadow 데이터가 더 필요합니다.",
  FIX_BLOCKERS_BEFORE_PROMOTION: "승격 전 차단 요인을 먼저 해결해야 합니다.",
  READY_FOR_LIMITED_PILOT_REVIEW: "제한 실주문 검토가 가능합니다.",
  CONTINUE_SHADOW_MODE: "Shadow 모드를 계속 유지합니다.",
  TEST_RECONCILE: "복구 점검 테스트 이벤트입니다."
};

const systemMessageLabels: Record<string, string> = {
  "startup open order sync failed.": "서버 시작 중 미체결 주문 동기화에 실패했습니다.",
  "open order sync list_open_orders failed.": "거래소 미체결 주문 목록 조회에 실패했습니다.",
  "balance reconciliation failed.": "잔고 대조에 실패했습니다.",
  "server restart moved running live sessions to live_paused. manual resume is required.": "서버 재시작으로 실행 중이던 실거래 세션을 일시정지했습니다. 수동 재개가 필요합니다.",
  "order status is unknown; blocked until exchange reconciliation succeeds.": "주문 상태를 알 수 없어 거래소 대조가 성공할 때까지 재주문을 차단합니다.",
  "internal order has no exchange uuid and must not be retried.": "내부 주문에 거래소 주문 ID가 없어 재시도하면 안 됩니다.",
  "cannot fetch order status without exchange uuid. new orders remain blocked.": "거래소 주문 ID가 없어 주문 상태를 조회할 수 없습니다. 신규 주문은 계속 차단됩니다.",
  "exchange order was not found during reconciliation; marked stale without deleting db history.": "거래소 대조 중 주문을 찾지 못해 DB 기록은 보존하고 오래된 취소 상태로 표시했습니다.",
  "pending order was not found on exchange and was marked stale canceled.": "대기 중인 주문을 거래소에서 찾지 못해 오래된 취소 상태로 표시했습니다.",
  "order status fetch failed during reconciliation.": "거래소 주문 상태 대조 중 조회에 실패했습니다.",
  "exchange request timed out; order status must be reconciled before any retry.": "거래소 요청 시간이 초과되어 재시도 전에 주문 상태 대조가 필요합니다.",
  "live strategy order timed out. re-ordering is blocked until reconciliation.": "실거래 전략 주문 요청 시간이 초과되었습니다. 상태 대조가 끝날 때까지 재주문을 차단합니다.",
  "auto live pilot order timed out. re-ordering is blocked until reconciliation.": "자동 실거래 파일럿 주문 요청 시간이 초과되었습니다. 상태 대조가 끝날 때까지 재주문을 차단합니다.",
  "auto live pilot partial fill residual cancel failed.": "자동 실거래 파일럿 부분 체결 잔량 취소에 실패했습니다.",
  "auto live pilot order status reconciliation failed.": "자동 실거래 파일럿 주문 상태 대조에 실패했습니다.",
  "exit order request timed out; status reconciliation is required before retry.": "청산 주문 요청 시간이 초과되어 재시도 전에 상태 대조가 필요합니다.",
  "exit order timed out. re-ordering is blocked until reconciliation.": "청산 주문 요청 시간이 초과되었습니다. 상태 대조가 끝날 때까지 재주문을 차단합니다.",
  "exit order cancel failed.": "청산 주문 취소에 실패했습니다.",
  "auto exit order submission failed.": "자동 청산 주문 제출에 실패했습니다.",
  "filled entry order without position_id was recovered as a live position.": "체결된 진입 주문에 누락된 포지션 기록을 복구했습니다.",
  "exchange btc balance was imported as an internal live position by explicit admin action.": "관리자 확인에 따라 거래소 BTC 잔고를 내부 실거래 포지션으로 편입했습니다.",
  "candidate strategy not found.": "후보 전략을 찾지 못했습니다.",
  "duplicate candle.": "이미 처리한 캔들이라 주문을 건너뜁니다.",
  "order chance failed.": "거래소 주문 가능 여부 확인에 실패했습니다.",
  "insufficient krw balance.": "KRW 잔고가 부족합니다.",
  "no exit order to cancel.": "취소할 청산 주문이 없습니다.",
  "exit candidate not found.": "청산 후보를 찾지 못했습니다."
};

const codeTokenLabels: Record<string, string> = {
  BLOCKED: "차단",
  BY: "",
  POLICY: "정책",
  AUTO: "자동",
  TRADING: "매매",
  DISABLED: "꺼짐",
  MAX: "최대",
  MIN: "최소",
  TOTAL: "전체",
  EXPOSURE: "투입 금액",
  INVALID: "설정 오류",
  KRW: "원화",
  BALANCE: "잔고",
  INSUFFICIENT: "부족",
  DAILY: "일일",
  LOSS: "손실",
  LIMIT: "한도",
  CONSECUTIVE: "연속",
  COOLDOWN: "대기 시간",
  ORDER: "주문",
  ORDERS: "주문",
  COUNT: "횟수",
  ENTRY: "진입",
  EXIT: "청산",
  OPEN: "열린",
  POSITION: "포지션",
  EXISTS: "있음",
  DUPLICATE: "중복",
  REQUEST: "요청",
  SIGNAL: "신호",
  CANDLE: "캔들",
  EMERGENCY: "긴급",
  STOP: "정지",
  LIVE: "실거래",
  LOCKED: "잠김",
  LOW: "낮은",
  VOLUME: "거래량",
  VOLATILITY: "변동성",
  FILTER: "필터",
  RISK: "리스크",
  API: "API",
  RESPONSE: "응답",
  ERROR: "오류",
  FAILED: "실패",
  SUCCESS: "성공",
  OK: "정상",
  INFO: "정보",
  WARNING: "주의",
  HIGH: "높음",
  MEDIUM: "보통",
  CRITICAL: "긴급",
  MARKET: "마켓",
  EXCHANGE: "거래소",
  ALLOWED: "허용",
  TYPE: "방식",
  NOT: "아님",
  PARTIAL: "부분",
  FILL: "체결",
  REQUIRES: "필요",
  RECOVERY: "복구",
  UNSUPPORTED: "미지원",
  UNRESOLVED: "미해결",
  MANUAL: "수동",
  CONFIRM: "확인",
  REQUIRED: "필요",
  CHANCE: "가능 여부",
  DATA: "데이터",
  UNAVAILABLE: "확인 불가",
  INCOMPLETE: "미완성",
  ACTIVE: "활성",
  SELECTOR: "전략 선택기",
  SYNCED: "동기화됨",
  AUTH: "인증",
  BROKER: "거래소 브로커",
  CANDIDATE: "후보",
  POOL: "풀",
  SAVE: "저장",
  GATE: "기준",
  BEST: "최고",
  ALREADY: "이미",
  SWITCH: "교체",
  SCORE: "점수",
  DELTA: "차이",
  TOO: "너무",
  SMALL: "작음",
  CREATED: "생성됨",
  COMPLETED: "완료",
  WITH: "일부",
  ERRORS: "오류",
  SHADOW: "Shadow",
  MODE: "모드",
  ENGINE: "엔진",
  SMART: "스마트",
  REHEARSAL: "리허설",
  REVIEW: "검토",
  REPORT: "리포트",
  READY: "준비",
  LIMITED: "제한",
  PILOT: "파일럿",
  PROMOTION: "승격",
  FORWARD: "Forward",
  SESSION: "세션",
  CURRENT: "현재",
  PRICE: "가격",
  NEW: "새",
  NO: "없음",
  UNKNOWN: "알 수 없음",
  TIMEOUT: "시간 초과",
  STALE: "오래된",
  IMPORTED: "가져옴",
  INTERNAL: "내부",
  MISMATCH: "불일치",
  RECONCILIATION: "대조",
  OPEN_ORDER: "미체결 주문",
  ORDERBOOK: "호가",
  CANCEL: "취소",
  CANCELED: "취소됨",
  CANCELLED: "취소됨",
  REMAINDER: "잔량",
  ATTACHED: "연결됨",
  ADOPTED: "편입됨",
  FROM: "에서",
  STRATEGY: "전략",
  STATUS: "상태",
  WAITING: "대기",
  FILLED: "체결됨",
  PARTIALLY: "부분",
  PREVIEWED: "미리보기됨",
  REVIEWABLE: "검토 가능",
  BLOCK: "차단",
  BLOCKERS: "차단 요인",
  ENABLE: "활성화",
  ENABLED: "활성화됨",
  CAN: "가능",
  PASS: "통과",
  PASSED: "통과",
  MISSING: "없음",
  PREVIEW: "미리보기",
  FOUND: "찾음",
  SUBMIT: "제출",
  SUBMITTED: "제출됨",
  BELOW: "미만",
  ZERO: "0",
  CAP: "상한",
  CAPPED: "제한됨",
  REMAINING: "남은",
  CORE: "코어",
  AGGRESSIVE: "공격",
  OVERHEATED: "과열",
  PANIC: "패닉",
  TREND: "추세",
  DOWN: "하락",
  NOTICE: "공지",
  NEWS: "뉴스",
  SENTIMENT: "심리",
  NEGATIVE: "부정적",
  WEAK: "약함",
  FEAR: "공포",
  GREED: "탐욕",
  AMOUNT: "금액",
  QTY: "수량",
  EXCEEDS: "초과",
  WINDOW: "시간대",
  CLOSED: "닫힘",
  SIDE: "방향",
  REBALANCE: "리밸런싱",
  REBALANCING: "리밸런싱",
  ACCUMULATION: "누적",
  PASSIVE: "수동형",
  MARKETABLE: "즉시체결형",
  FALLBACK: "대체",
  OFFSET: "오프셋",
  DEFAULT: "기본",
  HOLD: "보유",
  STOPPED: "정지됨",
  PAUSED: "일시정지",
  RUNNING: "실행 중",
  STARTUP: "시작",
  SERVER: "서버"
};

function readableCodeFallback(value?: string | null) {
  if (!value) return "-";
  const normalized = String(value).trim().toUpperCase();
  const tokens = normalized.split("_").filter(Boolean);
  if (!tokens.length) return String(value);
  const translated = tokens.map((token) => codeTokenLabels[token] ?? token).filter(Boolean);
  if (normalized.startsWith("BLOCKED_")) return `차단: ${translated.filter((token) => token !== "차단").join(" ")}`;
  if (normalized.startsWith("WAITING_")) return `대기: ${translated.filter((token) => token !== "대기").join(" ")}`;
  if (normalized.startsWith("ORDER_")) return `주문: ${translated.filter((token) => token !== "주문").join(" ")}`;
  return translated.join(" ");
}

function isOpenOrderWaitCode(value?: string | null) {
  return normalizeBlockCode(value) === "BLOCKED_OPEN_ORDER_EXISTS";
}

function statusLabel(value?: string | null) {
  if (!value) return "-";
  const normalized = String(value).toUpperCase();
  const labels: Record<string, string> = {
    OK: "정상",
    READY: "준비",
    READY_READ_ONLY: "조회",
    RUNNING: "실행",
    LIVE_PAUSED: "일시정지",
    PAUSED: "일시정지",
    STOPPED: "정지",
    WAITING: "대기",
    PENDING: "대기",
    SUBMITTED: "접수",
    FILLED: "완료",
    PARTIALLY_FILLED: "부분체결",
    CANCELED: "취소",
    CANCELLED: "취소",
    FAILED: "실패",
    ERROR: "오류",
    BLOCKED: "차단",
    WARNING: "주의",
    EMERGENCY_STOPPED: "긴급정지",
    LIVE_DISABLED: "비활성",
    LIVE_LOCKED: "잠김",
    LIVE_MANUAL_ONLY: "수동",
    LIVE_ARMED: "대기",
    MANUAL_REVIEW_REQUIRED: "확인필요",
    INACTIVE: "비활성",
    ACTIVE: "활성",
    BUY: "매수",
    SELL: "매도",
    NONE: "없음",
    LIMIT: "지정가",
    MARKET: "시장가"
  };
  const reasonLabels: Record<string, string> = {
    ...policyBlockReasonLabels,
    BLOCKED_OPEN_ORDER_EXISTS: "기존 주문 체결 대기",
    BLOCKED_OPEN_POSITION_EXISTS: "포지션 있음",
    BLOCKED_DUPLICATE_SIGNAL: "중복 신호",
    BLOCKED_DUPLICATE_CANDLE: "중복 캔들",
    BLOCKED_MIN_ORDER_AMOUNT: "최소 주문 금액 미만",
    BLOCKED_CONSECUTIVE_LOSS_LIMIT: "연속 손실 제한",
    BLOCKED_DAILY_LOSS_LIMIT: "일 손실 한도",
    BLOCKED_COOLDOWN: "주문 대기 시간",
    BLOCKED_DAILY_ORDER_COUNT: "일 주문 횟수 초과",
    BLOCKED_MAX_ORDERS_PER_DAY: "일 주문 한도 초과",
    BLOCKED_MAX_ENTRY_ORDERS_PER_DAY: "일 진입 주문 한도 초과",
    BLOCKED_MAX_EXIT_ORDERS_PER_DAY: "일 청산 주문 한도 초과",
    BLOCKED_MAX_ORDER_AMOUNT: "1회 주문 금액 초과",
    BLOCKED_MAX_POSITION_RATIO: "보유 비중 초과",
    BLOCKED_VOLATILITY_FILTER: "변동성 필터",
    BLOCKED_LOW_VOLUME: "거래량 부족",
    BLOCKED_LOW_1M_VOLUME: "1분 거래량 부족",
    BLOCKED_LOW_5M_AVG_VOLUME: "5분 평균 거래량 부족",
    BLOCKED_VOLUME_DATA_UNAVAILABLE: "거래량 데이터 없음",
    BLOCKED_INCOMPLETE_CANDLE: "미완성 캔들",
    BLOCKED_BALANCE_MISMATCH: "잔고 불일치",
    BLOCKED_BALANCE_RECONCILIATION_FAILED: "잔고 대조 실패",
    BLOCKED_PARTIAL_FILL_UNSUPPORTED: "부분 체결 처리 보류",
    BLOCKED_PARTIAL_FILL_REQUIRES_RECOVERY: "부분 체결 복구 필요",
    BLOCKED_UNRESOLVED_LIVE_ORDER: "미해결 실거래 주문",
    BLOCKED_LIVE_DISABLED: "실거래 꺼짐",
    BLOCKED_LIVE_LOCKED: "실거래 잠김",
    BLOCKED_EMERGENCY_STOP: "긴급 정지",
    BLOCKED_DUPLICATE_ORDER: "중복 주문",
    BLOCKED_DUPLICATE_REQUEST: "중복 요청",
    BLOCKED_INVALID_MODE: "실행 모드 불가",
    BLOCKED_AUTO_DISABLED: "자동매매 꺼짐",
    BLOCKED_AUTO_STRATEGY_DISABLED: "자동 전략 꺼짐",
    BLOCKED_EXCHANGE_NOT_ALLOWED: "거래소 미허용",
    BLOCKED_MARKET_NOT_ALLOWED: "마켓 미허용",
    BLOCKED_ORDER_TYPE_NOT_ALLOWED: "주문 방식 미허용",
    BLOCKED_MARKET_ORDER_DISABLED: "시장가 주문 꺼짐",
    BLOCKED_INSUFFICIENT_BALANCE: "잔고 부족",
    BLOCKED_INSUFFICIENT_POSITION: "보유 수량 부족",
    BLOCKED_ORDER_CHANCE_FAILED: "주문 보류",
    BLOCKED_API_RESPONSE_ERROR: "API 오류",
    BLOCKED_RISK_LIMIT: "리스크 차단",
    ALREADY_FILLED: "이미 체결",
    INSUFFICIENT_BALANCE: "잔고 부족"
  };
  return labels[normalized] ?? reasonLabels[normalized] ?? systemLogLabels[normalized] ?? readableCodeFallback(value);
}

function marketRegimeLabel(value?: string | null) {
  const key = String(value ?? "").toUpperCase();
  const labels: Record<string, string> = {
    PANIC: "급락/패닉",
    TREND_DOWN: "하락 추세",
    OVERHEATED: "과열",
    UNKNOWN: "판단 불가",
    RANGE: "박스권",
    TREND_UP: "상승 추세",
    BREAKOUT: "돌파"
  };
  return labels[key] ?? (value || "-");
}

function profitStrategyLabel(value?: string | null) {
  const key = String(value ?? "").toLowerCase();
  const labels: Record<string, string> = {
    trend_pullback: "상승 눌림목",
    volume_breakout: "거래량 돌파",
    range_reversion: "박스권 되돌림",
    panic_blocker: "패닉 차단"
  };
  return labels[key] ?? (value || "-");
}

function sizingReasonLabel(value?: string | null) {
  const key = String(value ?? "").toUpperCase();
  const labels: Record<string, string> = {
    REQUEST_WITHIN_AVAILABLE_BALANCE: "요청 금액 그대로 가능",
    REQUEST_EXCEEDS_AVAILABLE_BALANCE_CAPPED: "잔액 기준 자동 축소",
    ORDER_BELOW_MINIMUM: "최소 주문액 미만",
    INSUFFICIENT_BALANCE: "KRW 잔액 부족",
    BALANCE_UNAVAILABLE: "잔액 조회 대기",
    ORDER_AMOUNT_ZERO: "주문액 없음"
  };
  return labels[key] ?? (value || "-");
}

type PolicyBlockNotice = {
  code: string;
  text: string;
  createdAt?: string;
  source: "risk" | "analysis";
  detail?: PolicyBlockDetail | null;
};

function normalizeBlockCode(value?: string | null) {
  return String(value ?? "").trim().toUpperCase();
}

function isPolicyBlockCode(value?: string | null) {
  const normalized = normalizeBlockCode(value);
  return normalized.startsWith("BLOCKED_POLICY_")
    || normalized.startsWith("SMART_POLICY_")
    || normalized === "SMART_MAX_TOTAL_EXPOSURE_REACHED"
    || normalized === "SMART_DAILY_LOSS_LIMIT_REACHED"
    || normalized === "SMART_ORDER_DELTA_CAPPED_BY_MAX_TOTAL_EXPOSURE"
    || normalized === "POLICY_BLOCKED";
}

function extractPolicyBlockCode(value?: string | null) {
  const normalized = normalizeBlockCode(value);
  if (isPolicyBlockCode(normalized)) return normalized;
  const match = normalized.match(/(BLOCKED_POLICY_[A-Z0-9_]+|SMART_POLICY_[A-Z0-9_]+|SMART_[A-Z0-9_]+)/);
  return match && isPolicyBlockCode(match[1]) ? match[1] : null;
}

function policyBlockText(code?: string | null, fallback?: string | null) {
  const normalized = extractPolicyBlockCode(code) ?? extractPolicyBlockCode(fallback);
  if (normalized) return policyBlockReasonLabels[normalized] ?? statusLabel(normalized);
  if (isOpenOrderWaitCode(code) || isOpenOrderWaitCode(fallback)) return "기존 매수 주문 체결 대기";
  return fallback ?? statusLabel(code);
}

function translateEnglishSystemMessage(message?: string | null) {
  const compact = String(message ?? "").trim().replace(/\s+/g, " ");
  if (!compact) return "";
  const lower = compact.toLowerCase();
  if (systemMessageLabels[lower]) return systemMessageLabels[lower];

  const reconciled = compact.match(/^order reconciled as ([A-Z_]+)\.$/i);
  if (reconciled) return `주문 상태를 ${statusLabel(reconciled[1])}(으)로 대조했습니다.`;

  const missingOpenOrder = compact.match(/^exchange open order .+ is missing from internal liveorderlog\.$/i);
  if (missingOpenOrder) return "거래소에는 있지만 봇 기록에는 없는 미체결 주문이 있습니다.";

  const exitCandidate = compact.match(/^exit candidate created: ([A-Z_]+)\.$/i);
  if (exitCandidate) return `${statusLabel(exitCandidate[1])} 사유로 청산 후보가 생성되었습니다.`;

  const confirmation = compact.match(/^(.+) confirmation is required\.$/i);
  if (confirmation) return `확인 문구 ${confirmation[1]}가 필요합니다.`;

  return "";
}

function readableSystemLogText(code?: string | null, fallback?: string | null) {
  const normalized = normalizeBlockCode(code);
  if (normalized && systemLogLabels[normalized]) return systemLogLabels[normalized];
  if (normalized && policyBlockReasonLabels[normalized]) return policyBlockReasonLabels[normalized];
  if (fallback) {
    const fallbackCode = normalizeBlockCode(fallback);
    if (fallbackCode && systemLogLabels[fallbackCode]) return systemLogLabels[fallbackCode];
    if (fallbackCode && policyBlockReasonLabels[fallbackCode]) return policyBlockReasonLabels[fallbackCode];
    const translatedFallback = translateEnglishSystemMessage(fallback);
    if (translatedFallback) return translatedFallback;
    return fallback;
  }
  return readableCodeFallback(code);
}

function alertCodeLabel(code?: string | null) {
  return readableSystemLogText(code, undefined);
}

function riskSeverityLabel(value?: string | null) {
  const labels: Record<string, string> = {
    LOW: "낮음",
    MEDIUM: "보통",
    HIGH: "높음",
    CRITICAL: "긴급",
    INFO: "정보",
    WARNING: "주의",
    OK: "정상"
  };
  const normalized = String(value ?? "").trim().toUpperCase();
  return labels[normalized] ?? (value ? statusLabel(value) : "정상");
}

function riskLogIsPolicy(log: Pick<RiskLog, "block_code" | "block_reason" | "policy_block_detail">) {
  return Boolean(log.policy_block_detail || extractPolicyBlockCode(log.block_code) || extractPolicyBlockCode(log.block_reason));
}

function riskLogTypeLabel(log: Pick<RiskLog, "allowed" | "block_code" | "block_reason" | "policy_block_detail">) {
  if (isOpenOrderWaitLog(log)) return "주문 대기";
  if (riskLogIsPolicy(log)) return "정책 차단";
  return log.allowed ? "리스크 점검" : "리스크 차단";
}

function riskLogMessage(log: Pick<RiskLog, "allowed" | "risk_level" | "block_code" | "block_reason" | "policy_block_detail">) {
  if (isOpenOrderWaitLog(log)) return "기존 매수 주문이 체결/취소될 때까지 신규 매수를 보류합니다.";
  if (riskLogIsPolicy(log)) return policyBlockText(log.block_code, log.block_reason);
  if (!log.allowed) return readableSystemLogText(log.block_code, log.block_reason) || "리스크 조건에 의해 차단됐습니다.";
  return `리스크 점검 통과 · ${riskSeverityLabel(log.risk_level)}`;
}

function riskLogTone(log: Pick<RiskLog, "allowed" | "risk_level" | "block_code" | "block_reason" | "policy_block_detail">): "green" | "amber" | "red" | "cyan" | "neutral" {
  if (isOpenOrderWaitLog(log)) return "cyan";
  if (riskLogIsPolicy(log)) return "amber";
  if (!log.allowed) return "red";
  const severity = String(log.risk_level ?? "").toUpperCase();
  if (["HIGH", "CRITICAL"].includes(severity)) return "amber";
  return "green";
}

function riskLogDotType(log: Pick<RiskLog, "allowed" | "risk_level" | "block_code" | "block_reason" | "policy_block_detail">) {
  const tone = riskLogTone(log);
  if (tone === "green") return "ok";
  if (tone === "cyan") return "info";
  if (tone === "amber") return "warn";
  if (tone === "red") return "danger";
  return "info";
}

function isOpenOrderWaitLog(log: Pick<RiskLog, "block_code" | "block_reason">) {
  return isOpenOrderWaitCode(log.block_code) || isOpenOrderWaitCode(log.block_reason);
}

function isOpenOrderWaitOrder(order: Pick<LiveOrder, "risk_result" | "status">) {
  return order.status === "BLOCKED" && isOpenOrderWaitCode(order.risk_result);
}

function shadowRecommendationLabel(value?: string | null) {
  const labels: Record<string, string> = {
    MORE_SHADOW_DATA_REQUIRED: "데이터 수집 필요",
    FIX_BLOCKERS_BEFORE_PROMOTION: "차단요인 정리 필요",
    READY_FOR_LIMITED_PILOT_REVIEW: "제한 실주문 검토 가능",
    CONTINUE_SHADOW_MODE: "Shadow 유지"
  };
  return labels[String(value ?? "").toUpperCase()] ?? value ?? "-";
}

function shadowOutcomeLabel(value?: string | null) {
  const labels: Record<string, string> = {
    FAVORABLE: "유리",
    UNFAVORABLE: "불리",
    FLAT: "보합",
    PENDING: "대기",
    NOT_ACTIONABLE: "관망"
  };
  return labels[String(value ?? "").toUpperCase()] ?? value ?? "-";
}

function promotionStatusLabel(value?: string | null) {
  const labels: Record<string, string> = {
    SHADOW_ONLY: "Shadow 전용",
    READY_FOR_LIMITED: "제한 실주문 준비",
    BLOCKED: "승격 차단",
    SUBMITTED: "주문 제출"
  };
  return labels[String(value ?? "").toUpperCase()] ?? value ?? "-";
}

function smartReadinessLabel(value?: string | null) {
  const labels: Record<string, string> = {
    READY_TO_ENABLE_LIMITED: "limited 전환 가능",
    BLOCKED: "전환 차단"
  };
  return labels[String(value ?? "").toUpperCase()] ?? value ?? "-";
}

function smartReadinessTone(value?: string | null) {
  const normalized = String(value ?? "").toLowerCase();
  if (normalized === "pass") return "is-pass";
  if (normalized === "warn") return "is-warn";
  return "is-block";
}

function latestPolicyBlockNotice(data: DashboardData): PolicyBlockNotice | null {
  const riskNotices = (data.risk?.risk_logs ?? [])
    .filter((log) => !log.allowed && (extractPolicyBlockCode(log.block_code) || extractPolicyBlockCode(log.block_reason)))
    .map((log) => {
      const code = extractPolicyBlockCode(log.block_code) ?? extractPolicyBlockCode(log.block_reason) ?? "POLICY_BLOCKED";
      return {
        code,
        text: policyBlockText(code, log.block_reason),
        createdAt: log.created_at,
        source: "risk" as const,
        detail: log.policy_block_detail
      };
    });
  const latest = data.analysisLatest;
  const analysisNotices = (latest?.blockers ?? [])
    .filter((blocker) => extractPolicyBlockCode(blocker))
    .map((blocker) => {
      const code = extractPolicyBlockCode(blocker) ?? "POLICY_BLOCKED";
      return {
        code,
        text: policyBlockText(code, blocker),
        createdAt: latest?.decided_at ?? latest?.created_at,
        source: "analysis" as const,
        detail: undefined
      };
    });
  return [...riskNotices, ...analysisNotices]
    .sort((a, b) => (parseDate(b.createdAt)?.getTime() ?? 0) - (parseDate(a.createdAt)?.getTime() ?? 0))[0] ?? null;
}

function statusTone(value?: string | null): "green" | "amber" | "red" | "cyan" | "neutral" {
  if (!value) return "neutral";
  const normalized = String(value).toUpperCase();
  if (["OK", "READY", "RUNNING", "FILLED", "SUBMITTED", "ACTIVE"].includes(normalized)) return "green";
  if (["WARNING", "WAITING", "PENDING", "LIVE_PAUSED", "PAUSED", "READY_READ_ONLY", "LIVE_ARMED", "MANUAL_REVIEW_REQUIRED"].includes(normalized)) return "amber";
  if (["BLOCKED", "FAILED", "ERROR", "EMERGENCY_STOPPED", "LIVE_DISABLED", "CANCELED", "CANCELLED", "STOPPED", "INACTIVE"].includes(normalized) || normalized.startsWith("BLOCKED_")) return "red";
  return "neutral";
}

function refStatusChipClass(value?: string | null) {
  return `ref-status-chip ${statusTone(value)}`;
}

function isRunning(status?: string | null) {
  return status === "RUNNING" || status === "LIVE_PAUSED" || status === "READY";
}

function latestCandle(candles?: Candle[] | null) {
  if (!candles?.length) return null;
  return candles.length ? candles[candles.length - 1] : null;
}

function previousCandle(candles?: Candle[] | null) {
  if (!candles || candles.length <= 1) return null;
  return candles.length > 1 ? candles[candles.length - 2] : null;
}

function computeRsi(values: number[], period = 14) {
  if (values.length <= period) return null;
  let gains = 0;
  let losses = 0;
  const start = values.length - period;
  for (let i = start; i < values.length; i += 1) {
    const diff = values[i] - values[i - 1];
    if (diff >= 0) gains += diff;
    else losses += Math.abs(diff);
  }
  if (losses === 0) return 100;
  const rs = gains / losses;
  return 100 - 100 / (1 + rs);
}

function average(values: number[]) {
  if (!values.length) return null;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function standardDeviation(values: number[]) {
  const avg = average(values);
  if (avg == null) return null;
  return Math.sqrt(values.reduce((sum, value) => sum + (value - avg) ** 2, 0) / values.length);
}

function computeIndicators(candles: Candle[] = []) {
  const closes = candles.map((candle) => candle.trade_price);
  const latest = latestCandle(candles);
  const previous = previousCandle(candles);
  const rsi = computeRsi(closes);
  const last20 = closes.slice(-20);
  const sma20 = average(last20);
  const sd20 = standardDeviation(last20);
  const bbPercent = latest && sma20 != null && sd20 != null && sd20 > 0
    ? ((latest.trade_price - (sma20 - 2 * sd20)) / (4 * sd20)) * 100
    : null;
  const atrValues = candles.slice(-14).map((candle, index, list) => {
    const previousClose = index === 0 ? previous?.trade_price ?? candle.opening_price : list[index - 1].trade_price;
    return Math.max(
      candle.high_price - candle.low_price,
      Math.abs(candle.high_price - previousClose),
      Math.abs(candle.low_price - previousClose)
    );
  });
  const atr = average(atrValues);
  const volume24 = candles.reduce((sum, candle) => sum + (candle.candle_acc_trade_volume ?? 0), 0);
  const change = latest && previous ? latest.trade_price - previous.trade_price : null;
  const macdProxy = closes.length >= 26 ? (average(closes.slice(-12)) ?? 0) - (average(closes.slice(-26)) ?? 0) : null;
  const mfiProxy = latest && previous ? ((latest.trade_price - previous.trade_price) / previous.trade_price) * 100 : null;

  return { rsi, bbPercent, atr, volume24, change, macdProxy, mfiProxy, sma20 };
}

function accountStateText(status: LiveStatus | null) {
  if (status?.emergency_stop) return "긴급정지";
  if (status?.live_trading_enabled && (status?.broker_status === "READY" || status?.broker_status === "READY_READ_ONLY")) return "활성";
  if (status) return "비활성";
  return "-";
}

function accountStateTone(status: LiveStatus | null) {
  if (status?.emergency_stop) return "danger";
  if (status?.live_trading_enabled && (status?.broker_status === "READY" || status?.broker_status === "READY_READ_ONLY")) return "active";
  if (status) return "inactive";
  return "unknown";
}

function botRuntimeState(data: DashboardData) {
  if (data.runtimeStatus?.runtime_status) return data.runtimeStatus.runtime_status;
  const autoStatus = data.autoPilot?.session?.status;
  const strategyStatus = data.liveStrategy?.session?.status;
  if (isRunning(strategyStatus)) return strategyStatus ?? "RUNNING";
  if (isRunning(autoStatus)) return autoStatus ?? "RUNNING";
  if (strategyStatus) return strategyStatus;
  if (autoStatus) return autoStatus;
  if (data.liveStatus?.emergency_stop) return "EMERGENCY_STOPPED";
  if (!data.liveStatus?.live_trading_enabled) return "LIVE_DISABLED";
  return "대기";
}

function isRuntimeRunning(data: DashboardData) {
  if (data.runtimeStatus?.runtime_status) return data.runtimeStatus.runtime_status === "RUNNING";
  return isRunning(data.liveStrategy?.session?.status) || isRunning(data.autoPilot?.session?.status);
}

function autoRuntimeMs(data: DashboardData, now: number) {
  if (!isRuntimeRunning(data)) return null;
  const sessions = [data.liveStrategy?.session, data.autoPilot?.session].filter(Boolean);
  const runningStarts = sessions
    .filter((session) => isRunning(session?.status))
    .map((session) => parseDate(session?.created_at)?.getTime())
    .filter((time): time is number => time != null);
  if (runningStarts.length > 0) return now - Math.min(...runningStarts);

  const completed = sessions
    .map((session) => {
      const start = parseDate(session?.created_at)?.getTime();
      const stop = parseDate(session?.stopped_at)?.getTime();
      if (start == null || stop == null) return null;
      return { stoppedAt: stop, duration: stop - start };
    })
    .filter((item): item is { stoppedAt: number; duration: number } => item != null)
    .sort((a, b) => b.stoppedAt - a.stoppedAt);
  return completed[0]?.duration ?? null;
}

function useKstClock() {
  const [now, setNow] = React.useState(() => new Date());
  React.useEffect(() => {
    const intervalId = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(intervalId);
  }, []);
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    hourCycle: "h23"
  }).format(now).replace(/\. /g, "-").replace(".", "");
}

function Topbar({
  data,
  selectedExchange,
  onExchangeChange,
  onRefresh,
  onLogout
}: {
  data: DashboardData;
  selectedExchange: DashboardExchange;
  onExchangeChange: (exchange: DashboardExchange) => void;
  onRefresh: () => Promise<void>;
  onLogout: () => Promise<void>;
}) {
  const alertCount = data.risk?.risk_logs?.filter((log) => !log.allowed && log.read_status !== "READ").length ?? 0;
  const accountTone = accountStateTone(data.liveStatus);
  const kstTime = useKstClock();
  const [userMenuOpen, setUserMenuOpen] = React.useState(false);
  const [refreshing, setRefreshing] = React.useState(false);
  const [refreshMessage, setRefreshMessage] = React.useState<string | null>(null);

  const handleRefresh = React.useCallback(async () => {
    setRefreshing(true);
    setRefreshMessage(null);
    try {
      await onRefresh();
      setRefreshMessage(`갱신 완료 ${formatKstTime(new Date().toISOString())}`);
    } catch (err) {
      setRefreshMessage(err instanceof Error ? err.message : "데이터 갱신 실패");
    } finally {
      setRefreshing(false);
    }
  }, [onRefresh]);

  return (
    <header className="ref-topbar">
      <button className="ref-menu-button" aria-label="메뉴">
        <Menu size={22} />
      </button>
      <div className="ref-brand">
        <span className="ref-logo">Q</span>
        <strong>Auto Trader</strong>
        <span className="ref-kst-clock"><em>UTC+9</em>{kstTime}</span>
      </div>
      <div className="ref-topbar-right">
        <span className={`ref-account ${accountTone}`}>거래 상태 <i /> <b>{accountStateText(data.liveStatus)}</b></span>
        <label className="ref-exchange">
          <span>거래소</span>
          <select value={selectedExchange} onChange={(event) => onExchangeChange(event.target.value as DashboardExchange)}>
            <option value="bithumb">빗썸 (Bithumb)</option>
            <option value="upbit">업비트 (Upbit)</option>
          </select>
        </label>
        <div className="ref-search">
          <Search size={17} />
          <span>코인 이름 또는 심볼 검색</span>
        </div>
        <button className="ref-bell" aria-label="알림">
          <Bell size={21} />
          {alertCount > 0 && <em>{alertCount}</em>}
        </button>
        <span className="ref-trader">트레이더 <b>Pro</b></span>
        <div className="ref-user-menu-wrap">
          <button className={`ref-user-button ${userMenuOpen ? "is-open" : ""}`} type="button" aria-label="사용자 메뉴" aria-expanded={userMenuOpen} onClick={() => setUserMenuOpen((value) => !value)}>
            <CircleUserRound className="ref-user" size={30} />
          </button>
          {userMenuOpen && (
            <div className="ref-user-menu">
              <button className="ref-user-menu-action" type="button" onClick={() => void handleRefresh()} disabled={refreshing}>
                <RefreshCw size={16} className={refreshing ? "ref-spin" : ""} />
                <span>{refreshing ? "데이터 가져오는 중" : "데이터 가져오기"}</span>
              </button>
              <button className="ref-user-menu-action is-logout" type="button" onClick={() => void onLogout()}>
                <PowerOff size={16} />
                <span>로그아웃</span>
              </button>
              <p><span>최근 갱신</span><b>{refreshMessage ?? formatKstTime(data.updatedAt)}</b></p>
              {data.errors.length > 0 && <em>{data.errors[0]}</em>}
            </div>
          )}
        </div>
      </div>
    </header>
  );
}

function Sidebar({ activeView, onViewChange }: { activeView: ReferenceView; onViewChange: (view: ReferenceView) => void }) {
  return (
    <aside className="ref-sidebar">
      <nav className="ref-nav">
        {navItems.map((item) => {
          const Icon = item.icon;
          const isImplemented = item.id === "dashboard" || item.id === "auto-trade" || item.id === "analysis" || item.id === "operations" || item.id === "portfolio" || item.id === "trades" || item.id === "backtest" || item.id === "alerts";
          const isActive = activeView === item.id;
          return (
            <button
              key={item.label}
              className={`ref-nav-item ${isActive ? "is-active" : ""} ${isImplemented ? "" : "is-disabled"}`}
              disabled={!isImplemented}
              aria-disabled={!isImplemented}
              onClick={() => {
                if (isImplemented) onViewChange(item.id as ReferenceView);
              }}
            >
              <Icon size={22} />
              <span>{item.label}</span>
              {!isImplemented && <em>개발중</em>}
            </button>
          );
        })}
      </nav>
      <div className="ref-sidebar-footer">
        <span>Build</span>
        <b>{APP_BUILD_LABEL}</b>
      </div>
      <button className="ref-collapse">≪ 메뉴 접기</button>
    </aside>
  );
}

function SlotAnimatedText({ value }: { value: string }) {
  const [current, setCurrent] = React.useState(value);
  const [previous, setPrevious] = React.useState(value);
  const [rolling, setRolling] = React.useState(false);
  const currentRef = React.useRef(value);

  React.useEffect(() => {
    if (value === currentRef.current) return;
    setPrevious(currentRef.current);
    currentRef.current = value;
    setCurrent(value);
    setRolling(true);
    const timeoutId = window.setTimeout(() => setRolling(false), 560);
    return () => window.clearTimeout(timeoutId);
  }, [value]);

  const width = Math.max(previous.length, current.length);
  const previousChars = previous.padStart(width, " ").split("");
  const currentChars = current.padStart(width, " ").split("");

  return (
    <span className={`ref-slot-value ${rolling ? "is-rolling" : ""}`} aria-label={current}>
      {currentChars.map((char, index) => {
        const oldChar = previousChars[index] ?? " ";
        const newChar = char ?? " ";
        const isNumberSlot = /\d/.test(oldChar) || /\d/.test(newChar);
        const shouldRoll = rolling && isNumberSlot && oldChar !== newChar;
        const key = `${index}-${newChar}-${oldChar}`;
        const displayOld = oldChar === " " ? "\u00A0" : oldChar;
        const displayNew = newChar === " " ? "\u00A0" : newChar;
        return (
          <span key={key} className={`ref-slot-char ${isNumberSlot ? "is-number" : "is-symbol"} ${shouldRoll ? "is-rolling" : ""}`} aria-hidden="true">
            {shouldRoll ? (
              <span className="ref-slot-stack">
                <span>{displayOld}</span>
                <span>{displayNew}</span>
              </span>
            ) : displayNew}
          </span>
        );
      })}
    </span>
  );
}

function KpiCard({
  className,
  icon,
  label,
  value,
  sub,
  tone = "purple"
}: {
  className: string;
  icon: React.ReactNode;
  label: string;
  value: string;
  sub: string;
  tone?: Tone;
}) {
  const [direction, setDirection] = React.useState<"up" | "down" | null>(null);
  const previousNumericRef = React.useRef<number | null>(parseDisplayNumber(value));

  React.useEffect(() => {
    const next = parseDisplayNumber(value);
    const previous = previousNumericRef.current;
    if (next == null) {
      previousNumericRef.current = next;
      return;
    }
    if (previous != null && next !== previous) {
      setDirection(next > previous ? "up" : "down");
      const timeoutId = window.setTimeout(() => setDirection(null), 920);
      previousNumericRef.current = next;
      return () => window.clearTimeout(timeoutId);
    }
    previousNumericRef.current = next;
  }, [value]);

  return (
    <RefPanel className={`ref-kpi ${className} ${direction ? `is-value-${direction}` : ""}`}>
      <div className={`ref-kpi-icon ${tone}`}>{icon}</div>
      <div>
        <p>{label}</p>
        <strong className={value.startsWith("+") ? "ref-positive" : value.startsWith("-") ? "ref-negative" : ""}><SlotAnimatedText value={value} /></strong>
        <span className={`ref-kpi-sub ${sub.startsWith("+") ? "ref-positive" : sub.startsWith("-") ? "ref-negative" : ""}`}><SlotAnimatedText value={sub} /></span>
      </div>
    </RefPanel>
  );
}

function toChartTime(value: string): Time {
  return Math.floor((parseDate(value)?.getTime() ?? Date.now()) / 1000) as Time;
}

function movingAverage(candles: Candle[], period: number) {
  return candles
    .map((candle, index) => {
      if (index + 1 < period) return null;
      const window = candles.slice(index + 1 - period, index + 1);
      return {
        time: toChartTime(candle.candle_time_utc),
        value: window.reduce((sum, item) => sum + item.trade_price, 0) / period
      };
    })
    .filter((item): item is { time: Time; value: number } => item != null);
}

function TradingChart({
  candles,
  stageScale,
  chartUnit,
  onHoverCandle
}: {
  candles: Candle[];
  stageScale: number;
  chartUnit: number;
  onHoverCandle: (candle: Candle | null) => void;
}) {
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const chartRef = React.useRef<ReturnType<typeof createChart> | null>(null);
  const candleSeriesRef = React.useRef<any>(null);
  const ma20Ref = React.useRef<any>(null);
  const ma50Ref = React.useRef<any>(null);
  const volumeSeriesRef = React.useRef<any>(null);
  const candleByTimeRef = React.useRef<Map<number, Candle>>(new Map());
  const onHoverCandleRef = React.useRef(onHoverCandle);
  const didSetInitialRangeRef = React.useRef(false);
  const previousDataLengthRef = React.useRef(0);
  const [hoverPoint, setHoverPoint] = React.useState<{ x: number; y: number } | null>(null);
  const [hoverCandle, setHoverCandle] = React.useState<Candle | null>(null);
  const hasCandles = candles.length > 0;
  const latest = latestCandle(candles);

  React.useEffect(() => {
    onHoverCandleRef.current = onHoverCandle;
  }, [onHoverCandle]);

  React.useEffect(() => {
    if (!containerRef.current || !hasCandles) return;

    const container = containerRef.current;
    didSetInitialRangeRef.current = false;
    previousDataLengthRef.current = 0;
    const chart = createChart(container, {
      width: container.clientWidth,
      height: container.clientHeight,
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#aeb8c7"
      },
      grid: {
        vertLines: { color: "rgba(58, 76, 99, 0.32)" },
        horzLines: { color: "rgba(58, 76, 99, 0.32)" }
      },
      rightPriceScale: {
        borderColor: "rgba(52, 72, 96, 0.55)",
        scaleMargins: { top: 0.08, bottom: 0.24 }
      },
      timeScale: {
        borderColor: "rgba(52, 72, 96, 0.55)",
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (time: Time) => formatKstTime(new Date((time as number) * 1000).toISOString()).slice(0, 5)
      },
      crosshair: {
        vertLine: { visible: false, labelVisible: false },
        horzLine: { visible: false, labelVisible: false }
      },
      localization: {
        priceFormatter: (price: number) => formatKrw(price)
      },
      handleScroll: {
        mouseWheel: true,
        pressedMouseMove: true,
        horzTouchDrag: true,
        vertTouchDrag: false
      },
      handleScale: {
        axisPressedMouseMove: true,
        mouseWheel: true,
        pinch: true
      }
    });
    chartRef.current = chart;

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#12c979",
      downColor: "#ff3e4e",
      borderUpColor: "#12c979",
      borderDownColor: "#ff3e4e",
      wickUpColor: "#12c979",
      wickDownColor: "#ff3e4e"
    });
    candleSeriesRef.current = candleSeries;

    const ma20 = chart.addSeries(LineSeries, {
      color: "#d07b0c",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false
    });
    ma20Ref.current = ma20;

    const ma50 = chart.addSeries(LineSeries, {
      color: "#2c71d0",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false
    });
    ma50Ref.current = ma50;

    const volumeSeries = chart.addSeries(HistogramSeries, {
      color: "#1f9d68",
      priceFormat: { type: "volume" },
      priceScaleId: "",
      priceLineVisible: false,
      lastValueVisible: false
    });
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.78, bottom: 0 }
    });
    volumeSeriesRef.current = volumeSeries;

    return () => {
      chartRef.current = null;
      candleSeriesRef.current = null;
      ma20Ref.current = null;
      ma50Ref.current = null;
      volumeSeriesRef.current = null;
      didSetInitialRangeRef.current = false;
      previousDataLengthRef.current = 0;
      chart.remove();
    };
  }, [hasCandles, chartUnit]);

  React.useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !candleSeriesRef.current || !ma20Ref.current || !ma50Ref.current || !volumeSeriesRef.current || candles.length === 0) return;

    const timeScale = chart.timeScale();
    const previousLength = previousDataLengthRef.current;
    const currentRange = didSetInitialRangeRef.current ? timeScale.getVisibleLogicalRange() : null;
    const addedBars = Math.max(candles.length - previousLength, 0);
    const wasFollowingLatest = currentRange != null && previousLength > 0 && currentRange.to >= previousLength - 1.5;

    candleByTimeRef.current = new Map(candles.map((candle) => [toChartTime(candle.candle_time_utc) as number, candle]));

    candleSeriesRef.current.setData(candles.map((candle) => ({
      time: toChartTime(candle.candle_time_utc),
      open: candle.opening_price,
      high: candle.high_price,
      low: candle.low_price,
      close: candle.trade_price
    })));
    ma20Ref.current.setData(movingAverage(candles, 20));
    ma50Ref.current.setData(movingAverage(candles, Math.min(50, Math.max(5, Math.floor(candles.length / 2)))));
    volumeSeriesRef.current.setData(candles.map((candle) => ({
      time: toChartTime(candle.candle_time_utc),
      value: candle.candle_acc_trade_volume,
      color: candle.trade_price >= candle.opening_price ? "rgba(18, 201, 121, 0.48)" : "rgba(184, 67, 47, 0.48)"
    })));

    if (!didSetInitialRangeRef.current) {
      const from = Math.max(0, candles.length - 48);
      timeScale.setVisibleLogicalRange({ from, to: candles.length + 2 });
      didSetInitialRangeRef.current = true;
    } else if (currentRange) {
      timeScale.setVisibleLogicalRange(wasFollowingLatest && addedBars > 0
        ? { from: currentRange.from + addedBars, to: currentRange.to + addedBars }
        : currentRange);
    }

    previousDataLengthRef.current = candles.length;
  }, [candles]);

  if (!candles.length) {
    return (
      <div className="ref-chart-area">
        <div className="ref-chart-grid" />
        <div className="ref-empty-state">캔들 데이터 없음</div>
      </div>
    );
  }

  return (
    <div
      className="ref-chart-area"
      onPointerMove={(event) => {
        const rect = event.currentTarget.getBoundingClientRect();
        const scale = stageScale || 1;
        const x = (event.clientX - rect.left) / scale;
        const y = (event.clientY - rect.top) / scale;
        const coordinateToTime = chartRef.current?.timeScale() && (chartRef.current.timeScale() as any).coordinateToTime;
        const hoveredTime = typeof coordinateToTime === "function" ? coordinateToTime.call(chartRef.current?.timeScale(), x) : null;
        const hoveredCandle = typeof hoveredTime === "number" ? candleByTimeRef.current.get(hoveredTime) ?? null : null;
        setHoverPoint({
          x,
          y
        });
        setHoverCandle(hoveredCandle);
        onHoverCandleRef.current(hoveredCandle);
      }}
      onPointerLeave={() => {
        setHoverPoint(null);
        setHoverCandle(null);
        onHoverCandleRef.current(null);
      }}
      onMouseLeave={() => {
        setHoverPoint(null);
        setHoverCandle(null);
        onHoverCandleRef.current(null);
      }}
    >
      <div ref={containerRef} className="ref-chart-canvas" />
      {hoverPoint && (
        <div className="ref-chart-hover" aria-hidden="true">
          <span className="ref-chart-hover-v" style={{ left: `${hoverPoint.x}px` }} />
          <span className="ref-chart-hover-h" style={{ top: `${hoverPoint.y}px` }} />
          {hoverCandle && (
            <span
              className="ref-chart-tooltip"
              style={{
                left: `${Math.min(Math.max(hoverPoint.x + 14, 10), 660)}px`,
                top: `${Math.min(Math.max(hoverPoint.y + 14, 10), 245)}px`
              }}
            >
              <b>{formatChartCandleTime(hoverCandle.candle_time_utc, chartUnit)} · {chartUnitLabel(chartUnit)}</b>
              <em>시 {formatKrw(hoverCandle.opening_price)} 고 {formatKrw(hoverCandle.high_price)}</em>
              <em>저 {formatKrw(hoverCandle.low_price)} 종 {formatKrw(hoverCandle.trade_price)}</em>
            </span>
          )}
        </div>
      )}
      <span className="ref-chart-price">{formatKrw(latest?.trade_price)}</span>
      <div className="ref-tv">TV</div>
    </div>
  );
}

function MainChartPanel({
  data,
  stageScale,
  chartUnit,
  onChartUnitChange
}: {
  data: DashboardData;
  stageScale: number;
  chartUnit: number;
  onChartUnitChange: (unit: number) => void;
}) {
  const [hoveredCandle, setHoveredCandle] = React.useState<Candle | null>(null);
  const latest = latestCandle(data.candles);
  const displayCandle = hoveredCandle ?? latest;
  const displayIndex = displayCandle
    ? data.candles.findIndex((candle) => candle.candle_time_utc === displayCandle.candle_time_utc)
    : -1;
  const previous = displayIndex > 0 ? data.candles[displayIndex - 1] : hoveredCandle ? null : previousCandle(data.candles);
  const latestPrevious = previousCandle(data.candles);
  const indicators = computeIndicators(data.candles);
  const latestChange = latest && latestPrevious ? latest.trade_price - latestPrevious.trade_price : null;
  const latestChangeRate = latest && latestPrevious ? latestChange! / latestPrevious.trade_price : null;
  const change = displayCandle && previous ? displayCandle.trade_price - previous.trade_price : null;
  const changeRate = displayCandle && previous ? change! / previous.trade_price : null;
  const marketTone = latestChange == null ? "" : latestChange < 0 ? "ref-negative" : latestChange > 0 ? "ref-positive" : "";
  const displayTone = change == null ? "" : change < 0 ? "ref-negative" : change > 0 ? "ref-positive" : "";
  const high = data.candles.length ? Math.max(...data.candles.map((candle) => candle.high_price)) : null;
  const low = data.candles.length ? Math.min(...data.candles.map((candle) => candle.low_price)) : null;

  React.useEffect(() => {
    setHoveredCandle(null);
  }, [chartUnit, data.candles]);

  return (
    <RefPanel className="ref-chart-panel">
      <div className="ref-market-line">
        <div className="ref-market-title">
          <span className="ref-bitcoin"><Bitcoin size={20} /></span>
          <b>BTC/KRW</b>
          <strong className={marketTone}>{formatKrw(latest?.trade_price)}</strong>
        </div>
        <div className="ref-market-stats">
          <span className={marketTone}><b>{formatPercent(latestChangeRate)}</b><b>{formatSignedKrw(latestChange)}</b></span>
          <span><em>고가</em>{formatKrw(high)}</span>
          <span><em>저가</em>{formatKrw(low)}</span>
          <span><em>거래량 ({data.candles.length}캔들)</em>{formatNumber(indicators.volume24, 2)} BTC</span>
        </div>
      </div>
      <div className="ref-chart-toolbar">
        <div className="ref-left-tools">
          <button className="is-cross">+</button>
          {CHART_TIMEFRAMES.map((timeframe) => (
            <button
              key={timeframe.label}
              className={`${chartUnit === timeframe.unit ? "is-selected" : ""} ${timeframe.disabled ? "is-disabled" : ""}`}
              disabled={timeframe.disabled}
              title={timeframe.disabled ? "일봉 데이터 API 연결 준비중" : `${timeframe.label} 차트 보기`}
              onClick={() => {
                if (!timeframe.disabled) onChartUnitChange(timeframe.unit);
              }}
            >
              {timeframe.label}
            </button>
          ))}
          <span />
          <button><SlidersHorizontal size={18} /></button>
          <button>지표</button>
          <button>ƒx</button>
          <button>↶</button>
          <button className="is-muted">↷</button>
        </div>
        <div className="ref-right-tools">
          <button><Settings size={17} /></button>
          <button><Maximize size={17} /></button>
          <button><Camera size={17} /></button>
        </div>
      </div>
      <div className="ref-chart-meta">
        <b>BTC/KRW · {chartUnitLabel(chartUnit)} · {data.liveStatus?.exchange?.toUpperCase() ?? "EXCHANGE"}</b>
        <span>{hoveredCandle ? formatChartCandleTime(hoveredCandle.candle_time_utc, chartUnit) : "최신"}</span>
        <span>시 {formatKrw(displayCandle?.opening_price)}</span>
        <span>고 {formatKrw(displayCandle?.high_price)}</span>
        <span>저 {formatKrw(displayCandle?.low_price)}</span>
        <span>종 {formatKrw(displayCandle?.trade_price)}</span>
        <strong className={displayTone}>{formatSignedKrw(change)} ({formatPercent(changeRate)})</strong>
      </div>
      <div className="ref-ma-labels">
        <span>MA 20 close <b className="orange">{formatKrw(indicators.sma20)}</b></span>
        <span>RSI 14 <b className="blue">{indicators.rsi == null ? "-" : indicators.rsi.toFixed(1)}</b></span>
      </div>
      <TradingChart candles={data.candles} stageScale={stageScale} chartUnit={chartUnit} onHoverCandle={setHoveredCandle} />
      <div className="ref-chart-footer">
        <span>{formatChartCandleTime(latest?.candle_time_utc, chartUnit)} (UTC+9)</span>
        <span>%</span>
        <span>로그</span>
        <b>자동</b>
      </div>
    </RefPanel>
  );
}

function BotStatusPanel({ data, onOpenAutoTrade }: { data: DashboardData; onOpenAutoTrade: () => void }) {
  const autoRunning = isRuntimeRunning(data);
  const riskState = data.risk?.risk_state;
  const dailyPnl = riskState?.daily_total_pnl ?? data.paper?.balance?.total_pnl ?? null;
  const dailyLossPercent = Math.abs(riskState?.daily_loss_percent ?? 0);
  const maxDailyLossPercent = data.risk?.config?.max_daily_loss_percent ?? 20;
  const dailyLossBarWidth = Math.min(dailyLossPercent / Math.max(maxDailyLossPercent, 1) * 100, 100);
  const strategyName = data.liveStrategy?.session?.strategy_name ?? data.autoPilot?.session?.strategy_name ?? data.candidates[0]?.name ?? "-";
  const ordersToday = data.liveStrategy?.session?.orders_created_today ?? data.autoPilot?.session?.orders_created_today;
  const maxOrders = data.liveStrategy?.session?.max_orders_per_day ?? data.autoPilot?.session?.max_orders_per_day;
  const runtimeState = botRuntimeState(data);
  const runtime = data.runtimeStatus;
  const exchangeName = runtime?.exchange ?? data.health?.selected_exchange ?? data.liveStatus?.exchange ?? "bithumb";
  const orderText = ordersToday == null
    ? "-"
    : maxOrders == null || maxOrders === 0
      ? `${ordersToday} / 무제한`
      : `${ordersToday} / ${maxOrders}`;
  const runtimeTone = runtimeState === "RUNNING" ? "ref-positive" : runtimeState === "STOPPED" || runtimeState === "LIVE_DISABLED" ? "ref-negative" : "";

  return (
    <RefPanel className="ref-bot-panel">
      <div className="ref-panel-title">
        <b>봇 상태</b>
        <span className={`ref-auto-state ${autoRunning ? "is-on" : "is-off"}`}>자동매매 {autoRunning ? "ON" : "OFF"} <Power size={18} /></span>
      </div>
      <div className="ref-bot-body">
        <div className={`ref-bot-face ${autoRunning ? "is-running" : "is-paused"}`}><Bot size={48} /></div>
        <div className="ref-bot-summary">
          <p>현재 상태</p>
          <strong className={runtimeTone}>{statusLabel(runtimeState)}</strong>
          <span>{autoRunning ? "자동매매 실행 중" : "사용자 시작 대기"}</span>
        </div>
      </div>
      <div className="ref-bot-info-grid">
        <p><span>거래소</span><b>{exchangeName}</b></p>
        <p><span>오늘 주문</span><b>{orderText}</b></p>
        <p><span>현재 전략</span><b>{strategyName}</b></p>
        <p className="ref-bot-loss-card">
          <span>오늘 손익</span>
          <strong className={(dailyPnl ?? 0) >= 0 ? "ref-positive" : "ref-negative"}>{formatSignedKrw(dailyPnl)}</strong>
          <em>일 손실률 <b>{dailyLossPercent.toFixed(2)}% / {maxDailyLossPercent.toFixed(0)}%</b></em>
          <i><small style={{ width: `${dailyLossBarWidth}%` }} /></i>
        </p>
      </div>
    </RefPanel>
  );
}

function PositionPanel({ data }: { data: DashboardData }) {
  const latest = latestCandle(data.candles);
  const livePosition = data.liveStrategy?.position;
  const paperPosition = data.paper?.position;
  const liveBtc = liveBtcStats(data);
  const quantity = livePosition?.entry_volume ?? liveBtc?.quantity ?? paperPosition?.btc_quantity ?? paperPosition?.current_position_volume ?? 0;
  const entryPrice = livePosition?.entry_price ?? liveBtc?.averageEntry ?? paperPosition?.avg_buy_price ?? paperPosition?.average_entry_price ?? null;
  const currentPrice = livePosition?.current_price ?? liveBtc?.currentPrice ?? data.paper?.balance?.current_price ?? latest?.trade_price ?? null;
  const pnl = livePosition?.unrealized_pnl ?? liveBtc?.unrealizedPnl ?? data.paper?.balance?.unrealized_pnl ?? null;
  const hasPosition = quantity > 0;
  const left = [
    ["포지션", hasPosition ? "BTC/KRW" : "-", hasPosition ? livePosition?.status ?? "보유 중" : "없음"],
    ["수량", hasPosition ? `${formatNumber(quantity, 8)} BTC` : "-", ""],
    ["진입가", hasPosition ? formatKrw(entryPrice) : "-", ""],
    ["현재가", hasPosition ? formatKrw(currentPrice) : "-", ""],
    ["평가 손익", hasPosition ? formatSignedKrw(pnl) : "-", ""]
  ];
  const right = [
    ["레버리지", "SPOT (1:1)"],
    ["Stop Loss", formatKrw(livePosition?.stop_loss_price)],
    ["Take Profit", formatKrw(livePosition?.take_profit_price)],
    ["수익 실현", data.liveStrategy?.auto_exit_enabled ? "ON" : "OFF"]
  ];

  return (
    <RefPanel className="ref-position-panel">
      <h3>포지션 / 주문 현황</h3>
      <div className="ref-position-grid">
        <div>
          {left.map(([label, value, badge]) => (
            <p key={label}>
              <span>{label}</span>
              <b className={label === "평가 손익" && String(value).startsWith("+") ? "ref-positive" : label === "평가 손익" && String(value).startsWith("-") ? "ref-negative" : ""}>{value}</b>
              {badge && <em>{badge}</em>}
            </p>
          ))}
        </div>
        <div>
          {right.map(([label, value]) => (
            <p key={label}><span>{label}</span><b>{value}</b></p>
          ))}
        </div>
      </div>
      <button className="ref-emergency">◎ 긴급 청산 (모든 포지션 및 주문 취소)</button>
    </RefPanel>
  );
}

function SignalPanel({ data }: { data: DashboardData }) {
  const indicators = computeIndicators(data.candles);
  const items = [
    ["RSI (14)", indicators.rsi == null ? "-" : indicators.rsi.toFixed(1), indicators.rsi == null ? "-" : indicators.rsi >= 70 ? "과열" : indicators.rsi <= 30 ? "침체" : "중립", "purple"],
    ["MACD (12,26,9)", formatSignedKrw(indicators.macdProxy), indicators.macdProxy == null ? "-" : indicators.macdProxy > 0 ? "매수 신호" : "매도 신호", "bars"],
    ["거래량", indicators.volume24 == null ? "-" : `${formatNumber(indicators.volume24, 2)} BTC`, indicators.volume24 ? "갱신" : "-", "volume"],
    ["MFI 대용", indicators.mfiProxy == null ? "-" : `${indicators.mfiProxy.toFixed(2)}%`, indicators.mfiProxy == null ? "-" : indicators.mfiProxy >= 0 ? "상승" : "하락", "yellow"],
    ["BB %B", indicators.bbPercent == null ? "-" : indicators.bbPercent.toFixed(1), indicators.bbPercent == null ? "-" : "중립", "gauge"],
    ["ATR (14)", formatKrw(indicators.atr), indicators.atr == null ? "-" : "변동성", "greenline"]
  ];
  return (
    <RefPanel className="ref-signal-panel">
      <div className="ref-title-row">
        <h3>실행 현황 (BTC/KRW)</h3>
        <span>업데이트 {formatKstTime(data.updatedAt)}</span>
      </div>
      <div className="ref-signal-grid">
        {items.map(([label, value, sub, type]) => (
          <div key={label} className="ref-signal-card">
            <span>{label}</span>
            <strong>{value}</strong>
            <b className={sub === "하락" || sub === "과열" ? "amber" : ""}>{sub}</b>
            <i className={`spark ${type}`} />
          </div>
        ))}
      </div>
    </RefPanel>
  );
}

function PortfolioPanel({ data, totalEquity }: { data: DashboardData; totalEquity?: number | null }) {
  const fallbackTotal = totalEquity != null && totalEquity >= 10_000 ? totalEquity : portfolioBaseTotal;
  const rows = buildPortfolioRows(data, fallbackTotal);
  const total = portfolioDisplayTotal(data, rows, fallbackTotal);
  const mainRows = rows.slice(0, 5);
  const otherValue = rows.slice(5).reduce((sum, item) => sum + item.value, 0);
  const otherAllocation = total > 0 ? otherValue / total : 0;
  const legendRows = otherValue > 0
    ? [...mainRows, { ...portfolioMeta("기타"), symbol: "기타", value: otherValue, allocation: otherAllocation } as PortfolioAssetRow]
    : mainRows;
  const trend = portfolioTrend(data, total);

  return (
    <RefPanel className="ref-portfolio-panel">
      <div className="ref-portfolio-panel-head">
        <h3>포트폴리오</h3>
        <span>{data.liveBalances?.balance_fetch_status === "SUCCESS" ? "실잔고 기준" : "레퍼런스 표시"}</span>
      </div>
      <div className="ref-dashboard-portfolio-grid">
        <div className="ref-dashboard-portfolio-composition">
          <div className="ref-portfolio-page-donut ref-dashboard-portfolio-donut" style={{ background: buildConicGradient(legendRows) }}>
            <div><span>총 자산</span><b>{formatKrw(total)}</b><em>KRW</em></div>
          </div>
          <div className="ref-portfolio-page-legend ref-dashboard-portfolio-legend">
            {legendRows.map((asset) => (
              <p key={asset.symbol}><i style={{ background: asset.color }} /><span>{asset.symbol}</span><b>{formatRatioPercent(asset.allocation)}</b><em>{formatKrw(asset.value)} KRW</em></p>
            ))}
          </div>
        </div>
        <div className="ref-dashboard-portfolio-trend">
          <PortfolioTrendChart total={total} values={trend.values} labels={trend.labels} />
        </div>
      </div>
    </RefPanel>
  );
}

type PortfolioAssetRow = {
  symbol: string;
  name: string;
  color: string;
  qty: number;
  average: number | null;
  current: number | null;
  value: number;
  pnl: number | null;
  returnRate: number | null;
  allocation: number;
  change24h: number | null;
  sector: string;
  targetAllocation: number;
};

type PortfolioRebalanceRow = {
  symbol: string;
  allocation: number;
  target: number;
  suggestion: number;
};

type PortfolioTrendData = {
  values: number[];
  labels: string[];
  sourceLabel: string;
  hasHistory: boolean;
};

const portfolioAssets: PortfolioAssetRow[] = [
  { symbol: "BTC", name: "비트코인", color: "#ffab16", qty: 0.35255, average: 70150000, current: 89240000, value: 16076000, pnl: 6904706, returnRate: 0.2744, allocation: 0.565, change24h: 0.0235, sector: "레이어 1", targetAllocation: 0.5 },
  { symbol: "ETH", name: "이더리움", color: "#596dff", qty: 2.15, average: 3072000, current: 2660000, value: 5720000, pnl: -886800, returnRate: -0.134, allocation: 0.201, change24h: 0.0182, sector: "스마트 계약", targetAllocation: 0.25 },
  { symbol: "XRP", name: "리플", color: "#1bc5d8", qty: 10000, average: 692, current: 753, value: 2310000, pnl: 610000, returnRate: 0.359, allocation: 0.081, change24h: -0.0045, sector: "결제/송금", targetAllocation: 0.08 },
  { symbol: "SOL", name: "솔라나", color: "#7a5cff", qty: 12.5, average: 140000, current: 164000, value: 2050000, pnl: 300000, returnRate: 0.1714, allocation: 0.072, change24h: 0.0104, sector: "레이어 1", targetAllocation: 0.08 },
  { symbol: "ADA", name: "에이다", color: "#2e7cff", qty: 5000, average: 140, current: 182, value: 910000, pnl: 210000, returnRate: 0.3, allocation: 0.032, change24h: -0.0078, sector: "스마트 계약", targetAllocation: 0.03 },
  { symbol: "DOT", name: "폴카닷", color: "#e7429f", qty: 150, average: 8200, current: 7520, value: 1128000, pnl: -102600, returnRate: -0.0829, allocation: 0.04, change24h: -0.0126, sector: "레이어 1", targetAllocation: 0.03 },
  { symbol: "USDT", name: "테더", color: "#20b69d", qty: 3588, average: 1400, current: 1400, value: 3588000, pnl: 0, returnRate: 0, allocation: 0.126, change24h: 0, sector: "기타", targetAllocation: 0.03 }
];

const portfolioBaseTotal = 28_450_000;
const portfolioDustValueThresholdKrw = 100;

const assetMeta: Record<string, { name: string; color: string; sector: string; targetAllocation: number }> = {
  KRW: { name: "원화", color: "#38bdf8", sector: "현금", targetAllocation: 0.12 },
  BTC: { name: "비트코인", color: "#ffab16", sector: "레이어 1", targetAllocation: 0.5 },
  ETH: { name: "이더리움", color: "#596dff", sector: "스마트 계약", targetAllocation: 0.25 },
  XRP: { name: "리플", color: "#1bc5d8", sector: "결제/송금", targetAllocation: 0.08 },
  SOL: { name: "솔라나", color: "#7a5cff", sector: "레이어 1", targetAllocation: 0.08 },
  ADA: { name: "에이다", color: "#2e7cff", sector: "스마트 계약", targetAllocation: 0.03 },
  DOT: { name: "폴카닷", color: "#e7429f", sector: "레이어 1", targetAllocation: 0.03 },
  USDT: { name: "테더", color: "#20b69d", sector: "스테이블", targetAllocation: 0.03 }
};

const fallbackColors = ["#8b5cff", "#18e0c8", "#3d7cff", "#ffab16", "#e7429f", "#22c55e", "#8ea1b7"];

const coinIconUrls: Record<string, string> = {
  BTC: "https://cdn.jsdelivr.net/gh/spothq/cryptocurrency-icons@master/svg/color/btc.svg",
  ETH: "https://cdn.jsdelivr.net/gh/spothq/cryptocurrency-icons@master/svg/color/eth.svg",
  XRP: "https://cdn.jsdelivr.net/gh/spothq/cryptocurrency-icons@master/svg/color/xrp.svg",
  SOL: "https://cdn.jsdelivr.net/gh/spothq/cryptocurrency-icons@master/svg/color/sol.svg",
  ADA: "https://cdn.jsdelivr.net/gh/spothq/cryptocurrency-icons@master/svg/color/ada.svg",
  DOT: "https://cdn.jsdelivr.net/gh/spothq/cryptocurrency-icons@master/svg/color/dot.svg",
  USDT: "https://cdn.jsdelivr.net/gh/spothq/cryptocurrency-icons@master/svg/color/usdt.svg"
};

function portfolioMeta(symbol: string, index = 0) {
  return assetMeta[symbol] ?? {
    name: symbol,
    color: fallbackColors[index % fallbackColors.length],
    sector: "기타",
    targetAllocation: 0.03
  };
}

function boundedRatio(value: number) {
  return Math.min(Math.max(value, 0), 1);
}

function marketPriceFor(data: DashboardData, symbol: string) {
  return data.liveBalances?.prices?.[`KRW-${symbol}`] ?? null;
}

function withAllocations(rows: PortfolioAssetRow[], totalOverride?: number | null) {
  const total = totalOverride != null && totalOverride > 0 ? totalOverride : rows.reduce((sum, row) => sum + row.value, 0);
  return rows.map((row) => ({ ...row, allocation: total > 0 ? row.value / total : 0 }));
}

function isPortfolioDustRow(row: PortfolioAssetRow) {
  return row.symbol !== "KRW" && row.value > 0 && row.value < portfolioDustValueThresholdKrw;
}

function portfolioDisplayTotal(data: DashboardData, rows: PortfolioAssetRow[], fallbackTotal: number) {
  const computedTotal = rows.reduce((sum, row) => sum + row.value, 0);
  if (data.liveBalances?.balance_fetch_status === "SUCCESS") {
    return computedTotal;
  }
  return data.liveBalances?.estimated_total_equity_krw && data.liveBalances.estimated_total_equity_krw > 0
    ? data.liveBalances.estimated_total_equity_krw
    : computedTotal > 0 ? computedTotal : fallbackTotal;
}

function fallbackPortfolioRows(total: number) {
  const scale = total / portfolioBaseTotal;
  return portfolioAssets.map((asset) => ({ ...asset, value: asset.value * scale, pnl: asset.pnl == null ? null : asset.pnl * scale }));
}

function buildPortfolioRows(data: DashboardData, fallbackTotal: number) {
  const byCurrency = data.liveBalances?.balances?.by_currency;
  const hasLiveBalances = data.liveBalances?.balance_fetch_status === "SUCCESS" && byCurrency && Object.keys(byCurrency).length > 0;
  if (!hasLiveBalances || !byCurrency) {
    return withAllocations(fallbackPortfolioRows(fallbackTotal), fallbackTotal);
  }

  const rawRows = Object.entries(byCurrency)
    .map(([currency, entry], index): PortfolioAssetRow | null => {
      const symbol = currency.toUpperCase();
      const qty = balanceAmount(entry);
      if (qty <= 0) return null;
      const meta = portfolioMeta(symbol, index);
      const price = symbol === "KRW" ? null : marketPriceFor(data, symbol);
      const current = symbol === "KRW" ? 1 : price?.price ?? null;
      const average = symbol === "KRW" ? 1 : entry.avg_buy_price && entry.avg_buy_price > 0 ? entry.avg_buy_price : null;
      const value = symbol === "KRW" ? qty : current != null ? qty * current : average != null ? qty * average : 0;
      if (symbol !== "KRW" && value <= 0) return null;
      const cost = average != null ? qty * average : null;
      const pnl = symbol === "KRW" ? 0 : cost != null && current != null ? value - cost : null;
      return {
        symbol,
        name: meta.name,
        color: meta.color,
        qty,
        average,
        current,
        value,
        pnl,
        returnRate: cost != null && cost > 0 && pnl != null ? pnl / cost : null,
        allocation: 0,
        change24h: price?.signed_change_rate ?? null,
        sector: meta.sector,
        targetAllocation: meta.targetAllocation
      };
    })
    .filter((row): row is PortfolioAssetRow => row != null)
    .sort((a, b) => b.value - a.value);

  if (rawRows.length === 0) {
    return withAllocations(fallbackPortfolioRows(fallbackTotal), fallbackTotal);
  }
  const rows = rawRows.filter((row) => !isPortfolioDustRow(row));
  return withAllocations(rows);
}

function buildConicGradient(rows: PortfolioAssetRow[]) {
  let cursor = 0;
  const segments = rows.map((row) => {
    const start = cursor;
    cursor += Math.max(0, row.allocation) * 100;
    return `${row.color} ${start.toFixed(2)}% ${Math.min(cursor, 100).toFixed(2)}%`;
  });
  if (cursor < 100) segments.push(`#233348 ${cursor.toFixed(2)}% 100%`);
  return `conic-gradient(${segments.join(", ")})`;
}

function buildSectorRows(rows: PortfolioAssetRow[]) {
  const grouped = rows.reduce<Record<string, { value: number; color: string }>>((acc, row) => {
    acc[row.sector] = acc[row.sector] ?? { value: 0, color: row.color };
    acc[row.sector].value += row.value;
    return acc;
  }, {});
  const total = rows.reduce((sum, row) => sum + row.value, 0);
  return Object.entries(grouped)
    .map(([name, item]) => [name, total > 0 ? item.value / total : 0, item.color] as const)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5);
}

function latestSmartDecision(data: DashboardData) {
  return data.smartEngineStatus?.decision ?? data.analysisLatest ?? data.analysisHistory[0] ?? null;
}

function latestSmartIntent(data: DashboardData) {
  return data.smartEngineStatus?.latest_intent ?? latestSmartDecision(data)?.order_intents?.[0] ?? null;
}

function smartTargetPortfolioRatio(data: DashboardData, total: number) {
  const decision = latestSmartDecision(data);
  const intent = latestSmartIntent(data);
  if (total <= 0) return null;
  if (typeof intent?.target_value_krw === "number" && Number.isFinite(intent.target_value_krw)) {
    return boundedRatio(intent.target_value_krw / total);
  }
  if (
    typeof decision?.max_total_exposure_krw === "number"
    && decision.max_total_exposure_krw > 0
    && typeof decision.target_exposure_pct === "number"
    && Number.isFinite(decision.target_exposure_pct)
  ) {
    return boundedRatio((decision.max_total_exposure_krw * decision.target_exposure_pct / 100) / total);
  }
  return typeof decision?.target_exposure_pct === "number" && Number.isFinite(decision.target_exposure_pct)
    ? boundedRatio(decision.target_exposure_pct / 100)
    : null;
}

function buildRebalanceRows(rows: PortfolioAssetRow[], total: number, data: DashboardData): PortfolioRebalanceRow[] {
  const targetExposure = smartTargetPortfolioRatio(data, total);
  const lockedNonCore = rows
    .filter((row) => row.symbol !== "KRW" && row.symbol !== "BTC")
    .reduce((sum, row) => sum + row.allocation, 0);
  const coreBudget = Math.max(1 - lockedNonCore, 0);
  const targetBtc = targetExposure == null ? null : Math.min(targetExposure, coreBudget);
  const targetKrw = targetBtc == null ? null : Math.max(coreBudget - targetBtc, 0);
  return rows.slice(0, 6).map((row) => {
    let target = row.allocation;
    if (targetBtc != null && targetKrw != null) {
      if (row.symbol === "BTC") target = targetBtc;
      else if (row.symbol === "KRW") target = targetKrw;
    }
    const suggestion = (target - row.allocation) * total;
    return { symbol: row.symbol, allocation: row.allocation, target, suggestion };
  });
}

function buildContributionRows(rows: PortfolioAssetRow[]) {
  const withPnl = rows.filter((row) => row.pnl != null && row.symbol !== "KRW");
  const top = [...withPnl].filter((row) => (row.pnl ?? 0) > 0).sort((a, b) => (b.pnl ?? 0) - (a.pnl ?? 0)).slice(0, 3);
  const bottom = [...withPnl].filter((row) => (row.pnl ?? 0) < 0).sort((a, b) => (a.pnl ?? 0) - (b.pnl ?? 0)).slice(0, 3);
  return { top, bottom };
}

function portfolioTrend(data: DashboardData, total: number): PortfolioTrendData {
  const hasPaperCurve = Boolean(data.paper?.equity_curve?.length);
  const source = (hasPaperCurve ? data.paper?.equity_curve : data.forward?.equity_curve) ?? [];
  const curve = source
    .filter((point) => point.equity != null && Number.isFinite(point.equity))
    .slice(-30)
    .map((point) => ({ value: point.equity ?? 0, time: point.time ?? point.candle_time_utc }));
  if (curve.length >= 2) {
    const last = curve[curve.length - 1];
    const values = total > 0 && Math.abs(last.value - total) / Math.max(total, 1) > 0.005
      ? [...curve.map((point) => point.value), total]
      : curve.map((point) => point.value);
    const labelValues = curve.map((point) => point.time ?? "");
    return {
      values,
      labels: values.length > labelValues.length ? [...labelValues, data.updatedAt ?? ""] : labelValues,
      sourceLabel: hasPaperCurve ? "Paper equity curve" : "Forward equity curve",
      hasHistory: true
    };
  }
  return {
    values: [total, total],
    labels: [data.updatedAt ?? "", data.updatedAt ?? ""],
    sourceLabel: data.liveBalances?.balance_fetch_status === "SUCCESS" ? "실잔고 현재 스냅샷" : "실잔고 대기",
    hasHistory: false
  };
}

function formatAxisDate(value?: string) {
  if (!value) return "";
  const date = parseDate(value);
  if (!date) return "";
  return new Intl.DateTimeFormat("ko-KR", { month: "2-digit", day: "2-digit", timeZone: "Asia/Seoul" }).format(date).replace(". ", "-").replace(".", "");
}

function CoinLogo({ symbol, color }: { symbol: string; color: string }) {
  const normalized = symbol.toUpperCase();
  const fallback = normalized === "BTC" ? "₿" : normalized.slice(0, 1);
  return (
    <span
      className="ref-coin-logo"
      data-fallback={fallback}
      style={{ backgroundColor: color } as React.CSSProperties}
    >
      <img
        src={coinIconUrls[normalized]}
        alt=""
        aria-hidden="true"
        onError={(event) => {
          event.currentTarget.style.display = "none";
        }}
      />
    </span>
  );
}

function PortfolioTrendChart({ total, values, labels }: { total: number; values: number[]; labels: string[] }) {
  const width = 510;
  const height = 166;
  const min = Math.min(...values) * 0.97;
  const max = Math.max(...values) * 1.02;
  const spread = Math.max(max - min, 1);
  const points = values.map((value, index) => {
    const x = 48 + (index / (values.length - 1)) * (width - 72);
    const y = 18 + ((max - value) / spread) * (height - 50);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const pointList = points.split(" ");
  const lastPoint = pointList[pointList.length - 1]?.split(",").map(Number) ?? [width - 24, 40];
  const uniqueLabels = Array.from(new Set(labels.filter(Boolean)));
  const axisLabels = [0, 1, 2, 3, 4, 5, 6].map((item) => {
    if (labels.length < 2 || uniqueLabels.length <= 1) return item === 3 ? (formatAxisDate(uniqueLabels[0]) || "현재") : "";
    const index = Math.round((item / 6) * (labels.length - 1));
    return formatAxisDate(labels[index]);
  });
  return (
    <div className="ref-portfolio-trend-chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="포트폴리오 자산 추이">
        <defs>
          <linearGradient id="portfolioLineFill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="#8b5cff" stopOpacity="0.42" />
            <stop offset="100%" stopColor="#8b5cff" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0, 1, 2, 3, 4].map((item) => <line key={item} x1="48" x2={width - 24} y1={24 + item * 26} y2={24 + item * 26} />)}
        <polygon points={`48,${height - 28} ${points} ${width - 24},${height - 28}`} fill="url(#portfolioLineFill)" />
        <polyline points={points} fill="none" stroke="#8b5cff" strokeWidth="3" />
        <circle cx={lastPoint[0]} cy={lastPoint[1]} r="4" fill="#9f7bff" />
        <text x={Math.max(330, lastPoint[0] - 48)} y={lastPoint[1] - 10}>{formatKrw(total)}</text>
        {axisLabels.map((label, index) => (
          <text key={`${label}-${index}`} x={48 + index * 68} y={height - 8} className="axis">{label}</text>
        ))}
      </svg>
    </div>
  );
}

function PortfolioView({
  data,
  totalEquity,
  totalPnl,
  totalReturn,
  onSimulate
}: {
  data: DashboardData;
  totalEquity?: number | null;
  totalPnl?: number | null;
  totalReturn?: number | null;
  onSimulate: () => void;
}) {
  const fallbackTotal = totalEquity != null && totalEquity >= 10_000 ? totalEquity : portfolioBaseTotal;
  const rows = buildPortfolioRows(data, fallbackTotal);
  const total = portfolioDisplayTotal(data, rows, fallbackTotal);
  const cashValue = rows.find((row) => row.symbol === "KRW")?.value ?? 0;
  const livePnl = rows.reduce((sum, row) => sum + (row.pnl ?? 0), 0);
  const costBasis = rows.reduce((sum, row) => row.symbol === "KRW" ? sum : sum + ((row.average ?? 0) * row.qty), 0);
  const pnl = data.liveBalances?.balance_fetch_status === "SUCCESS" ? totalPnl ?? livePnl : totalPnl ?? livePnl;
  const returnRate = data.liveBalances?.balance_fetch_status === "SUCCESS" ? totalReturn ?? (costBasis > 0 ? livePnl / costBasis : null) : totalReturn ?? null;
  const cashRatio = total > 0 ? cashValue / total : 0;
  const mainRows = rows.slice(0, 5);
  const otherValue = rows.slice(5).reduce((sum, item) => sum + item.value, 0);
  const otherAllocation = total > 0 ? otherValue / total : 0;
  const legendRows = otherValue > 0
    ? [...mainRows, { ...portfolioMeta("기타"), symbol: "기타", value: otherValue, allocation: otherAllocation } as PortfolioAssetRow]
    : mainRows;
  const trend = portfolioTrend(data, total);
  const trendStart = trend.values[0] ?? total;
  const trendEnd = trend.values[trend.values.length - 1] ?? total;
  const trendPnl = trendEnd - trendStart;
  const trendReturn = trendStart > 0 ? trendPnl / trendStart : null;
  const smartIntent = latestSmartIntent(data);
  const smartTargetRatio = smartTargetPortfolioRatio(data, total);
  const rebalanceRows = buildRebalanceRows(rows, total, data);
  const estimatedFee = rebalanceRows.reduce((sum, row) => sum + Math.abs(row.suggestion), 0) * 0.0005;
  const largestSuggestion = rebalanceRows.reduce((max, row) => Math.max(max, Math.abs(row.suggestion)), 0);
  const intentDelta = Math.abs(smartIntent?.delta_value_krw ?? 0);
  const rebalanceNeeded = smartTargetRatio != null && (intentDelta >= 5_000 || largestSuggestion >= 5_000) && String(smartIntent?.side ?? "").toUpperCase() !== "NONE";
  const rebalanceSummary = smartTargetRatio == null
    ? "최근 Smart 목표 대기"
    : rebalanceNeeded
      ? "리밸런싱 후보 있음"
      : "현재 유지 권장";
  const smartIntentSide = String(smartIntent?.side ?? "").toUpperCase();
  const smartIntentLabel = smartIntentSide && smartIntentSide !== "NONE"
    ? `${statusLabel(smartIntent?.side)} 후보 ${formatSignedKrw(smartIntent?.delta_value_krw)} KRW`
    : "주문 후보 없음";
  const rebalanceDetail = smartTargetRatio == null
    ? "자동 판단 스냅샷이 쌓이면 목표 비중을 표시합니다."
    : `${smartIntentLabel} · 목표 BTC ${formatRatioPercent(smartTargetRatio)}`;
  const sectorRows = buildSectorRows(rows);
  const contributions = buildContributionRows(rows);
  const riskState = data.risk?.risk_state;
  const riskScore = riskState?.balance_mismatch_detected ? "검토" : data.liveBalances?.balance_fetch_status === "SUCCESS" ? "양호" : "대기";
  const mdd = data.paper?.balance?.mdd ?? data.forward?.balance?.mdd ?? null;

  return (
    <>
      <KpiCard className="ref-portfolio-kpi-total" icon={<Wallet size={28} />} label="총자산(KRW)" value={formatKrw(total)} sub={formatAssetSub(total)} />
      <KpiCard className="ref-portfolio-kpi-profit" icon={<LineChart size={28} />} label="총 손익 (KRW)" value={formatSignedKrw(pnl)} sub={formatPercent(returnRate)} tone="cyan" />
      <KpiCard className="ref-portfolio-kpi-return" icon={<PieChart size={28} />} label="수익률" value={formatPercent(returnRate)} sub={formatSignedKrw(pnl)} tone="green" />
      <KpiCard className="ref-portfolio-kpi-cash" icon={<ShieldCheck size={28} />} label="현금 비율" value={formatRatioPercent(cashRatio, 1)} sub={`${formatKrw(cashValue)} KRW`} tone="amber" />
      <KpiCard className="ref-portfolio-kpi-risk" icon={<Target size={28} />} label="리스크 상태" value={riskScore} sub={data.liveBalances?.balance_fetch_status ?? "-"} tone="cyan" />

      <RefPanel className="ref-portfolio-composition">
        <h3>포트폴리오 구성</h3>
        <div className="ref-portfolio-page-body">
          <div className="ref-portfolio-page-donut" style={{ background: buildConicGradient(legendRows) }}>
            <div><span>총 자산</span><b>{formatKrw(total)}</b><em>KRW</em></div>
          </div>
          <div className="ref-portfolio-page-legend">
            {legendRows.map((asset) => (
              <p key={asset.symbol}><i style={{ background: asset.color }} /><span>{asset.symbol}</span><b>{formatRatioPercent(asset.allocation)}</b><em>{formatKrw(asset.value)} KRW</em></p>
            ))}
          </div>
        </div>
        <small>* {data.liveBalances?.balance_fetch_status === "SUCCESS" ? "거래소 실잔고 기준" : "실잔고 대기: 레퍼런스 데이터 표시"}</small>
      </RefPanel>

      <RefPanel className="ref-portfolio-trend">
        <div className="ref-portfolio-panel-head"><h3>포트폴리오 자산 추이</h3><div><button>1일</button><button>7일</button><button className="is-active">30일</button><button>90일</button><button>전체</button></div></div>
        <span>자산 (KRW) · {trend.sourceLabel}</span>
        <PortfolioTrendChart total={total} values={trend.values} labels={trend.labels} />
        <div className="ref-portfolio-trend-summary">
          <p><span>{trend.hasHistory ? "기간 시작" : "스냅샷 시작"}</span><b>{formatKrw(trendStart)}</b></p>
          <p><span>{trend.hasHistory ? "기간 종료" : "현재 평가액"}</span><b>{formatKrw(trendEnd)}</b></p>
          <p><span>{trend.hasHistory ? "변동액" : "확정 변동"}</span><b className={trendPnl >= 0 ? "ref-positive" : "ref-negative"}>{formatSignedKrw(trendPnl)}</b></p>
          <p><span>{trend.hasHistory ? "수익률" : "스냅샷 수익률"}</span><b className={trendPnl >= 0 ? "ref-positive" : "ref-negative"}>{formatPercent(trendReturn)}</b></p>
        </div>
      </RefPanel>

      <RefPanel className="ref-portfolio-rebalance">
        <div className="ref-portfolio-panel-head"><h3>리밸런싱 제안</h3><span>업데이트: {formatKstTime(data.updatedAt)} ↻</span></div>
        <div className="ref-rebalance-tabs"><button className="is-active">권장 조정</button><button>사용자 설정</button></div>
        <div className="ref-rebalance-summary"><b>{rebalanceSummary}</b><span>{rebalanceDetail}</span></div>
        <table><thead><tr><th>자산</th><th>현재 비중</th><th>권장 비중</th><th>조정 제안</th></tr></thead><tbody>{rebalanceRows.map((row) => <tr key={row.symbol}><td>{row.symbol}</td><td>{formatRatioPercent(row.allocation)}</td><td>{formatRatioPercent(row.target)}</td><td className={row.suggestion >= 0 ? "ref-positive" : "ref-negative"}>{formatSignedKrw(row.suggestion)} KRW</td></tr>)}</tbody></table>
        <p><span>예상 거래 비용</span><b>{formatKrw(estimatedFee)} KRW</b></p>
        <button className="ref-rebalance-action" type="button" onClick={onSimulate}>권장 리밸런싱 실행 / 시뮬레이션</button>
      </RefPanel>

      <RefPanel className="ref-portfolio-holdings">
        <div className="ref-portfolio-panel-head"><h3>보유 자산</h3><div className="ref-portfolio-holdings-tools"><button>KRW 기준⌄</button><label>보유 자산만 보기 <i /></label></div></div>
        <table>
          <thead><tr><th>자산</th><th>보유 수량</th><th>평균 매수가 (KRW)</th><th>현재가 (KRW)</th><th>평가액 (KRW)</th><th>평가 손익 (KRW)</th><th>수익률</th><th>비중</th><th>24h 변동률</th></tr></thead>
          <tbody>{rows.map((asset) => <tr key={asset.symbol}><td><span className="ref-asset-cell"><CoinLogo symbol={asset.symbol} color={asset.color} /><b>{asset.symbol}<em>{asset.name}</em></b></span></td><td>{formatNumber(asset.qty, 8)}</td><td>{formatKrw(asset.average)}</td><td>{formatKrw(asset.current)}</td><td>{formatKrw(asset.value)}</td><td className={asset.pnl != null && asset.pnl >= 0 ? "ref-positive" : "ref-negative"}>{formatSignedKrw(asset.pnl)}</td><td className={asset.returnRate != null && asset.returnRate >= 0 ? "ref-positive" : "ref-negative"}>{formatPercent(asset.returnRate)}</td><td>{formatRatioPercent(asset.allocation)}</td><td className={asset.change24h != null && asset.change24h >= 0 ? "ref-positive" : "ref-negative"}>{formatPercent(asset.change24h)}</td></tr>)}</tbody>
          <tfoot><tr><td>총 합계</td><td /><td /><td /><td>{formatKrw(total)}</td><td className={pnl >= 0 ? "ref-positive" : "ref-negative"}>{formatSignedKrw(pnl)}</td><td className={returnRate != null && returnRate >= 0 ? "ref-positive" : "ref-negative"}>{formatPercent(returnRate)}</td><td>100%</td><td>-</td></tr></tfoot>
        </table>
      </RefPanel>

      <RefPanel className="ref-portfolio-risk">
        <h3>위험 노출 현황</h3>
        <div className="ref-risk-metrics"><p><span>일일 손익</span><b className={(riskState?.daily_total_pnl ?? 0) >= 0 ? "ref-positive" : "ref-negative"}>{formatSignedKrw(riskState?.daily_total_pnl)}</b></p><p><span>최대 낙폭</span><b className="ref-negative">{formatPercent(mdd)}</b></p><p><span>주문 수</span><b>{riskState?.daily_order_count ?? 0}</b></p><p><span>오픈 포지션</span><b>{riskState?.open_position_count ?? rows.filter((row) => row.symbol !== "KRW").length}</b></p></div>
      </RefPanel>

      <RefPanel className="ref-portfolio-sector">
        <div className="ref-portfolio-panel-head"><h3>섹터/테마 노출</h3><span>상세 보기 ›</span></div>
        <div>{sectorRows.map((row) => <p key={row[0]}><span>{row[0]}</span><i><b style={{ width: `${row[1] * 100}%`, background: row[2] }} /></i><em>{(row[1] * 100).toFixed(1)}%</em></p>)}</div>
      </RefPanel>

      <RefPanel className="ref-portfolio-contribution">
        <div className="ref-portfolio-panel-head"><h3>수익 기여도 TOP 3 / BOTTOM 3</h3><span>상세 보기 ›</span></div>
        <div className="ref-contribution-grid">
          <section>
            <b>TOP 3</b>
            <div>
              {contributions.top.length
                ? contributions.top.map((asset) => <p key={`top-${asset.symbol}`}><span>{asset.symbol}</span><em className="ref-positive">{formatSignedKrw(asset.pnl)} KRW</em></p>)
                : <p className="ref-contribution-empty"><span>-</span><em>수익 기여 자산 없음</em></p>}
            </div>
          </section>
          <section>
            <b>BOTTOM 3</b>
            <div>
              {contributions.bottom.length
                ? contributions.bottom.map((asset) => <p key={`bottom-${asset.symbol}`}><span>{asset.symbol}</span><em className="ref-negative">{formatSignedKrw(asset.pnl)} KRW</em></p>)
                : <p className="ref-contribution-empty"><span>-</span><em>손실 기여 자산 없음</em></p>}
            </div>
          </section>
        </div>
      </RefPanel>
    </>
  );
}

type TradeHistoryRow = {
  id: string;
  exchange: string;
  market: string;
  strategy: string;
  position: "롱" | "숏";
  orderType: string;
  rawStatus: string;
  createdAt: string | null;
  updatedAt: string | null;
  entryTime: string;
  exitTime: string;
  entryPrice: number | null;
  exitPrice: number | null;
  volume: number | null;
  pnl: number | null;
  returnRate: number | null;
  fee: number | null;
  status: string;
  orderId: string;
  holdMs: number | null;
};

const tradeAssetColors: Record<string, string> = {
  BTC: "#ff8a16",
  ETH: "#6277ff",
  SOL: "#111827",
  XRP: "#0f172a",
  DOGE: "#c9a62a",
  ADA: "#2e7cff"
};

type TradeSortKey = "market" | "strategy" | "position" | "entryTime" | "exitTime" | "entryPrice" | "exitPrice" | "volume" | "pnl" | "returnRate" | "fee" | "status";

const tradeSortLabels: Record<TradeSortKey, string> = {
  market: "종목",
  strategy: "전략명",
  position: "포지션",
  entryTime: "진입시간",
  exitTime: "청산시간",
  entryPrice: "진입가",
  exitPrice: "청산가",
  volume: "수량",
  pnl: "실현손익",
  returnRate: "수익률",
  fee: "수수료",
  status: "상태"
};

function normalizeTradeRow(order: LiveOrder, index: number): TradeHistoryRow {
  const market = marketDisplay(order.market);
  const side = String(order.side ?? order.order_type ?? "").toUpperCase();
  const entryPrice = order.price ?? null;
  const exitPrice = order.filled_amount_krw && order.executed_volume ? order.filled_amount_krw / order.executed_volume : entryPrice;
  const pnl = order.actual_pnl ?? order.expected_pnl ?? null;
  const cost = entryPrice && order.executed_volume ? entryPrice * order.executed_volume : order.amount_krw ?? null;
  const created = parseDate(order.created_at);
  const updated = parseDate(order.updated_at ?? order.created_at);
  const rawStatus = String(order.status ?? "UNKNOWN").toUpperCase();
  const filled = rawStatus === "FILLED" || rawStatus === "PARTIALLY_FILLED";
  return {
    id: String(order.request_id ?? order.id ?? `live-${index}`),
    exchange: order.exchange ?? "-",
    market,
    strategy: order.strategy_name ?? "자동 전략",
    position: side.includes("ASK") || side.includes("SELL") ? "숏" : "롱",
    orderType: statusLabel(order.order_type ?? order.side ?? "-"),
    rawStatus,
    createdAt: order.created_at ?? null,
    updatedAt: order.updated_at ?? order.created_at ?? null,
    entryTime: formatKstShort(order.created_at),
    exitTime: filled ? formatKstShort(order.updated_at ?? order.created_at) : "-",
    entryPrice,
    exitPrice: filled ? exitPrice : null,
    volume: order.executed_volume ?? order.volume ?? null,
    pnl,
    returnRate: pnl != null && cost && cost > 0 ? pnl / cost : null,
    fee: order.paid_fee ?? null,
    status: filled ? "정산완료" : statusLabel(order.status),
    orderId: String(order.request_id ?? order.id ?? "-"),
    holdMs: created && updated && updated >= created ? updated.getTime() - created.getTime() : null
  };
}

function tradeSymbol(market: string) {
  return market.split("/")[0] ?? "BTC";
}

function TradeCoinIcon({ market }: { market: string }) {
  const symbol = tradeSymbol(market);
  const color = tradeAssetColors[symbol] ?? "#8b5cff";
  return <span className="ref-trade-coin" style={{ backgroundColor: color }}><CoinLogo symbol={symbol} color={color} /></span>;
}

function exportTradeCsv(rows: TradeHistoryRow[]) {
  const header = ["종목", "전략명", "포지션", "진입시간", "청산시간", "진입가", "청산가", "수량", "실현손익", "수익률", "수수료", "상태", "주문ID"];
  const lines = [header, ...rows.map((row) => [
    row.market,
    row.strategy,
    row.position,
    row.entryTime,
    row.exitTime,
    row.entryPrice ?? "",
    row.exitPrice ?? "",
    row.volume ?? "",
    row.pnl ?? "",
    row.returnRate ?? "",
    row.fee ?? "",
    row.status,
    row.orderId
  ])].map((line) => line.map((cell) => `"${String(cell).replace(/"/g, '""')}"`).join(","));
  const blob = new Blob([`\uFEFF${lines.join("\n")}`], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `trade-history-${new Date().toISOString().slice(0, 10)}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}

function TradeHistoryView({ data, refresh }: { data: DashboardData; refresh: () => Promise<void> }) {
  const [search, setSearch] = React.useState("");
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [exchangeFilter, setExchangeFilter] = React.useState("ALL");
  const [strategyFilter, setStrategyFilter] = React.useState("ALL");
  const [marketFilter, setMarketFilter] = React.useState("ALL");
  const [orderTypeFilter, setOrderTypeFilter] = React.useState("ALL");
  const [statusFilter, setStatusFilter] = React.useState("ALL");
  const [startDate, setStartDate] = React.useState("");
  const [endDate, setEndDate] = React.useState("");
  const [sortKey, setSortKey] = React.useState<TradeSortKey>("entryTime");
  const [sortDirection, setSortDirection] = React.useState<"asc" | "desc">("desc");
  const [page, setPage] = React.useState(1);
  const [pageSize, setPageSize] = React.useState(10);
  const liveRows = data.liveOrders.map(normalizeTradeRow);
  const exchangeOptions = Array.from(new Set(liveRows.map((row) => row.exchange).filter(Boolean)));
  const strategyOptions = Array.from(new Set(liveRows.map((row) => row.strategy).filter(Boolean)));
  const marketOptions = Array.from(new Set(liveRows.map((row) => row.market).filter(Boolean)));
  const orderTypeOptions = Array.from(new Set(liveRows.map((row) => row.orderType).filter(Boolean)));
  const statusOptions = Array.from(new Set(liveRows.map((row) => row.status).filter(Boolean)));
  const startMs = startDate ? new Date(`${startDate}T00:00:00+09:00`).getTime() : null;
  const endMs = endDate ? new Date(`${endDate}T23:59:59+09:00`).getTime() : null;
  const filteredRows = liveRows.filter((row) => {
    const query = search.trim().toLowerCase();
    const created = parseDate(row.createdAt);
    if (query && ![row.market, row.strategy, row.orderId, row.status].some((value) => value.toLowerCase().includes(query))) return false;
    if (exchangeFilter !== "ALL" && row.exchange !== exchangeFilter) return false;
    if (strategyFilter !== "ALL" && row.strategy !== strategyFilter) return false;
    if (marketFilter !== "ALL" && row.market !== marketFilter) return false;
    if (orderTypeFilter !== "ALL" && row.orderType !== orderTypeFilter) return false;
    if (statusFilter !== "ALL" && row.status !== statusFilter) return false;
    if (startMs != null && (!created || created.getTime() < startMs)) return false;
    if (endMs != null && (!created || created.getTime() > endMs)) return false;
    return true;
  });
  const sortedRows = [...filteredRows].sort((a, b) => {
    const value = (row: TradeHistoryRow) => {
      if (sortKey === "entryTime") return parseDate(row.createdAt)?.getTime() ?? 0;
      if (sortKey === "exitTime") return parseDate(row.updatedAt)?.getTime() ?? 0;
      if (sortKey === "entryPrice") return row.entryPrice ?? 0;
      if (sortKey === "exitPrice") return row.exitPrice ?? 0;
      if (sortKey === "volume") return row.volume ?? 0;
      if (sortKey === "pnl") return row.pnl ?? 0;
      if (sortKey === "returnRate") return row.returnRate ?? 0;
      if (sortKey === "fee") return row.fee ?? 0;
      return String(row[sortKey] ?? "");
    };
    const av = value(a);
    const bv = value(b);
    const result = typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv), "ko-KR");
    return sortDirection === "asc" ? result : -result;
  });
  const totalPages = Math.max(1, Math.ceil(sortedRows.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const pagedRows = sortedRows.slice((safePage - 1) * pageSize, safePage * pageSize);
  const selected = sortedRows.find((row) => row.id === selectedId) ?? pagedRows[0] ?? sortedRows[0] ?? null;
  const todayKey = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul" }).format(new Date());
  const todayCount = liveRows.filter((row) => {
    const created = parseDate(row.createdAt);
    return created && new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul" }).format(created) === todayKey;
  }).length;
  const totalTrades = filteredRows.length;
  const settledRows = filteredRows.filter((row) => row.rawStatus === "FILLED" || row.pnl != null);
  const profitable = settledRows.filter((row) => (row.pnl ?? 0) > 0).length;
  const winRate = settledRows.length ? profitable / settledRows.length : null;
  const realizedPnl = filteredRows.reduce((sum, row) => sum + (row.pnl ?? 0), 0);
  const totalFee = filteredRows.reduce((sum, row) => sum + (row.fee ?? 0), 0);
  const holdDurations = filteredRows.map((row) => row.holdMs).filter((value): value is number => value != null && value >= 0);
  const avgHoldMs = holdDurations.length ? holdDurations.reduce((sum, value) => sum + value, 0) / holdDurations.length : null;
  const chartCandles = data.candles.slice(-46);
  const selectedMarket = selected?.market ?? marketDisplay(MARKET);
  const selectedEntry = selected?.entryPrice ?? latestCandle(data.candles)?.trade_price ?? 0;
  const selectedExit = selected?.exitPrice ?? selectedEntry;
  const chartMin = chartCandles.length ? Math.min(...chartCandles.map((candle) => candle.low_price), selectedEntry) : selectedEntry * 0.98;
  const chartMax = chartCandles.length ? Math.max(...chartCandles.map((candle) => candle.high_price), selectedExit) : selectedEntry * 1.02;
  const chartSpread = Math.max(chartMax - chartMin, 1);
  const handleSort = (key: TradeSortKey) => {
    setSortKey((prev) => {
      if (prev === key) {
        setSortDirection((direction) => direction === "asc" ? "desc" : "asc");
        return prev;
      }
      setSortDirection(key === "entryTime" || key === "exitTime" ? "desc" : "asc");
      return key;
    });
  };
  const resetPage = () => setPage(1);
  React.useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);

  return (
    <>
      <KpiCard className="ref-trade-kpi-total" icon={<ClipboardList size={28} />} label="전체 거래 수" value={`${formatNumber(totalTrades, 0)} 건`} sub={`오늘 ${todayCount} 건`} />
      <KpiCard className="ref-trade-kpi-win" icon={<Target size={28} />} label="승률" value={formatRatioPercent(winRate, 1)} sub={`${profitable} 승 / ${Math.max(settledRows.length - profitable, 0)} 패`} tone="cyan" />
      <KpiCard className="ref-trade-kpi-profit" icon={<TrendingUp size={28} />} label="누적 실현 손익 (KRW)" value={formatSignedKrw(realizedPnl)} sub={formatPercent(realizedPnl / Math.max(data.liveBalances?.estimated_total_equity_krw ?? portfolioBaseTotal, 1))} tone="green" />
      <KpiCard className="ref-trade-kpi-time" icon={<History size={28} />} label="평균 보유 시간" value={formatRuntimeDuration(avgHoldMs)} sub="필터 기준" tone="amber" />
      <KpiCard className="ref-trade-kpi-fee" icon={<DollarSign size={28} />} label="총 수수료 (KRW)" value={`${formatSignedKrw(totalFee)} KRW`} sub="수수료 제외전" tone="green" />

      <RefPanel className="ref-trade-filter-panel">
        <div className="ref-trade-filter-grid">
          <label><span>계정/거래소</span><select value={exchangeFilter} onChange={(event) => { setExchangeFilter(event.target.value); resetPage(); }}><option value="ALL">전체</option>{exchangeOptions.map((item) => <option key={item} value={item}>{item === "bithumb" ? "빗썸 (Bithumb)" : item === "upbit" ? "업비트 (Upbit)" : item}</option>)}</select></label>
          <label><span>전략</span><select value={strategyFilter} onChange={(event) => { setStrategyFilter(event.target.value); resetPage(); }}><option value="ALL">전체</option>{strategyOptions.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
          <label><span>종목</span><select value={marketFilter} onChange={(event) => { setMarketFilter(event.target.value); resetPage(); }}><option value="ALL">전체</option>{marketOptions.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
          <label><span>기간</span><div className="ref-trade-period"><input type="date" value={startDate} onChange={(event) => { setStartDate(event.target.value); resetPage(); }} /><em>~</em><input type="date" value={endDate} onChange={(event) => { setEndDate(event.target.value); resetPage(); }} /></div></label>
          <label><span>주문 유형</span><select value={orderTypeFilter} onChange={(event) => { setOrderTypeFilter(event.target.value); resetPage(); }}><option value="ALL">전체</option>{orderTypeOptions.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
          <label><span>상태</span><select value={statusFilter} onChange={(event) => { setStatusFilter(event.target.value); resetPage(); }}><option value="ALL">전체</option>{statusOptions.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
        </div>
        <div className="ref-trade-filter-actions">
          <div className="ref-trade-search"><Search size={17} /><input value={search} onChange={(event) => { setSearch(event.target.value); resetPage(); }} placeholder="종목, 전략명, 주문ID 검색" /></div>
          <button type="button" className="ref-trade-export" onClick={() => exportTradeCsv(sortedRows)} disabled={sortedRows.length === 0}><Download size={16} />CSV 내보내기</button>
          <button type="button" className="ref-trade-refresh" onClick={() => void refresh()}><RotateCw size={16} /></button>
        </div>
      </RefPanel>

      <RefPanel className="ref-trade-table-panel">
        <table>
          <thead><tr>{(["market", "strategy", "position", "entryTime", "exitTime", "entryPrice", "exitPrice", "volume", "pnl", "returnRate", "fee", "status"] as TradeSortKey[]).map((key) => <th key={key}><button type="button" onClick={() => handleSort(key)}>{tradeSortLabels[key]} {sortKey === key ? sortDirection === "asc" ? "↑" : "↓" : "↕"}</button></th>)}</tr></thead>
          <tbody>
            {pagedRows.length === 0 && <tr><td colSpan={12} className="ref-trade-empty">실제 거래내역 데이터가 없습니다.</td></tr>}
            {pagedRows.map((row) => (
              <tr key={row.id} className={selected && row.id === selected.id ? "is-selected" : ""} onClick={() => setSelectedId(row.id)}>
                <td><span className="ref-trade-market"><TradeCoinIcon market={row.market} />{row.market}</span></td>
                <td>{row.strategy}</td>
                <td className={row.position === "롱" ? "ref-positive" : "ref-negative"}>{row.position}</td>
                <td>{row.entryTime}</td>
                <td>{row.exitTime}</td>
                <td>{formatKrw(row.entryPrice)}</td>
                <td>{formatKrw(row.exitPrice)}</td>
                <td>{formatNumber(row.volume, 4)}</td>
                <td className={(row.pnl ?? 0) >= 0 ? "ref-positive" : "ref-negative"}>{formatSignedKrw(row.pnl)}</td>
                <td className={(row.returnRate ?? 0) >= 0 ? "ref-positive" : "ref-negative"}>{formatPercent(row.returnRate)}</td>
                <td>{formatKrw(row.fee)}</td>
                <td><span className={refStatusChipClass(row.rawStatus)}>{row.status}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="ref-trade-pagination"><span>전체 {formatNumber(filteredRows.length, 0)} 건</span><button type="button" onClick={() => setPage(1)} disabled={safePage === 1}>≪</button><button type="button" onClick={() => setPage((value) => Math.max(1, value - 1))} disabled={safePage === 1}>‹</button>{Array.from({ length: Math.min(5, totalPages) }, (_, index) => Math.min(Math.max(1, safePage - 2), Math.max(1, totalPages - 4)) + index).filter((value, index, list) => value <= totalPages && list.indexOf(value) === index).map((item) => <button key={item} type="button" className={item === safePage ? "is-active" : ""} onClick={() => setPage(item)}>{item}</button>)}{totalPages > 5 && <em>...</em>}{totalPages > 5 && <button type="button" onClick={() => setPage(totalPages)}>{totalPages}</button>}<button type="button" onClick={() => setPage((value) => Math.min(totalPages, value + 1))} disabled={safePage === totalPages}>›</button><button type="button" onClick={() => setPage(totalPages)} disabled={safePage === totalPages}>≫</button><select value={pageSize} onChange={(event) => { setPageSize(Number(event.target.value)); setPage(1); }}><option value={10}>10개씩 보기</option><option value={20}>20개씩 보기</option><option value={50}>50개씩 보기</option></select></div>
      </RefPanel>

      <RefPanel className="ref-trade-detail-card">
        <div className="ref-trade-detail-head"><span><TradeCoinIcon market={selectedMarket} />{selected?.market ?? "-"}</span><b className={refStatusChipClass(selected?.rawStatus)}>{selected?.status ?? "데이터 없음"}</b></div>
        <div className="ref-trade-detail-grid">
          <p><span>전략</span><b>{selected?.strategy ?? "-"}</b></p><p><span>수량</span><b>{formatNumber(selected?.volume, 6)} {tradeSymbol(selectedMarket)}</b></p>
          <p><span>주문 유형</span><b>{selected?.orderType ?? "-"}</b></p><p><span>수수료</span><b>{formatKrw(selected?.fee)} KRW</b></p>
          <p><span>진입가</span><b>{formatKrw(selected?.entryPrice)} KRW</b></p><p><span>보유시간</span><b>{formatRuntimeDuration(selected?.holdMs)}</b></p>
          <p><span>청산가</span><b>{formatKrw(selected?.exitPrice)} KRW</b></p><p><span>진입시간</span><b>{selected?.entryTime ?? "-"}</b></p>
          <p><span>수익률</span><b className={(selected?.returnRate ?? 0) >= 0 ? "ref-positive" : "ref-negative"}>{formatPercent(selected?.returnRate)}</b></p><p><span>청산시간</span><b>{selected?.exitTime ?? "-"}</b></p>
          <p><span>실현손익</span><b className={(selected?.pnl ?? 0) >= 0 ? "ref-positive" : "ref-negative"}>{formatSignedKrw(selected?.pnl)} KRW</b></p><p><span>주문ID</span><b>{selected?.orderId ?? "-"}</b></p>
        </div>
      </RefPanel>

      <RefPanel className="ref-trade-timeline-card">
        <h3>거래 타임라인</h3>
        <div><p className="ok"><b>주문 생성</b><span>{selected?.createdAt ? formatKstShort(selected.createdAt) : "거래 데이터 없음"}</span></p><p className="ok"><b>거래소 제출</b><span>{selected?.exchange ?? "-"}</span></p><p className={selected?.rawStatus === "FAILED" || selected?.rawStatus === "BLOCKED" ? "danger" : "ok"}><b>{selected?.status ?? "상태 없음"}</b><span>{selected?.updatedAt ? formatKstShort(selected.updatedAt) : "-"}</span></p><p className={selected?.pnl != null && selected.pnl < 0 ? "danger" : "ok"}><b>손익 반영</b><span>{formatSignedKrw(selected?.pnl)} KRW</span></p></div>
      </RefPanel>

      <RefPanel className="ref-trade-log-card">
        <h3>체결 로그</h3>
        <table><thead><tr><th>시간</th><th>구분</th><th>가격</th><th>수량</th></tr></thead><tbody><tr><td>{selected?.entryTime ?? "-"}</td><td>{selected?.position === "숏" ? "매도" : "매수"}</td><td>{formatKrw(selected?.entryPrice)}</td><td>{formatNumber(selected?.volume, 4)}</td></tr><tr><td>{selected?.exitTime ?? "-"}</td><td className={selected?.position === "숏" ? "ref-positive" : "ref-negative"}>{selected?.rawStatus ?? "-"}</td><td>{formatKrw(selected?.exitPrice)}</td><td>{formatNumber(selected?.volume, 4)}</td></tr></tbody></table>
      </RefPanel>

      <RefPanel className="ref-trade-signal-card">
        <h3>신호 근거</h3>
        <div><p>✓ 전략: {selected?.strategy ?? "-"}</p><p>✓ 종목: {selected?.market ?? "-"}</p><p>✓ 주문 상태: <b>{selected?.status ?? "-"}</b></p><p>✓ 거래소: {selected?.exchange ?? "-"}</p></div>
        <div><p>› 기준가: {formatKrw(selected?.entryPrice)} KRW</p><p>› 체결가: {formatKrw(selected?.exitPrice)} KRW</p><p>› 수수료: {formatKrw(selected?.fee)} KRW</p><p>› 수익률: <b>{formatPercent(selected?.returnRate)}</b></p></div>
      </RefPanel>

      <RefPanel className="ref-trade-chart-card">
        <h3>가격 차트 (5분)</h3>
        <svg viewBox="0 0 360 164" role="img" aria-label="거래 가격 차트">
          {[0, 1, 2, 3, 4].map((line) => <line key={line} x1="18" x2="338" y1={24 + line * 28} y2={24 + line * 28} />)}
          {chartCandles.map((candle, index) => {
            const x = 22 + index * 7;
            const open = 142 - ((candle.opening_price - chartMin) / chartSpread) * 118;
            const close = 142 - ((candle.trade_price - chartMin) / chartSpread) * 118;
            const high = 142 - ((candle.high_price - chartMin) / chartSpread) * 118;
            const low = 142 - ((candle.low_price - chartMin) / chartSpread) * 118;
            const up = close <= open;
            return <g key={`${candle.candle_time_utc}-${index}`}><line x1={x} x2={x} y1={high} y2={low} className={up ? "up" : "down"} /><rect x={x - 2} y={Math.min(open, close)} width="4" height={Math.max(Math.abs(close - open), 2)} className={up ? "up" : "down"} /></g>;
          })}
          <text x="35" y="137">매수</text><text x="156" y="75">익절1</text><text x="284" y="25">최종 청산</text>
        </svg>
      </RefPanel>
    </>
  );
}

function RecentTradesPanel({ data }: { data: DashboardData }) {
  const rows = data.liveOrders.filter((order) => !isOpenOrderWaitOrder(order)).slice(0, 5);
  return (
    <RefPanel className="ref-trades-panel">
      <h3>최근 거래 내역 <span>⊞</span></h3>
      <table>
        <thead>
          <tr><th>종목</th><th>진입가</th><th>청산가</th><th>수량</th><th>수익률</th><th>수익 (KRW)</th><th>상태</th><th>시간</th></tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr><td colSpan={8}>거래 데이터 없음</td></tr>
          )}
          {rows.map((order) => {
            const pnl = order.actual_pnl ?? order.expected_pnl ?? null;
            const amount = order.filled_amount_krw ?? order.amount_krw ?? null;
            const rate = amount && pnl != null && amount > 0 ? pnl / amount : null;
            const status = order.status ?? "-";
            return (
              <tr key={order.request_id ?? order.id}>
                <td>{marketDisplay(order.market)}</td>
                <td>{formatKrw(order.price)}</td>
                <td>{status === "FILLED" ? formatKrw(order.price) : "-"}</td>
                <td>{formatNumber(order.executed_volume ?? order.volume, 8)}</td>
                <td className={rate != null && rate >= 0 ? "ref-positive" : rate != null ? "ref-negative" : ""}>{formatPercent(rate)}</td>
                <td className={pnl != null && pnl >= 0 ? "ref-positive" : pnl != null ? "ref-negative" : ""}>{formatSignedKrw(pnl)}</td>
                <td><span className={refStatusChipClass(status)}>{statusLabel(status)}</span></td>
                <td>{formatKstShort(order.created_at)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </RefPanel>
  );
}

function LogPanel({ data }: { data: DashboardData }) {
  const riskLogs = (data.risk?.risk_logs ?? []).slice(0, 7).map((log) => ({
    key: `risk-${log.id ?? log.created_at ?? ""}-${log.block_code ?? log.risk_level ?? ""}`,
    createdAt: log.created_at,
    type: riskLogDotType(log),
    time: formatKstTime(log.created_at),
    text: riskLogMessage(log),
    rawText: log.block_code ?? log.block_reason
  }));
  const orderLogs = data.liveOrders.slice(0, 7).map((order) => ({
    key: `order-${order.request_id ?? order.id ?? order.created_at ?? ""}`,
    createdAt: order.created_at,
    type: isOpenOrderWaitOrder(order) ? "info" : order.status === "BLOCKED" || order.status === "FAILED" ? "danger" : "info",
    time: formatKstTime(order.created_at),
    text: isOpenOrderWaitOrder(order)
      ? `${marketDisplay(order.market)} 기존 매수 주문 체결 대기`
      : `${marketDisplay(order.market)} ${statusLabel(order.side)} ${statusLabel(order.status)}`,
    rawText: order.risk_result ?? order.status
  }));
  const recoveryLogs = data.recoveryEvents.slice(0, 7).map((event, index) => ({
    key: `recovery-${event.created_at ?? index}-${event.event_type ?? ""}`,
    createdAt: event.created_at,
    type: event.severity === "ERROR" ? "danger" : "info",
    time: formatKstTime(event.created_at),
    text: readableSystemLogText(event.event_type, event.message ?? "복구 로그"),
    rawText: event.event_type
  }));
  const rows = [...riskLogs, ...orderLogs, ...recoveryLogs]
    .sort((a, b) => (parseDate(b.createdAt)?.getTime() ?? 0) - (parseDate(a.createdAt)?.getTime() ?? 0))
    .slice(0, 7);

  return (
    <RefPanel className="ref-log-panel">
      <div className="ref-title-row">
        <h3>시스템 로그</h3>
        <button>전체⌄</button>
      </div>
      <div className="ref-log-list">
        {rows.length === 0 && <p><span>-</span><i className="info" />로그 데이터 없음</p>}
        {rows.map((row, index) => (
          <p key={row.key} style={{ ["--log-index" as string]: index }}><span>{row.time}</span><i className={row.type} /><b title={row.rawText ? `${row.text} (${row.rawText})` : row.text}>{row.text}</b></p>
        ))}
      </div>
    </RefPanel>
  );
}

function AlertsView({ data }: { data: DashboardData }) {
  const logs = data.risk?.risk_logs ?? [];
  const [selectedId, setSelectedId] = React.useState<number | null>(null);
  const selected = logs.find((log) => log.id === selectedId)
    ?? data.risk?.latest_policy_block
    ?? logs.find((log) => !log.allowed)
    ?? logs[0]
    ?? null;

  React.useEffect(() => {
    if (selectedId != null && logs.some((log) => log.id === selectedId)) return;
    setSelectedId(data.risk?.latest_policy_block?.id ?? logs.find((log) => !log.allowed)?.id ?? logs[0]?.id ?? null);
  }, [data.risk?.latest_policy_block?.id, logs, selectedId]);

  return (
    <>
      <RefPanel className="ref-alerts-list-panel">
        <div className="ref-title-row">
          <h3>알림로그</h3>
          <span>{logs.length}건</span>
        </div>
        <div className="ref-alerts-table-wrap">
          <table className="ref-alerts-table">
            <thead><tr><th>시간</th><th>심각도</th><th>유형</th><th>메시지</th><th>상태</th></tr></thead>
            <tbody>
              {logs.length === 0 && <tr><td colSpan={5}>최근 리스크 로그 없음</td></tr>}
              {logs.slice(0, 50).map((log) => {
                const typeLabel = riskLogTypeLabel(log);
                const message = riskLogMessage(log);
                const readLabel = log.read_status === "READ" || log.allowed ? "읽음" : log.read_status === "IGNORED" ? "무시됨" : "미해결";
                return (
                  <tr key={log.id ?? log.created_at} className={`${selected?.id === log.id ? "is-selected" : ""} ${!log.allowed ? "is-alert" : ""}`} onClick={() => setSelectedId(log.id ?? null)}>
                    <td>{formatKstShort(log.created_at)}</td>
                    <td><RefStatusBadge value={riskSeverityLabel(log.risk_level ?? (log.allowed ? "OK" : "BLOCKED"))} tone={riskLogTone(log)} /></td>
                    <td title={typeLabel}>{typeLabel}</td>
                    <td title={message}>{message}</td>
                    <td>{readLabel}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </RefPanel>
      <RefPanel className="ref-alerts-detail-panel">
        <div className="ref-title-row">
          <h3>상세 근거</h3>
          <span>{selected?.policy_block_detail ? "Policy" : "Risk"}</span>
        </div>
        {selected ? (
          <div className="ref-alert-detail-body">
            <strong>{selected.policy_block_detail?.summary ?? riskLogMessage(selected)}</strong>
            <p><span>발생 시간</span><b>{formatKstShort(selected.created_at)}</b></p>
            <p><span>심각도</span><b>{riskSeverityLabel(selected.risk_level ?? (selected.allowed ? "OK" : "BLOCKED"))}</b></p>
            <p><span>유형</span><b>{riskLogTypeLabel(selected)}</b></p>
            <p><span>차단 코드</span><b title={selected.block_code ?? "-"}>{alertCodeLabel(selected.block_code)}</b></p>
            <p><span>상태</span><b>{selected.allowed ? "허용" : "차단"}</b></p>
            <PolicyBlockDetailGrid detail={selected.policy_block_detail} />
            {!selected.policy_block_detail && <em>{selected.allowed ? "차단이 아닌 일반 리스크 점검 통과 로그입니다." : "정책 차단이 아닌 일반 리스크 차단 로그입니다."}</em>}
          </div>
        ) : (
          <div className="ref-alert-detail-body empty">선택할 알림이 없습니다.</div>
        )}
      </RefPanel>
    </>
  );
}

function RefStatusBadge({ value, tone = "green" }: { value: string; tone?: "green" | "amber" | "red" | "cyan" | "neutral" }) {
  return <span className={`ref-status-badge ${tone}`}>{value}</span>;
}

function PolicyBlockDetailGrid({ detail, compact = false }: { detail?: PolicyBlockDetail | null; compact?: boolean }) {
  if (!detail) return null;
  const rows = [
    ["요청금액", formatKrw(detail.requested_order_krw)],
    ["최대투입", formatKrw(detail.max_total_exposure_krw)],
    ["현재포지션", formatKrw(detail.current_bot_position_value_krw)],
    ["예상포지션", formatKrw(detail.projected_bot_position_value_krw)],
    ["남은한도", formatKrw(detail.remaining_exposure_krw)],
    ["초과금액", formatKrw(detail.exceeded_by_krw)],
    ["KRW잔고", detail.available_krw_balance == null ? "-" : formatKrw(detail.available_krw_balance)],
    ["잔고부족", formatKrw(detail.krw_shortfall_krw)],
    ["일손실", formatKrw(detail.daily_loss_krw)],
    ["손실한도", formatKrw(detail.daily_loss_limit_krw)],
    ["손실사용률", formatRatioPercent(detail.daily_loss_usage_pct, 1)]
  ];
  const visibleRows = compact
    ? rows.filter(([, value]) => value !== "-" && value !== "0").slice(0, 4)
    : rows;
  return (
    <div className={`ref-policy-detail-grid ${compact ? "compact" : ""}`}>
      {visibleRows.map(([label, value]) => (
        <p key={label}><span>{label}</span><b>{value}</b></p>
      ))}
      {!compact && detail.next_action && <em>{detail.next_action}</em>}
    </div>
  );
}

function AutoMiniChart({ candles }: { candles: Candle[] }) {
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const visible = candles.slice(-42);

  React.useEffect(() => {
    if (!containerRef.current || visible.length === 0) return;

    const container = containerRef.current;
    const chart = createChart(container, {
      width: container.clientWidth,
      height: container.clientHeight,
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#8f9db1"
      },
      grid: {
        vertLines: { color: "rgba(58, 76, 99, 0.24)" },
        horzLines: { color: "rgba(58, 76, 99, 0.24)" }
      },
      rightPriceScale: {
        borderVisible: false,
        scaleMargins: { top: 0.08, bottom: 0.24 }
      },
      timeScale: {
        borderVisible: false,
        timeVisible: true,
        secondsVisible: false
      },
      crosshair: {
        vertLine: { visible: false, labelVisible: false },
        horzLine: { visible: false, labelVisible: false }
      },
      localization: {
        priceFormatter: (price: number) => formatKrw(price)
      }
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#12c979",
      downColor: "#ff3e4e",
      borderUpColor: "#12c979",
      borderDownColor: "#ff3e4e",
      wickUpColor: "#12c979",
      wickDownColor: "#ff3e4e"
    });
    candleSeries.setData(visible.map((candle) => ({
      time: toChartTime(candle.candle_time_utc),
      open: candle.opening_price,
      high: candle.high_price,
      low: candle.low_price,
      close: candle.trade_price
    })));

    const ma = chart.addSeries(LineSeries, {
      color: "#2c71d0",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false
    });
    ma.setData(movingAverage(visible, Math.min(20, Math.max(5, Math.floor(visible.length / 2)))));

    const volumeSeries = chart.addSeries(HistogramSeries, {
      color: "#1f9d68",
      priceFormat: { type: "volume" },
      priceScaleId: "",
      priceLineVisible: false,
      lastValueVisible: false
    });
    volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });
    volumeSeries.setData(visible.map((candle) => ({
      time: toChartTime(candle.candle_time_utc),
      value: candle.candle_acc_trade_volume,
      color: candle.trade_price >= candle.opening_price ? "rgba(18, 201, 121, 0.46)" : "rgba(184, 67, 47, 0.46)"
    })));

    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [visible]);

  return (
    <div className="ref-auto-mini-chart">
      {visible.length ? <div ref={containerRef} className="ref-auto-chart-canvas" /> : <div className="ref-empty-state">차트 데이터 없음</div>}
    </div>
  );
}

function AutoStatusPanel({
  data,
  onToggle,
  isToggling,
  toggleError
}: {
  data: DashboardData;
  onToggle: () => void;
  isToggling: boolean;
  toggleError: string | null;
}) {
  const [now, setNow] = React.useState(() => Date.now());
  const autoRunning = isRuntimeRunning(data);
  React.useEffect(() => {
    const intervalId = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(intervalId);
  }, []);
  const runningStrategies = [
    data.liveStrategy?.session?.status,
    data.autoPilot?.session?.status,
    data.paper?.status,
    data.forward?.status
  ].filter(isRunning).length;
  const activeAssets = new Set([
    ...data.candidates.map((candidate) => candidate.market).filter(Boolean),
    ...data.liveOrders.map((order) => order.market).filter(Boolean)
  ]).size;
  const dailyPnl = data.risk?.risk_state?.daily_total_pnl ?? data.paper?.balance?.total_pnl ?? null;
  const totalPnl = data.paper?.balance?.total_pnl ?? data.forward?.balance?.total_pnl ?? data.risk?.risk_state?.daily_total_pnl ?? null;
  const runtimeText = autoRunning ? formatRuntimeDuration(autoRuntimeMs(data, now)) : "-";

  return (
    <RefPanel className="ref-auto-status-panel">
      <span className="ref-auto-status-label">자동매매 상태</span>
      <div className={`ref-auto-orb ${autoRunning ? "is-running" : "is-paused"}`}>
        <Bot size={46} />
      </div>
      <div className="ref-auto-status-copy">
        <h2>{autoRunning ? "자동매매 실행 중" : "자동매매 대기 중"}</h2>
        <p>봇이 {runningStrategies || 0}개 전략으로 {activeAssets || 0}개 자산을 모니터링합니다.</p>
        <div className="ref-auto-status-meta">
          <RefStatusBadge value={data.risk?.risk_state?.status === "OK" ? "정상 운영" : statusLabel(data.risk?.risk_state?.status ?? "WAITING")} tone={autoRunning ? "green" : statusTone(data.risk?.risk_state?.status)} />
          <span>가동 시간 {runtimeText}</span>
        </div>
      </div>
      <div className="ref-auto-master">
        <span>자동매매 전체 제어</span>
        <button
          className={`${autoRunning ? "is-on" : "is-off"} ${isToggling ? "is-loading" : ""}`}
          onClick={onToggle}
          disabled={isToggling}
          aria-pressed={autoRunning}
        >
          <i />
          <span className="ref-toggle-text">{isToggling ? "전환 중" : autoRunning ? "ON" : "OFF"}</span>
          {isToggling && <span className="ref-toggle-loader" aria-hidden="true" />}
        </button>
        {toggleError && <em>{toggleError}</em>}
      </div>
      <div className="ref-auto-mode">
        <span>대시 모드</span>
        <div><button>모의매매</button><button className="is-selected">실거래</button></div>
      </div>
      <div className="ref-auto-profit today">
        <span>일 총 손익 (KRW)</span>
        <strong className={dailyPnl != null && dailyPnl < 0 ? "ref-negative" : "ref-positive"}>{formatSignedKrw(dailyPnl)}</strong>
        <b>{formatPercent(data.paper?.balance?.total_return)}</b>
      </div>
      <div className="ref-auto-profit total">
        <span>누적 손익 (KRW)</span>
        <strong className={totalPnl != null && totalPnl < 0 ? "ref-negative" : "ref-positive"}>{formatSignedKrw(totalPnl)}</strong>
        <b>{formatPercent(data.forward?.balance?.total_return ?? data.paper?.balance?.total_return)}</b>
      </div>
    </RefPanel>
  );
}

function AutoOperationsStrip({ data }: { data: DashboardData }) {
  const policy = data.botPolicy;
  const readiness = data.smartEngineStatus?.limited_readiness;
  const latestRehearsal = data.smartEngineStatus?.latest_rehearsal_order ?? readiness?.latest_rehearsal_order;
  const review = data.smartEngineStatus?.rehearsal_review ?? latestRehearsal?.review ?? null;
  const blockers = data.smartEngineStatus?.remaining_rehearsal_blockers ?? readiness?.rehearsal_blockers ?? [];
  const balanceStatus = policy?.balance_fetch_status ?? data.liveBalances?.balance_fetch_status ?? "WAITING";
  const availableKrw = policy?.available_krw_balance ?? balanceAmount(data.liveBalances?.balances?.krw ?? data.liveBalances?.balances?.by_currency?.KRW);
  const maxOrder = data.liveStrategy?.max_order_krw ?? policy?.max_total_exposure_krw ?? null;
  const positionValue = policy?.current_bot_position_value_krw ?? null;
  const remainingExposure = (
    policy?.max_total_exposure_krw != null && positionValue != null
      ? Math.max(policy.max_total_exposure_krw - positionValue, 0)
      : null
  );
  const reviewStatus = review?.decision ?? latestRehearsal?.review_status ?? "미검토";
  const reviewTone = reviewStatus === "APPROVED" ? "green" : reviewStatus === "REJECTED" ? "red" : "amber";
  const limitedReady = readiness?.can_enable_limited === true || readiness?.status === "READY";
  const limitedBlocked = readiness?.can_enable_limited === false || readiness?.status === "BLOCKED";
  const readinessTone = limitedBlocked ? "red" : limitedReady ? "green" : "amber";
  const blockerText = blockers.length ? blockers.slice(0, 2).join(" · ") : "남은 차단 사유 없음";
  const cards = [
    {
      label: "운용정책",
      value: policy?.auto_trading_enabled ? "ON" : "OFF",
      detail: `최대 투입 ${formatKrw(policy?.max_total_exposure_krw)} · 일 손실 ${formatRatioPercent(policy?.daily_loss_limit_pct, 1)}`,
      tone: policy?.auto_trading_enabled ? "green" : "red"
    },
    {
      label: "주문 한도",
      value: formatOrderLimit(maxOrder),
      detail: `남은 한도 ${formatKrw(remainingExposure)} · 현재 포지션 ${formatKrw(positionValue)}`,
      tone: "cyan"
    },
    {
      label: "거래소 잔고",
      value: formatKrw(availableKrw),
      detail: `조회 상태 ${statusLabel(balanceStatus)} · 정책 대비 여유 ${formatKrw(remainingExposure)}`,
      tone: balanceStatus === "SUCCESS" ? "green" : "amber"
    },
    {
      label: "리허설 검토",
      value: reviewStatus === "APPROVED" ? "승인됨" : reviewStatus === "REJECTED" ? "반려됨" : "미검토",
      detail: latestRehearsal ? `${latestRehearsal.side ?? "-"} · ${formatKrw(latestRehearsal.amount_krw)}` : "최근 리허설 없음",
      tone: reviewTone
    },
    {
      label: "limited 전환",
      value: limitedBlocked ? "차단" : limitedReady ? "통과" : "점검",
      detail: blockerText,
      tone: readinessTone
    }
  ];

  return (
    <section className="ref-auto-ops-section">
      <div className="ref-auto-section-title">
        <b>운용 기준 요약</b>
        <em>{policy?.auto_trading_enabled ? "정책 ON" : "정책 OFF"}</em>
        <button>운용설정 보기 <ChevronRight size={16} /></button>
      </div>
      <div className="ref-auto-ops-grid">
        {cards.map((card) => (
          <RefPanel key={card.label} className={`ref-auto-ops-card ${card.tone}`}>
            <span>{card.label}</span>
            <strong title={card.value}>{card.value}</strong>
            <p title={card.detail}>{card.detail}</p>
          </RefPanel>
        ))}
      </div>
    </section>
  );
}

function AutoWatchPanel({ data }: { data: DashboardData }) {
  const latest = latestCandle(data.candles);
  const previous = previousCandle(data.candles);
  const btcChange = latest && previous ? (latest.trade_price - previous.trade_price) / previous.trade_price : null;
  const markets = Array.from(new Set([
    MARKET,
    ...data.candidates.map((candidate) => candidate.market).filter(Boolean),
    ...data.liveOrders.map((order) => order.market).filter(Boolean)
  ])).slice(0, 5);
  const rows = markets.length ? markets : [MARKET];

  return (
    <RefPanel className="ref-auto-watch-panel">
      <div className="ref-title-row">
        <h3>관심 심볼 모니터링 ({rows.length})</h3>
        <span>⋮</span>
      </div>
      <table className="ref-auto-table">
        <thead><tr><th>심볼</th><th>전략</th><th>현재가</th><th>24H 변동률</th><th>상태</th></tr></thead>
        <tbody>
          {rows.map((market, index) => {
            const candidate = data.candidates.find((item) => item.market === market);
            return (
              <tr key={`${market}-${index}`}>
                <td>{marketDisplay(market)}</td>
                <td>{candidate?.name ?? strategyLabel(candidate?.strategy) ?? "-"}</td>
                <td>{market === MARKET ? formatKrw(latest?.trade_price) : "-"}</td>
                <td className={btcChange != null && btcChange < 0 ? "ref-negative" : "ref-positive"}>{market === MARKET ? formatPercent(btcChange) : "-"}</td>
                <td><RefStatusBadge value={index === rows.length - 1 && rows.length > 3 ? "대기" : "실행"} tone={index === rows.length - 1 && rows.length > 3 ? "amber" : "green"} /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <button className="ref-auto-more">전체 심볼 보기 <ChevronRight size={15} /></button>
    </RefPanel>
  );
}

function AutoRiskPanel({
  data,
  onImportExchangePosition,
  isImportingPosition,
  importPositionError
}: {
  data: DashboardData;
  onImportExchangePosition: () => void;
  isImportingPosition: boolean;
  importPositionError: string | null;
}) {
  const risk = data.risk?.risk_state;
  const dailyLoss = Math.abs(risk?.daily_loss_percent ?? 0);
  const maxDailyLossPercent = data.risk?.config?.max_daily_loss_percent ?? 20;
  const maxOrder = data.liveStrategy?.max_order_krw ?? null;
  const latestRecovery = data.recoveryEvents[0];
  const latestMismatch = latestRecovery?.event_type === "BALANCE_MISMATCH" ? latestRecovery : undefined;
  const balanceMismatch = Boolean(
    !data.liveStrategy?.position
    && (latestMismatch
    || data.liveStrategy?.session?.last_risk_result === "BLOCKED_BALANCE_MISMATCH"
    || data.risk?.risk_state?.balance_mismatch_detected)
  );
  const exchangeBtc = latestMismatch?.payload?.exchange_btc_total;
  const policyBlock = latestPolicyBlockNotice(data);
  const policyUsage = data.botPolicy?.exposure_usage_pct;
  return (
    <RefPanel className="ref-auto-risk-panel">
      <div className="ref-title-row"><h3>리스크 관리</h3></div>
      <div className="ref-risk-ok"><ShieldCheck size={26} /><b>{risk?.status === "OK" ? "리스크 양호" : statusLabel(risk?.status ?? "WAITING")}</b></div>
      {policyBlock && (
        <div className="ref-policy-block-card">
          <span>최근 정책 차단</span>
          <b>{policyBlock.text}</b>
          <em>{formatKstShort(policyBlock.createdAt)} · {policyBlock.source === "risk" ? "주문 리스크" : "분석근거"}</em>
          <PolicyBlockDetailGrid detail={policyBlock.detail} compact />
        </div>
      )}
      {balanceMismatch && (
        <div className="ref-balance-recovery">
          <span>거래소 BTC 잔고 불일치 감지</span>
          <b>{exchangeBtc != null ? `${formatNumber(exchangeBtc, 8)} BTC` : "확인 필요"}</b>
          <button onClick={onImportExchangePosition} disabled={isImportingPosition}>
            {isImportingPosition ? "편입 중" : "봇 포지션으로 가져오기"}
          </button>
          {importPositionError && <em>{importPositionError}</em>}
        </div>
      )}
      <div className="ref-auto-risk-row"><span>개정 리스크</span><b>{dailyLoss.toFixed(2)}% / {maxDailyLossPercent.toFixed(0)}%</b></div>
      <div className="ref-auto-bar"><i style={{ width: `${Math.min(dailyLoss / Math.max(maxDailyLossPercent, 1) * 100, 100)}%` }} /></div>
      <div className="ref-auto-risk-row"><span>일일 손익 한도</span><b>{formatOrderLimit(maxOrder)}</b></div>
      <div className="ref-auto-bar"><i style={{ width: "38%" }} /></div>
      <div className="ref-auto-risk-row"><span>운용정책 사용률</span><b>{formatRatioPercent(policyUsage)}</b></div>
      <div className="ref-auto-risk-foot">
        <span>연속 손실 <b>{risk?.consecutive_loss_count ?? 0}회</b></span>
        <span>최대 손실 한도 <b>-</b></span>
      </div>
      <button className="ref-auto-more">리스크 설정 <ChevronRight size={15} /></button>
    </RefPanel>
  );
}

function AutoChartPanel({ data }: { data: DashboardData }) {
  const latest = latestCandle(data.candles);
  const previous = previousCandle(data.candles);
  const change = latest && previous ? latest.trade_price - previous.trade_price : null;
  return (
    <RefPanel className="ref-auto-chart-panel">
      <div className="ref-auto-chart-head">
        <h3>BTC/KRW 차트</h3>
        <strong>{formatKrw(latest?.trade_price)}</strong>
        <span className={change != null && change < 0 ? "ref-negative" : "ref-positive"}>{formatSignedKrw(change)}</span>
      </div>
      <div className="ref-auto-time-tabs"><button>1m</button><button className="is-selected">15m</button><button>1h</button><button>4h</button><button>1D</button><button>Y</button></div>
      <AutoMiniChart candles={data.candles} />
      <button className="ref-auto-more chart">전체 차트 보기 <ChevronRight size={15} /></button>
    </RefPanel>
  );
}

function AutoRightStack({ data }: { data: DashboardData }) {
  const latest = latestCandle(data.candles);
  const total = data.liveBalances?.balance_fetch_status === "SUCCESS"
    ? data.liveBalances?.estimated_total_equity_krw
    : data.paper?.balance?.equity ?? data.forward?.balance?.equity ?? null;
  const btcBalance = data.liveBalances?.balances?.btc ?? data.liveBalances?.balances?.by_currency?.BTC;
  const krwBalance = data.liveBalances?.balances?.krw ?? data.liveBalances?.balances?.by_currency?.KRW;
  const btcValue = balanceAmount(btcBalance) * (data.liveBalances?.prices?.[MARKET]?.price ?? latest?.trade_price ?? 0);
  const krwValue = balanceAmount(krwBalance);
  const btcPercent = total && btcValue ? Math.min((btcValue / total) * 100, 100) : 50;
  const krwPercent = total && krwValue ? Math.min((krwValue / total) * 100, 100) : 20;
  const otherPercent = Math.max(100 - btcPercent - krwPercent, 0);
  const smartRows = [
    ["BTC/KRW", "-2.50%", "+5.00%", data.liveStrategy?.auto_exit_enabled ? "ON" : "OFF"],
    ["ETH/KRW", "-2.50%", "+4.00%", "OFF"],
    ["SOL/KRW", "-3.00%", "+6.00%", "OFF"]
  ];
  const allocation = [
    ["BTC", btcPercent, btcValue],
    ["KRW", krwPercent, krwValue],
    ["기타", otherPercent, total == null ? null : Math.max(total - btcValue - krwValue, 0)]
  ];

  return (
    <>
      <RefPanel className="ref-auto-stop-panel">
        <div className="ref-title-row"><h3>스마트스탑 / 익절 관리</h3><span>⋮</span></div>
        {smartRows.map(([market, stop, take, enabled]) => (
          <p key={market}><b>{market}</b><span className="ref-negative">로 {stop}</span><span className="ref-positive">구 +{String(take).replace("+", "")}</span><i className={enabled === "ON" ? "on" : ""} /></p>
        ))}
        <button className="ref-auto-more">전체 설정 <ChevronRight size={15} /></button>
      </RefPanel>
      <RefPanel className="ref-auto-allocation-panel">
        <h3>자산 배분 설정</h3>
        <div className="ref-auto-allocation-body">
          <div className="ref-auto-donut" style={{ background: `conic-gradient(#ffa817 0 ${btcPercent}%, #327fe5 ${btcPercent}% ${btcPercent + krwPercent}%, #8091a6 ${btcPercent + krwPercent}% 100%)` }}>
            <div><span>총 자산</span><b>{formatKrw(total)}</b><em>KRW</em></div>
          </div>
          <div className="ref-auto-alloc-list">
            {allocation.map(([name, percent, value], index) => (
              <p key={name as string}><i className={`c${index}`} /><span>{name}</span><b>{Number(percent).toFixed(0)}%</b><em>{typeof value === "number" ? formatKrw(value) : "-"}</em></p>
            ))}
          </div>
        </div>
        <button className="ref-auto-more">배분 설정 <ChevronRight size={15} /></button>
      </RefPanel>
      <RefPanel className="ref-auto-emergency-panel">
        <h3><Target size={24} />긴급 정지</h3>
        <p>모든 전략의 주문을 즉시 중단합니다.</p>
        <button>긴급 정지 실행</button>
      </RefPanel>
    </>
  );
}

function AutoBottomPanels({ data }: { data: DashboardData }) {
  const latest = latestCandle(data.candles);
  const livePosition = data.liveStrategy?.position;
  const quantity = livePosition?.entry_volume ?? data.paper?.position?.btc_quantity ?? data.paper?.position?.current_position_volume ?? 0;
  const positions = quantity > 0
    ? [{
      market: MARKET,
      strategy: data.liveStrategy?.session?.strategy_name ?? data.autoPilot?.session?.strategy_name ?? "-",
      qty: `${formatNumber(quantity, 8)} BTC`,
      entry: formatKrw(livePosition?.entry_price ?? data.paper?.position?.avg_buy_price),
      value: formatKrw((livePosition?.current_price ?? latest?.trade_price ?? 0) * quantity),
      pnl: formatSignedKrw(livePosition?.unrealized_pnl ?? data.paper?.balance?.unrealized_pnl)
    }]
    : [];
  const pending = data.liveOrders.filter((order) => order.status !== "FILLED").slice(0, 4);
  const logs = [
    ...data.liveOrders.slice(0, 4).map((order) => ({
      time: formatKstTime(order.created_at),
      strategy: order.strategy_name ?? "-",
      market: marketDisplay(order.market),
      type: statusLabel(order.order_type ?? order.side ?? "-"),
      price: formatKrw(order.price),
      status: order.status ?? "-"
    })),
    ...data.risk?.risk_logs?.slice(0, 2).map((log) => ({
      time: formatKstTime(log.created_at),
      strategy: riskLogIsPolicy(log) ? "Policy" : "Risk",
      market: "-",
      type: riskLogMessage(log),
      price: "-",
      status: log.allowed ? "OK" : "BLOCKED"
    })) ?? []
  ].slice(0, 5);

  return (
    <>
      <RefPanel className="ref-auto-position-list">
        <h3>현재 포지션 ({positions.length})</h3>
        <table className="ref-auto-table">
          <thead><tr><th>심볼</th><th>전략</th><th>수량</th><th>평균가</th><th>평가금</th><th>수익률</th></tr></thead>
          <tbody>
            {positions.length === 0 && <tr><td colSpan={6}>보유 포지션 없음</td></tr>}
            {positions.map((row) => <tr key={row.market}><td>{marketDisplay(row.market)}</td><td>{row.strategy}</td><td>{row.qty}</td><td>{row.entry}</td><td>{row.value}</td><td className={row.pnl.startsWith("-") ? "ref-negative" : "ref-positive"}>{row.pnl}</td></tr>)}
          </tbody>
        </table>
        <button className="ref-auto-more">전체 포지션 보기 <ChevronRight size={15} /></button>
      </RefPanel>
      <RefPanel className="ref-auto-orders-panel">
        <h3>대기 주문 ({pending.length})</h3>
        <table className="ref-auto-table">
          <thead><tr><th>심볼</th><th>전략</th><th>주문 유형</th><th>가격 (KRW)</th><th>수량</th><th>상태</th></tr></thead>
          <tbody>
            {pending.length === 0 && <tr><td colSpan={6}>대기 주문 없음</td></tr>}
            {pending.map((order) => <tr key={order.request_id ?? order.id}><td>{marketDisplay(order.market)}</td><td>{order.strategy_name ?? "-"}</td><td>{statusLabel(order.order_type ?? order.side)}</td><td>{formatKrw(order.price)}</td><td>{formatNumber(order.volume, 8)}</td><td><RefStatusBadge value={statusLabel(order.status)} tone={statusTone(order.status)} /></td></tr>)}
          </tbody>
        </table>
        <button className="ref-auto-more">전체 주문 보기 <ChevronRight size={15} /></button>
      </RefPanel>
      <RefPanel className="ref-auto-exec-panel">
        <h3>주문 실행 로그 (실시간)</h3>
        <table className="ref-auto-table">
          <thead><tr><th>시간</th><th>전략</th><th>심볼</th><th>주문 유형</th><th>가격</th><th>상태</th></tr></thead>
          <tbody>
            {logs.length === 0 && <tr><td colSpan={6}>실행 로그 없음</td></tr>}
            {logs.map((row, index) => <tr key={`${row.time}-${index}`}><td>{row.time}</td><td>{row.strategy}</td><td>{row.market}</td><td title={row.type}>{row.type}</td><td>{row.price}</td><td><RefStatusBadge value={statusLabel(row.status)} tone={statusTone(row.status)} /></td></tr>)}
          </tbody>
        </table>
        <button className="ref-auto-more">전체 실행 내역 보기 <ChevronRight size={15} /></button>
      </RefPanel>
    </>
  );
}

function AutoTradeView({
  data,
  onToggleAutoTrading,
  onImportExchangePosition,
  isAutoToggling,
  autoToggleError,
  isImportingPosition,
  importPositionError
}: {
  data: DashboardData;
  onToggleAutoTrading: () => void;
  onImportExchangePosition: () => void;
  isAutoToggling: boolean;
  autoToggleError: string | null;
  isImportingPosition: boolean;
  importPositionError: string | null;
}) {
  return (
    <>
      <AutoStatusPanel data={data} onToggle={onToggleAutoTrading} isToggling={isAutoToggling} toggleError={autoToggleError} />
      <AutoOperationsStrip data={data} />
      <AutoWatchPanel data={data} />
      <AutoRiskPanel data={data} onImportExchangePosition={onImportExchangePosition} isImportingPosition={isImportingPosition} importPositionError={importPositionError} />
      <AutoChartPanel data={data} />
      <AutoRightStack data={data} />
      <AutoBottomPanels data={data} />
    </>
  );
}

function OperationsView({ data, refresh }: { data: DashboardData; refresh: () => Promise<void> }) {
  const policy = data.botPolicy;
  const profit = data.profitEngineStatus;
  const readiness = data.smartEngineStatus?.limited_readiness;
  const readinessChecks = readiness?.checks ?? [];
  const latestRehearsal = data.smartEngineStatus?.latest_rehearsal_order ?? readiness?.latest_rehearsal_order;
  const rehearsalReview = data.smartEngineStatus?.rehearsal_review ?? latestRehearsal?.review ?? null;
  const rehearsalBlockers = data.smartEngineStatus?.remaining_rehearsal_blockers ?? readiness?.rehearsal_blockers ?? [];
  const visibleReadinessChecks = readinessChecks.slice(0, 3);
  const hiddenReadinessCount = Math.max(readinessChecks.length - visibleReadinessChecks.length, 0);
  const visibleRehearsalBlockers = (rehearsalBlockers.length ? rehearsalBlockers : ["남은 리허설 차단 사유 없음"]).slice(0, 2);
  const [autoTradingEnabled, setAutoTradingEnabled] = React.useState(false);
  const [maxExposure, setMaxExposure] = React.useState(500000);
  const [dailyLossPct, setDailyLossPct] = React.useState(3);
  const [isSaving, setIsSaving] = React.useState(false);
  const [reviewNote, setReviewNote] = React.useState("");
  const [isReviewing, setIsReviewing] = React.useState(false);
  const [message, setMessage] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!policy) return;
    setAutoTradingEnabled(Boolean(policy.auto_trading_enabled));
    setMaxExposure(Math.max(1, Math.round(policy.max_total_exposure_krw ?? 500000)));
    setDailyLossPct(Number((policy.daily_loss_limit_pct ?? 3).toFixed(2)));
  }, [policy?.auto_trading_enabled, policy?.daily_loss_limit_pct, policy?.max_total_exposure_krw]);

  const save = async () => {
    if (isSaving) return;
    setIsSaving(true);
    setMessage(null);
    setError(null);
    try {
      await patchJson<{ policy: BotPolicy }>("/api/bot/policy?market=KRW-BTC&exchange=bithumb", {
        auto_trading_enabled: autoTradingEnabled,
        max_total_exposure_krw: maxExposure,
        daily_loss_limit_pct: dailyLossPct
      });
      await refresh();
      setMessage("운용정책 저장 완료");
    } catch (err) {
      setError(err instanceof Error ? err.message : "운용정책 저장 실패");
    } finally {
      setIsSaving(false);
    }
  };

  const submitRehearsalReview = async (decision: "APPROVED" | "REJECTED") => {
    if (isReviewing || !latestRehearsal?.request_id) return;
    setIsReviewing(true);
    setMessage(null);
    setError(null);
    try {
      await postJson<{ ok?: boolean }>("/api/smart-engine/rehearsal-review", {
        request_id: latestRehearsal.request_id,
        exchange: latestRehearsal.exchange ?? "bithumb",
        market: MARKET,
        decision,
        note: reviewNote.trim()
      });
      await refresh();
      setMessage(decision === "APPROVED" ? "리허설 검토 승인 완료" : "리허설 반려 기록 완료");
      setReviewNote("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "리허설 검토 저장 실패");
    } finally {
      setIsReviewing(false);
    }
  };

  const projectedLossLimit = maxExposure * dailyLossPct / 100;
  const usage = policy?.exposure_usage_pct ?? 0;
  const usageWidth = Math.min(Math.max(usage, 0), 100);
  const profitGateBlocked = profit?.entry_gate?.entry_allowed === false;
  const profitGateLabel = profitGateBlocked
    ? `차단됨 (${profit?.entry_gate?.block_code ?? "사유 확인 필요"})`
    : profit?.entry_gate?.entry_allowed === true
      ? "진입 가능"
      : "판단 대기";
  const profitKillSwitchLabel = profit?.kill_switch?.status === "PAUSED"
    ? "전략 일시정지"
    : profit?.kill_switch?.status === "OK"
      ? "정상"
      : profit?.kill_switch?.status ?? "-";

  return (
    <>
      <RefPanel className="ref-ops-summary">
        <span>Bot Operation Policy</span>
        <h2>Smart Autonomous Live</h2>
        <p>{autoTradingEnabled ? "자동매매 ON" : "자동매매 OFF"} · {marketDisplay(policy?.market)} · 마지막 수정 {formatKstShort(policy?.updated_at)}</p>
        {(message || error) && <strong className={error ? "is-error" : ""}>{error ?? message}</strong>}
      </RefPanel>
      <RefPanel className="ref-ops-form">
        <h3>운용설정</h3>
        <label className="ref-ops-toggle">
          <span>자동매매</span>
          <button type="button" className={autoTradingEnabled ? "is-on" : ""} onClick={() => setAutoTradingEnabled((value) => !value)}>
            {autoTradingEnabled ? "ON" : "OFF"}
          </button>
        </label>
        <label>
          <span>최대 투입 금액</span>
          <input type="number" min={1} step={10000} value={maxExposure} onChange={(event) => setMaxExposure(Math.max(1, Number(event.target.value) || 1))} />
          <em>KRW</em>
        </label>
        <label>
          <span>일 손실률 제한</span>
          <input type="number" min={0.1} max={100} step={0.1} value={dailyLossPct} onChange={(event) => setDailyLossPct(Math.min(100, Math.max(0.1, Number(event.target.value) || 0.1)))} />
          <em>%</em>
        </label>
        <button className="ref-ops-save" type="button" onClick={() => void save()} disabled={isSaving}>
          <Save size={18} />{isSaving ? "저장 중" : "저장"}
        </button>
      </RefPanel>
      <RefPanel className="ref-ops-limits">
        <h3>정책 한도</h3>
        <div>
          <p><span>최대 투입</span><b>{formatKrw(policy?.max_total_exposure_krw)} KRW</b></p>
          <p><span>현재 포지션</span><b>{formatKrw(policy?.current_bot_position_value_krw)} KRW</b></p>
          <p><span>사용률</span><b>{formatRatioPercent(policy?.exposure_usage_pct, 1)}</b></p>
          <p><span>일 손실 한도</span><b>{formatKrw(policy?.daily_loss_limit_krw)} KRW</b></p>
        </div>
        <section className="ref-ops-usage">
          <span><i style={{ width: `${usageWidth}%` }} /></span>
          <b>{formatRatioPercent(usage, 1)}</b>
        </section>
      </RefPanel>
      <RefPanel className="ref-ops-preview">
        <h3>저장 예정 값</h3>
        <p><span>자동매매</span><b>{autoTradingEnabled ? "ON" : "OFF"}</b></p>
        <p><span>최대 투입</span><b>{formatKrw(maxExposure)} KRW</b></p>
        <p><span>일 손실률</span><b>{formatRatioPercent(dailyLossPct, 1)}</b></p>
        <p><span>일 손실 한도</span><b>{formatKrw(projectedLossLimit)} KRW</b></p>
      </RefPanel>
      <RefPanel className="ref-ops-balance">
        <h3>거래소 잔고</h3>
        <p><span>조회 상태</span><b>{policy?.balance_fetch_status ?? "-"}</b></p>
        <p><span>사용 가능 KRW</span><b>{formatKrw(policy?.available_krw_balance)} KRW</b></p>
        <p><span>정책 대비 여유</span><b>{formatKrw(Math.max((policy?.max_total_exposure_krw ?? 0) - (policy?.current_bot_position_value_krw ?? 0), 0))} KRW</b></p>
        {policy?.balance_error && <em>{policy.balance_error}</em>}
      </RefPanel>
      <RefPanel className="ref-ops-readiness">
        <h3>Smart Engine 점검</h3>
        <strong className={readiness?.can_enable_limited ? "is-ready" : "is-blocked"}>
          {smartReadinessLabel(readiness?.status)}
        </strong>
        <p>{readiness?.next_required_operator_action ?? readiness?.recommended_next_action ?? "Smart Engine 상태를 불러오는 중입니다."}</p>
        {latestRehearsal && (
          <p title={`${formatKstShort(latestRehearsal.created_at)} · ${latestRehearsal.side ?? "-"} · ${latestRehearsal.status ?? "-"} · ${latestRehearsal.risk_result ?? "-"}`}>
            최신 리허설 {formatKstShort(latestRehearsal.created_at)} · {statusLabel(latestRehearsal.side)} · {statusLabel(latestRehearsal.status)} · {alertCodeLabel(latestRehearsal.risk_result)}
          </p>
        )}
        <section>
          {visibleReadinessChecks.map((check) => (
            <div key={check.id ?? check.label} className={smartReadinessTone(check.status)}>
              <span>{check.label ?? "-"}</span>
              <b>{check.status === "pass" ? "통과" : check.status === "warn" ? "주의" : "차단"}</b>
              <em title={check.detail ?? "-"}>{check.detail ?? "-"}</em>
            </div>
          ))}
          {hiddenReadinessCount > 0 && (
            <div className="is-warn">
              <span>추가 점검</span>
              <b>{hiddenReadinessCount}개</b>
              <em>나머지 항목은 Smart Engine 상세 상태에서 확인</em>
            </div>
          )}
          {readinessChecks.length === 0 && <div className="is-warn"><span>점검</span><b>대기</b><em>상태 데이터가 아직 없습니다.</em></div>}
        </section>
      </RefPanel>
      <RefPanel className="ref-ops-review">
        <h3>리허설 검토</h3>
        {latestRehearsal ? (
          <>
            <p><span>요청</span><b title={latestRehearsal.request_id ?? "-"}>{latestRehearsal.request_id ?? "-"}</b></p>
            <p><span>주문</span><b>{latestRehearsal.side ?? "-"} · {formatKrw(latestRehearsal.amount_krw)} KRW</b></p>
            <p><span>상태</span><b title={`${latestRehearsal.status ?? "-"} · ${latestRehearsal.risk_result ?? "-"}`}>{statusLabel(latestRehearsal.status)} · {alertCodeLabel(latestRehearsal.risk_result)}</b></p>
            <p><span>리뷰</span><b>{rehearsalReview?.decision ?? "미검토"}{rehearsalReview?.is_active ? " · 유효" : ""}</b></p>
            <div className="ref-ops-review-actions">
              <button type="button" onClick={() => void submitRehearsalReview("APPROVED")} disabled={isReviewing}>
                <ShieldCheck size={17} />{isReviewing ? "처리 중" : "검토 승인"}
              </button>
              <button type="button" onClick={() => void submitRehearsalReview("REJECTED")} disabled={isReviewing}>
                <PowerOff size={17} />반려
              </button>
            </div>
            {latestRehearsal.error_message && <em title={latestRehearsal.error_message}>{latestRehearsal.error_message}</em>}
            <label>
              <span>검토 메모</span>
              <textarea value={reviewNote} onChange={(event) => setReviewNote(event.target.value)} placeholder="선택 입력" />
            </label>
            {rehearsalReview?.expires_at && <p><span>만료</span><b>{formatKstShort(rehearsalReview.expires_at)}</b></p>}
            <section>
              {visibleRehearsalBlockers.map((item) => (
                <p key={item}><span>남은 차단</span><b title={item}>{item}</b></p>
              ))}
            </section>
          </>
        ) : (
          <p><span>최신 리허설</span><b>없음</b></p>
        )}
      </RefPanel>
      <RefPanel className="ref-ops-profit-engine">
        <h3>수익 엔진 상태</h3>
        <div className="ref-profit-engine-head">
          <strong className={profit?.config?.enabled ? "is-on" : ""}>{profit?.config?.enabled ? "작동 중" : "꺼짐"}</strong>
          <span>{profit?.config?.mode === "aggressive" ? "공격형 수익 모드" : profit?.config?.mode ?? "-"}</span>
        </div>
        <p><span>시장 국면</span><b>{marketRegimeLabel(profit?.entry_gate?.market_regime)}</b></p>
        <p><span>진입 판단</span><b className={profitGateBlocked ? "is-blocked" : "is-ok"} title={profit?.entry_gate?.block_code ?? "-"}>{profitGateLabel}</b></p>
        <p><span>전략</span><b>{profitStrategyLabel(profit?.entry_gate?.strategy_name)}</b></p>
        <p><span>요청 주문액</span><b>{formatKrw(profit?.latest_order_sizing?.requested_order_krw)} KRW</b></p>
        <p><span>사용 가능 KRW</span><b>{formatKrw(profit?.latest_order_sizing?.available_krw)} KRW</b></p>
        <p><span>실제 주문액</span><b>{formatKrw(profit?.latest_order_sizing?.actual_order_krw)} KRW</b></p>
        <p><span>금액 산정</span><b title={profit?.latest_order_sizing?.sizing_reason ?? "-"}>{sizingReasonLabel(profit?.latest_order_sizing?.sizing_reason)}</b></p>
        <p><span>킬 스위치</span><b>{profitKillSwitchLabel}</b></p>
        {profit?.entry_gate?.entry_block_reason && <em>{profit.entry_gate.entry_block_reason}</em>}
      </RefPanel>
    </>
  );
}

function cleanStrategyName(candidate?: Candidate | null) {
  if (!candidate) return "전략 없음";
  const base = candidate.name && !/[Â�ìëí]/.test(candidate.name)
    ? candidate.name
    : `${strategyLabel(candidate.strategy)} · ${formatTimeframe(candidate.unit)} · ${formatNumber(candidate.score, 2)}pt`;
  return base.replace("ma_cross", "MA 교차");
}

function formatTimeframe(unit?: number | null) {
  if (!unit) return "-";
  if (unit < 60) return `${unit}분`;
  if (unit === 60) return "1시간";
  if (unit % 60 === 0) return `${unit / 60}시간`;
  return `${unit}분`;
}

function chartUnitLabel(unit: number) {
  return CHART_TIMEFRAMES.find((item) => item.unit === unit)?.label ?? formatTimeframe(unit);
}

function strategySparkTone(candidate?: Candidate | null) {
  const value = candidate?.backtest_total_return ?? candidate?.score ?? 0;
  return value < 0 ? "down" : "up";
}

type StrategyFilter = "ALL" | "ACTIVE" | "INACTIVE";

type StrategyDraft = {
  name: string;
  description: string;
  strategy: string;
  unit: number;
  market: string;
  parameters: Record<string, number>;
  status: string;
};

type StrategyValidationResult = {
  run_id?: number;
  rows?: Array<{
    strategy?: string;
    unit?: number;
    period_label?: string;
    metrics?: {
      total_return?: number;
      mdd?: number;
      win_rate?: number;
      profit_factor?: number;
      trade_count?: number;
      score?: number;
    };
  }>;
};

const STRATEGY_OPTIONS = ["ma_cross", "rsi", "volatility_breakout"];
const TIMEFRAME_OPTIONS = [1, 5, 15, 60];

function defaultStrategyParameters(strategy = "ma_cross"): Record<string, number> {
  if (strategy === "rsi") return { period: 14, oversold: 30, overbought: 70 };
  if (strategy === "volatility_breakout") return { k: 0.5, atr_period: 14, volume_window: 20 };
  return { short_window: 12, long_window: 26, signal_window: 9 };
}

function strategyDraftFromCandidate(candidate: Candidate | null): StrategyDraft {
  const strategy = candidate?.strategy ?? "ma_cross";
  return {
    name: candidate ? cleanStrategyName(candidate) : `${strategyLabel(strategy)} v2.1`,
    description: candidate?.description && !/[Â�ìëí]/.test(candidate.description)
      ? candidate.description
      : "실제 후보 전략 데이터를 기반으로 조건과 리스크를 관리하는 자동매매 전략입니다.",
    strategy,
    unit: candidate?.unit ?? CHART_UNIT,
    market: candidate?.market ?? MARKET,
    parameters: { ...defaultStrategyParameters(strategy), ...(candidate?.parameters ?? {}) },
    status: candidate?.status ?? "ACTIVE"
  };
}

function formatFieldLabel(key: string) {
  const labels: Record<string, string> = {
    short_window: "단기 EMA",
    long_window: "장기 EMA",
    signal_window: "신호선",
    period: "기간",
    oversold: "과매도",
    overbought: "과매수",
    k: "돌파 계수",
    atr_period: "ATR 기간",
    volume_window: "거래량 기간"
  };
  return labels[key] ?? key.replace(/_/g, " ");
}

function buildCandidatePayload(draft: StrategyDraft, candidate?: Candidate | null) {
  return {
    name: draft.name.trim() || `${strategyLabel(draft.strategy)} v2.1`,
    description: draft.description.trim(),
    strategy: draft.strategy,
    parameters: draft.parameters,
    unit: draft.unit,
    market: draft.market,
    backtest_period: candidate?.backtest_period ?? "manual",
    score: candidate?.score ?? 0,
    backtest_total_return: candidate?.backtest_total_return ?? 0,
    backtest_mdd: candidate?.backtest_mdd ?? 0,
    backtest_win_rate: candidate?.backtest_win_rate ?? 0,
    backtest_profit_factor: candidate?.backtest_profit_factor ?? 0,
    backtest_trade_count: candidate?.backtest_trade_count ?? 0,
    backtest_average_trade_pnl: candidate?.backtest_average_trade_pnl ?? 0,
    warning: candidate?.warning ?? "",
    status: draft.status
  };
}

function StrategyListPanel({
  candidates,
  selectedId,
  onSelect,
  filter,
  onFilterChange,
  search,
  onSearchChange,
  onCreate,
  isBusy
}: {
  candidates: Candidate[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  filter: StrategyFilter;
  onFilterChange: (filter: StrategyFilter) => void;
  search: string;
  onSearchChange: (value: string) => void;
  onCreate: () => void;
  isBusy: boolean;
}) {
  const activeCount = candidates.filter((candidate) => candidate.status === "ACTIVE").length;
  const inactiveCount = candidates.filter((candidate) => candidate.status !== "ACTIVE").length;
  const filtered = candidates.filter((candidate) => {
    const matchesStatus = filter === "ALL" || (filter === "ACTIVE" ? candidate.status === "ACTIVE" : candidate.status !== "ACTIVE");
    const query = search.trim().toLowerCase();
    const haystack = [cleanStrategyName(candidate), candidate.market, strategyLabel(candidate.strategy), formatTimeframe(candidate.unit), candidate.status].join(" ").toLowerCase();
    return matchesStatus && (!query || haystack.includes(query));
  });
  const rows = filtered.length ? filtered : [{ id: 0, strategy: "ma_cross", market: MARKET, unit: CHART_UNIT, status: "INACTIVE", name: candidates.length ? "검색 결과 없음" : "전략 없음" } as Candidate];

  return (
    <RefPanel className="ref-strategy-list-panel">
      <div className="ref-strategy-list-head">
        <h2>전략 목록</h2>
        <button onClick={onCreate} disabled={isBusy}><Plus size={18} />새 전략</button>
      </div>
      <label className="ref-strategy-search"><Search size={17} /><input value={search} onChange={(event) => onSearchChange(event.target.value)} placeholder="전략 검색" /></label>
      <div className="ref-strategy-tabs">
        <button className={filter === "ALL" ? "is-active" : ""} onClick={() => onFilterChange("ALL")}>전체 ({candidates.length})</button>
        <button className={filter === "ACTIVE" ? "is-active" : ""} onClick={() => onFilterChange("ACTIVE")}>활성 {activeCount}</button>
        <button className={filter === "INACTIVE" ? "is-active" : ""} onClick={() => onFilterChange("INACTIVE")}>비활성 {inactiveCount}</button>
      </div>
      <div className="ref-strategy-cards">
        {rows.slice(0, 6).map((candidate) => {
          const active = candidate.id === selectedId;
          return (
            <button key={candidate.id} className={`ref-strategy-list-card ${active ? "is-selected" : ""}`} onClick={() => candidate.id > 0 && onSelect(candidate.id)} disabled={candidate.id <= 0}>
              <div>
                <strong>{cleanStrategyName(candidate)}</strong>
                <RefStatusBadge value={statusLabel(candidate.status)} tone={statusTone(candidate.status)} />
              </div>
              <p>{marketDisplay(candidate.market)} · {formatTimeframe(candidate.unit)} · {strategyLabel(candidate.strategy)}</p>
              <span>최근 실행: {formatKstShort(candidate.updated_at)}</span>
              <i className={`ref-strategy-spark ${strategySparkTone(candidate)}`} />
            </button>
          );
        })}
      </div>
      <div className="ref-strategy-pages"><button>‹</button><b>1</b><span>2</span><button>›</button></div>
    </RefPanel>
  );
}

function StrategyEditorPanel({
  draft,
  selected,
  onDraftChange
}: {
  draft: StrategyDraft;
  selected: Candidate | null;
  onDraftChange: (next: StrategyDraft) => void;
}) {
  const params = draft.parameters;
  const setParam = (key: string, value: number) => onDraftChange({ ...draft, parameters: { ...draft.parameters, [key]: value } });
  const parameterRows = Object.entries(params).slice(0, 6);
  const shortWindow = params.short_window ?? params.period ?? 12;
  const longWindow = params.long_window ?? params.overbought ?? 26;
  const conditionRows = draft.strategy === "rsi"
    ? [["RSI", "<", String(params.oversold ?? 30)], ["RSI", ">", String(params.overbought ?? 70)], ["기간", "=", String(params.period ?? 14)]]
    : draft.strategy === "volatility_breakout"
      ? [["전일 고가", "+", `변동폭 x ${params.k ?? 0.5}`], ["ATR", ">", String(params.atr_period ?? 14)], ["거래량", ">", `${params.volume_window ?? 20}봉 평균`]]
      : [[`EMA(${shortWindow})`, ">", `EMA(${longWindow})`], ["종가", ">", `EMA(${shortWindow})`], ["신호선", "=", String(params.signal_window ?? 9)]];
  const exitRows = draft.strategy === "rsi"
    ? [["RSI", ">", String(params.overbought ?? 70)], ["손절", "<", "-2.0%"]]
    : [["종가", "<", `EMA(${shortWindow})`], ["트레일링", "=", "1.5%"]];
  const filters = [
    ["시장", "=", marketDisplay(draft.market)],
    ["타임프레임", "=", formatTimeframe(draft.unit)],
    ["상태", "=", statusLabel(draft.status)]
  ];

  return (
    <RefPanel className="ref-strategy-editor-panel">
      <h2>전략 편집</h2>
      <div className="ref-editor-top">
        <label><span>전략 이름</span><input value={draft.name} onChange={(event) => onDraftChange({ ...draft, name: event.target.value })} /></label>
        <label className="ref-editor-state"><span>상태</span><RefStatusBadge value={statusLabel(draft.status)} tone={statusTone(draft.status)} /></label>
      </div>
      <label className="ref-editor-description">
        <span>설명</span>
        <textarea value={draft.description} onChange={(event) => onDraftChange({ ...draft, description: event.target.value.slice(0, 200) })} />
        <em>{draft.description.length} / 200</em>
      </label>
      <div className="ref-editor-row">
        <label><span>거래 대상</span><div className="ref-token-input"><b>{marketDisplay(draft.market)}</b><button type="button" disabled>KRW-BTC 고정</button></div></label>
        <label><span>타임프레임</span><select value={draft.unit} onChange={(event) => onDraftChange({ ...draft, unit: Number(event.target.value) })}>{TIMEFRAME_OPTIONS.map((unit) => <option key={unit} value={unit}>{formatTimeframe(unit)}</option>)}</select></label>
        <label><span>전략 유형</span><select value={draft.strategy} onChange={(event) => onDraftChange({ ...draft, strategy: event.target.value, parameters: defaultStrategyParameters(event.target.value) })}>{STRATEGY_OPTIONS.map((item) => <option key={item} value={item}>{strategyLabel(item)}</option>)}</select></label>
      </div>
      <div className="ref-editor-grid">
        <section>
          <h3>진입 조건 <button>AND⌄</button></h3>
          {conditionRows.map((row, index) => <p key={index}><span>{row[0]}</span><b>{row[1]}</b><span>{row[2]}</span><button type="button">⋮</button></p>)}
          <button className="ref-add-condition" type="button">실제 조건 미리보기</button>
          <h3>청산 조건 <button>OR⌄</button></h3>
          {exitRows.map((row, index) => <p key={index}><span>{row[0]}</span><b>{row[1]}</b><span>{row[2]}</span><button type="button">⋮</button></p>)}
          <button className="ref-add-condition" type="button">리스크 조건 연결</button>
        </section>
        <section>
          <h3>파라미터 <em>(실제 저장)</em></h3>
          <div className="ref-param-form">
            {parameterRows.map(([key, value]) => (
              <label key={key}>
                <span>{formatFieldLabel(key)}</span>
                <input type="number" value={Number(value)} step={key === "k" ? "0.1" : "1"} onChange={(event) => setParam(key, Number(event.target.value))} />
              </label>
            ))}
          </div>
          <h3>실행 필터</h3>
          {filters.map((row) => <p key={row[0]}><span>{row[0]}</span><b>{row[1]}</b><span>{row[2]}</span><button type="button">⊕</button></p>)}
          <div className="ref-editor-hint">선택 ID {selected?.id ?? "-"} · 최근 갱신 {formatKstShort(selected?.updated_at)}</div>
          <div className="ref-risk-form compact">
            <span>백테스트 기간</span><b>{selected?.backtest_period ?? "manual"}</b><strong>{formatNumber(selected?.score, 2)}점</strong>
            <span>손익비</span><b>{formatNumber(selected?.backtest_profit_factor, 2)}</b><strong>{selected?.warning || "정상"}</strong>
          </div>
        </section>
      </div>
      <div className="ref-capital-settings">
        <h3>자본 설정</h3>
        <p><span>전략 방식</span><b>비례 배분</b><span>최대 동시 포지션</span><b>3개</b></p>
        <p><span>전략 금 비율</span><b>25%</b><span>1회 진입 금액</span><b>{formatKrw(1000000)} KRW</b></p>
      </div>
    </RefPanel>
  );
}

function StrategyRightPanels({
  data,
  candidate,
  draft,
  backtestDays,
  isBusy,
  actionMessage,
  actionError,
  validationResult,
  onBacktestDaysChange,
  onSave,
  onClone,
  onToggle,
  onDelete,
  onRunTest
}: {
  data: DashboardData;
  candidate: Candidate | null;
  draft: StrategyDraft;
  backtestDays: number;
  isBusy: boolean;
  actionMessage: string | null;
  actionError: string | null;
  validationResult: StrategyValidationResult | null;
  onBacktestDaysChange: (days: number) => void;
  onSave: () => void;
  onClone: () => void;
  onToggle: () => void;
  onDelete: () => void;
  onRunTest: (days: number) => void;
}) {
  const totalReturn = candidate?.backtest_total_return ?? data.paper?.balance?.total_return ?? null;
  const winRate = candidate?.backtest_win_rate ?? null;
  const tradeCount = candidate?.backtest_trade_count ?? data.liveOrders.length;
  const mdd = candidate?.backtest_mdd ?? data.paper?.balance?.mdd ?? null;
  const averageReturn = candidate?.backtest_average_trade_pnl ?? null;
  const latestValidation = validationResult?.rows?.[0];
  const score = candidate?.score ?? latestValidation?.metrics?.score ?? null;
  const statusIsActive = draft.status === "ACTIVE";
  const toggleLabel = statusIsActive ? "비활성화" : "활성화";
  const toggleClass = statusIsActive ? "warning" : "enable";
  const normalizedBacktestDays = Math.max(1, Math.min(365, Math.round(backtestDays || 30)));

  return (
    <>
      <RefPanel className="ref-strategy-performance-panel">
        <div className="ref-title-row"><h3>전략 성과 <span>(과거지원)</span></h3><button>최근 {normalizedBacktestDays}일</button></div>
        <div className="ref-performance-grid">
          <p><span>총 수익률</span><b className={valueToneClass(totalReturn)}>{formatPercent(totalReturn)}</b><em className={valueToneClass(data.paper?.balance?.total_pnl)}>{formatSignedKrw(data.paper?.balance?.total_pnl)}</em></p>
          <p><span>승률</span><b>{formatPercent(winRate)}</b><em>{tradeCount}건</em></p>
          <p><span>총 거래 수</span><b>{tradeCount}건</b><em>최근 로그</em></p>
          <p><span>평균 수익률</span><b className={valueToneClass(averageReturn)}>{formatPercent(averageReturn)}</b></p>
          <p><span>최대 연속 승리</span><b>{Math.max(1, Math.round((winRate ?? 0) * 10))}연속</b></p>
          <p><span>최대 낙폭 (MDD)</span><b className={lossToneClass(mdd)}>{formatPercent(mdd)}</b></p>
        </div>
        <div className="ref-performance-chart"><i /></div>
      </RefPanel>
      <RefPanel className="ref-strategy-backtest-panel">
        <h3>백테스트 요약 <span>({data.liveStatus?.exchange ?? "거래소"} · {marketDisplay(candidate?.market)} · {formatTimeframe(candidate?.unit)})</span></h3>
        <div className="ref-backtest-period">
          <span>기간</span>
          <label>
            <input
              type="number"
              min={1}
              max={365}
              value={normalizedBacktestDays}
              onChange={(event) => onBacktestDaysChange(Math.max(1, Math.min(365, Number(event.target.value) || 1)))}
            />
            <em>일</em>
          </label>
          <b>{validationResult ? `검증 Run #${validationResult.run_id ?? "-"} · ${latestValidation?.period_label ?? `${normalizedBacktestDays}d`}` : candidate?.backtest_period ?? "최근 데이터 기준"}</b>
        </div>
        <div className="ref-backtest-grid">
          <p><span>총 수익률</span><b className={valueToneClass(totalReturn)}>{formatPercent(totalReturn)}</b></p>
          <p><span>CAGR</span><b className={valueToneClass(totalReturn)}>{formatPercent(totalReturn)}</b></p>
          <p><span>승률</span><b>{formatPercent(winRate)}</b></p>
          <p><span>총 거래 수</span><b>{tradeCount}</b></p>
          <p><span>평균 수익률</span><b className={valueToneClass(averageReturn)}>{formatPercent(averageReturn)}</b></p>
          <p><span>손익비</span><b>{formatNumber(candidate?.backtest_profit_factor, 2)}</b></p>
          <p><span>최대 손실률</span><b className={lossToneClass(mdd)}>{formatPercent(mdd)}</b></p>
          <p><span>점수</span><b>{formatNumber(score, 2)}</b></p>
        </div>
        <button onClick={() => onRunTest(normalizedBacktestDays)} disabled={isBusy}>{normalizedBacktestDays}일 백테스트 다시 실행</button>
      </RefPanel>
      <RefPanel className="ref-strategy-actions-panel">
        <button className="primary" onClick={onSave} disabled={isBusy}><Save size={18} />저장</button>
        <button onClick={onClone} disabled={isBusy || !candidate}><Copy size={18} />복제</button>
        <button className={toggleClass} onClick={onToggle} disabled={isBusy || !candidate}><PowerOff size={18} />{toggleLabel}</button>
        <button className="danger" onClick={onDelete} disabled={isBusy || !candidate}><Trash2 size={18} />삭제</button>
        <button className="blue" onClick={() => onRunTest(normalizedBacktestDays)} disabled={isBusy}><Play size={18} />실행 테스트</button>
        {(actionMessage || actionError) && <p className={actionError ? "ref-action-error" : "ref-action-message"}>{actionError ?? actionMessage}</p>}
      </RefPanel>
    </>
  );
}

function AnalysisView({ data }: { data: DashboardData }) {
  const latest = data.analysisLatest ?? data.analysisHistory[0] ?? null;
  const intent = latest?.order_intents?.[0] ?? null;
  const policyBlock = latestPolicyBlockNotice(data);
  const positiveReasons = latest?.positive_reasons?.length ? latest.positive_reasons : ["저장된 긍정 근거가 없습니다."];
  const negativeReasons = latest?.negative_reasons?.length ? latest.negative_reasons : ["저장된 부정 근거가 없습니다."];
  const blockers = latest?.blockers?.length ? latest.blockers : ["차단 사유 없음"];
  const signalEntries = compactEntries(latest?.internal_signals, 10);
  const externalProviders = compactEntries((latest?.external_factors?.providers as Record<string, unknown> | undefined) ?? {}, 8);
  const externalAdjustment = typeof latest?.external_factors?.target_adjustment_pct === "number" ? latest.external_factors.target_adjustment_pct : null;
  const externalRiskScore = typeof latest?.external_factors?.external_risk_score === "number" ? latest.external_factors.external_risk_score : null;
  const externalHardBlockers = Array.isArray(latest?.external_factors?.hard_blockers) ? latest.external_factors.hard_blockers.length : 0;
  const shadowSummary = data.shadowReport?.summary;
  const shadowRows = data.shadowReport?.recent_rows ?? [];
  const rehearsal = intent?.policy_preview?.rehearsal as { allowed?: boolean; blockers?: string[]; daily_smart_order_count?: number; risk_score?: number } | undefined;
  const noAveragingDownBlocked = Boolean(intent?.no_averaging_down_blocked ?? latest?.aggressive_blockers?.includes("SMART_AGGRESSIVE_NO_AVERAGING_DOWN"));
  const aggressiveBuyBlockers = latest?.aggressive_buy_blockers?.length ? latest.aggressive_buy_blockers : latest?.aggressive_blockers ?? [];
  const aggressiveWarnings = latest?.aggressive_warnings?.length ? latest.aggressive_warnings : [];

  if (!latest) {
    return (
      <RefPanel className="ref-analysis-empty">
        <h2>분석근거</h2>
        <p>아직 저장된 smart decision snapshot이 없습니다.</p>
        <span>자동매매 전략 틱이 한 번 실행되면 Shadow Mode 판단이 이곳에 표시됩니다.</span>
      </RefPanel>
    );
  }

  return (
    <>
      <RefPanel className="ref-analysis-hero">
        <span>Smart Engine · Shadow Mode</span>
        <h2>{latest.one_line_summary ?? "최근 판단 요약이 없습니다."}</h2>
        <p>최근 판단 {formatKstShort(latest.decided_at ?? latest.created_at)} · snapshot #{latest.id ?? "-"}</p>
      </RefPanel>
      <RefPanel className="ref-analysis-decision">
        <h3>현재 판단</h3>
        <strong>{latest.action_hint ?? "-"}</strong>
        <div>
          <p><span>시장상태</span><b>{latest.market_regime ?? "-"}</b></p>
          <p><span>기존 전략</span><b>{latest.legacy_signal ?? "-"}</b></p>
          <p><span>공격모드</span><b>{latest.attack_mode ?? "-"}</b></p>
          <p><span>공격점수</span><b>{formatNumber(latest.attack_score, 1)}</b></p>
          <p><span>확신도</span><b>{formatNumber(latest.confidence_score, 1)}</b></p>
          <p><span>위험점수</span><b>{formatNumber(latest.risk_score, 1)}</b></p>
        </div>
      </RefPanel>
      <RefPanel className="ref-analysis-exposure">
        <h3>보유비중</h3>
        <div className="ref-analysis-bars">
          <p><span>현재</span><b>{formatRatioPercent(latest.current_exposure_pct)}</b><i style={{ width: `${Math.min(Math.max(latest.current_exposure_pct ?? 0, 0), 100)}%` }} /></p>
          <p><span>보수형</span><b>{formatRatioPercent(latest.conservative_target_exposure_pct)}</b><i style={{ width: `${Math.min(Math.max(latest.conservative_target_exposure_pct ?? 0, 0), 100)}%` }} /></p>
          <p><span>공격형</span><b>{formatRatioPercent(latest.aggressive_target_exposure_pct)}</b><i style={{ width: `${Math.min(Math.max(latest.aggressive_target_exposure_pct ?? 0, 0), 100)}%` }} /></p>
          <p><span>코어 BTC</span><b>{formatRatioPercent(latest.core_exposure_pct)}</b><i style={{ width: `${Math.min(Math.max(latest.core_exposure_pct ?? 0, 0), 100)}%` }} /></p>
          <p><span>목표</span><b>{formatRatioPercent(latest.target_exposure_pct)}</b><i style={{ width: `${Math.min(Math.max(latest.target_exposure_pct ?? 0, 0), 100)}%` }} /></p>
        </div>
        <small>출처 {latest.final_target_exposure_source ?? "-"} · 포지션 수익률 {formatPercent((latest.current_position_pnl_pct ?? 0) / 100)} · {formatNumber(latest.current_bot_position_qty, 8)} BTC · {formatKrw(latest.current_bot_position_value_krw)} KRW</small>
      </RefPanel>
      <RefPanel className="ref-analysis-aggressive">
        <h3>공격형 관리</h3>
        <div>
          <p><span>피라미딩</span><b>{latest.pyramiding_allowed ? "허용 후보" : "비허용"}</b></p>
          <p><span>코어 적용</span><b>{latest.core_exposure_applied ? "적용" : "미적용"}</b></p>
          <p><span>패닉 core break</span><b>{latest.core_exposure_broken_by_panic ? "발동" : "미발동"}</b></p>
          <p><span>손실 물타기 차단</span><b>{noAveragingDownBlocked ? "차단" : "해당 없음"}</b></p>
          <p><span>부분익절</span><b>{latest.partial_take_profit_triggered ? "후보" : "대기"}</b></p>
          <p><span>트레일링 스탑</span><b>{latest.trailing_stop_price ? `${formatKrw(latest.trailing_stop_price)} KRW` : "-"}</b></p>
          <p><span>최고가</span><b>{latest.highest_price_since_entry ? `${formatKrw(latest.highest_price_since_entry)} KRW` : "-"}</b></p>
          <p><span>주문 출처</span><b>{intent?.target_source ?? latest.final_target_exposure_source ?? "-"}</b></p>
        </div>
      </RefPanel>
      <RefPanel className="ref-analysis-reasons">
        <h3>판단 근거</h3>
        <div>
          <section>
            <b>긍정</b>
            {positiveReasons.map((reason, index) => <p key={`positive-${index}`}>{reason}</p>)}
          </section>
          <section>
            <b>부정</b>
            {negativeReasons.map((reason, index) => <p key={`negative-${index}`}>{reason}</p>)}
          </section>
        </div>
      </RefPanel>
      <RefPanel className="ref-analysis-blockers">
        <h3>리스크 차단</h3>
        {policyBlock && (
          <div className="ref-analysis-policy-block">
            <span>최근 정책 차단</span>
            <b>{policyBlock.text}</b>
            <em>{formatKstShort(policyBlock.createdAt)} · {policyBlock.source === "risk" ? "주문 리스크" : "스마트 엔진"}</em>
            <PolicyBlockDetailGrid detail={policyBlock.detail} />
          </div>
        )}
        {blockers.map((blocker, index) => {
          const code = extractPolicyBlockCode(blocker);
          return <p key={`blocker-${index}`}>{code ? `정책 차단 · ${policyBlockText(code, blocker)}` : blocker}</p>;
        })}
        <h3>공격 매수 차단</h3>
        {(aggressiveBuyBlockers.length ? aggressiveBuyBlockers : ["차단 사유 없음"]).map((blocker, index) => <p key={`aggressive-buy-blocker-${index}`}>{blocker}</p>)}
        <h3>공격 경고</h3>
        {(aggressiveWarnings.length ? aggressiveWarnings : ["경고 없음"]).map((warning, index) => <p key={`aggressive-warning-${index}`}>{warning}</p>)}
      </RefPanel>
      <RefPanel className="ref-analysis-intent">
        <h3>주문 후보</h3>
        {intent ? (
          <div>
            <p><span>상태</span><b>{intent.status ?? "-"}</b></p>
            <p><span>방향</span><b>{intent.side ?? "-"}</b></p>
            <p><span>필요금액</span><b>{formatSignedKrw(intent.delta_value_krw)}</b></p>
            <p><span>목표금액</span><b>{formatKrw(intent.target_value_krw)}</b></p>
            <p><span>지정가</span><b>{formatKrw(intent.limit_price)}</b></p>
            <p><span>승격상태</span><b>{promotionStatusLabel(intent.promotion_status ?? data.smartEngineStatus?.promotion_status)}</b></p>
            <p><span>제한주문상한</span><b>{formatOrderLimit(intent.pilot_order_cap_krw)}</b></p>
            <p><span>공격점수</span><b>{formatNumber(intent.attack_score ?? latest.attack_score, 1)} · {intent.attack_mode ?? latest.attack_mode ?? "-"}</b></p>
            <p><span>익절/트레일링</span><b>{intent.partial_take_profit_pct ? `${formatNumber(intent.partial_take_profit_pct, 1)}% 익절` : intent.trailing_stop_price ? `${formatKrw(intent.trailing_stop_price)} KRW` : "-"}</b></p>
            <p><span>리허설</span><b>{rehearsal ? `${rehearsal.allowed ? "통과" : "차단"} · ${rehearsal.blockers?.[0] ?? "사유 없음"}` : "-"}</b></p>
          </div>
        ) : (
          <p>생성된 주문 후보가 없습니다.</p>
        )}
      </RefPanel>
      <RefPanel className="ref-analysis-features">
        <h3>내부 신호 / 외부요인</h3>
        <div>
          <p><span>external_adjustment</span><b>{externalAdjustment == null ? "-" : `${formatNumber(externalAdjustment, 2)}pt`}</b></p>
          <p><span>external_risk</span><b>{externalRiskScore == null ? "-" : `${formatNumber(externalRiskScore, 1)} · hard ${externalHardBlockers}`}</b></p>
          {signalEntries.map(([key, value]) => {
            const item = value as { direction?: string; score?: number; confidence?: number };
            return <p key={key}><span>{key}</span><b>{item.direction ?? "-"} · {formatNumber(item.score, 1)} · {formatRatioPercent(item.confidence, 0)}</b></p>;
          })}
          {externalProviders.slice(0, 4).map(([key, value]) => {
            const item = value as { value?: unknown; stale?: boolean; reason?: string };
            return <p key={`external-${key}`}><span>{key}</span><b>{item.stale ? "대기" : formatAnalysisValue(item.value)}</b></p>;
          })}
        </div>
      </RefPanel>
      <RefPanel className="ref-analysis-shadow-report">
        <h3>Shadow 성과 리포트</h3>
        <div>
          <p><span>승격 준비도</span><b>{formatRatioPercent(shadowSummary?.readiness_score, 1)}</b></p>
          <p><span>추천</span><b>{shadowRecommendationLabel(shadowSummary?.recommendation)}</b></p>
          <p><span>판단/주문후보</span><b>{formatNumber(shadowSummary?.decision_count, 0)} / {formatNumber(shadowSummary?.intent_count, 0)}</b></p>
          <p><span>평가/유리</span><b>{formatNumber(shadowSummary?.evaluated_count, 0)} / {formatNumber(shadowSummary?.favorable_count, 0)}</b></p>
          <p><span>방향 적중률</span><b>{formatRatioPercent(shadowSummary?.directional_win_rate, 1)}</b></p>
          <p><span>평균 Markout</span><b>{formatPercent((shadowSummary?.average_markout_pct ?? 0) / 100)}</b></p>
          <p><span>하드 차단</span><b>{formatNumber(shadowSummary?.hard_block_count, 0)}건</b></p>
          <p><span>정책 차단</span><b>{formatNumber(shadowSummary?.policy_block_count, 0)}건</b></p>
        </div>
        <section>
          {shadowRows.slice(0, 4).map((row) => (
            <p key={row.decision_id ?? row.decided_at}>
              <span>{formatKstShort(row.decided_at)}</span>
              <b>{row.action_hint ?? "-"}</b>
              <em>{shadowOutcomeLabel(row.outcome)}</em>
              <i className={(row.markout_pct ?? 0) >= 0 ? "ref-positive" : "ref-negative"}>{formatPercent((row.markout_pct ?? 0) / 100)}</i>
            </p>
          ))}
          {shadowRows.length === 0 && <p><span>-</span><b>데이터 없음</b><em>-</em><i>-</i></p>}
        </section>
      </RefPanel>
      <RefPanel className="ref-analysis-history">
        <h3>최근 판단 히스토리</h3>
        <div>
          {data.analysisHistory.slice(0, 10).map((item) => (
            <p key={item.id ?? item.created_at}>
              <span>{formatKstShort(item.decided_at ?? item.created_at)}</span>
              <b>{item.action_hint ?? "-"}</b>
              <em>{item.market_regime ?? "-"}</em>
              <i>{formatRatioPercent(item.target_exposure_pct)}</i>
            </p>
          ))}
        </div>
      </RefPanel>
    </>
  );
}

function StrategiesView({ data, refresh }: { data: DashboardData; refresh: () => Promise<void> }) {
  const [selectedId, setSelectedId] = React.useState<number | null>(null);
  const [filter, setFilter] = React.useState<StrategyFilter>("ALL");
  const [search, setSearch] = React.useState("");
  const [draft, setDraft] = React.useState<StrategyDraft>(() => strategyDraftFromCandidate(null));
  const [backtestDays, setBacktestDays] = React.useState(30);
  const [isBusy, setIsBusy] = React.useState(false);
  const [actionMessage, setActionMessage] = React.useState<string | null>(null);
  const [actionError, setActionError] = React.useState<string | null>(null);
  const [validationResult, setValidationResult] = React.useState<StrategyValidationResult | null>(null);

  React.useEffect(() => {
    if (data.candidates.length === 0) {
      setSelectedId(null);
      return;
    }
    if (selectedId == null || !data.candidates.some((candidate) => candidate.id === selectedId)) {
      setSelectedId(data.candidates[0].id);
    }
  }, [data.candidates, selectedId]);

  const selected = data.candidates.find((candidate) => candidate.id === selectedId) ?? data.candidates[0] ?? null;
  React.useEffect(() => {
    setDraft(strategyDraftFromCandidate(selected));
    setActionMessage(null);
    setActionError(null);
    setValidationResult(null);
  }, [selected?.id]);

  const runAction = async (label: string, action: () => Promise<number | null | void>) => {
    if (isBusy) return;
    setIsBusy(true);
    setActionError(null);
    setActionMessage(`${label} 처리 중`);
    try {
      const nextSelectedId = await action();
      await refresh();
      if (typeof nextSelectedId === "number") setSelectedId(nextSelectedId);
      setActionMessage(`${label} 완료`);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : `${label} 실패`);
      setActionMessage(null);
    } finally {
      setIsBusy(false);
    }
  };

  const createStrategy = () => runAction("새 전략 생성", async () => {
    const payload = buildCandidatePayload(strategyDraftFromCandidate(null), null);
    const created = await postJson<Candidate>("/api/candidate-strategies", payload);
    setFilter("ALL");
    setSearch("");
    return created.id;
  });

  const saveStrategy = () => runAction(selected ? "전략 저장" : "전략 생성", async () => {
    const payload = buildCandidatePayload(draft, selected);
    if (!selected) {
      const created = await postJson<Candidate>("/api/candidate-strategies", payload);
      return created.id;
    }
    const result = await patchJson<{ candidate: Candidate }>(`/api/candidate-strategies/${selected.id}`, payload);
    return result.candidate.id;
  });

  const cloneStrategy = () => runAction("전략 복제", async () => {
    if (!selected) throw new Error("복제할 전략이 없습니다.");
    const result = await postJson<{ candidate: Candidate }>(`/api/candidate-strategies/${selected.id}/clone`);
    setFilter("ALL");
    return result.candidate.id;
  });

  const toggleStrategy = () => runAction(draft.status === "ACTIVE" ? "전략 비활성화" : "전략 활성화", async () => {
    if (!selected) throw new Error("상태를 변경할 전략이 없습니다.");
    const nextStatus = draft.status === "ACTIVE" ? "INACTIVE" : "ACTIVE";
    const result = await postJson<{ candidate: Candidate }>(`/api/candidate-strategies/${selected.id}/toggle`, { status: nextStatus });
    setDraft(strategyDraftFromCandidate(result.candidate));
    return result.candidate.id;
  });

  const deleteStrategy = () => runAction("전략 삭제", async () => {
    if (!selected) throw new Error("삭제할 전략이 없습니다.");
    const ok = window.confirm(`${cleanStrategyName(selected)} 전략을 삭제할까요? 실행 이력이 있는 전략은 삭제되지 않습니다.`);
    if (!ok) return selected.id;
    await deleteJson<{ ok: boolean; deleted_id: number }>(`/api/candidate-strategies/${selected.id}`);
    setSelectedId(null);
    setFilter("ALL");
    return null;
  });

  const runStrategyTest = (days = backtestDays) => runAction("전략 검증", async () => {
    const normalizedDays = Math.max(1, Math.min(365, Math.round(days || 30)));
    const result = await postJson<StrategyValidationResult>("/api/strategy-validation/run", {
      market: draft.market,
      strategy: draft.strategy,
      timeframes: [draft.unit],
      periods: [`${normalizedDays}d`],
      settings: draft.parameters,
      risk: {}
    });
    setValidationResult(result);
    const bestRow = result.rows?.find((row) => row.unit === draft.unit) ?? result.rows?.[0];
    if (selected && bestRow?.metrics) {
      const metrics = bestRow.metrics;
      await patchJson<{ candidate: Candidate }>(`/api/candidate-strategies/${selected.id}`, {
        ...buildCandidatePayload(draft, selected),
        backtest_period: bestRow.period_label ?? `${normalizedDays}d`,
        score: metrics.score ?? selected.score ?? 0,
        backtest_total_return: metrics.total_return ?? selected.backtest_total_return ?? 0,
        backtest_mdd: metrics.mdd ?? selected.backtest_mdd ?? 0,
        backtest_win_rate: metrics.win_rate ?? selected.backtest_win_rate ?? 0,
        backtest_profit_factor: metrics.profit_factor ?? selected.backtest_profit_factor ?? 0,
        backtest_trade_count: metrics.trade_count ?? selected.backtest_trade_count ?? 0,
        backtest_average_trade_pnl: metrics.trade_count ? (metrics.total_return ?? 0) / metrics.trade_count : selected.backtest_average_trade_pnl ?? 0
      });
    }
    return selected?.id ?? null;
  });

  return (
    <>
      <StrategyListPanel
        candidates={data.candidates}
        selectedId={selected?.id ?? null}
        onSelect={setSelectedId}
        filter={filter}
        onFilterChange={setFilter}
        search={search}
        onSearchChange={setSearch}
        onCreate={createStrategy}
        isBusy={isBusy}
      />
      <StrategyEditorPanel draft={draft} selected={selected} onDraftChange={setDraft} />
      <StrategyRightPanels
        data={data}
        candidate={selected}
        draft={draft}
        backtestDays={backtestDays}
        isBusy={isBusy}
        actionMessage={actionMessage}
        actionError={actionError}
        validationResult={validationResult}
        onBacktestDaysChange={setBacktestDays}
        onSave={saveStrategy}
        onClone={cloneStrategy}
        onToggle={toggleStrategy}
        onDelete={deleteStrategy}
        onRunTest={runStrategyTest}
      />
    </>
  );
}

function DashboardView({
  data,
  scale,
  totalEquity,
  totalPnl,
  totalReturn,
  onOpenAutoTrade,
  chartUnit,
  onChartUnitChange
}: {
  data: DashboardData;
  scale: number;
  totalEquity?: number | null;
  totalPnl?: number | null;
  totalReturn?: number | null;
  onOpenAutoTrade: () => void;
  chartUnit: number;
  onChartUnitChange: (unit: number) => void;
}) {
  const policy = data.botPolicy;
  const policyState = policy?.auto_trading_enabled ? "정책 ON" : "정책 OFF";
  return (
    <>
      <KpiCard className="kpi-asset" icon={<Wallet size={28} />} label="총 자산 (KRW)" value={formatKrw(totalEquity)} sub={data.liveBalances?.balance_fetch_status === "FAILED" ? "실잔고 조회 실패" : formatAssetSub(totalEquity)} />
      <KpiCard className="kpi-profit" icon={<LineChart size={28} />} label="총 수익 (KRW)" value={formatSignedKrw(totalPnl)} sub={formatPercent(totalReturn)} tone="cyan" />
      <KpiCard className="kpi-return" icon={<PieChart size={28} />} label="누적 수익률" value={formatPercent(totalReturn)} sub={formatSignedKrw(totalPnl)} tone="green" />
      <KpiCard className="kpi-strategy" icon={<Bot size={28} />} label="최대 투입 금액" value={formatKrw(policy?.max_total_exposure_krw)} sub={`${policyState} · 손실한도 ${formatRatioPercent(policy?.daily_loss_limit_pct, 1)}`} tone="amber" />
      <KpiCard className="kpi-win" icon={<Crosshair size={30} />} label="정책 사용률" value={formatRatioPercent(policy?.exposure_usage_pct, 1)} sub={`${formatKrw(policy?.current_bot_position_value_krw)} KRW 사용`} tone="red" />
      <MainChartPanel data={data} stageScale={scale} chartUnit={chartUnit} onChartUnitChange={onChartUnitChange} />
      <BotStatusPanel data={data} onOpenAutoTrade={onOpenAutoTrade} />
      <PositionPanel data={data} />
      <SignalPanel data={data} />
      <PortfolioPanel data={data} totalEquity={totalEquity} />
      <RecentTradesPanel data={data} />
      <LogPanel data={data} />
    </>
  );
}

function LoginScreen({
  auth,
  onAuthenticated
}: {
  auth: AuthStatus | null;
  onAuthenticated: (status: AuthStatus) => void;
}) {
  const [username, setUsername] = React.useState("admin");
  const [password, setPassword] = React.useState("");
  const [message, setMessage] = React.useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = React.useState(false);

  const submit = React.useCallback(async (event: React.FormEvent) => {
    event.preventDefault();
    setIsSubmitting(true);
    setMessage(null);
    try {
      const status = await postJson<AuthStatus & { ok?: boolean }>("/api/auth/login", { username, password });
      onAuthenticated(status);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "로그인 실패");
    } finally {
      setIsSubmitting(false);
    }
  }, [onAuthenticated, password, username]);

  return (
    <main className="ref-login-page">
      <form className="ref-login-card" onSubmit={submit}>
        <span className="ref-logo">Q</span>
        <h1>Auto Trader</h1>
        <p>관리자 로그인 후 대시보드와 실거래 제어를 사용할 수 있습니다.</p>
        {!auth?.auth_configured && auth?.auth_required && (
          <em>ADMIN_PASSWORD_HASH와 SESSION_SECRET 설정이 필요합니다.</em>
        )}
        <label>
          <span>아이디</span>
          <input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" />
        </label>
        <label>
          <span>비밀번호</span>
          <input value={password} onChange={(event) => setPassword(event.target.value)} type="password" autoComplete="current-password" />
        </label>
        {message && <strong>{message}</strong>}
        <button type="submit" disabled={isSubmitting || (auth?.auth_required && !auth?.auth_configured)}>
          {isSubmitting ? "확인 중" : "로그인"}
        </button>
      </form>
    </main>
  );
}

function ReferenceDashboardContent({ onLogout }: { onLogout: () => Promise<void> }) {
  const scale = useStageScale();
  const [chartUnit, setChartUnit] = React.useState(CHART_UNIT);
  const [selectedExchange, setSelectedExchange] = React.useState<DashboardExchange>("bithumb");
  const { data, refresh } = useDashboardData(chartUnit, selectedExchange);
  const [activeView, setActiveView] = React.useState<ReferenceView>("dashboard");
  const [isAutoToggling, setIsAutoToggling] = React.useState(false);
  const [autoToggleError, setAutoToggleError] = React.useState<string | null>(null);
  const [isImportingPosition, setIsImportingPosition] = React.useState(false);
  const [importPositionError, setImportPositionError] = React.useState<string | null>(null);
  const scaledWidth = STAGE_WIDTH * scale;
  const scaledHeight = STAGE_HEIGHT * scale;
  const liveAccount = liveAccountPerformance(data);
  const liveEquity = data.liveBalances?.balance_fetch_status === "SUCCESS" ? data.liveBalances?.estimated_total_equity_krw : null;
  const paperEquity = data.paper?.balance?.equity ?? data.forward?.balance?.equity ?? null;
  const totalEquity = liveEquity ?? paperEquity;
  const totalPnl = liveAccount?.totalPnl ?? data.paper?.balance?.total_pnl ?? data.forward?.balance?.total_pnl ?? data.risk?.risk_state?.daily_total_pnl ?? null;
  const totalReturn = liveAccount?.totalReturn ?? data.paper?.balance?.total_return ?? data.forward?.balance?.total_return ?? null;
  const isAutoTradingOn = isRuntimeRunning(data);

  const toggleAutoTrading = React.useCallback(async () => {
    if (isAutoToggling) return;
    setIsAutoToggling(true);
    setAutoToggleError(null);
    try {
      if (isAutoTradingOn) {
        await postJson<RuntimeStatus & { ok?: boolean; message?: string }>("/api/runtime/stop");
      } else {
        const confirmation = window.prompt(`Smart Autonomous Live를 시작하려면 확인 문구를 입력하세요: ${AUTO_TRADING_CONFIRMATION}`)?.trim();
        if (confirmation !== AUTO_TRADING_CONFIRMATION) throw new Error("확인 문구가 일치하지 않아 자동매매 시작을 취소했습니다.");
        const body = await postJson<RuntimeStatus & { ok?: boolean; message?: string }>("/api/runtime/start", {
          confirmation,
          order_confirmation: "PLACE AUTO LIVE ORDER"
        });
        if (body.ok === false) throw new Error(body.message ?? "자동매매 시작이 차단되었습니다.");
      }
      await refresh();
    } catch (err) {
      setAutoToggleError(err instanceof Error ? err.message : "자동매매 상태 변경 실패");
      await refresh();
    } finally {
      setIsAutoToggling(false);
    }
  }, [isAutoToggling, isAutoTradingOn, refresh]);

  const importExchangePosition = React.useCallback(async () => {
    if (isImportingPosition) return;
    const confirmation = window.prompt("거래소 BTC 잔고를 봇 포지션으로 가져오려면 확인 문구를 입력하세요: IMPORT BTC POSITION")?.trim();
    if (confirmation !== "IMPORT BTC POSITION") {
      setImportPositionError("확인 문구가 일치하지 않아 편입을 취소했습니다.");
      return;
    }
    setIsImportingPosition(true);
    setImportPositionError(null);
    try {
      const body = await postJson<{ ok?: boolean; status?: string; message?: string }>("/api/live-recovery/import-exchange-position?exchange=bithumb", { confirmation });
      if (body.ok === false) throw new Error(body.message ?? body.status ?? "거래소 잔고 편입 실패");
      await refresh();
    } catch (err) {
      setImportPositionError(err instanceof Error ? err.message : "거래소 잔고 편입 실패");
      await refresh();
    } finally {
      setIsImportingPosition(false);
    }
  }, [isImportingPosition, refresh]);

  return (
    <main className="ref-viewport" style={{ ["--ref-scale" as string]: scale }}>
      <div className="ref-stage-shell" style={{ width: scaledWidth, height: scaledHeight }}>
        <div className="ref-stage">
          <Topbar data={data} selectedExchange={selectedExchange} onExchangeChange={setSelectedExchange} onRefresh={refresh} onLogout={onLogout} />
          <Sidebar activeView={activeView} onViewChange={setActiveView} />
          {activeView === "dashboard" && (
            <DashboardView
              data={data}
              scale={scale}
              totalEquity={totalEquity}
              totalPnl={totalPnl}
              totalReturn={totalReturn}
              onOpenAutoTrade={() => setActiveView("auto-trade")}
              chartUnit={chartUnit}
              onChartUnitChange={setChartUnit}
            />
          )}
          {activeView === "auto-trade" && (
            <AutoTradeView
              data={data}
              onToggleAutoTrading={toggleAutoTrading}
              onImportExchangePosition={importExchangePosition}
              isAutoToggling={isAutoToggling}
              autoToggleError={autoToggleError}
              isImportingPosition={isImportingPosition}
              importPositionError={importPositionError}
            />
          )}
          {activeView === "analysis" && <AnalysisView data={data} />}
          {activeView === "operations" && <OperationsView data={data} refresh={refresh} />}
          {activeView === "portfolio" && <PortfolioView data={data} totalEquity={totalEquity} totalPnl={totalPnl} totalReturn={totalReturn} onSimulate={() => setActiveView("operations")} />}
          {activeView === "trades" && <TradeHistoryView data={data} refresh={refresh} />}
          {activeView === "backtest" && <BacktestValidationView exchange={selectedExchange} />}
          {activeView === "alerts" && <AlertsView data={data} />}
        </div>
      </div>
    </main>
  );
}

export function ReferenceDashboard() {
  const [auth, setAuth] = React.useState<AuthStatus | null>(null);
  const [loading, setLoading] = React.useState(true);

  const loadAuth = React.useCallback(async () => {
    setLoading(true);
    try {
      const status = await fetchJson<AuthStatus>("/api/auth/status");
      setAuth(status);
    } catch {
      setAuth({ auth_required: true, auth_configured: false, authenticated: false });
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void loadAuth();
  }, [loadAuth]);

  const logout = React.useCallback(async () => {
    await postJson("/api/auth/logout");
    setAuth((current) => ({ ...(current ?? { auth_required: true, auth_configured: true }), authenticated: false }));
  }, []);

  if (loading) {
    return (
      <main className="ref-login-page">
        <div className="ref-login-card">
          <span className="ref-logo">Q</span>
          <h1>Auto Trader</h1>
          <p>인증 상태를 확인하는 중입니다.</p>
        </div>
      </main>
    );
  }

  if (auth?.auth_required && !auth.authenticated) {
    return <LoginScreen auth={auth} onAuthenticated={setAuth} />;
  }

  return <ReferenceDashboardContent onLogout={logout} />;
}
