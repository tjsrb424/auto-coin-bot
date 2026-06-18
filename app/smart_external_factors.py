from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Callable

import httpx


COINGECKO_SIMPLE_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
ALTERNATIVE_FEAR_GREED_URL = "https://api.alternative.me/fng/"
DEFAULT_EXCHANGE_NOTICE_URL = "https://global-docs.upbit.com/changelog"

HARD_NOTICE_KEYWORDS = ["거래지원 종료", "상장폐지", "입출금 중단", "긴급 점검", "거래 중지"]
SOFT_NOTICE_KEYWORDS = ["유의 종목", "네트워크 점검", "지갑 점검", "변동성 주의", "maintenance", "suspended"]


def load_external_factors(
    market: str = "KRW-BTC",
    *,
    local_price_krw: float | None = None,
    usd_krw_rate: float | None = None,
    fetcher: Callable[[], dict[str, Any]] | None = None,
    fear_greed_fetcher: Callable[[], dict[str, Any]] | None = None,
    notice_fetcher: Callable[[], Any] | None = None,
    news_fetcher: Callable[[], Any] | None = None,
) -> dict:
    now = _utc_now()
    usd_payload, usd_error = _safe_fetch(fetcher or _fetch_btc_usd_payload)
    fear_greed_payload, fear_greed_error = _safe_fetch(fear_greed_fetcher or _fetch_fear_greed_payload)
    notice_payload, notice_error = _safe_fetch(notice_fetcher or _fetch_exchange_notice_payload)
    news_payload, news_error = _safe_fetch(news_fetcher or _fetch_news_sentiment_payload)
    btc_usd = _nested_float(usd_payload, "bitcoin", "usd")
    btc_usd_change_24h = _nested_float(usd_payload, "bitcoin", "usd_24h_change")
    usd_krw = usd_krw_rate if usd_krw_rate is not None else _float(os.getenv("SMART_EXTERNAL_USD_KRW_RATE"), 1350.0)
    providers = {
        "kimchi_premium": _kimchi_premium_provider(local_price_krw=local_price_krw, btc_usd=btc_usd, usd_krw_rate=usd_krw, now=now, provider_error=usd_error),
        "btc_usd_momentum": _btc_usd_momentum_provider(btc_usd_change_24h=btc_usd_change_24h, now=now, provider_error=usd_error),
        "exchange_notice_risk": _exchange_notice_provider(market=market, payload=notice_payload, now=now, provider_error=notice_error),
        "fear_greed_score": _fear_greed_provider(payload=fear_greed_payload, now=now, provider_error=fear_greed_error),
        "news_sentiment_score": _news_sentiment_provider(payload=news_payload, now=now, provider_error=news_error),
    }
    aggregate = aggregate_external_risk(providers)
    stale = all(bool(item.get("stale")) for item in providers.values())
    return {
        "market": market,
        "stale": stale,
        "last_success_at": now if not stale else None,
        "fetched_at": now,
        "reason": "External factors are advisory only; stale providers never stop Smart Engine decisions.",
        "providers": providers,
        **aggregate,
    }


def aggregate_external_risk(providers: dict[str, dict]) -> dict:
    hard_blockers: list[str] = []
    soft_warnings: list[str] = []
    score = 0.0
    notice = providers.get("exchange_notice_risk") or {}
    if not notice.get("stale"):
        if notice.get("severity") == "hard":
            score += 70
            hard_blockers.append("SMART_EXCHANGE_NOTICE_RISK_BLOCK")
        elif notice.get("severity") == "soft":
            score += 25
            soft_warnings.append("SMART_EXCHANGE_NOTICE_RISK_WARNING")
    news = providers.get("news_sentiment_score") or {}
    news_value = _optional_float(news.get("value")) if not news.get("stale") else None
    if news_value is not None:
        if news_value <= -60:
            score += 35
            soft_warnings.append("SMART_NEWS_SENTIMENT_NEGATIVE")
        elif news_value <= -30:
            score += 18
            soft_warnings.append("SMART_NEWS_SENTIMENT_WEAK")
    fear = providers.get("fear_greed_score") or {}
    fear_value = _optional_float(fear.get("value")) if not fear.get("stale") else None
    if fear_value is not None and fear_value >= 80:
        score += 15
        soft_warnings.append("SMART_FEAR_GREED_OVERHEATED")
    return {
        "external_risk_score": round(min(score, 100.0), 4),
        "hard_blockers": list(dict.fromkeys(hard_blockers)),
        "soft_warnings": list(dict.fromkeys(soft_warnings)),
    }


def _safe_fetch(fetcher: Callable[[], Any]) -> tuple[Any | None, str | None]:
    try:
        return fetcher(), None
    except Exception as exc:
        return None, str(exc)


def _fetch_btc_usd_payload() -> dict[str, Any]:
    with httpx.Client(timeout=_timeout()) as client:
        response = client.get(COINGECKO_SIMPLE_PRICE_URL, params={"ids": "bitcoin", "vs_currencies": "usd", "include_24hr_change": "true"})
        response.raise_for_status()
        return response.json()


def _fetch_fear_greed_payload() -> dict[str, Any]:
    with httpx.Client(timeout=_timeout()) as client:
        response = client.get(ALTERNATIVE_FEAR_GREED_URL, params={"limit": 1, "format": "json"})
        response.raise_for_status()
        return response.json()


def _fetch_exchange_notice_payload() -> str:
    url = os.getenv("SMART_EXCHANGE_NOTICE_URL", DEFAULT_EXCHANGE_NOTICE_URL).strip()
    if not url:
        raise RuntimeError("SMART_EXCHANGE_NOTICE_URL is empty.")
    with httpx.Client(timeout=_timeout(), follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text


def _fetch_news_sentiment_payload() -> Any:
    url = os.getenv("SMART_NEWS_PROVIDER_URL", "").strip()
    if not url:
        raise RuntimeError("SMART_NEWS_PROVIDER_URL is not configured.")
    with httpx.Client(timeout=_timeout(), follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        return response.json() if "json" in content_type else response.text


def _exchange_notice_provider(*, market: str, payload: Any, now: str, provider_error: str | None) -> dict:
    if payload is None:
        return _stale_provider(provider_error or "Exchange notice provider was not available.", "exchange_notice")
    text = _payload_text(payload)
    if not text:
        return _stale_provider("Exchange notice payload was empty.", "exchange_notice")
    matched_hard = [keyword for keyword in HARD_NOTICE_KEYWORDS if keyword.lower() in text.lower()]
    matched_soft = [keyword for keyword in SOFT_NOTICE_KEYWORDS if keyword.lower() in text.lower()]
    severity = "hard" if matched_hard else "soft" if matched_soft else "none"
    matched = matched_hard or matched_soft
    return {
        "value": len(matched),
        "stale": False,
        "severity": severity,
        "matched_keywords": matched,
        "affected_markets": _affected_markets(text, market),
        "source": os.getenv("SMART_EXCHANGE_NOTICE_URL", DEFAULT_EXCHANGE_NOTICE_URL),
        "reason": "Exchange notice keywords were scanned from a public notice source.",
        "last_success_at": now,
    }


def _news_sentiment_provider(*, payload: Any, now: str, provider_error: str | None) -> dict:
    if payload is None:
        return _stale_provider(provider_error or "News sentiment provider was not available.", "news_sentiment")
    if isinstance(payload, dict):
        score = _optional_float(payload.get("score") or payload.get("value") or payload.get("sentiment_score"))
        headline_count = int(_float(payload.get("headline_count") or payload.get("count"), 0))
        negative_count = int(_float(payload.get("negative_count"), 0))
        source = str(payload.get("source") or os.getenv("SMART_NEWS_PROVIDER_URL", "custom"))
    else:
        text = _payload_text(payload)
        score = _simple_news_score(text)
        headline_count = max(text.count("\n"), 1) if text else 0
        negative_count = sum(1 for word in ["hack", "lawsuit", "crash", "exploit", "ban", "급락", "해킹", "제재"] if word in text.lower())
        source = os.getenv("SMART_NEWS_PROVIDER_URL", "custom_text")
    if score is None:
        return _stale_provider("News sentiment payload did not include a score.", "news_sentiment")
    score = round(max(min(score, 100.0), -100.0), 4)
    return {
        "value": score,
        "stale": False,
        "headline_count": headline_count,
        "negative_count": negative_count,
        "source": source,
        "reason": "News sentiment score normalized to -100..100.",
        "last_success_at": now,
    }


def _fear_greed_provider(*, payload: dict[str, Any] | None, now: str, provider_error: str | None) -> dict:
    first = (payload.get("data") or [None])[0] if isinstance(payload, dict) else None
    value = _optional_float(first.get("value")) if isinstance(first, dict) else None
    if value is None:
        return _stale_provider(provider_error or "Fear and Greed Index value was not available.", "alternative.me")
    return {
        "value": round(value, 4),
        "unit": "score_0_100",
        "classification": first.get("value_classification"),
        "timestamp": first.get("timestamp"),
        "time_until_update": first.get("time_until_update"),
        "stale": False,
        "reason": "Alternative.me Crypto Fear and Greed Index.",
        "last_success_at": now,
        "source": "alternative.me",
    }


def _btc_usd_momentum_provider(*, btc_usd_change_24h: float | None, now: str, provider_error: str | None) -> dict:
    if btc_usd_change_24h is None:
        return _stale_provider(provider_error or "BTC/USD 24h change was not available.", "coingecko")
    return {"value": round(btc_usd_change_24h, 4), "unit": "pct_24h", "stale": False, "reason": "CoinGecko BTC/USD 24h change.", "last_success_at": now, "source": "coingecko"}


def _kimchi_premium_provider(*, local_price_krw: float | None, btc_usd: float | None, usd_krw_rate: float, now: str, provider_error: str | None) -> dict:
    if local_price_krw is None or local_price_krw <= 0:
        return _stale_provider("Local KRW-BTC price was not available.", "coingecko+local_price")
    if btc_usd is None or btc_usd <= 0 or usd_krw_rate <= 0:
        return _stale_provider(provider_error or "BTC/USD price or USD/KRW rate was not available.", "coingecko+local_price")
    premium_pct = ((local_price_krw / (btc_usd * usd_krw_rate)) - 1) * 100
    return {"value": round(premium_pct, 4), "unit": "pct", "stale": False, "reason": "Derived from local KRW-BTC price and CoinGecko BTC/USD using SMART_EXTERNAL_USD_KRW_RATE.", "last_success_at": now, "source": "coingecko+local_price", "usd_krw_rate": usd_krw_rate}


def _stale_provider(reason: str, source: str) -> dict:
    return {"value": None, "stale": True, "reason": reason, "last_success_at": None, "source": source}


def _payload_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        return " ".join(str(value) for value in payload.values())
    if isinstance(payload, list):
        return " ".join(_payload_text(item) for item in payload)
    return str(payload or "")


def _affected_markets(text: str, market: str) -> list[str]:
    symbols = {market.upper(), market.replace("KRW-", "").upper(), "BTC"}
    return [market] if any(symbol in text.upper() for symbol in symbols) else []


def _simple_news_score(text: str) -> float | None:
    if not text:
        return None
    lowered = text.lower()
    negative = sum(lowered.count(word) for word in ["hack", "lawsuit", "crash", "exploit", "ban", "급락", "해킹", "제재", "소송"])
    positive = sum(lowered.count(word) for word in ["etf", "approval", "surge", "record", "adoption", "승인", "상승"])
    return max(min((positive - negative) * 15.0, 100.0), -100.0)


def _nested_float(payload: dict[str, Any] | None, *keys: str) -> float | None:
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return _optional_float(value)


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _timeout() -> float:
    return _float(os.getenv("SMART_EXTERNAL_TIMEOUT_SECONDS"), 2.5)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
