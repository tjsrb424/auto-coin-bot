import React from "react";
import { BarChart3, Bot, RefreshCw, ShieldCheck, Target, TestTube2 } from "lucide-react";
import {
  evaluateAutoStrategySelector,
  fetchAutoStrategySelectorStatus,
  fetchMarketUniverse,
  fetchStrategyDiscoverySchedulerStatus,
  runMultiMarketValidation,
  scanMarketUniverse
} from "../api/backtest";
import type {
  AutoStrategySelectorStatus,
  MarketUniverseItem,
  MultiMarketValidationResponse,
  StrategyDiscoverySchedulerStatus,
  SchedulerTaskState
} from "../types/backtest";

type Props = {
  exchange: string;
};

function formatKrw(value?: number | null) {
  if (value == null || Number.isNaN(value)) return "-";
  return `${Math.round(value).toLocaleString("ko-KR")} KRW`;
}

function formatPercent(value?: number | null) {
  if (value == null || Number.isNaN(value)) return "-";
  return `${(value * 100).toFixed(2)}%`;
}

function statusTone(status?: string) {
  const normalized = String(status ?? "").toUpperCase();
  if (["LIVE_ACTIVE", "LIVE_ELIGIBLE", "BACKTEST_PASSED", "SHADOW_PASSED", "DISCOVERED"].includes(normalized)) return "green";
  if (["BACKTEST_RUNNING", "SHADOW_RUNNING", "PAUSED"].includes(normalized)) return "amber";
  if (["REJECTED", "BACKTEST_FAILED"].includes(normalized)) return "red";
  return "neutral";
}

function formatStatusLabel(status?: string | null) {
  const normalized = String(status ?? "").toUpperCase();
  const labels: Record<string, string> = {
    DISCOVERED: "발견됨",
    BACKTEST_RUNNING: "백테스트 중",
    BACKTEST_PASSED: "백테스트 통과",
    BACKTEST_FAILED: "백테스트 실패",
    SHADOW_RUNNING: "섀도우 진행",
    SHADOW_PASSED: "섀도우 통과",
    LIVE_ELIGIBLE: "실거래 후보",
    LIVE_ACTIVE: "실거래 적용",
    PAUSED: "일시정지",
    REJECTED: "제외",
    LIVE: "실거래 허용",
    READY: "준비",
    RUNNING: "진행 중",
    APPLY: "적용 가능",
    APPLIED: "적용됨",
    BLOCKED: "차단됨",
    AUTO_SAVE: "후보 저장",
    REJECT: "제외",
  };
  return labels[normalized] ?? status ?? "-";
}

function formatStrategyLabel(strategy?: string | null) {
  const normalized = String(strategy ?? "").toLowerCase();
  const labels: Record<string, string> = {
    ma_cross: "이동평균 교차",
    rsi: "RSI 반전",
    volatility_breakout: "변동성 돌파",
  };
  return labels[normalized] ?? strategy ?? "-";
}

function formatReasonLabel(reason?: string | null) {
  if (!reason) return "";
  const normalized = reason.toUpperCase();
  const labels: Record<string, string> = {
    NO_LIVE_ELIGIBLE_CANDIDATE: "실거래 후보 전략이 없습니다.",
    POLICY_AUTO_TRADING_DISABLED: "자동매매가 꺼져 있습니다.",
    EMERGENCY_STOP_ACTIVE: "긴급 정지가 활성화되어 있습니다.",
    MARKET_NOT_AUTO_SELECTABLE: "자동 선택 가능한 마켓이 아닙니다.",
    MARKET_NOT_LIVE_ALLOWED: "실거래 허용 마켓이 아닙니다.",
    RISK_STATE_BLOCKED: "리스크 정책에 의해 차단되었습니다.",
    UNRESOLVED_OPEN_ORDER: "미해결 주문이 있습니다.",
    OPEN_POSITION_LIMIT: "보유 포지션 한도에 도달했습니다.",
    SWITCH_COOLDOWN_ACTIVE: "전략 전환 대기 시간이 남아 있습니다.",
    SCORE_DELTA_TOO_SMALL: "현재 전략 대비 점수 차이가 충분하지 않습니다.",
    DAILY_SWITCH_LIMIT: "하루 전략 전환 한도에 도달했습니다.",
    BEST_CANDIDATE_ALREADY_ACTIVE: "최고 후보가 이미 적용 중입니다.",
    AUTO_TRADING_DISABLED_SELECTOR_NOT_APPLIED: "자동매매 OFF라 실거래 전략 교체는 보류했습니다.",
    DAILY_CANDIDATE_SAVE_LIMIT: "하루 후보 자동 저장 한도에 도달했습니다.",
    CANDIDATE_POOL_LIMIT: "후보 풀이 가득 차 저장을 보류했습니다.",
    DUPLICATE_CANDIDATE: "중복 후보라 저장하지 않았습니다.",
    NO_AUTO_SELECTABLE_MARKETS: "자동 검증 가능한 마켓이 없습니다.",
    "SCAN PASSED": "스캔 통과",
    "LOW 24H TRADE PRICE": "24시간 거래대금 부족",
    "NO VALID CANDLE PRICES": "유효한 캔들 가격 없음",
    "VOLATILITY TOO HIGH": "변동성 과다",
  };
  if (labels[normalized]) return labels[normalized];
  if (normalized.startsWith("INSUFFICIENT CANDLES:")) {
    return `캔들 데이터 부족: ${reason.split(":").slice(1).join(":").trim()}`;
  }
  if (normalized.startsWith("CANDLE FETCH FAILED:")) {
    return `캔들 조회 실패: ${reason.split(":").slice(1).join(":").trim()}`;
  }
  return reason.replace(/_/g, " ");
}

function formatSwitchDecision(decision?: string | null) {
  const normalized = String(decision ?? "").toUpperCase();
  const labels: Record<string, string> = {
    APPLIED: "적용됨",
    APPLY: "적용 가능",
    BLOCKED: "보류",
  };
  return labels[normalized] ?? formatStatusLabel(decision);
}

function formatDateTime(value?: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatCompactDateTime(value?: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function schedulerTone(task?: SchedulerTaskState | null) {
  const status = String(task?.status ?? "").toUpperCase();
  if (!task?.enabled || status === "DISABLED") return "neutral";
  if (status === "RUNNING") return "amber";
  if (status === "FAILED") return "red";
  if (status === "COMPLETED" || status === "COMPLETED_WITH_ERRORS") return "green";
  return "neutral";
}

function schedulerLabel(task?: SchedulerTaskState | null) {
  if (!task?.enabled) return "꺼짐";
  const labels: Record<string, string> = {
    IDLE: "대기",
    RUNNING: "실행 중",
    COMPLETED: "완료",
    COMPLETED_WITH_ERRORS: "일부 오류",
    FAILED: "실패",
    SKIPPED: "건너뜀",
    DISABLED: "꺼짐",
    LOCKED: "실행 보류",
  };
  return labels[String(task.status ?? "IDLE").toUpperCase()] ?? task.status ?? "대기";
}

function schedulerSummary(task?: SchedulerTaskState | null) {
  const result = task?.last_result ?? {};
  const saved = result.saved_candidate_count;
  const accepted = result.accepted_count;
  const errors = result.error_count;
  const skipReason = result.skip_reason;
  const selectorDecision = result.selector_decision;
  if (typeof saved === "number") return `저장 ${saved}개 · 오류 ${typeof errors === "number" ? errors : 0}개`;
  if (typeof accepted === "number") return `발견 ${accepted}개`;
  if (typeof skipReason === "string" && skipReason) return formatReasonLabel(skipReason);
  if (typeof selectorDecision === "string" && selectorDecision) return `Selector ${formatStatusLabel(selectorDecision)}`;
  return task?.last_error || "최근 결과 없음";
}

function coinLogoUrls(symbol?: string | null) {
  const normalized = String(symbol ?? "").trim().toUpperCase();
  if (!normalized) return [];
  const encoded = encodeURIComponent(normalized);
  return [
    `https://static.upbit.com/logos/${encoded}.png`,
    `https://img.logokit.com/token/${encoded}`,
  ];
}

function CoinLogo({ symbol }: { symbol?: string | null }) {
  const [sourceIndex, setSourceIndex] = React.useState(0);
  const normalized = String(symbol ?? "").trim().toUpperCase();
  const initial = normalized.slice(0, 1) || "?";
  const urls = coinLogoUrls(normalized);
  const url = urls[sourceIndex];

  React.useEffect(() => {
    setSourceIndex(0);
  }, [normalized]);

  return (
    <span className="ref-coin-logo" aria-label={`${normalized || "coin"} logo`}>
      {url ? (
        <img src={url} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setSourceIndex((current) => current + 1)} />
      ) : (
        <span>{initial}</span>
      )}
    </span>
  );
}

function Chip({ value, tone = "neutral" }: { value?: string; tone?: string }) {
  return <span className={`ref-status-chip ${tone}`}>{formatStatusLabel(value)}</span>;
}

export function BacktestValidationView({ exchange }: Props) {
  const [markets, setMarkets] = React.useState<MarketUniverseItem[]>([]);
  const [selectedMarkets, setSelectedMarkets] = React.useState<string[]>([]);
  const [validation, setValidation] = React.useState<MultiMarketValidationResponse | null>(null);
  const [selector, setSelector] = React.useState<AutoStrategySelectorStatus | null>(null);
  const [scheduler, setScheduler] = React.useState<StrategyDiscoverySchedulerStatus | null>(null);
  const [busy, setBusy] = React.useState<string | null>(null);
  const [message, setMessage] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const refresh = React.useCallback(async () => {
    const [marketResult, selectorResult, schedulerResult] = await Promise.all([
      fetchMarketUniverse(exchange),
      fetchAutoStrategySelectorStatus(exchange),
      fetchStrategyDiscoverySchedulerStatus()
    ]);
    setMarkets(marketResult.markets);
    setSelector(selectorResult);
    setScheduler(schedulerResult);
    setSelectedMarkets((current) => {
      if (current.length) return current.filter((market) => marketResult.markets.some((item) => item.market === market));
      return marketResult.markets.filter((item) => item.is_enabled && item.is_auto_selectable).slice(0, 5).map((item) => item.market);
    });
  }, [exchange]);

  React.useEffect(() => {
    setError(null);
    void refresh().catch((err) => setError(err instanceof Error ? err.message : "새로고침에 실패했습니다."));
  }, [refresh]);

  const runAction = async (label: string, successMessage: string, action: () => Promise<void>) => {
    if (busy) return;
    setBusy(label);
    setError(null);
    setMessage(null);
    try {
      await action();
      setMessage(successMessage);
    } catch (err) {
      setError(err instanceof Error ? err.message : `${label}에 실패했습니다.`);
    } finally {
      setBusy(null);
    }
  };

  const toggleMarket = (market: string) => {
    setSelectedMarkets((current) => current.includes(market) ? current.filter((item) => item !== market) : [...current, market].slice(0, 10));
  };

  const topRows = validation?.rows.slice(0, 8) ?? [];
  const enabledCount = markets.filter((item) => item.is_enabled).length;
  const liveAllowedCount = markets.filter((item) => item.is_live_allowed).length;
  const switchLogs = selector?.recent_switch_logs?.slice(0, 4) ?? [];

  return (
    <section className={`ref-backtest-view${busy ? " is-busy" : ""}`}>
      <div className="ref-panel ref-backtest-command">
        <div className="ref-backtest-head">
          <span><TestTube2 size={18} /> 전략 검증 센터</span>
          <div className="ref-backtest-status">
            {busy ? <span className="ref-loading-state"><span className="ref-loading-spinner" />{busy} 진행 중</span> : null}
            <Chip value={busy ? "RUNNING" : "READY"} tone={busy ? "amber" : "green"} />
          </div>
        </div>
        <div className="ref-backtest-actions">
          <button className={busy === "마켓 스캔" ? "is-loading" : ""} onClick={() => runAction("마켓 스캔", "마켓 스캔이 완료되었습니다.", async () => { await scanMarketUniverse(exchange); await refresh(); })} disabled={!!busy}>
            {busy === "마켓 스캔" ? <span className="ref-loading-spinner" /> : <RefreshCw size={16} />} 마켓 스캔
          </button>
          <button className={busy === "다중 검증" ? "is-loading" : ""} onClick={() => runAction("다중 검증", "다중 마켓 검증이 완료되었습니다.", async () => { setValidation(await runMultiMarketValidation(exchange, selectedMarkets)); await refresh(); })} disabled={!!busy || selectedMarkets.length === 0}>
            {busy === "다중 검증" ? <span className="ref-loading-spinner" /> : <BarChart3 size={16} />} 검증 실행
          </button>
          <button className={busy === "전략 선택 평가" ? "is-loading" : ""} onClick={() => runAction("전략 선택 평가", "자동 전략 선택 평가가 완료되었습니다.", async () => { setSelector(await evaluateAutoStrategySelector(exchange)); })} disabled={!!busy}>
            {busy === "전략 선택 평가" ? <span className="ref-loading-spinner" /> : <Bot size={16} />} 선택 평가
          </button>
        </div>
        {(message || error) && <p className={error ? "ref-backtest-error" : "ref-backtest-message"}>{error ?? message}</p>}
        <div className="ref-backtest-kpis">
          <p><span>전체 마켓</span><b>{markets.length}</b></p>
          <p><span>검증 가능</span><b>{enabledCount}</b></p>
          <p><span>실거래 허용</span><b>{liveAllowedCount}</b></p>
          <p><span>선택됨</span><b>{selectedMarkets.length}</b></p>
        </div>
        <div className="ref-scheduler-strip">
          {[
            { label: "마켓 스캔", task: scheduler?.scan },
            { label: "빠른 검증", task: scheduler?.fast_validation },
            { label: "정밀 검증", task: scheduler?.deep_validation },
            { label: "Selector", task: scheduler?.promotion_selector },
          ].map(({ label, task }) => (
            <div key={label} className={`ref-scheduler-card ${schedulerTone(task)}`}>
              <strong>{label}</strong>
              <Chip value={schedulerLabel(task)} tone={schedulerTone(task)} />
              <span>최근 {formatCompactDateTime(task?.last_finished_at)}</span>
              <span>다음 {formatCompactDateTime(task?.next_run_at)}</span>
              <em title={task?.last_error || schedulerSummary(task)}>{task?.last_error || schedulerSummary(task)}</em>
            </div>
          ))}
        </div>
      </div>

      <div className="ref-panel ref-backtest-markets">
        <div className="ref-backtest-title"><Target size={17} /> 마켓 후보군</div>
        <div className="ref-backtest-market-list">
          {markets.slice(0, 20).map((item) => (
            <button key={item.id} className={selectedMarkets.includes(item.market) ? "is-selected" : ""} onClick={() => toggleMarket(item.market)}>
              <CoinLogo symbol={item.symbol} />
              <strong>{item.market}</strong>
              <span>{formatKrw(item.last_24h_trade_price_krw)}</span>
              <Chip value={item.is_live_allowed ? "LIVE" : item.status} tone={item.is_live_allowed ? "green" : statusTone(item.status)} />
              <em>{item.reason ? formatReasonLabel(item.reason) : `${item.score.toFixed(1)}점`}</em>
            </button>
          ))}
          {markets.length === 0 && <p className="ref-backtest-empty">마켓 스캔을 실행하면 KRW 후보군이 표시됩니다.</p>}
        </div>
      </div>

      <div className="ref-panel ref-backtest-ranking">
        <div className="ref-backtest-title"><ShieldCheck size={17} /> 검증 순위</div>
        <table>
          <thead><tr><th>마켓</th><th>전략</th><th>주기</th><th>점수</th><th>수익률</th><th>MDD</th><th>판정</th></tr></thead>
          <tbody>
            {topRows.map((row, index) => (
              <tr key={`${row.market}-${row.strategy}-${row.unit}-${row.period_label}-${index}`}>
                <td>{row.market}</td>
                <td>{formatStrategyLabel(row.strategy)}</td>
                <td>{row.unit}m</td>
                <td>{row.stability_score.toFixed(1)}</td>
                <td>{formatPercent(row.metrics.total_return)}</td>
                <td>{formatPercent(row.metrics.mdd)}</td>
                <td><Chip value={row.decision} tone={row.decision === "AUTO_SAVE" ? "green" : "neutral"} /></td>
              </tr>
            ))}
            {topRows.length === 0 && <tr><td colSpan={7}>아직 검증 실행 결과가 없습니다.</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="ref-panel ref-backtest-selector">
        <div className="ref-backtest-title"><Bot size={17} /> 자동 전략 선택기</div>
        <div className="ref-selector-summary">
          <p><span>판정</span><b>{formatStatusLabel(selector?.decision)}</b></p>
          <p><span>최고 후보</span><b>{selector?.best_candidate ? `${selector.best_candidate.market} · ${formatStrategyLabel(selector.best_candidate.strategy)}` : "-"}</b></p>
          <p><span>적용 중</span><b>{selector?.active_selection?.market ?? "-"}</b></p>
          <p><span>점수 차이</span><b>{selector?.score_delta?.toFixed(1) ?? "-"}</b></p>
        </div>
        <div className="ref-selector-blockers">
          {(selector?.blockers?.length ? selector.blockers : ["차단 사유가 없습니다."]).slice(0, 6).map((item) => <span key={item}>{formatReasonLabel(item)}</span>)}
        </div>
        <div className="ref-switch-logs">
          <strong>최근 교체 로그</strong>
          {switchLogs.length ? switchLogs.map((log) => (
            <p key={log.id}>
              <b>{formatSwitchDecision(log.decision)}</b>
              <span>{log.to_candidate ? `${log.to_candidate.market} · ${formatStrategyLabel(log.to_candidate.strategy)}` : (log.to_market ?? "-")}</span>
              <em>{log.blocked_reason ? formatReasonLabel(log.blocked_reason.split(",")[0]?.trim()) : (log.reason || formatDateTime(log.created_at))}</em>
            </p>
          )) : <p><b>-</b><span>아직 교체 로그가 없습니다.</span><em>조건 충족 또는 보류 시 이곳에 표시됩니다.</em></p>}
        </div>
        {validation?.saved_candidates?.length ? (
          <div className="ref-saved-candidates">
            {validation.saved_candidates.slice(0, 4).map((candidate) => (
              <p key={candidate.id}><b>{candidate.market}</b><span>{formatStrategyLabel(candidate.strategy)} · {candidate.unit}m</span><Chip value={candidate.status} tone={statusTone(candidate.status)} /></p>
            ))}
          </div>
        ) : null}
      </div>
    </section>
  );
}
