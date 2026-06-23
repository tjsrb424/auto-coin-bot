from __future__ import annotations

import os
from typing import Any


SIZING_MODE_AVAILABLE_BALANCE_CAP = "available_balance_cap"


def profit_engine_extra_fee_buffer_rate() -> float:
    return _float(os.getenv("PROFIT_ENGINE_EXTRA_FEE_BUFFER_RATE"), 0.0002)


def calculate_available_balance_capped_order(
    *,
    requested_order_krw: float,
    available_krw: float | None,
    min_order_krw: float,
    fee_rate: float,
    extra_fee_buffer_rate: float | None = None,
) -> dict:
    requested = max(_float(requested_order_krw), 0.0)
    available = None if available_krw is None else max(_float(available_krw), 0.0)
    minimum = max(_float(min_order_krw), 0.0)
    fee_buffer_rate = max(_float(fee_rate) + _float(extra_fee_buffer_rate, profit_engine_extra_fee_buffer_rate()), 0.0)
    base = {
        "sizing_mode": SIZING_MODE_AVAILABLE_BALANCE_CAP,
        "requested_order_krw": requested,
        "available_krw": available,
        "min_order_krw": minimum,
        "fee_buffer_rate": fee_buffer_rate,
        "actual_order_krw": 0.0,
        "cap_applied": False,
    }
    if requested <= 0:
        return {**base, "allowed": False, "block_code": "ORDER_AMOUNT_ZERO", "sizing_reason": "ORDER_AMOUNT_ZERO"}
    if available is None:
        return {**base, "allowed": False, "block_code": "BALANCE_UNAVAILABLE", "sizing_reason": "BALANCE_UNAVAILABLE"}
    if available <= 0:
        return {**base, "allowed": False, "block_code": "INSUFFICIENT_BALANCE", "sizing_reason": "INSUFFICIENT_BALANCE"}

    max_affordable = available / (1 + fee_buffer_rate) if fee_buffer_rate > -1 else available
    actual = min(requested, max_affordable)
    cap_applied = actual + 1e-9 < requested
    if actual < minimum:
        return {
            **base,
            "allowed": False,
            "block_code": "ORDER_BELOW_MINIMUM",
            "sizing_reason": "ORDER_BELOW_MINIMUM",
            "max_affordable_krw": max_affordable,
            "actual_order_krw": 0.0,
            "cap_applied": cap_applied,
        }
    return {
        **base,
        "allowed": True,
        "block_code": None,
        "sizing_reason": "REQUEST_EXCEEDS_AVAILABLE_BALANCE_CAPPED" if cap_applied else "REQUEST_WITHIN_AVAILABLE_BALANCE",
        "max_affordable_krw": max_affordable,
        "actual_order_krw": actual,
        "cap_applied": cap_applied,
    }


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
