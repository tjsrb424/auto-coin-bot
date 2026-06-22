export type MarketUniverseItem = {
  id: number;
  exchange: string;
  market: string;
  symbol: string;
  quote_currency: string;
  status: string;
  is_enabled: boolean;
  is_live_allowed: boolean;
  is_auto_selectable: boolean;
  scan_rank: number;
  score: number;
  reason: string;
  min_24h_trade_price_krw: number;
  last_24h_trade_price_krw: number;
  last_price: number;
  last_change_rate: number;
  last_volatility_score: number;
  last_liquidity_score: number;
  last_risk_score: number;
  last_scanned_at?: string | null;
};

export type CandidateStrategy = {
  id: number;
  name?: string;
  market: string;
  strategy: string;
  unit: number;
  status: string;
  score: number;
  warning?: string;
  backtest_total_return?: number;
  backtest_mdd?: number;
  backtest_win_rate?: number;
  backtest_profit_factor?: number;
  backtest_trade_count?: number;
};

export type ValidationRow = {
  market: string;
  unit: number;
  strategy: string;
  parameters: Record<string, number>;
  period_label: string;
  metrics: {
    total_return?: number;
    mdd?: number;
    win_rate?: number;
    trade_count?: number;
    profit_factor?: number;
    score?: number;
  };
  stability_score: number;
  warnings: string[];
  decision?: string;
};

export type MultiMarketValidationResponse = {
  run_id: number;
  exchange: string;
  markets: string[];
  strategies: string[];
  summary: {
    market_count: number;
    strategy_count: number;
    row_count: number;
    saved_candidate_count: number;
    error_count: number;
  };
  rows: ValidationRow[];
  saved_candidates: CandidateStrategy[];
  errors: Array<{ market: string; strategy: string; error: string }>;
};

export type AutoStrategySelectorStatus = {
  exchange: string;
  evaluated_at: string;
  decision: string;
  can_apply: boolean;
  blockers: string[];
  warnings: string[];
  best_candidate?: CandidateStrategy | null;
  active_selection?: {
    candidate_strategy_id?: number;
    market?: string;
    strategy?: string;
    unit?: number;
    selected_reason?: string;
    selected_at?: string;
    candidate?: CandidateStrategy;
  } | null;
  score_delta: number;
  daily_switch_count: number;
  recent_switch_logs?: Array<Record<string, unknown>>;
};
