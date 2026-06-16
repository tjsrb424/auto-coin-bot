from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx

from app.upbit import UPBIT_BASE_URL


class BrokerInterface(Protocol):
    async def get_balances(self) -> dict: ...
    async def get_order_chance(self, market: str) -> dict: ...
    async def create_order_preview(self, order: dict) -> dict: ...
    async def place_order(self, order: dict) -> dict: ...
    async def get_order(self, order_id: str) -> dict: ...
    async def list_open_orders(self, market: str) -> dict: ...
    async def cancel_order(self, order_id: str) -> dict: ...


@dataclass(frozen=True)
class LiveTradingConfig:
    exchange: str
    access_key_loaded: bool
    secret_key_loaded: bool
    live_trading_enabled: bool
    base_url: str
    max_live_order_krw: float
    max_daily_live_loss_percent: float
    min_order_krw: float
    max_position_ratio: float
    duplicate_window_seconds: int
    fee_rate: float
    volatility_block_rate: float
    min_volume: float

    @classmethod
    def from_env(cls) -> "LiveTradingConfig":
        exchange = os.getenv("EXCHANGE", "upbit").strip().lower()
        return cls.for_exchange(exchange)

    @classmethod
    def for_exchange(cls, exchange: str) -> "LiveTradingConfig":
        exchange = exchange.strip().lower()
        if exchange not in {"upbit", "bithumb"}:
            exchange = "upbit"
        if exchange == "bithumb":
            access_key = os.getenv("BITHUMB_ACCESS_KEY", "")
            secret_key = os.getenv("BITHUMB_SECRET_KEY", "")
            base_url = os.getenv("BITHUMB_BASE_URL", "https://api.bithumb.com")
        else:
            access_key = os.getenv("UPBIT_ACCESS_KEY", "")
            secret_key = os.getenv("UPBIT_SECRET_KEY", "")
            base_url = os.getenv("UPBIT_BASE_URL", UPBIT_BASE_URL)
        return cls(
            exchange=exchange,
            access_key_loaded=bool(access_key),
            secret_key_loaded=bool(secret_key),
            live_trading_enabled=os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true",
            base_url=base_url.rstrip("/"),
            max_live_order_krw=float(os.getenv("MAX_LIVE_ORDER_KRW", "10000")),
            max_daily_live_loss_percent=float(os.getenv("MAX_DAILY_LIVE_LOSS_PERCENT", "1")),
            min_order_krw=float(os.getenv("MIN_LIVE_ORDER_KRW", "5000")),
            max_position_ratio=float(os.getenv("MAX_LIVE_POSITION_RATIO", "0.5")),
            duplicate_window_seconds=int(os.getenv("LIVE_DUPLICATE_WINDOW_SECONDS", "30")),
            fee_rate=float(os.getenv("LIVE_FEE_RATE", "0.0005")),
            volatility_block_rate=float(os.getenv("LIVE_VOLATILITY_BLOCK_RATE", "0.03")),
            min_volume=float(os.getenv("LIVE_MIN_CANDLE_VOLUME", "0")),
        )

    @property
    def api_key_loaded(self) -> bool:
        return self.access_key_loaded and self.secret_key_loaded


_live_mode = "PAPER"
_emergency_stop = False


def reset_live_runtime_state() -> None:
    global _live_mode, _emergency_stop
    config = LiveTradingConfig.from_env()
    _live_mode = "LIVE_LOCKED" if config.live_trading_enabled else "PAPER"
    _emergency_stop = False


def current_live_mode() -> str:
    if _emergency_stop:
        return "EMERGENCY_STOPPED"
    return _live_mode


def is_emergency_stopped() -> bool:
    return _emergency_stop


def arm_live_manual_mode(confirmation: str, acknowledged: bool) -> tuple[bool, str, str]:
    global _live_mode
    config = LiveTradingConfig.from_env()
    if not config.live_trading_enabled:
        _live_mode = "PAPER"
        return False, _live_mode, "LIVE_TRADING_ENABLED=false 입니다."
    if _emergency_stop:
        return False, "EMERGENCY_STOPPED", "Emergency Stop 상태입니다."
    if not config.api_key_loaded:
        _live_mode = "LIVE_LOCKED"
        return False, _live_mode, "UPBIT_ACCESS_KEY/UPBIT_SECRET_KEY가 모두 필요합니다."
    if not acknowledged or confirmation != "LIVE ENABLE":
        _live_mode = "LIVE_LOCKED"
        return False, _live_mode, "확인 문구 LIVE ENABLE이 필요합니다."
    _live_mode = "LIVE_MANUAL_ONLY"
    return True, _live_mode, "수동 소액 실주문 모드가 활성화되었습니다."


def lock_live_trading() -> str:
    global _live_mode
    config = LiveTradingConfig.from_env()
    _live_mode = "LIVE_LOCKED" if config.live_trading_enabled else "PAPER"
    return _live_mode


def trigger_emergency_stop() -> str:
    global _live_mode, _emergency_stop
    _emergency_stop = True
    _live_mode = "EMERGENCY_STOPPED"
    return _live_mode


def reset_emergency_stop(confirmation: str) -> tuple[bool, str, str]:
    global _emergency_stop, _live_mode
    if confirmation != "RESET EMERGENCY":
        return False, current_live_mode(), "확인 문구 RESET EMERGENCY가 필요합니다."
    _emergency_stop = False
    return True, lock_live_trading(), "Emergency Stop이 해제되고 실거래는 잠금 상태로 돌아갔습니다."


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _query_string(params: dict[str, Any] | None) -> str:
    if not params:
        return ""
    compact = {key: value for key, value in params.items() if value is not None}
    return urlencode(compact, doseq=True)


def _market_asset(market: str) -> str:
    return market.split("-")[-1]


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _balance_amount(balances: dict, currency: str) -> float:
    item = balances.get("by_currency", {}).get(currency, {})
    return _float(item.get("balance")) + _float(item.get("locked"))


def _available_balance(balances: dict, currency: str) -> float:
    item = balances.get("by_currency", {}).get(currency, {})
    return _float(item.get("balance"))


class BaseJwtBroker:
    exchange = "unknown"

    def __init__(self) -> None:
        self.config = LiveTradingConfig.from_env()
        self.access_key = ""
        self.secret_key = ""
        self.base_url = self.config.base_url

    @property
    def is_ready(self) -> bool:
        return bool(self.access_key and self.secret_key)

    def _jwt(self, params: dict[str, Any] | None = None) -> str:
        raise NotImplementedError

    def _headers(self, params: dict[str, Any] | None = None) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._jwt(params)}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, path: str, params: dict[str, Any] | None = None) -> dict | list:
        if not self.is_ready:
            raise LiveBrokerError(f"{self.exchange} API keys are missing.")
        async with httpx.AsyncClient(timeout=10.0) as client:
            url = f"{self.base_url}{path}"
            headers = self._headers(params)
            if method == "GET":
                response = await client.get(url, params=params, headers=headers)
            elif method == "DELETE":
                response = await client.delete(url, params=params, headers=headers)
            elif method == "POST":
                response = await client.post(url, json=params or {}, headers=headers)
            else:
                raise LiveBrokerError("Unsupported method")
        if response.status_code >= 400:
            raise LiveBrokerError(f"{self.exchange} private API error: {response.status_code} {response.text[:300]}")
        return response.json()

    async def get_balance(self) -> dict:
        return await self.get_balances()

    async def get_order_status(self, order_id: str) -> dict:
        return await self.get_order(order_id)


class UpbitBroker(BaseJwtBroker):
    exchange = "upbit"

    def __init__(self) -> None:
        super().__init__()
        self.access_key = os.getenv("UPBIT_ACCESS_KEY", "")
        self.secret_key = os.getenv("UPBIT_SECRET_KEY", "")
        self.base_url = os.getenv("UPBIT_BASE_URL", UPBIT_BASE_URL).rstrip("/")

    def _jwt(self, params: dict[str, Any] | None = None) -> str:
        header = {"alg": "HS512", "typ": "JWT"}
        payload: dict[str, Any] = {
            "access_key": self.access_key,
            "nonce": str(uuid.uuid4()),
        }
        query = _query_string(params)
        if query:
            payload["query_hash"] = hashlib.sha512(query.encode("utf-8")).hexdigest()
            payload["query_hash_alg"] = "SHA512"
        signing_input = f"{_b64url(json.dumps(header, separators=(',', ':')).encode())}.{_b64url(json.dumps(payload, separators=(',', ':')).encode())}"
        signature = hmac.new(self.secret_key.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha512).digest()
        return f"{signing_input}.{_b64url(signature)}"

    async def get_balances(self) -> dict:
        accounts = await self._request("GET", "/v1/accounts")
        return normalize_accounts(accounts if isinstance(accounts, list) else [])

    async def get_order_chance(self, market: str) -> dict:
        result = await self._request("GET", "/v1/orders/chance", {"market": market})
        return result if isinstance(result, dict) else {"raw": result}

    async def create_order_preview(self, order: dict) -> dict:
        balances = await self.get_balances()
        chance = await self.get_order_chance(str(order.get("market", "KRW-BTC")))
        return {"balances": balances, "order_chance": chance, "order": order}

    async def place_order(self, order: dict) -> dict:
        payload = to_upbit_order_payload(order)
        return await self._request("POST", "/v1/orders", payload)

    async def get_order(self, order_id: str) -> dict:
        result = await self._request("GET", "/v1/order", {"uuid": order_id})
        return result if isinstance(result, dict) else {"raw": result}

    async def list_open_orders(self, market: str) -> dict:
        result = await self._request("GET", "/v1/orders", {"market": market, "state": "wait"})
        return {"orders": result if isinstance(result, list) else result}

    async def cancel_order(self, order_id: str) -> dict:
        result = await self._request("DELETE", "/v1/order", {"uuid": order_id})
        return result if isinstance(result, dict) else {"raw": result}


class BithumbBroker(BaseJwtBroker):
    exchange = "bithumb"

    def __init__(self) -> None:
        super().__init__()
        self.access_key = os.getenv("BITHUMB_ACCESS_KEY", "")
        self.secret_key = os.getenv("BITHUMB_SECRET_KEY", "")
        self.base_url = os.getenv("BITHUMB_BASE_URL", "https://api.bithumb.com").rstrip("/")

    def _jwt(self, params: dict[str, Any] | None = None) -> str:
        header = {"alg": "HS256", "typ": "JWT"}
        payload: dict[str, Any] = {
            "access_key": self.access_key,
            "nonce": str(uuid.uuid4()),
            "timestamp": int(time.time() * 1000),
        }
        query = _query_string(params)
        if query:
            payload["query_hash"] = hashlib.sha512(query.encode("utf-8")).hexdigest()
            payload["query_hash_alg"] = "SHA512"
        signing_input = f"{_b64url(json.dumps(header, separators=(',', ':')).encode())}.{_b64url(json.dumps(payload, separators=(',', ':')).encode())}"
        signature = hmac.new(self.secret_key.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
        return f"{signing_input}.{_b64url(signature)}"

    async def get_balances(self) -> dict:
        accounts = await self._request("GET", "/v1/accounts")
        return normalize_accounts(accounts if isinstance(accounts, list) else [])

    async def get_order_chance(self, market: str) -> dict:
        result = await self._request("GET", "/v1/orders/chance", {"market": market})
        return result if isinstance(result, dict) else {"raw": result}

    async def create_order_preview(self, order: dict) -> dict:
        balances = await self.get_balances()
        chance = await self.get_order_chance(str(order.get("market", "KRW-BTC")))
        return {"balances": balances, "order_chance": chance, "order": order}

    async def place_order(self, order: dict) -> dict:
        side = str(order.get("side", "")).lower()
        ord_type = str(order.get("ord_type", order.get("order_type", ""))).lower()
        if side not in {"bid", "ask"}:
            side = "bid" if str(order.get("side", "")).upper() == "BUY" else "ask"
        if ord_type == "limit" or str(order.get("order_type", "")).upper() == "LIMIT":
            ord_type = "limit"
        payload: dict[str, Any] = {
            "market": order["market"],
            "side": side,
            "volume": str(order["volume"]),
            "price": str(order["price"]),
            "ord_type": ord_type,
        }
        client_order_id = order.get("client_order_id") or order.get("request_id")
        if client_order_id:
            payload["client_order_id"] = str(client_order_id)[:36]
        if payload["side"] != "bid" or payload["ord_type"] != "limit":
            raise LiveBrokerError("Bithumb Auto Live Pilot only allows limit bid orders.")
        result = await self._request("POST", "/v1/orders", payload)
        return result if isinstance(result, dict) else {"raw": result}

    async def get_order(self, order_id: str) -> dict:
        result = await self._request("GET", "/v1/order", {"uuid": order_id})
        return result if isinstance(result, dict) else {"raw": result}

    async def list_open_orders(self, market: str) -> dict:
        result = await self._request("GET", "/v1/orders", {"market": market, "state": "wait"})
        return {"orders": result if isinstance(result, list) else result}

    async def cancel_order(self, order_id: str) -> dict:
        result = await self._request("DELETE", "/v1/order", {"uuid": order_id})
        return result if isinstance(result, dict) else {"raw": result}


LiveBroker = UpbitBroker


class LiveBrokerError(RuntimeError):
    pass


def normalize_accounts(accounts: list[dict]) -> dict:
    by_currency = {}
    for account in accounts:
        currency = str(account.get("currency", "")).upper()
        if not currency:
            continue
        by_currency[currency] = {
            "currency": currency,
            "balance": _float(account.get("balance")),
            "locked": _float(account.get("locked")),
            "avg_buy_price": _float(account.get("avg_buy_price")),
            "unit_currency": account.get("unit_currency"),
        }
    krw = by_currency.get("KRW", {"balance": 0.0, "locked": 0.0})
    return {
        "by_currency": by_currency,
        "krw": krw,
        "btc": by_currency.get("BTC", {"balance": 0.0, "locked": 0.0, "avg_buy_price": 0.0}),
        "eth": by_currency.get("ETH", {"balance": 0.0, "locked": 0.0, "avg_buy_price": 0.0}),
        "fetched_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


def to_upbit_order_payload(order: dict) -> dict:
    side = str(order.get("side", "")).upper()
    order_type = str(order.get("order_type", "LIMIT")).upper()
    payload: dict[str, Any] = {
        "market": order["market"],
        "side": "bid" if side == "BUY" else "ask",
        "identifier": order["request_id"],
    }
    if order_type == "LIMIT":
        payload.update(
            {
                "ord_type": "limit",
                "price": str(order["price"]),
                "volume": str(order["volume"]),
            }
        )
    elif order_type == "MARKET" and side == "BUY":
        payload.update({"ord_type": "price", "price": str(order["amount_krw"])})
    elif order_type == "MARKET" and side == "SELL":
        payload.update({"ord_type": "market", "volume": str(order["volume"])})
    else:
        raise LiveBrokerError("지원하지 않는 주문 타입입니다.")
    return payload


def masked_exchange_request(order: dict) -> dict:
    exchange = str(order.get("exchange", "upbit")).lower()
    if exchange == "bithumb":
        return {
            "market": order["market"],
            "side": "bid" if str(order.get("side", "")).upper() == "BUY" else "ask",
            "order_type": str(order.get("order_type", "LIMIT")).lower(),
            "price": str(order.get("price")),
            "volume": str(order.get("volume")),
            "authorization": "MASKED",
        }
    return {**to_upbit_order_payload(order), "authorization": "MASKED"}


def get_live_broker(exchange: str | None = None) -> BrokerInterface:
    selected = (exchange or LiveTradingConfig.from_env().exchange).strip().lower()
    if selected not in {"upbit", "bithumb"}:
        raise LiveBrokerError(f"Unknown exchange: {selected}")
    if selected == "bithumb":
        return BithumbBroker()
    return UpbitBroker()


def evaluate_live_order_risk(
    *,
    order: dict,
    config: LiveTradingConfig,
    mode: str,
    balances: dict,
    request_exists: bool,
    recent_duplicate: bool,
    market_snapshot: dict | None,
) -> dict:
    side = str(order.get("side", "")).upper()
    market = str(order.get("market", "KRW-BTC"))
    asset = _market_asset(market)
    price = _float(order.get("price")) or _float(market_snapshot.get("price") if market_snapshot else 0.0)
    volume = _float(order.get("volume"))
    amount_krw = _float(order.get("amount_krw"))
    if side == "BUY" and amount_krw <= 0 and price > 0 and volume > 0:
        amount_krw = price * volume
    if side == "SELL" and amount_krw <= 0 and price > 0 and volume > 0:
        amount_krw = price * volume
    if side == "BUY" and volume <= 0 and price > 0 and amount_krw > 0:
        volume = amount_krw / price
    fee_estimate = amount_krw * config.fee_rate
    krw_available = _available_balance(balances, "KRW")
    asset_available = _available_balance(balances, asset)
    asset_total_before = _balance_amount(balances, asset)
    krw_total_before = _balance_amount(balances, "KRW")
    current_asset_value = asset_total_before * price
    total_equity_estimate = krw_total_before + current_asset_value
    post_asset_value = (asset_total_before + (volume if side == "BUY" else -volume)) * price
    position_ratio_after = post_asset_value / total_equity_estimate if total_equity_estimate > 0 else 0.0

    risk_result = "ALLOWED"
    allowed = True
    reason = ""
    if mode != "LIVE_MANUAL_ONLY":
        risk_result = "BLOCKED_EMERGENCY_STOP" if mode == "EMERGENCY_STOPPED" else "BLOCKED_LIVE_LOCKED"
        if risk_result == "BLOCKED_LIVE_LOCKED" and not config.live_trading_enabled:
            risk_result = "BLOCKED_LIVE_DISABLED"
    elif request_exists or recent_duplicate:
        risk_result = "BLOCKED_DUPLICATE_ORDER"
    elif side == "SELL" and asset_available < volume:
        risk_result = "BLOCKED_INSUFFICIENT_POSITION"
    elif amount_krw > config.max_live_order_krw:
        risk_result = "BLOCKED_MAX_ORDER_AMOUNT"
    elif amount_krw < config.min_order_krw:
        risk_result = "BLOCKED_MIN_ORDER_AMOUNT"
    elif side == "BUY" and krw_available < amount_krw + fee_estimate:
        risk_result = "BLOCKED_INSUFFICIENT_BALANCE"
    elif side == "BUY" and position_ratio_after > config.max_position_ratio:
        risk_result = "BLOCKED_MAX_POSITION_RATIO"
    elif market_snapshot and market_snapshot.get("range_rate", 0.0) >= config.volatility_block_rate:
        risk_result = "BLOCKED_VOLATILITY_FILTER"
    elif market_snapshot and config.min_volume > 0 and market_snapshot.get("volume", 0.0) < config.min_volume:
        risk_result = "BLOCKED_LOW_VOLUME"

    if risk_result != "ALLOWED":
        allowed = False
        reason = risk_result

    post_krw = krw_available
    post_asset = asset_available
    if allowed and side == "BUY":
        post_krw = max(krw_available - amount_krw - fee_estimate, 0.0)
        post_asset = asset_available + volume
    elif allowed and side == "SELL":
        post_krw = krw_available + max(amount_krw - fee_estimate, 0.0)
        post_asset = max(asset_available - volume, 0.0)

    return {
        "allowed": allowed,
        "risk_result": risk_result,
        "blocked_reason": reason,
        "market": market,
        "side": side,
        "order_type": str(order.get("order_type", "LIMIT")).upper(),
        "price": price,
        "amount_krw": amount_krw,
        "volume": volume,
        "fee_estimate": fee_estimate,
        "estimated_post_krw_balance": post_krw,
        "estimated_post_asset_balance": post_asset,
        "position_ratio_after": position_ratio_after,
        "max_live_order_krw": config.max_live_order_krw,
        "min_order_krw": config.min_order_krw,
    }
