import React from "react";
import { BarChart3, Bot, RefreshCw, ShieldCheck, Target, TestTube2 } from "lucide-react";
import {
  evaluateAutoStrategySelector,
  fetchAutoStrategySelectorStatus,
  fetchMarketUniverse,
  runMultiMarketValidation,
  scanMarketUniverse
} from "../api/backtest";
import type {
  AutoStrategySelectorStatus,
  MarketUniverseItem,
  MultiMarketValidationResponse
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

function Chip({ value, tone = "neutral" }: { value?: string; tone?: string }) {
  return <span className={`ref-status-chip ${tone}`}>{value || "-"}</span>;
}

export function BacktestValidationView({ exchange }: Props) {
  const [markets, setMarkets] = React.useState<MarketUniverseItem[]>([]);
  const [selectedMarkets, setSelectedMarkets] = React.useState<string[]>([]);
  const [validation, setValidation] = React.useState<MultiMarketValidationResponse | null>(null);
  const [selector, setSelector] = React.useState<AutoStrategySelectorStatus | null>(null);
  const [busy, setBusy] = React.useState<string | null>(null);
  const [message, setMessage] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const refresh = React.useCallback(async () => {
    const [marketResult, selectorResult] = await Promise.all([
      fetchMarketUniverse(exchange),
      fetchAutoStrategySelectorStatus(exchange)
    ]);
    setMarkets(marketResult.markets);
    setSelector(selectorResult);
    setSelectedMarkets((current) => {
      if (current.length) return current.filter((market) => marketResult.markets.some((item) => item.market === market));
      return marketResult.markets.filter((item) => item.is_enabled && item.is_auto_selectable).slice(0, 5).map((item) => item.market);
    });
  }, [exchange]);

  React.useEffect(() => {
    setError(null);
    void refresh().catch((err) => setError(err instanceof Error ? err.message : "refresh failed"));
  }, [refresh]);

  const runAction = async (label: string, action: () => Promise<void>) => {
    if (busy) return;
    setBusy(label);
    setError(null);
    setMessage(null);
    try {
      await action();
      setMessage(`${label} completed`);
    } catch (err) {
      setError(err instanceof Error ? err.message : `${label} failed`);
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

  return (
    <section className="ref-backtest-view">
      <div className="ref-panel ref-backtest-command">
        <div className="ref-backtest-head">
          <span><TestTube2 size={18} /> Strategy Validation Center</span>
          <Chip value={busy ? "RUNNING" : "READY"} tone={busy ? "amber" : "green"} />
        </div>
        <div className="ref-backtest-actions">
          <button onClick={() => runAction("Market scan", async () => { await scanMarketUniverse(exchange); await refresh(); })} disabled={!!busy}>
            <RefreshCw size={16} /> Scan Markets
          </button>
          <button onClick={() => runAction("Multi validation", async () => { setValidation(await runMultiMarketValidation(exchange, selectedMarkets)); await refresh(); })} disabled={!!busy || selectedMarkets.length === 0}>
            <BarChart3 size={16} /> Run Validation
          </button>
          <button onClick={() => runAction("Selector evaluation", async () => { setSelector(await evaluateAutoStrategySelector(exchange)); })} disabled={!!busy}>
            <Bot size={16} /> Evaluate Selector
          </button>
        </div>
        {(message || error) && <p className={error ? "ref-backtest-error" : "ref-backtest-message"}>{error ?? message}</p>}
        <div className="ref-backtest-kpis">
          <p><span>Universe</span><b>{markets.length}</b></p>
          <p><span>Enabled</span><b>{enabledCount}</b></p>
          <p><span>Live Allowed</span><b>{liveAllowedCount}</b></p>
          <p><span>Selected</span><b>{selectedMarkets.length}</b></p>
        </div>
      </div>

      <div className="ref-panel ref-backtest-markets">
        <div className="ref-backtest-title"><Target size={17} /> Market Universe</div>
        <div className="ref-backtest-market-list">
          {markets.slice(0, 20).map((item) => (
            <button key={item.id} className={selectedMarkets.includes(item.market) ? "is-selected" : ""} onClick={() => toggleMarket(item.market)}>
              <strong>{item.market}</strong>
              <span>{formatKrw(item.last_24h_trade_price_krw)}</span>
              <Chip value={item.is_live_allowed ? "LIVE" : item.status} tone={item.is_live_allowed ? "green" : statusTone(item.status)} />
              <em>{item.reason || `${item.score.toFixed(1)}pt`}</em>
            </button>
          ))}
          {markets.length === 0 && <p className="ref-backtest-empty">Run a market scan to build the KRW universe.</p>}
        </div>
      </div>

      <div className="ref-panel ref-backtest-ranking">
        <div className="ref-backtest-title"><ShieldCheck size={17} /> Validation Ranking</div>
        <table>
          <thead><tr><th>Market</th><th>Strategy</th><th>TF</th><th>Score</th><th>Return</th><th>MDD</th><th>Decision</th></tr></thead>
          <tbody>
            {topRows.map((row, index) => (
              <tr key={`${row.market}-${row.strategy}-${row.unit}-${row.period_label}-${index}`}>
                <td>{row.market}</td>
                <td>{row.strategy}</td>
                <td>{row.unit}m</td>
                <td>{row.stability_score.toFixed(1)}</td>
                <td>{formatPercent(row.metrics.total_return)}</td>
                <td>{formatPercent(row.metrics.mdd)}</td>
                <td><Chip value={row.decision} tone={row.decision === "AUTO_SAVE" ? "green" : "neutral"} /></td>
              </tr>
            ))}
            {topRows.length === 0 && <tr><td colSpan={7}>No validation run yet.</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="ref-panel ref-backtest-selector">
        <div className="ref-backtest-title"><Bot size={17} /> Auto Strategy Selector</div>
        <div className="ref-selector-summary">
          <p><span>Decision</span><b>{selector?.decision ?? "-"}</b></p>
          <p><span>Best</span><b>{selector?.best_candidate ? `${selector.best_candidate.market} · ${selector.best_candidate.strategy}` : "-"}</b></p>
          <p><span>Active</span><b>{selector?.active_selection?.market ?? "-"}</b></p>
          <p><span>Score Delta</span><b>{selector?.score_delta?.toFixed(1) ?? "-"}</b></p>
        </div>
        <div className="ref-selector-blockers">
          {(selector?.blockers?.length ? selector.blockers : ["No selector blockers reported."]).slice(0, 6).map((item) => <span key={item}>{item}</span>)}
        </div>
        {validation?.saved_candidates?.length ? (
          <div className="ref-saved-candidates">
            {validation.saved_candidates.slice(0, 4).map((candidate) => (
              <p key={candidate.id}><b>{candidate.market}</b><span>{candidate.strategy} · {candidate.unit}m</span><Chip value={candidate.status} tone={statusTone(candidate.status)} /></p>
            ))}
          </div>
        ) : null}
      </div>
    </section>
  );
}
