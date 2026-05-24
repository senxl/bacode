"""
交易对信息与精度计算
"""
import math
import logging

logger = logging.getLogger(__name__)


def get_symbol_filters(client, symbol: str) -> dict:
    """获取交易对的 tick_size、step_size、min_notional。"""
    exchange_info = client.futures_exchange_info()
    symbol_info = next(
        (s for s in exchange_info["symbols"] if s["symbol"] == symbol), None
    )
    if symbol_info is None:
        raise ValueError(f"交易对 {symbol} 不存在")
    price_f = next((f for f in symbol_info["filters"] if f["filterType"] == "PRICE_FILTER"), None)
    lot_f = next((f for f in symbol_info["filters"] if f["filterType"] == "LOT_SIZE"), None)
    notional_f = next((f for f in symbol_info["filters"] if f["filterType"] == "MIN_NOTIONAL"), None)
    if price_f is None or lot_f is None:
        raise ValueError(f"交易对 {symbol} 缺少必要的 PRICE_FILTER 或 LOT_SIZE filter")
    result = {
        "tick_size": float(price_f["tickSize"]),
        "step_size": float(lot_f["stepSize"]),
        "min_notional": float(notional_f["notional"]) if notional_f else 5.0,
    }
    return result


def round_price(price: float, tick_size: float) -> float:
    """按 tick_size 舍入价格。"""
    precision = int(round(-math.log10(tick_size)))
    return round(price, precision)


def round_quantity(quantity: float, step_size: float) -> float:
    """按 step_size 取整数量。"""
    precision = int(round(-math.log10(step_size)))
    quantity = round(quantity / step_size) * step_size
    return round(quantity, precision)
