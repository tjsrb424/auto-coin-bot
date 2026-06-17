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
  Save,
  Search,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Target,
  TrendingUp,
  Wallet
} from "lucide-react";

const STAGE_WIDTH = 1672;
const STAGE_HEIGHT = 941;
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";
const MARKET = "KRW-BTC";
const CHART_UNIT = 15;
const CHART_TIMEFRAMES = [
  { label: "1m", unit: 1, disabled: false },
  { label: "15m", unit: 15, disabled: false },
  { label: "1h", unit: 60, disabled: false },
  { label: "4h", unit: 240, disabled: false },
  { label: "1D", unit: 1440, disabled: false }
] as const;

type Tone = "purple" | "cyan" | "green" | "amber" | "red";

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
  balance?: number;
  locked?: number;
  avg_buy_price?: number;
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
  prices?: Record<string, { price?: number }>;
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
  };
  risk_logs?: Array<{
    id?: number;
    risk_level?: string;
    allowed?: boolean;
    block_code?: string | null;
    block_reason?: string | null;
    read_status?: string | null;
    created_at?: string;
  }>;
};

type AutoPilotStatus = {
  session?: {
    created_at?: string;
    stopped_at?: string | null;
    status?: string;
    strategy_name?: string;
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
  errors: string[];
  updatedAt: string | null;
};

type ReferenceView = "dashboard" | "auto-trade" | "strategies";

const navItems = [
  { id: "dashboard", label: "대시보드", icon: Home },
  { id: "auto-trade", label: "자동매매", icon: Bot },
  { id: "strategies", label: "전략관리", icon: ClipboardList },
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
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `${path} ${response.status}`);
  }
  return response.json() as Promise<T>;
}

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
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
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail ?? payload.message ?? `${path} ${response.status}`);
  }
  return payload as T;
}

function useDashboardData(chartUnit: number) {
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

      const [candlesResult, status, ordersResult, paper, forward, candidatesResult, autoPilot, liveStrategy] = await Promise.all([
        settle("캔들", fetchJson<{ candles?: Candle[]; unit?: number }>(`/api/candles?market=${MARKET}&unit=${chartUnit}&count=120`)),
        settle("실거래 상태", fetchJson<LiveStatus>("/api/live/status")),
        settle("주문", fetchJson<{ orders?: LiveOrder[] }>("/api/live-orders")),
        settle("실시간 페이퍼", fetchJson<PaperSession>("/api/paper-trading/live/latest")),
        settle("Forward Paper", fetchJson<PaperSession>("/api/forward-paper/latest")),
        settle("전략", fetchJson<{ candidates?: Candidate[] }>("/api/candidate-strategies")),
        settle("자동매매", fetchJson<AutoPilotStatus>("/api/auto-live-pilot/status")),
        settle("전략 파일럿", fetchJson<LiveStrategyStatus>("/api/live-strategy-pilot/status"))
      ]);

      const exchange = status?.exchange ?? "bithumb";
      const [balances, risk] = await Promise.all([
        settle("잔고", fetchJson<LiveBalances>(`/api/live/balances?exchange=${exchange}`)),
        settle("리스크", fetchJson<RiskDashboard>(`/api/risk/status?exchange=${exchange === "bithumb" ? "bithumb" : "bithumb"}`))
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
        errors,
        updatedAt: new Date().toISOString()
      });
  }, [chartUnit]);

  React.useEffect(() => {
    let cancelled = false;

    const guardedRefresh = async () => {
      if (!cancelled) await refresh();
    };

    void guardedRefresh();
    const intervalId = window.setInterval(() => void guardedRefresh(), 30_000);
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
    BLOCKED_OPEN_POSITION_EXISTS: "포지션 있음",
    BLOCKED_DUPLICATE_SIGNAL: "중복 신호",
    BLOCKED_DUPLICATE_CANDLE: "중복 캔들",
    BLOCKED_INSUFFICIENT_BALANCE: "잔고 부족",
    BLOCKED_ORDER_CHANCE_FAILED: "주문 보류",
    BLOCKED_API_RESPONSE_ERROR: "API 오류",
    BLOCKED_RISK_LIMIT: "리스크 차단",
    ALREADY_FILLED: "이미 체결",
    INSUFFICIENT_BALANCE: "잔고 부족"
  };
  return labels[normalized] ?? reasonLabels[normalized] ?? value
    .replace(/^BLOCKED_/, "차단: ")
    .replace(/^WAITING_/, "대기: ")
    .replace(/^ORDER_/, "주문 ")
    .replace(/_/g, " ")
    .toLowerCase();
}

function statusTone(value?: string | null): "green" | "amber" | "red" | "cyan" | "neutral" {
  if (!value) return "neutral";
  const normalized = String(value).toUpperCase();
  if (["OK", "READY", "RUNNING", "FILLED", "SUBMITTED", "ACTIVE"].includes(normalized)) return "green";
  if (["WARNING", "WAITING", "PENDING", "LIVE_PAUSED", "PAUSED", "READY_READ_ONLY", "LIVE_ARMED", "MANUAL_REVIEW_REQUIRED"].includes(normalized)) return "amber";
  if (["BLOCKED", "FAILED", "ERROR", "EMERGENCY_STOPPED", "LIVE_DISABLED", "CANCELED", "CANCELLED", "STOPPED", "INACTIVE"].includes(normalized) || normalized.startsWith("BLOCKED_")) return "red";
  return "neutral";
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

function autoRuntimeMs(data: DashboardData, now: number) {
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

function exchangeLabel(exchange?: string) {
  if (exchange === "bithumb") return "빗썸 (Bithumb)";
  if (exchange === "upbit") return "업비트 (Upbit)";
  return "-";
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

function Topbar({ data }: { data: DashboardData }) {
  const alertCount = data.risk?.risk_logs?.filter((log) => !log.allowed && log.read_status !== "READ").length ?? 0;
  const accountTone = accountStateTone(data.liveStatus);
  const kstTime = useKstClock();
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
          <select value={data.liveStatus?.exchange ?? ""} onChange={() => undefined}>
            <option value={data.liveStatus?.exchange ?? ""}>{exchangeLabel(data.liveStatus?.exchange)}</option>
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
        <CircleUserRound className="ref-user" size={30} />
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
          const isImplemented = item.id === "dashboard" || item.id === "auto-trade" || item.id === "strategies";
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
  const indicators = computeIndicators(data.candles);
  const change = displayCandle && previous ? displayCandle.trade_price - previous.trade_price : null;
  const changeRate = displayCandle && previous ? change! / previous.trade_price : null;
  const marketTone = change == null ? "" : change < 0 ? "ref-negative" : change > 0 ? "ref-positive" : "";
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
          <span className={marketTone}><b>{formatPercent(changeRate)}</b><b>{formatSignedKrw(change)}</b></span>
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
        <strong className={marketTone}>{formatSignedKrw(change)} ({formatPercent(changeRate)})</strong>
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
  const autoRunning = isRunning(data.autoPilot?.session?.status) || isRunning(data.liveStrategy?.session?.status);
  const riskState = data.risk?.risk_state;
  const dailyPnl = riskState?.daily_total_pnl ?? data.paper?.balance?.total_pnl ?? null;
  const strategyName = data.liveStrategy?.session?.strategy_name ?? data.autoPilot?.session?.strategy_name ?? data.candidates[0]?.name ?? "-";
  const ordersToday = data.liveStrategy?.session?.orders_created_today ?? data.autoPilot?.session?.orders_created_today;
  const maxOrders = data.liveStrategy?.session?.max_orders_per_day ?? data.autoPilot?.session?.max_orders_per_day;
  const runtimeState = botRuntimeState(data);

  return (
    <RefPanel className="ref-bot-panel">
      <div className="ref-panel-title">
        <b>봇 상태</b>
        <span className={`ref-auto-state ${autoRunning ? "is-on" : "is-off"}`}>자동매매 {autoRunning ? "ON" : "OFF"} <Power size={18} /></span>
      </div>
      <div className="ref-bot-body">
        <div className={`ref-bot-face ${autoRunning ? "is-running" : "is-paused"}`}><Bot size={48} /></div>
        <div className="ref-bot-metrics">
          <p><span>현재 상태</span><b className={runtimeState === "RUNNING" ? "ref-positive" : runtimeState === "STOPPED" || runtimeState === "LIVE_DISABLED" ? "ref-negative" : ""}>{statusLabel(runtimeState)}</b></p>
          <p><span>일일 수익</span><strong>{formatSignedKrw(dailyPnl)}</strong></p>
          <p><span>주문 (오늘)</span><b>{ordersToday == null || maxOrders == null ? "-" : `${ordersToday} / ${maxOrders}`}</b></p>
          <p><span>현재 전략</span><b>{strategyName}</b></p>
        </div>
      </div>
      <button className="ref-detail-button" onClick={onOpenAutoTrade}>상세 보기 <ChevronRight size={16} /></button>
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

function PortfolioPanel({ data }: { data: DashboardData }) {
  const balanceOk = data.liveBalances?.balance_fetch_status === "SUCCESS";
  const btcPrice = latestCandle(data.candles)?.trade_price ?? 0;
  const krwBalance = data.liveBalances?.balances?.krw ?? data.liveBalances?.balances?.by_currency?.KRW;
  const btcBalance = data.liveBalances?.balances?.btc ?? data.liveBalances?.balances?.by_currency?.BTC;
  const krw = balanceOk ? balanceAmount(krwBalance) : null;
  const btc = balanceOk ? balanceAmount(btcBalance) : null;
  const btcValue = btc == null ? null : btc * (data.liveBalances?.prices?.[MARKET]?.price ?? btcPrice);
  const paperEquity = data.paper?.balance?.equity ?? data.forward?.balance?.equity ?? null;
  const paperBtcValue = (data.paper?.position?.market_value ?? 0) || (data.paper?.position?.btc_quantity ?? 0) * (data.paper?.balance?.current_price ?? btcPrice);
  const total = balanceOk ? data.liveBalances?.estimated_total_equity_krw ?? null : paperEquity;
  const rows = balanceOk
    ? [
      ["KRW", krw ?? 0],
      ["BTC", btcValue ?? 0]
    ]
    : [
      ["현금", data.paper?.balance?.cash_krw ?? null],
      ["BTC", paperBtcValue || null]
    ];
  const filteredRows = rows.filter(([, value]) => value != null && Number(value) > 0);
  const displayRows = filteredRows.length ? filteredRows : [["데이터 없음", null]];

  return (
    <RefPanel className="ref-portfolio-panel">
      <h3>포트폴리오 비중</h3>
      <div className="ref-portfolio-body">
        <div className="ref-donut">
          <div><span>총 자산</span><b>{formatKrw(total)}</b><em>KRW</em></div>
        </div>
        <div className="ref-legend">
          {displayRows.map(([name, value], index) => {
            const percent = total && typeof value === "number" ? (value / total) * 100 : null;
            return (
              <p key={name}>
                <i className={`c${index}`} />
                <span>{name}</span>
                <b>{percent == null ? "-" : `${percent.toFixed(1)}%`}</b>
                <em>{typeof value === "number" ? `${formatKrw(value)} KRW` : "-"}</em>
              </p>
            );
          })}
        </div>
      </div>
    </RefPanel>
  );
}

function RecentTradesPanel({ data }: { data: DashboardData }) {
  const rows = data.liveOrders.slice(0, 5);
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
                <td><span className={status === "FILLED" || status === "SUBMITTED" ? "ref-done" : "ref-holding"}>{statusLabel(status)}</span></td>
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
  const riskLogs = (data.risk?.risk_logs ?? []).slice(0, 4).map((log) => ({
    type: log.allowed ? "ok" : "warn",
    time: formatKstTime(log.created_at),
    text: log.block_reason ?? log.block_code ?? log.risk_level ?? "리스크 로그"
  }));
  const orderLogs = data.liveOrders.slice(0, 2).map((order) => ({
    type: order.status === "BLOCKED" || order.status === "FAILED" ? "warn" : "info",
    time: formatKstTime(order.created_at),
    text: `${marketDisplay(order.market)} ${statusLabel(order.side)} ${statusLabel(order.status)}`
  }));
  const rows = [...riskLogs, ...orderLogs].slice(0, 6);

  return (
    <RefPanel className="ref-log-panel">
      <div className="ref-title-row">
        <h3>시스템 로그</h3>
        <button>전체⌄</button>
      </div>
      <div className="ref-log-list">
        {rows.length === 0 && <p><span>-</span><i className="info" />로그 데이터 없음</p>}
        {rows.map((row, index) => (
          <p key={`${row.time}-${row.text}-${index}`}><span>{row.time}</span><i className={row.type} />{row.text}</p>
        ))}
      </div>
    </RefPanel>
  );
}

function RefStatusBadge({ value, tone = "green" }: { value: string; tone?: "green" | "amber" | "red" | "cyan" | "neutral" }) {
  return <span className={`ref-status-badge ${tone}`}>{value}</span>;
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
  const autoRunning = isRunning(data.liveStrategy?.session?.status) || isRunning(data.autoPilot?.session?.status);
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
  const runtimeText = formatRuntimeDuration(autoRuntimeMs(data, now));

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
          <span>{autoRunning ? "가동 시간" : "최근 가동"} {runtimeText}</span>
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

function AutoStrategyStrip({ data }: { data: DashboardData }) {
  const autoRunning = isRunning(data.liveStrategy?.session?.status) || isRunning(data.autoPilot?.session?.status);
  const candidates = data.candidates.slice(0, 5);
  const placeholders = Array.from({ length: Math.max(5 - candidates.length, 0) }, (_, index) => ({
    id: -(index + 1),
    name: "전략 없음",
    strategy: "-",
    market: "-",
    status: "INACTIVE"
  } as Candidate));
  const cards = [...candidates, ...placeholders].slice(0, 5);

  return (
    <section className="ref-auto-strategy-section">
      <div className="ref-auto-section-title">
        <b>실행 중인 전략</b>
        <em>{autoRunning ? `${Math.max(1, candidates.filter((candidate) => candidate.status === "ACTIVE").length)}개 실행 중` : "대기 중"}</em>
        <button>전체 전략 보기 <ChevronRight size={16} /></button>
      </div>
      <div className="ref-auto-strategy-grid">
        {cards.map((candidate, index) => {
          const active = autoRunning && index < 3 && candidate.id > 0;
          return (
            <RefPanel key={`${candidate.id}-${candidate.name}-${index}`} className="ref-auto-strategy-card">
              <div className="ref-auto-card-head">
                <strong>{candidate.name || strategyLabel(candidate.strategy)}</strong>
                <RefStatusBadge value={active ? "실행 중" : "대기"} tone={active ? "green" : "amber"} />
                <button>상세</button>
              </div>
              <p>{marketDisplay(candidate.market)} · {strategyLabel(candidate.strategy)}</p>
              <div className="ref-auto-card-bottom">
                <span>수익</span>
                <b className={(candidate.backtest_total_return ?? 0) < 0 ? "ref-negative" : "ref-positive"}>{formatPercent(candidate.backtest_total_return)}</b>
                <span>포지션</span>
                <b>{active ? `${index + 2} / 5` : "-"}</b>
                <i className="ref-auto-spark" />
              </div>
            </RefPanel>
          );
        })}
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

function AutoRiskPanel({ data }: { data: DashboardData }) {
  const risk = data.risk?.risk_state;
  const dailyLoss = Math.abs(risk?.daily_loss_percent ?? 0);
  const maxOrder = data.liveStrategy?.max_order_krw ?? null;
  return (
    <RefPanel className="ref-auto-risk-panel">
      <div className="ref-title-row"><h3>리스크 관리</h3></div>
      <div className="ref-risk-ok"><ShieldCheck size={26} /><b>{risk?.status === "OK" ? "리스크 양호" : statusLabel(risk?.status ?? "WAITING")}</b></div>
      <div className="ref-auto-risk-row"><span>개정 리스크</span><b>{dailyLoss.toFixed(0)}% / 30%</b></div>
      <div className="ref-auto-bar"><i style={{ width: `${Math.min(dailyLoss / 30 * 100, 100)}%` }} /></div>
      <div className="ref-auto-risk-row"><span>일일 손익 한도</span><b>{formatKrw(maxOrder)}</b></div>
      <div className="ref-auto-bar"><i style={{ width: "38%" }} /></div>
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
      type: order.order_type ?? order.side ?? "-",
      price: formatKrw(order.price),
      status: order.status ?? "-"
    })),
    ...data.risk?.risk_logs?.slice(0, 2).map((log) => ({
      time: formatKstTime(log.created_at),
      strategy: "Risk",
      market: "-",
      type: statusLabel(log.block_code ?? log.risk_level ?? "-"),
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
            {logs.map((row, index) => <tr key={`${row.time}-${index}`}><td>{row.time}</td><td>{row.strategy}</td><td>{row.market}</td><td>{statusLabel(row.type)}</td><td>{row.price}</td><td><RefStatusBadge value={statusLabel(row.status)} tone={statusTone(row.status)} /></td></tr>)}
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
  isAutoToggling,
  autoToggleError
}: {
  data: DashboardData;
  onToggleAutoTrading: () => void;
  isAutoToggling: boolean;
  autoToggleError: string | null;
}) {
  return (
    <>
      <AutoStatusPanel data={data} onToggle={onToggleAutoTrading} isToggling={isAutoToggling} toggleError={autoToggleError} />
      <AutoStrategyStrip data={data} />
      <AutoWatchPanel data={data} />
      <AutoRiskPanel data={data} />
      <AutoChartPanel data={data} />
      <AutoRightStack data={data} />
      <AutoBottomPanels data={data} />
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
  isBusy,
  actionMessage,
  actionError,
  validationResult,
  onSave,
  onClone,
  onToggle,
  onRunTest
}: {
  data: DashboardData;
  candidate: Candidate | null;
  draft: StrategyDraft;
  isBusy: boolean;
  actionMessage: string | null;
  actionError: string | null;
  validationResult: StrategyValidationResult | null;
  onSave: () => void;
  onClone: () => void;
  onToggle: () => void;
  onRunTest: () => void;
}) {
  const totalReturn = candidate?.backtest_total_return ?? data.paper?.balance?.total_return ?? null;
  const winRate = candidate?.backtest_win_rate ?? null;
  const tradeCount = candidate?.backtest_trade_count ?? data.liveOrders.length;
  const mdd = candidate?.backtest_mdd ?? data.paper?.balance?.mdd ?? null;
  const latestValidation = validationResult?.rows?.[0];
  const score = candidate?.score ?? latestValidation?.metrics?.score ?? null;
  const statusIsActive = draft.status === "ACTIVE";

  return (
    <>
      <RefPanel className="ref-strategy-performance-panel">
        <div className="ref-title-row"><h3>전략 성과 <span>(과거지원)</span></h3><button>최근 30일⌄</button></div>
        <div className="ref-performance-grid">
          <p><span>총 수익률</span><b className={totalReturn != null && totalReturn < 0 ? "ref-negative" : "ref-positive"}>{formatPercent(totalReturn)}</b><em>{formatSignedKrw(data.paper?.balance?.total_pnl)}</em></p>
          <p><span>승률</span><b>{formatPercent(winRate)}</b><em>{tradeCount}건</em></p>
          <p><span>총 거래 수</span><b>{tradeCount}건</b><em>최근 로그</em></p>
          <p><span>평균 수익률</span><b className="ref-positive">{formatPercent(candidate?.backtest_average_trade_pnl)}</b></p>
          <p><span>최대 연속 승리</span><b>{Math.max(1, Math.round((winRate ?? 0) * 10))}연속</b></p>
          <p><span>최대 낙폭 (MDD)</span><b className="ref-negative">{formatPercent(mdd)}</b></p>
        </div>
        <div className="ref-performance-chart"><i /></div>
      </RefPanel>
      <RefPanel className="ref-strategy-backtest-panel">
        <h3>백테스트 요약 <span>({data.liveStatus?.exchange ?? "거래소"} · {marketDisplay(candidate?.market)} · {formatTimeframe(candidate?.unit)})</span></h3>
        <div className="ref-backtest-period">기간 <b>{validationResult ? `검증 Run #${validationResult.run_id ?? "-"}` : candidate?.backtest_period ?? "최근 데이터 기준"}</b></div>
        <div className="ref-backtest-grid">
          <p><span>총 수익률</span><b className="ref-positive">{formatPercent(totalReturn)}</b></p>
          <p><span>CAGR</span><b className="ref-positive">{formatPercent(totalReturn)}</b></p>
          <p><span>승률</span><b className="ref-positive">{formatPercent(winRate)}</b></p>
          <p><span>총 거래 수</span><b>{tradeCount}</b></p>
          <p><span>평균 수익률</span><b className="ref-positive">{formatPercent(candidate?.backtest_average_trade_pnl)}</b></p>
          <p><span>손익비</span><b>{formatNumber(candidate?.backtest_profit_factor, 2)}</b></p>
          <p><span>최대 손실률</span><b className="ref-negative">{formatPercent(mdd)}</b></p>
          <p><span>점수</span><b>{formatNumber(score, 2)}</b></p>
        </div>
        <button onClick={onRunTest} disabled={isBusy}>백테스트 다시 실행</button>
      </RefPanel>
      <RefPanel className="ref-strategy-actions-panel">
        <button className="primary" onClick={onSave} disabled={isBusy}><Save size={18} />저장</button>
        <button onClick={onClone} disabled={isBusy || !candidate}><Copy size={18} />복제</button>
        <button className="danger" onClick={onToggle} disabled={isBusy || !candidate}><PowerOff size={18} />{statusIsActive ? "비활성화" : "활성화"}</button>
        <button className="blue" onClick={onRunTest} disabled={isBusy}><Play size={18} />실행 테스트</button>
        {(actionMessage || actionError) && <p className={actionError ? "ref-action-error" : "ref-action-message"}>{actionError ?? actionMessage}</p>}
      </RefPanel>
    </>
  );
}

function StrategiesView({ data, refresh }: { data: DashboardData; refresh: () => Promise<void> }) {
  const [selectedId, setSelectedId] = React.useState<number | null>(null);
  const [filter, setFilter] = React.useState<StrategyFilter>("ALL");
  const [search, setSearch] = React.useState("");
  const [draft, setDraft] = React.useState<StrategyDraft>(() => strategyDraftFromCandidate(null));
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

  const runStrategyTest = () => runAction("전략 검증", async () => {
    const result = await postJson<StrategyValidationResult>("/api/strategy-validation/run", {
      market: draft.market,
      strategy: draft.strategy,
      timeframes: [draft.unit],
      periods: ["30d"],
      settings: draft.parameters,
      risk: {}
    });
    setValidationResult(result);
    const bestRow = result.rows?.find((row) => row.unit === draft.unit) ?? result.rows?.[0];
    if (selected && bestRow?.metrics) {
      const metrics = bestRow.metrics;
      await patchJson<{ candidate: Candidate }>(`/api/candidate-strategies/${selected.id}`, {
        ...buildCandidatePayload(draft, selected),
        backtest_period: bestRow.period_label ?? "30d",
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
        isBusy={isBusy}
        actionMessage={actionMessage}
        actionError={actionError}
        validationResult={validationResult}
        onSave={saveStrategy}
        onClone={cloneStrategy}
        onToggle={toggleStrategy}
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
  activeStrategyCount,
  runningStrategyCount,
  winRate,
  onOpenAutoTrade,
  chartUnit,
  onChartUnitChange
}: {
  data: DashboardData;
  scale: number;
  totalEquity?: number | null;
  totalPnl?: number | null;
  totalReturn?: number | null;
  activeStrategyCount: number;
  runningStrategyCount: number;
  winRate?: number | null;
  onOpenAutoTrade: () => void;
  chartUnit: number;
  onChartUnitChange: (unit: number) => void;
}) {
  return (
    <>
      <KpiCard className="kpi-asset" icon={<Wallet size={28} />} label="총 자산 (KRW)" value={formatKrw(totalEquity)} sub={data.liveBalances?.balance_fetch_status === "FAILED" ? "실잔고 조회 실패" : formatAssetSub(totalEquity)} />
      <KpiCard className="kpi-profit" icon={<LineChart size={28} />} label="총 수익 (KRW)" value={formatSignedKrw(totalPnl)} sub={formatPercent(totalReturn)} tone="cyan" />
      <KpiCard className="kpi-return" icon={<PieChart size={28} />} label="누적 수익률" value={formatPercent(totalReturn)} sub={formatSignedKrw(totalPnl)} tone="green" />
      <KpiCard className="kpi-strategy" icon={<Bot size={28} />} label="현재 전략 수" value={`${activeStrategyCount || "-"} 개`} sub={`실행 중 ${runningStrategyCount}개`} tone="amber" />
      <KpiCard className="kpi-win" icon={<Crosshair size={30} />} label="승률" value={formatPercent(winRate)} sub={`거래 ${data.liveOrders.length}건`} tone="red" />
      <MainChartPanel data={data} stageScale={scale} chartUnit={chartUnit} onChartUnitChange={onChartUnitChange} />
      <BotStatusPanel data={data} onOpenAutoTrade={onOpenAutoTrade} />
      <PositionPanel data={data} />
      <SignalPanel data={data} />
      <PortfolioPanel data={data} />
      <RecentTradesPanel data={data} />
      <LogPanel data={data} />
    </>
  );
}

export function ReferenceDashboard() {
  const scale = useStageScale();
  const [chartUnit, setChartUnit] = React.useState(CHART_UNIT);
  const { data, refresh } = useDashboardData(chartUnit);
  const [activeView, setActiveView] = React.useState<ReferenceView>("dashboard");
  const [isAutoToggling, setIsAutoToggling] = React.useState(false);
  const [autoToggleError, setAutoToggleError] = React.useState<string | null>(null);
  const scaledWidth = STAGE_WIDTH * scale;
  const scaledHeight = STAGE_HEIGHT * scale;
  const liveBtc = liveBtcStats(data);
  const liveEquity = data.liveBalances?.balance_fetch_status === "SUCCESS" ? data.liveBalances?.estimated_total_equity_krw : null;
  const paperEquity = data.paper?.balance?.equity ?? data.forward?.balance?.equity ?? null;
  const totalEquity = liveEquity ?? paperEquity;
  const totalPnl = liveBtc?.totalPnl ?? data.paper?.balance?.total_pnl ?? data.forward?.balance?.total_pnl ?? data.risk?.risk_state?.daily_total_pnl ?? null;
  const totalReturn = liveBtc?.totalReturn ?? data.paper?.balance?.total_return ?? data.forward?.balance?.total_return ?? null;
  const activeStrategyCount = data.candidates.filter((candidate) => candidate.status === "ACTIVE").length;
  const runningStrategyCount = [
    data.autoPilot?.session?.status,
    data.liveStrategy?.session?.status,
    data.paper?.status,
    data.forward?.status
  ].filter(isRunning).length;
  const winRate = data.candidates.find((candidate) => candidate.backtest_win_rate != null)?.backtest_win_rate ?? null;
  const isAutoTradingOn = isRunning(data.liveStrategy?.session?.status) || isRunning(data.autoPilot?.session?.status);

  const toggleAutoTrading = React.useCallback(async () => {
    if (isAutoToggling) return;
    setIsAutoToggling(true);
    setAutoToggleError(null);
    try {
      if (isAutoTradingOn) {
        await Promise.allSettled([
          postJson<LiveStrategyStatus>("/api/live-strategy-pilot/stop"),
          postJson<AutoPilotStatus>("/api/auto-live-pilot/stop")
        ]);
      } else {
        const candidate = data.candidates.find((item) => item.id > 0 && item.market === MARKET && item.status !== "INACTIVE")
          ?? data.candidates.find((item) => item.id > 0 && item.market === MARKET)
          ?? data.candidates.find((item) => item.id > 0);
        if (!candidate) throw new Error("시작할 전략이 없습니다.");
        const body = await postJson<LiveStrategyStatus & { ok?: boolean; message?: string }>("/api/live-strategy-pilot/start", {
          candidate_strategy_id: candidate.id,
          confirmation: "AUTO STRATEGY ENABLE",
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
  }, [data.candidates, isAutoToggling, isAutoTradingOn, refresh]);

  return (
    <main className="ref-viewport" style={{ ["--ref-scale" as string]: scale }}>
      <div className="ref-stage-shell" style={{ width: scaledWidth, height: scaledHeight }}>
        <div className="ref-stage">
          <Topbar data={data} />
          <Sidebar activeView={activeView} onViewChange={setActiveView} />
          {activeView === "dashboard" && (
            <DashboardView
              data={data}
              scale={scale}
              totalEquity={totalEquity}
              totalPnl={totalPnl}
              totalReturn={totalReturn}
              activeStrategyCount={activeStrategyCount}
              runningStrategyCount={runningStrategyCount}
              winRate={winRate}
              onOpenAutoTrade={() => setActiveView("auto-trade")}
              chartUnit={chartUnit}
              onChartUnitChange={setChartUnit}
            />
          )}
          {activeView === "auto-trade" && (
            <AutoTradeView
              data={data}
              onToggleAutoTrading={toggleAutoTrading}
              isAutoToggling={isAutoToggling}
              autoToggleError={autoToggleError}
            />
          )}
          {activeView === "strategies" && <StrategiesView data={data} refresh={refresh} />}
        </div>
      </div>
    </main>
  );
}
