from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

PROTECTED_NOTIFICATION_EVENTS = {
    "PROTECTED_AUTO_STARTED",
    "PROTECTED_AUTO_RUNNING",
    "PROTECTED_AUTO_STOPPED",
    "PROTECTED_AUTO_STALE",
    "TRADE_OPENED",
    "TRADE_CLOSED",
    "SESSION_LOSS_LIMIT_REACHED",
    "ACCOUNTING_ERROR",
    "MISSING_LEDGER_FILL",
    "DUPLICATE_FILL",
    "FEE_DIFF_ERROR",
    "EQUITY_DIFF_ERROR",
    "OPEN_ORDER_STALE",
    "DAILY_SUMMARY",
}

EVENT_COPY = {
    "PROTECTED_AUTO_STARTED": ("🟢 보호형 자동매매 시작", "PROTECTED_FULL_AUTO_LIVE_V1이 서버에서 실행을 시작했습니다."),
    "PROTECTED_AUTO_RUNNING": ("🟢 보호형 자동매매 실행 중", "보호형 자동매매 heartbeat가 정상 갱신 중입니다."),
    "PROTECTED_AUTO_STOPPED": ("🛑 보호형 자동매매 정지", "보호 조건에 따라 자동매매가 정지되었습니다."),
    "PROTECTED_AUTO_STALE": ("🟡 보호형 자동매매 응답 지연", "Heartbeat가 일정 시간 이상 갱신되지 않아 신규 주문을 차단합니다."),
    "TRADE_OPENED": ("🔵 자동매매 진입", "조건을 충족해 신규 포지션에 진입했습니다."),
    "TRADE_CLOSED": ("✅ 자동매매 청산 완료", "포지션이 청산되고 정산이 완료되었습니다."),
    "SESSION_LOSS_LIMIT_REACHED": ("🔴 세션 손실 한도 도달", "세션 손실 한도에 도달하여 자동매매를 중지했습니다."),
    "ACCOUNTING_ERROR": ("🔴 회계/정산 오류 감지", "체결 저장 또는 평가 자산 정합성 문제가 감지되어 자동매매를 중지했습니다."),
    "MISSING_LEDGER_FILL": ("🔴 체결 원장 누락", "거래소 체결과 내부 원장 사이 누락이 감지되었습니다."),
    "DUPLICATE_FILL": ("🔴 중복 체결 감지", "중복 체결이 감지되었습니다."),
    "FEE_DIFF_ERROR": ("🔴 수수료 불일치", "거래소 수수료와 내부 정산 수수료가 일치하지 않습니다."),
    "EQUITY_DIFF_ERROR": ("🔴 평가 자산 불일치", "거래소 평가 자산과 내부 정산 자산이 일치하지 않습니다."),
    "OPEN_ORDER_STALE": ("🟡 미체결 주문 장기 유지", "오래된 미체결 주문이 감지되어 신규 주문을 차단합니다."),
    "DAILY_SUMMARY": ("📊 일일 자동매매 요약", "오늘의 보호형 자동매매 결과입니다."),
}

EVENT_COLORS = {
    "PROTECTED_AUTO_STARTED": 0x22C55E,
    "PROTECTED_AUTO_RUNNING": 0x22C55E,
    "TRADE_OPENED": 0x3B82F6,
    "TRADE_CLOSED": 0x3B82F6,
    "PROTECTED_AUTO_STALE": 0xF59E0B,
    "OPEN_ORDER_STALE": 0xF59E0B,
    "DAILY_SUMMARY": 0x64748B,
}

ERROR_EVENTS = {
    "PROTECTED_AUTO_STOPPED",
    "SESSION_LOSS_LIMIT_REACHED",
    "ACCOUNTING_ERROR",
    "MISSING_LEDGER_FILL",
    "DUPLICATE_FILL",
    "FEE_DIFF_ERROR",
    "EQUITY_DIFF_ERROR",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def format_kst(value: Any) -> str:
    dt = _parse_utc(value) if value else datetime.now(timezone.utc)
    if dt is None:
        return "-"
    return (dt.astimezone(timezone(timedelta(hours=9)))).strftime("%Y-%m-%d %H:%M:%S KST")


def format_utc(value: Any) -> str:
    dt = _parse_utc(value) if value else datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else "-"


def format_krw(value: Any, *, signed: bool = False) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    sign = "+" if signed and number > 0 else ""
    return f"{sign}{number:,.0f} KRW"


def format_percent(value: Any, *, signed: bool = True) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    sign = "+" if signed and number > 0 else ""
    return f"{sign}{number:.2f}%"


def format_quantity(value: Any) -> str:
    try:
        return f"{float(value):.8f}"
    except (TypeError, ValueError):
        return "-"


def event_title(event_type: str) -> str:
    return EVENT_COPY.get(str(event_type).upper(), (str(event_type).upper(), ""))[0]


def event_summary(event_type: str) -> str:
    return EVENT_COPY.get(str(event_type).upper(), (str(event_type).upper(), "Coin Bot 알림입니다."))[1]


def event_color(event_type: str) -> int:
    event_type = str(event_type).upper()
    if event_type in ERROR_EVENTS:
        return 0xEF4444
    return EVENT_COLORS.get(event_type, 0x64748B)


def common_fields(payload: dict[str, Any]) -> list[dict[str, str]]:
    return _fields(
        [
            ("모드", payload.get("mode") or "PROTECTED_FULL_AUTO_LIVE_V1"),
            ("세션 ID", payload.get("protected_session_id")),
            ("상태", payload.get("status") or payload.get("session_status") or payload.get("worker_status")),
            ("종목", _join_symbols(payload.get("symbols") or payload.get("allowed_symbols"))),
            ("전략", payload.get("strategy") or payload.get("allowed_strategy") or "controlled_entry_v3"),
            ("거래 수", payload.get("trade_count")),
            ("보호형 손익", format_krw(payload.get("protected_strategy_pnl"), signed=True)),
            ("세션 손실 여유", format_krw(payload.get("session_loss_remaining"))),
            ("회계 상태", payload.get("accounting_status")),
            ("정지 사유", payload.get("stop_reason") or payload.get("reason")),
        ]
    )


def event_fields(event_type: str, payload: dict[str, Any]) -> list[dict[str, str]]:
    event_type = str(event_type).upper()
    if event_type == "PROTECTED_AUTO_STARTED":
        return _fields(
            [
                ("모드", "PROTECTED_FULL_AUTO_LIVE_V1"),
                ("세션 ID", payload.get("protected_session_id")),
                ("허용 종목", _join_symbols(payload.get("symbols") or payload.get("allowed_symbols") or ["BTC", "ETH"])),
                ("전략", payload.get("strategy") or payload.get("allowed_strategy") or "controlled_entry_v3"),
                ("주문 한도", format_krw(payload.get("amount_krw") or payload.get("max_notional_krw") or 6000)),
                ("세션 손실 한도", format_krw(payload.get("session_loss_limit_krw") or 1000)),
                ("상태", payload.get("status") or "RUNNING"),
                ("시작 시각", format_kst(payload.get("started_at_utc") or payload.get("last_heartbeat_at_utc"))),
            ]
        )
    if event_type == "TRADE_OPENED":
        return _fields(
            [
                ("종목", payload.get("symbol") or payload.get("market")),
                ("방향", "매수"),
                ("전략", payload.get("strategy") or "controlled_entry_v3"),
                ("타임프레임", payload.get("timeframe")),
                ("주문금액", format_krw(payload.get("notional_krw") or payload.get("amount_krw"))),
                ("진입가", format_krw(payload.get("entry_price"))),
                ("기대 우위", format_percent(payload.get("expected_edge_after_cost"))),
                ("신호 점수", payload.get("signal_score")),
                ("주문 UUID", payload.get("exchange_order_uuid")),
                ("세션 ID", payload.get("protected_session_id")),
            ]
        )
    if event_type == "TRADE_CLOSED":
        return _fields(
            [
                ("종목", payload.get("symbol") or payload.get("market")),
                ("진입가", format_krw(payload.get("entry_price"))),
                ("청산가", format_krw(payload.get("exit_price"))),
                ("청산 사유", payload.get("exit_reason")),
                ("보유 시간", _minutes(payload.get("holding_minutes"))),
                ("총손익", format_krw(payload.get("gross_pnl"), signed=True)),
                ("수수료", format_krw(payload.get("total_fee"))),
                ("순손익", format_krw(payload.get("net_pnl_after_fee"), signed=True)),
                ("누락 체결", payload.get("missing_ledger_fill_count")),
                ("수수료 차이", format_krw(payload.get("fee_diff"), signed=True)),
            ]
        )
    if event_type in {"PROTECTED_AUTO_STOPPED", "SESSION_LOSS_LIMIT_REACHED"}:
        return _fields(
            [
                ("정지 사유", payload.get("stop_reason") or payload.get("reason")),
                ("세션 ID", payload.get("protected_session_id")),
                ("보호형 손익", format_krw(payload.get("protected_strategy_pnl"), signed=True)),
                ("세션 손익 변화", format_krw(payload.get("session_pnl_delta") or payload.get("account_session_pnl_delta"), signed=True)),
                ("손실 한도 여유", format_krw(payload.get("session_loss_remaining"))),
                ("거래 수", payload.get("trade_count")),
                ("미체결 주문", payload.get("open_order_count")),
                ("회계 상태", payload.get("accounting_status")),
                ("정지 시각", format_kst(payload.get("stopped_at_utc") or payload.get("last_heartbeat_at_utc"))),
            ]
        )
    if event_type in {"ACCOUNTING_ERROR", "MISSING_LEDGER_FILL", "DUPLICATE_FILL", "FEE_DIFF_ERROR", "EQUITY_DIFF_ERROR"}:
        return _fields(
            [
                ("오류 유형", event_type),
                ("세션 ID", payload.get("protected_session_id")),
                ("Run ID", payload.get("controlled_run_id") or payload.get("run_id")),
                ("missing fill", payload.get("missing_ledger_fill_count")),
                ("duplicate fill", payload.get("duplicate_fill_count")),
                ("fee diff", format_krw(payload.get("fee_diff"), signed=True)),
                ("equity diff", format_krw(payload.get("equity_diff_after"), signed=True)),
                ("조치", "자동매매 STOP"),
            ]
        )
    if event_type == "PROTECTED_AUTO_STALE":
        return _fields(
            [
                ("마지막 heartbeat", format_kst(payload.get("last_heartbeat_at_utc"))),
                ("마지막 tick", format_kst(payload.get("last_tick_at_utc"))),
                ("다음 tick 예정", format_kst(payload.get("next_tick_at_utc"))),
                ("상태", "STALE"),
                ("조치", "신규 주문 금지"),
            ]
        )
    if event_type == "DAILY_SUMMARY":
        return _fields(
            [
                ("거래 수", payload.get("trade_count")),
                ("승리 거래", payload.get("profitable_trade_count")),
                ("손실 거래", payload.get("losing_trade_count")),
                ("총 수수료", format_krw(payload.get("total_fee"))),
                ("보호형 순손익", format_krw(payload.get("protected_strategy_pnl"), signed=True)),
                ("계좌 평가손익 변화", format_krw(payload.get("account_session_pnl_delta"), signed=True)),
                ("기존 보유 평가손익", format_krw(payload.get("legacy_holding_valuation_delta"), signed=True)),
                ("정지 횟수", payload.get("stop_count")),
                ("현재 상태", payload.get("runtime_status") or payload.get("status")),
            ]
        )
    return common_fields(payload)


def _parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if "+" not in text[-6:] and "-" not in text[-6:]:
        text = f"{text}+00:00"
    try:
        return datetime.fromisoformat(text).astimezone(timezone.utc)
    except ValueError:
        return None


def _fields(values: list[tuple[str, Any]]) -> list[dict[str, str]]:
    fields = []
    for name, value in values:
        text = _string_value(value)
        if text == "-":
            continue
        fields.append({"name": name, "value": text[:1024], "inline": True})
    return fields[:10]


def _string_value(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, float):
        return f"{value:.8g}"
    return str(value)


def _join_symbols(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return _string_value(value)


def _minutes(value: Any) -> str:
    try:
        return f"{float(value):.1f}분"
    except (TypeError, ValueError):
        return "-"
