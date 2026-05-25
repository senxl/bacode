"""
ATR（平均真实波幅）计算模块
用于动态网格步长调整。
"""
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)


def _true_range(candle: Dict, prev_close: float) -> float:
    """计算单根K线的 True Range。"""
    high = candle["high"]
    low = candle["low"]
    return max(
        high - low,
        abs(high - prev_close),
        abs(low - prev_close),
    )


def calc_atr(klines: List[Dict], period: int = 14) -> float:
    """
    从K线数据计算 ATR（指数移动平均）。

    参数:
        klines: K线列表，每条含 high/low/close，按时间升序
        period: ATR 周期，默认 14

    返回:
        最新 ATR 值；数据不足时返回 0
    """
    if len(klines) < period + 1:
        logger.warning("K线数据不足（%d < %d），无法计算 ATR", len(klines), period + 1)
        return 0.0

    # 第一根没有 prev_close，跳过
    tr_list = []
    for i in range(1, len(klines)):
        tr_list.append(_true_range(klines[i], klines[i - 1]["close"]))

    if len(tr_list) < period:
        return 0.0

    # 第一个 ATR 用 SMA
    atr = sum(tr_list[:period]) / period
    # 后续用 EMA 平滑（Wilder 方法）
    for tr in tr_list[period:]:
        atr = (atr * (period - 1) + tr) / period

    return atr
