import type {
  AutonomousOrchestratorRunResponse,
  AutonomousOrchestratorStatus,
  AutoStrategySelectorStatus,
  BotPolicy,
  CapitalAllocatorStatus,
  CapitalSnapshotResponse,
  DbSchemaStatus,
  HealthStatus,
  MarketUniverseItem,
  MultiMarketValidationResponse,
  StrategyDiscoverySchedulerStatus
} from "../types/backtest";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    ...init,
    headers: init?.body ? { "Content-Type": "application/json", ...(init.headers ?? {}) } : init?.headers
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail ?? payload.message ?? `${path} ${response.status}`);
  }
  return payload as T;
}

export function fetchMarketUniverse(exchange: string) {
  return requestJson<{ markets: MarketUniverseItem[] }>(`/api/markets/universe?exchange=${exchange}`);
}

export function scanMarketUniverse(exchange: string) {
  return requestJson<{
    accepted: MarketUniverseItem[];
    rejected: MarketUniverseItem[];
    market_count: number;
    persisted_count: number;
  }>("/api/markets/scan", {
    method: "POST",
    body: JSON.stringify({ exchange, top_n: 10, max_candidates: 20 })
  });
}

export function runMultiMarketValidation(exchange: string, markets: string[]) {
  return requestJson<MultiMarketValidationResponse>("/api/strategy-validation/multi-market", {
    method: "POST",
    body: JSON.stringify({
      exchange,
      markets,
      strategies: ["ma_cross", "rsi", "volatility_breakout"],
      timeframes: [5, 15, 60],
      periods: ["7d", "30d"],
      risk: { initial_cash: 1_000_000, fee_rate: 0.0005, slippage_rate: 0.0005 },
      max_markets: Math.max(1, Math.min(markets.length || 10, 10)),
      auto_save_candidates: true,
      min_score: 70
    })
  });
}

export function fetchAutoStrategySelectorStatus(exchange: string) {
  return requestJson<AutoStrategySelectorStatus>(`/api/auto-strategy-selector/status?exchange=${exchange}`);
}

export function fetchStrategyDiscoverySchedulerStatus() {
  return requestJson<StrategyDiscoverySchedulerStatus>("/api/strategy-discovery-scheduler/status");
}

export function fetchAutonomousOrchestratorStatus() {
  return requestJson<AutonomousOrchestratorStatus>("/api/autonomous-orchestrator/status");
}

export function fetchDbSchemaStatus() {
  return requestJson<DbSchemaStatus>("/health/db-schema");
}

export function fetchHealthStatus() {
  return requestJson<HealthStatus>("/health");
}

export function fetchBotPolicy(exchange: string, market = "KRW-BTC") {
  return requestJson<{ policy: BotPolicy }>(`/api/bot/policy?market=${market}&exchange=${exchange}`);
}

export function runAutonomousOrchestratorNow() {
  return requestJson<AutonomousOrchestratorRunResponse>("/api/autonomous-orchestrator/run-now", {
    method: "POST",
    body: JSON.stringify({ reason: "MANUAL_RUN_NOW" })
  });
}

export function fetchCapitalAllocatorStatus(exchange: string) {
  return requestJson<CapitalAllocatorStatus>(`/api/capital-allocator/status?exchange=${exchange}`);
}

export function fetchCapitalSnapshot(exchange: string) {
  return requestJson<CapitalSnapshotResponse>(`/api/capital-snapshot?exchange=${exchange}`);
}

export function runCapitalAllocatorNow(exchange: string) {
  return requestJson<{
    ok?: boolean;
    run?: Record<string, unknown>;
    accepted?: unknown[];
    blocked?: unknown[];
    status?: CapitalAllocatorStatus;
    task_name?: string;
  }>("/api/capital-allocator/run-now", {
    method: "POST",
    body: JSON.stringify({ exchange })
  });
}

export function evaluateAutoStrategySelector(exchange: string) {
  return requestJson<AutoStrategySelectorStatus>("/api/auto-strategy-selector/evaluate", {
    method: "POST",
    body: JSON.stringify({ exchange })
  });
}
