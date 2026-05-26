"""
网格交易编排器
负责补仓监控、网格维护、突破执行的完整状态机。
内置配置热重载：每个循环自动检测 config.ini 是否被修改。
"""
import time
import logging
from binance.exceptions import BinanceAPIException

from . import config
from .contract import round_price, round_quantity, get_symbol_filters
from .orders import (
    OrderSide, cancel_all_open_orders, get_order_counts,
    create_limit_order_with_fallback, create_savpos_order,
    modify_order_to_queue, create_market_order,
)
from .position import get_position_amount, get_last_filled_price
from .strategy import (
    GridState, is_price_in_range,
    should_place_grid_orders, should_trigger_breakout,
    breakout_direction, new_price_after_breakout,
)
from .atr import calc_atr
from . import gui

logger = logging.getLogger(__name__)


class GridOrchestrator:
    """网格交易统一编排。"""

    def __init__(self, client, symbol: str, trade_quantity: float, savepos_qty: float, tick_size: float, step_size: float):
        self.client = client
        self.symbol = symbol
        self.trade_quantity = trade_quantity
        self.savepos_qty = savepos_qty
        self.tick_size = tick_size
        self.step_size = step_size
        self.state = GridState()
        self._last_reload_time: float = 0.0
        self._last_kline_fetch: float = 0.0
        self._kline_interval: str = config.KLINE_INTERVAL
        # —— ATR 动态步长状态 ——
        self._last_atr_update: float = 0.0
        self._last_atr_value: float = 0.0

    def _current_price(self) -> float:
        return float(self.client.futures_symbol_ticker(symbol=self.symbol)["price"])

    def _cancel_all(self) -> None:
        cancel_all_open_orders(self.client, self.symbol)

    def _log_grid_orders(self, buy_price: float, sell_price: float, current_price: float) -> None:
        logger.info(
            "限价挂单 %s %s | 买入价: %s | 卖出价: %s | 当前价: %s",
            self.trade_quantity, self.symbol,
            round_price(buy_price, self.tick_size),
            round_price(sell_price, self.tick_size),
            current_price,
        )

    # ---------- K 线工具方法 ----------
    def _fetch_parsed_klines(self, interval: str, limit: int) -> list[dict]:
        """获取并解析 K 线数据，返回标准格式列表。"""
        raw = self.client.futures_klines(symbol=self.symbol, interval=interval, limit=limit)
        return [
            {
                "time": k[0],
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            }
            for k in raw
        ]

    # ---------- ATR 动态步长 ----------
    def _try_update_atr(self) -> None:
        """定时刷新 ATR，变化超过阈值时更新网格步长。"""
        if config.get_grid_mode() != "atr":
            return

        now = time.time()
        interval = config.get_atr_update_interval()
        if now - self._last_atr_update < interval:
            return

        try:
            klines = self._fetch_parsed_klines(
                config.get_atr_kline_interval(),
                max(config.get_atr_period() + 5, 50),
            )
            atr = calc_atr(klines, config.get_atr_period())
            if atr <= 0:
                self._last_atr_update = now  # 推进定时器，避免每 0.3s 重试
                self._push_state(atr_value=0.0)
                return

            threshold = config.get_atr_change_threshold()
            # 与上次 ATR 比较，变化超过阈值才更新
            if self._last_atr_value > 0:
                change = abs(atr - self._last_atr_value) / self._last_atr_value
                if change < threshold:
                    logger.debug(
                        "ATR 变化 %.2f%% 未达阈值 %.0f%%，保持当前步长",
                        change * 100, threshold * 100,
                    )
                    self._push_state(atr_value=round(atr, 4))
                    self._last_atr_update = now  # 成功获取，推进定时器
                    return

            new_grid_size = round_price(atr * config.get_atr_multiplier(), self.tick_size)
            # 防止 round 后变 0（低价格资产 + 小倍数场景）
            if new_grid_size <= 0:
                new_grid_size = self.tick_size  # 最小一个 tick
                logger.warning("ATR 步长计算为 0，使用最小 tick_size=%s", self.tick_size)

            old_grid_size = abs(self.state.signed_grid_size)
            self.state.signed_grid_size = (
                new_grid_size if config.get_trade_type() == "LONG" else -new_grid_size
            )
            self._last_atr_value = atr
            self._last_atr_update = now  # 成功更新，推进定时器

            logger.info(
                "📐 ATR 动态步长更新 | ATR(%d)=%.4f | 旧步长=%s → 新步长=%s | 倍数=%.2f | 周期=%s",
                config.get_atr_period(), atr,
                round_price(old_grid_size, self.tick_size),
                new_grid_size, config.get_atr_multiplier(),
                config.get_atr_kline_interval(),
            )
            self._push_state(
                atr_value=round(atr, 4),
                grid_size=new_grid_size,
                message=f"📐 ATR步长更新: {round_price(old_grid_size, self.tick_size)} → {new_grid_size}",
            )

            # ATR 更新步长后立即重建网格
            if config.get_atr_immediate_rebuild():
                self._cancel_all()
                # 注意：用当前价近似成交价，非实际成交价
                price = self._current_price()
                self.state.last_filled_price = price
                self._place_both_grid_orders(price)
                logger.info("📐 ATR 步长变更，网格已立即重建")
        except Exception as e:
            # 失败不推进定时器，下次循环立即重试
            logger.warning("ATR 刷新失败（下次循环重试）: %s", e)

    # ---------- 推送到 GUI ----------
    def _push_state(self, **kwargs) -> None:
        """向 GUI 共享状态写入，当 GUI 关闭时无副作用。"""
        base = dict(
            symbol=self.symbol,
            grid_size=abs(self.state.signed_grid_size),
            buy_order_price=self.state.buy_price(),
            sell_order_price=self.state.sell_price(),
            trade_type=config.get_trade_type(),
            leverage=config.get_leverage(),
            trade_quantity=self.trade_quantity,
            savepos_qty=self.savepos_qty,
            upper_limit=config.get_price_upper_limit(),
            lower_limit=config.get_price_lower_limit(),
            grid_mode=config.get_grid_mode(),
            atr_value=self._last_atr_value,
            testnet=config.TESTNET,
        )
        base.update(kwargs)  # kwargs override base — no duplicate key errors
        gui.set_state(**base)

    # ---------- K线数据 ----------
    def _fetch_klines(self, force: bool = False) -> None:
        """定期获取K线数据并推送到共享状态（30秒刷新、interval 变更时立即刷新）。"""
        now = time.time()
        if not force and now - self._last_kline_fetch < 30.0:
            return
        state = gui.get_state_copy()
        desired = state.get("kline_interval", config.KLINE_INTERVAL)
        if desired != self._kline_interval:
            self._kline_interval = desired
            force = True
        if not force and now - self._last_kline_fetch < 30.0:
            return
        try:
            klines = self._fetch_parsed_klines(self._kline_interval, config.KLINE_LIMIT)
            self._last_kline_fetch = now
            self._push_state(klines=klines, kline_interval=self._kline_interval)
        except Exception as e:
            self._last_kline_fetch = now  # 推进定时器，避免每 0.3s 重试
            logger.warning("获取K线数据失败: %s", e)

    # ---------- 阶段 1: 初始化 ----------
    def setup(self) -> None:
        """设置杠杆 & 初始化网格。"""
        try:
            self.client.futures_change_leverage(symbol=self.symbol, leverage=config.get_leverage())
            logger.info("已设置杠杆为 %sx", config.get_leverage())
        except BinanceAPIException as e:
            logger.error("设置杠杆失败: %s", e)
            raise

        price = self._current_price()
        logger.info("初始 %s 价格: %s", self.symbol, price)
        self.state.init_from_price(price)
        self._cancel_all()
        self._place_both_grid_orders(price)
        self._push_state(current_price=price, last_filled=self.state.last_filled_price,
                         message="初始化完成")

    def _place_both_grid_orders(self, current_price: float = 0.0) -> None:
        """挂双边网格限价单。"""
        create_limit_order_with_fallback(
            self.client, self.symbol,
            "BUY", self.state.buy_price(),
            self.trade_quantity, self.tick_size,
        )
        create_limit_order_with_fallback(
            self.client, self.symbol,
            "SELL", self.state.sell_price(),
            self.trade_quantity, self.tick_size,
        )
        if current_price <= 0:
            current_price = self._current_price()
        self._log_grid_orders(self.state.buy_price(), self.state.sell_price(), current_price)

    # ---------- 阶段 2: 补仓监控 ----------
    def handle_savpos(self, add_order: dict | None) -> dict | None:
        """处理补仓挂单。返回更新后的 add_order（可能被设为 None）。"""
        if add_order is None:
            return self._maybe_place_savpos()
        return self._track_savpos(add_order)

    def _maybe_place_savpos(self) -> dict | None:
        pos = get_position_amount(self.client, self.symbol)
        self._push_state(position=pos, savpos_active=False)
        if pos < self.savepos_qty:
            order = create_savpos_order(self.client, self.symbol, OrderSide.buy(), self.savepos_qty)
            logger.info("补仓挂单 %s %s", self.savepos_qty, self.symbol)
            self._push_state(savpos_active=True, message="补仓挂单中...")
            return order
        return None

    def _track_savpos(self, order: dict) -> dict | None:
        """跟踪补仓订单状态。"""
        try:
            current = self.client.futures_get_order(
                symbol=self.symbol, orderId=order["orderId"]
            )
            status = current["status"]
            if status == "FILLED":
                logger.info("补仓订单完全成交 | 成交数量: %s", current["executedQty"])
                self._push_state(savpos_active=False, message="补仓订单已成交")
                return None
            if status == "CANCELED" or status == "EXPIRED":
                logger.warning("补仓订单已被取消/过期: %s", status)
                self._push_state(savpos_active=False, message=f"补仓订单已{status}")
                return None
            if status in ("NEW", "PARTIALLY_FILLED"):
                modify_order_to_queue(self.client, self.symbol, current)
                logger.info("补仓追单修改订单成功")
                self._push_state(message="补仓追单中...")
        except Exception as e:
            # 区分"订单不存在"（可能已成交被系统清理）和网络错误
            if "-2013" in str(e) or "Order does not exist" in str(e):
                logger.warning("补仓订单已不存在（可能已成交），停止追踪")
                self._push_state(savpos_active=False, message="补仓订单已完成")
                return None
            logger.debug("查询补仓订单状态失败（下次重试）: %s", e)
        return order

    # ---------- 阶段 3: 网格维护 ----------
    def handle_grid_orders(self) -> None:
        """检查并重建网格挂单。"""
        buy_num, sell_num = get_order_counts(self.client, self.symbol)
        self._push_state(buy_orders=buy_num, sell_orders=sell_num)
        if should_place_grid_orders(buy_num, sell_num):
            self._cancel_all()
            price = get_last_filled_price(self.client, self.symbol, self.tick_size)
            if price > 0:
                self.state.unlock()
                changed = (self.state.last_filled_price != price)
                self.state.last_filled_price = price
                if changed:
                    logger.info("🔔 最新成交价格: %s", self.state.last_filled_price)
            self._place_both_grid_orders(price)
            self._push_state(last_filled=self.state.last_filled_price, message="网格重建完成")

    # ---------- 阶段 4: 突破处理 ----------
    def handle_breakout(self) -> None:
        """检测并处理价格突破网格。"""
        price = self._current_price()
        if not should_trigger_breakout(self.state, price):
            return

        # 确认挂单状态（价格已取过，不再重复调用）
        buy_num, sell_num = get_order_counts(self.client, self.symbol)
        if buy_num == 0 or sell_num == 0:
            return  # 网格不全，先补充网格

        direction = breakout_direction(self.state, price)
        if direction is None:
            return

        logger.info("🔔 价格突破网格，方向: %s，当前价: %s", direction, price)
        self._cancel_all()
        create_market_order(self.client, self.symbol, direction, self.trade_quantity)
        logger.info("市价吃单 %s %s，当前价: %s", self.trade_quantity, self.symbol, price)
        new_price = new_price_after_breakout(direction, price, self.state)
        self.state.lock(new_price)
        logger.info("🔔 最新成交价格: %s", self.state.last_filled_price)
        self._push_state(last_filled=new_price, current_price=price,
                         message=f"🔔 突破吃单 {direction} @ {price}")

    # ---------- 热重载 ----------
    def _try_config_reload(self) -> None:
        """按 reload_check_interval 节流检测 config.ini 变更并热加载。"""
        now = time.time()
        if now - self._last_reload_time >= config.RELOAD_CHECK_INTERVAL:
            self._last_reload_time = now
            if config.try_reload():
                new_symbol = config.get_symbol()
                symbol_changed = (new_symbol != self.symbol)
                if symbol_changed:
                    logger.info("♻️ 交易对切换: %s → %s", self.symbol, new_symbol)
                    self.symbol = new_symbol
                    # 重新获取新交易对的精度信息
                    try:
                        filters = get_symbol_filters(self.client, self.symbol)
                        self.tick_size = filters["tick_size"]
                        self.step_size = filters["step_size"]
                    except Exception as e:
                        logger.error("♻️ 获取新交易对 %s 信息失败: %s", self.symbol, e)
                        return

                self.trade_quantity = round_quantity(config.get_trade_quantity(), self.step_size)
                self.savepos_qty = round_quantity(
                    config.get_trade_quantity() * config.get_savepos_multiplier(), self.step_size
                )
                # ATR 模式不覆盖动态步长，仅 fixed 模式从 config 取值
                if config.get_grid_mode() != "atr":
                    grid_size = config.get_grid_size()
                    self.state.signed_grid_size = (
                        grid_size if config.get_trade_type() == "LONG" else -grid_size
                    )
                # ATR 模式切换时重置状态，触发即时刷新
                if config.get_grid_mode() == "atr":
                    self._last_atr_update = 0.0
                    self._last_atr_value = 0.0
                    logger.info("♻️ ATR 模式已启用，将在下个周期计算动态步长")
                logger.info(
                    "♻️ 参数已热更新 | quantity=%s | savepos=%s | grid=%s | mode=%s | symbol=%s",
                    self.trade_quantity, self.savepos_qty,
                    abs(self.state.signed_grid_size), config.get_grid_mode(), self.symbol,
                )
                # 重新设置杠杆（热更新生效）
                try:
                    self.client.futures_change_leverage(symbol=self.symbol, leverage=config.get_leverage())
                    logger.info("♻️ 已重设杠杆为 %sx", config.get_leverage())
                except Exception as e:
                    logger.warning("♻️ 重设杠杆失败（非致命）: %s", e)
                # 交易对切换时重新初始化网格
                if symbol_changed:
                    price = self._current_price()
                    self.state.init_from_price(price)
                    self._cancel_all()
                    self._place_both_grid_orders(price)
                    logger.info("♻️ 已在新交易对 %s 上重新初始化网格", self.symbol)
                self._push_state(message="♻️ 配置已热更新")


def _trading_loop(orch):
    """交易主循环（可在主线程或后台线程运行）。"""
    add_order = None
    while True:
        try:
            time.sleep(config.POLL_INTERVAL)

            # 0. 配置热重载（节流检查）
            orch._try_config_reload()

            # 0.5. K线数据获取（节流30秒）
            orch._fetch_klines()

            # 0.6. ATR 动态步长刷新
            orch._try_update_atr()

            # 1. 补仓监控
            add_order = orch.handle_savpos(add_order)
            if add_order is not None:
                time.sleep(config.POLL_INTERVAL)
                continue

            price = orch._current_price()
            orch._push_state(current_price=price)

            # 2. 价格区间检查
            if not is_price_in_range(price):
                orch._push_state(price_status="超出区间", message="价格超出区间，等待中...")
                time.sleep(config.PRICE_OOR_SLEEP)
                continue

            orch._push_state(price_status="区间内")

            # 3. 网格维护（无挂单 → 重建）
            orch.handle_grid_orders()

            # 4. 突破处理
            orch.handle_breakout()

        except BinanceAPIException as e:
            logger.error("API 错误: %s", e)
            orch._push_state(message=f"API 错误: {e}")
            time.sleep(config.ERROR_SLEEP)
        except Exception as e:
            logger.error("未知错误: %s", e)
            orch._push_state(message=f"错误: {e}")
            time.sleep(config.ERROR_SLEEP)


def run(client):
    """主入口：初始化并进入事件循环。"""
    symbol = config.get_symbol()

    # 获取交易对精度信息
    try:
        filters = get_symbol_filters(client, symbol)
    except Exception as e:
        logger.error("获取交易对信息失败: %s", e)
        return

    tick_size = filters["tick_size"]
    step_size = filters["step_size"]
    logger.info(
        "交易对 %s 规则: tickSize=%s, stepSize=%s, minNotional=%s",
        symbol, tick_size, step_size, filters["min_notional"],
    )

    trade_qty = round_quantity(config.get_trade_quantity(), step_size)
    savepos_qty = round_quantity(config.get_trade_quantity() * config.get_savepos_multiplier(), step_size)
    logger.info("调整后交易数量: %s, 补仓数量: %s", trade_qty, savepos_qty)

    orch = GridOrchestrator(
        client, symbol, trade_qty, savepos_qty, tick_size, step_size
    )
    orch.setup()

    if config.GUI_ENABLED:
        # GUI 需要在主线程运行（tkinter 要求），
        # 交易循环放到后台线程。
        logger.info("🖥️ 启动 GUI 监控面板...")
        from threading import Thread
        t = Thread(target=_trading_loop, args=(orch,), daemon=True)
        t.start()
        gui.launch()  # 阻塞在主线程
    else:
        logger.info("开始监控价格...")
        _trading_loop(orch)
