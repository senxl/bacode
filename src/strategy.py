"""
网格交易策略模块
封装所有与「网格逻辑」相关的决策，与订单执行分离。
"""
import logging
from . import config
from .orders import OrderSide
from .contract import round_price

logger = logging.getLogger(__name__)


class GridState:
    """网格状态机，管理 last_filled_price 和 lock 状态。"""

    def __init__(self):
        self.last_filled_price: float = 0.0
        self.last_filled_price_lock: bool = False
        self.signed_grid_size: float = 0.0

    def init_from_price(self, current_price: float) -> None:
        """用当前价格初始化网格。"""
        grid_size = config.get_grid_size()
        self.signed_grid_size = (
            grid_size if config.get_trade_type() == "LONG" else -grid_size
        )
        # 将当前价格对齐到网格
        self.last_filled_price = (
            int(current_price / abs(self.signed_grid_size)) * abs(self.signed_grid_size)
        )
        self.last_filled_price_lock = False

    def lock(self, new_price: float) -> None:
        """锁定/更新上次成交价。"""
        self.last_filled_price = new_price
        self.last_filled_price_lock = True

    def unlock(self) -> None:
        self.last_filled_price_lock = False

    def buy_price(self) -> float:
        """计算网格下限挂单价格。"""
        return self.last_filled_price - abs(self.signed_grid_size)

    def sell_price(self) -> float:
        """计算网格上限挂单价格。"""
        return self.last_filled_price + abs(self.signed_grid_size)

    @property
    def abs_grid_size(self) -> float:
        return abs(self.signed_grid_size)

    @property
    def over_threshold(self) -> float:
        return abs(self.signed_grid_size * config.get_grid_size_over())


def is_price_in_range(current_price: float) -> bool:
    """价格是否在允许区间内。"""
    return config.get_price_lower_limit() <= current_price <= config.get_price_upper_limit()


def should_place_grid_orders(buy_num: int, sell_num: int) -> bool:
    """是否有边缺少挂单（需要重新挂网格）。"""
    return buy_num == 0 or sell_num == 0


def should_trigger_breakout(
    state: GridState, current_price: float
) -> bool:
    """
    价格是否超出了「突破网格」的阈值。
    threshold = GRID_SIZE * GRID_SIZE_OVER（默认 1.5 倍网格）
    """
    lo = state.last_filled_price - state.over_threshold
    hi = state.last_filled_price + state.over_threshold
    return not (lo <= current_price <= hi)


def breakout_direction(
    state: GridState, current_price: float
) -> str | None:
    """
    判断突破方向。
    返回 'SELL'（价格向上突破 → 做空方向吃单）
          'BUY' （价格向下突破 → 做多方向吃单）
          None  （未突破）
    """
    limit = state.last_filled_price + state.abs_grid_size
    if current_price > limit:
        return "SELL"
    elif current_price < state.last_filled_price - state.abs_grid_size:
        return "BUY"
    return None


def new_price_after_breakout(
    direction: str, current_price: float, state: GridState
) -> float:
    """计算突破后新的 last_filled_price。"""
    if direction == "SELL":
        return state.last_filled_price + state.abs_grid_size
    else:
        return state.last_filled_price - state.abs_grid_size
