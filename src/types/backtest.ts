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

export type StrategySwitchLog = {
  id: number;
  from_candidate_strategy_id?: number | null;
  to_candidate_strategy_id?: number | null;
  from_market?: string | null;
  to_market?: string | null;
  decision: string;
  reason?: string;
  blocked_reason?: string;
  score_delta: number;
  created_at: string;
  from_candidate?: CandidateStrategy | null;
  to_candidate?: CandidateStrategy | null;
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
  recent_switch_logs?: StrategySwitchLog[];
};

export type SchedulerTaskState = {
  task_name: string;
  enabled?: boolean;
  interval_seconds?: number;
  interval_minutes?: number;
  max_markets?: number;
  max_save_per_run?: number;
  max_save_per_day?: number;
  max_candidate_pool?: number;
  status: string;
  lock_owner?: string;
  lock_until?: string | null;
  last_started_at?: string | null;
  last_finished_at?: string | null;
  next_run_at?: string | null;
  last_error?: string;
  last_result?: Record<string, unknown>;
  run_count?: number;
  updated_at?: string;
};

export type StrategyDiscoverySchedulerStatus = {
  enabled: boolean;
  exchange: string;
  scan: SchedulerTaskState;
  fast_validation: SchedulerTaskState;
  deep_validation: SchedulerTaskState;
  promotion_selector: SchedulerTaskState;
};

export type DbSchemaStatus = {
  schema_status: "OK" | "MISSING_TABLES" | "ERROR" | string;
  database_path?: string;
  required_tables?: string[];
  missing_tables?: string[];
  repair_attempted?: boolean;
  repair_status?: "NOT_NEEDED" | "REPAIRED" | "FAILED" | string;
  initial_missing_tables?: string[];
  error?: string;
  error_type?: string;
};

export type HealthStatus = {
  server_status?: string;
  database_status?: string;
  schema_status?: string;
  broker_status?: string;
  selected_exchange?: string;
  scheduler_status?: string;
  emergency_stop_status?: string;
  live_trading_enabled?: boolean;
  auto_trading_enabled?: boolean;
  auto_strategy_enabled?: boolean;
  auto_runtime_status?: string;
  auto_strategy_status?: string;
  live_session_status?: string;
};

export type BotPolicy = {
  market?: string;
  auto_trading_enabled?: boolean;
  max_total_exposure_krw?: number;
  daily_loss_limit_pct?: number;
};

export type AutonomousOrchestratorStatus = {
  config: Record<string, unknown>;
  orchestrator: SchedulerTaskState;
  scan: SchedulerTaskState;
  fast_validation: SchedulerTaskState;
  deep_validation: SchedulerTaskState;
  promotion_selector: SchedulerTaskState;
  recent_live_eligible?: CandidateStrategy[];
  recent_live_active?: CandidateStrategy[];
  active_selection?: {
    candidate_strategy_id?: number;
    market?: string;
    strategy?: string;
    unit?: number;
    selected_reason?: string;
    selected_at?: string;
    candidate?: CandidateStrategy;
  } | null;
};

export type PositionSlot = {
  id: number;
  slot_number: number;
  status: string;
  exchange: string;
  market?: string | null;
  candidate_strategy_id?: number | null;
  live_position_id?: number | null;
  live_strategy_session_id?: number | null;
  allocated_krw?: number;
  reserved_krw?: number;
  current_value_krw?: number;
  unrealized_pnl?: number;
  realized_pnl?: number;
  entry_reason?: string | null;
  exit_reason?: string | null;
  opened_at?: string | null;
  closed_at?: string | null;
};

export type NextEntryQueueItem = {
  id: number;
  candidate_strategy_id: number;
  market: string;
  strategy: string;
  unit: number;
  score: number;
  allocation_score: number;
  status: string;
  blocked_reason?: string | null;
  queued_at: string;
  expires_at?: string | null;
};

export type CapitalAllocatorStatus = {
  enabled: boolean;
  exchange: string;
  policy: BotPolicy;
  max_slots: number;
  open_slot_count: number;
  empty_slot_count: number;
  max_total_exposure_krw: number;
  current_open_position_value_krw: number;
  db_open_position_value_krw?: number;
  exchange_position_value_krw?: number;
  pending_buy_reserved_krw: number;
  pending_exchange_buy_order_krw?: number;
  available_krw_balance?: number | null;
  available_budget_krw?: number;
  remaining_exposure_krw: number;
  cash_reserve_krw: number;
  balance_mismatch_detected?: boolean;
  open_order_mismatch_detected?: boolean;
  snapshot_created_at?: string | null;
  snapshot_error?: string;
  snapshot_warnings?: string[];
  snapshot_blockers?: string[];
  slots: PositionSlot[];
  reservations: Record<string, unknown>[];
  next_entry_queue: NextEntryQueueItem[];
  required_edge_pct: number;
};

export type CapitalSnapshotResponse = {
  snapshot: Record<string, unknown>;
  warnings: string[];
  blockers: string[];
};

export type AutonomousOrchestratorRunResponse =
  | SchedulerTaskState
  | {
      task_name: string;
      status: "SKIPPED_LOCKED" | string;
      reason?: string;
      current?: SchedulerTaskState | null;
    };
