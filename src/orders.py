"""
订单操作：挂单、市价单、取消、查询
"""
import logging
from . import config
from .contract import round_price, round_quantity

logger = logging.getLogger(__name__)


class OrderSide:
    """根据 TRADE_TYPE 映射多空方向到 BUY/SELL。"""
    @staticmethod
    def buy() -> str:
        return "BUY" if config.get_trade_type() == "LONG" else "SELL"

    @staticmethod
    def sell() -> str:
        return "SELL" if config.get_trade_type() == "LONG" else "BUY"


def cancel_all_open_orders(client, symbol: str):
    """取消指定交易对的所有未成交订单。失败时抛异常。"""
    try:
        result = client.futures_cancel_all_open_orders(symbol=symbol)
        logger.info("已取消 %s 的所有挂单", symbol)
        return result
    except Exception as e:
        logger.error("取消所有挂单失败: %s", e)
        raise


def get_order_counts(client, symbol: str) -> tuple[int, int]:
    """返回 (buy_count, sell_count)。"""
    try:
        orders = client.futures_get_open_orders(symbol=symbol)
        buy = sum(1 for o in orders if o["side"] == "BUY")
        sell = sum(1 for o in orders if o["side"] == "SELL")
        return buy, sell
    except Exception as e:
        logger.error("获取挂单数量失败: %s", e)
        return 0, 0


def create_limit_order(
    client,
    symbol: str,
    side: str,
    price: float,
    quantity: float,
    tick_size: float,
    time_in_force: str = "GTX",
):
    """创建限价单，自动舍入价格。"""
    p = round_price(price, tick_size)
    if quantity <= 0:
        logger.error("下单数量无效: %s（symbol=%s）", quantity, symbol)
        raise ValueError(f"下单数量必须 > 0，当前: {quantity}")
    if p <= 0:
        logger.error("下单价格无效: %s（symbol=%s）", p, symbol)
        raise ValueError(f"下单价格必须 > 0，当前: {p}")
    return client.futures_create_order(
        symbol=symbol,
        side=side,
        type="LIMIT",
        timeInForce=time_in_force,
        quantity=quantity,
        price=p,
        positionSide=config.get_trade_type(),
    )


def create_market_order(client, symbol: str, side: str, quantity: float):
    """创建市价单。"""
    if quantity <= 0:
        logger.error("市价单数量无效: %s（symbol=%s）", quantity, symbol)
        raise ValueError(f"市价单数量必须 > 0，当前: {quantity}")
    return client.futures_create_order(
        symbol=symbol,
        side=side,
        type="MARKET",
        quantity=quantity,
        positionSide=config.get_trade_type(),
    )


def create_limit_order_with_fallback(
    client,
    symbol: str,
    side: str,
    price: float,
    quantity: float,
    tick_size: float,
):
    """先用 GTX 创建，失败则回退到 GTC。"""
    try:
        return create_limit_order(client, symbol, side, price, quantity, tick_size, "GTX")
    except Exception:
        logger.warning("GTX 订单失败，回退到 GTC 订单")
        return create_limit_order(client, symbol, side, price, quantity, tick_size, "GTC")


def create_savpos_order(client, symbol: str, side: str, savepos_qty: float):
    """补仓挂单（GTC 追价单）。"""
    return client.futures_create_order(
        symbol=symbol,
        side=side,
        type="LIMIT",
        timeInForce="GTC",
        quantity=savepos_qty,
        priceMatch="QUEUE",
        positionSide=config.get_trade_type(),
    )


def modify_order_to_queue(client, symbol: str, order: dict):
    """将已有订单修改为 QUEUE 追价模式。"""
    return client.futures_modify_order(
        symbol=symbol,
        orderId=order["orderId"],
        side=order["side"],
        quantity=order["origQty"],
        timeInForce="GTC",
        priceMatch="QUEUE",
        positionSide=config.get_trade_type(),
    )
