"""
持仓管理：查询持仓、获取成交价格
"""
import logging
from . import config
from .contract import round_price

logger = logging.getLogger(__name__)


def get_position_amount(client, symbol: str) -> float:
    """获取当前 TRADE_TYPE 方向的持仓数量（绝对值）。"""
    try:
        positions = client.futures_position_information(symbol=symbol)
        trade_type = config.get_trade_type()
        for p in positions:
            if p["positionSide"] == trade_type:
                return abs(float(p["positionAmt"]))
        return 0.0
    except Exception as e:
        logger.error("获取持仓失败: %s", e)
        return 0.0


def get_last_filled_price(client, symbol: str, tick_size: float) -> float:
    """获取最近一笔成交的价格（已舍入）。"""
    try:
        trades = client.futures_account_trades(symbol=symbol, limit=2)
        if not trades:
            return 0.0
        if len(trades) >= 2 and trades[0]["time"] < trades[1]["time"]:
            trades[0], trades[1] = trades[1], trades[0]
        return round_price(float(trades[0]["price"]), tick_size)
    except Exception as e:
        logger.error("获取最新成交价格失败: %s", e)
        return 0.0
