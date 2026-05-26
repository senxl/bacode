"""
交易对信息与精度计算
"""
import logging
from decimal import Decimal, ROUND_DOWN

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
    """按 tick_size 舍入价格（用 Decimal 避免浮点精度问题）。"""
    if tick_size <= 0:
        return price
    d_price = Decimal(str(price))
    d_tick = Decimal(str(tick_size))
    # 向下取整到 tick_size 的整数倍
    result = (d_price / d_tick).quantize(Decimal("1"), rounding=ROUND_DOWN) * d_tick
    return float(result)


def round_quantity(quantity: float, step_size: float) -> float:
    """按 step_size 取整数量（用 Decimal 避免浮点精度问题）。"""
    if step_size <= 0:
        return quantity
    d_qty = Decimal(str(quantity))
    d_step = Decimal(str(step_size))
    result = (d_qty / d_step).quantize(Decimal("1"), rounding=ROUND_DOWN) * d_step
    return float(result)
