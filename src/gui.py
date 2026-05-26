"""
GUI 监控面板（tkinter，零外部依赖）
从共享状态字典读取数据，定时刷新。
包含 K线图（可切换时间周期）。
可在 config.ini [gui] enabled 中开关。
"""
import tkinter as tk
from tkinter import ttk
import logging
import math
import time
from datetime import datetime, timezone
from threading import Lock
from . import config

logger = logging.getLogger(__name__)

# =====================================================
# 线程安全共享状态（GridOrchestrator 写，GUI 读）
# =====================================================
_state_lock = Lock()
_shared = {
    "symbol":           "—",
    "current_price":    0.0,
    "last_filled":      0.0,
    "grid_size":        0.0,
    "buy_order_price":  0.0,
    "sell_order_price": 0.0,
    "trade_type":       "—",
    "leverage":         0,
    "position":         0.0,
    "buy_orders":       0,
    "sell_orders":      0,
    "savpos_active":    False,
    "price_status":     "等待中",
    "trade_quantity":   0.0,
    "savepos_qty":      0.0,
    "upper_limit":      0.0,
    "lower_limit":      0.0,
    "message":          "启动中...",
    "running":          True,
    "klines":           [],
    "kline_interval":   config.KLINE_INTERVAL,
}


def set_state(**kwargs):
    """批量更新共享状态（线程安全）。"""
    with _state_lock:
        _shared.update(kwargs)


def get_state_copy():
    """获取状态快照。"""
    with _state_lock:
        return dict(_shared)


# =====================================================
# K线绘制常量
# =====================================================
CANDLE_BODY_MIN = 1       # 最小实体高度（像素）
WICK_COLOR = "#4b5563"    # 影线颜色
UP_COLOR    = "#10b981"   # 阳线（涨）
DOWN_COLOR  = "#ef4444"   # 阴线（跌）
BG_COLOR    = "#1e293b"   # 图表背景
GRID_COLOR  = "#334155"   # 网格线
TEXT_COLOR  = "#94a3b8"   # 坐标文字
BORDER_COLOR = "#475569"  # 边框

KLINE_INTERVALS = ["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"]


# =====================================================
# K线图表组件（Canvas 绘制）
# =====================================================
class KlineChart(tk.Canvas):
    """用 tkinter Canvas 绘制 K 线蜡烛图。"""

    def __init__(self, parent, **kw):
        super().__init__(parent, bg=BG_COLOR, highlightthickness=0, **kw)
        self._klines: list[dict] = []
        self._interval = config.KLINE_INTERVAL
        self._grid_price: float = 0.0
        self._current_price: float = 0.0

    def set_data(self, klines: list[dict], interval: str,
                 grid_price: float = 0.0, current_price: float = 0.0):
        """更新数据和元信息，随后重绘。"""
        self._klines = klines
        self._interval = interval
        self._grid_price = grid_price
        self._current_price = current_price
        self._redraw()

    def _redraw(self):
        self.delete("all")
        klines = self._klines
        if not klines:
            self.create_text(
                self.winfo_width() / 2 if self.winfo_width() > 1 else 300,
                self.winfo_height() / 2 if self.winfo_height() > 1 else 200,
                text="等待K线数据...", fill=TEXT_COLOR, font=("Consolas", 12),
            )
            return

        w = self.winfo_width()
        h = self.winfo_height()
        if w < 100 or h < 100:
            return

        # 边距
        margin_top    = 30
        margin_bottom = 40
        margin_left   = 70
        margin_right  = 20

        chart_w = w - margin_left - margin_right
        chart_h = h - margin_top - margin_bottom
        if chart_w <= 0 or chart_h <= 0:
            return

        # 价格范围（包含当前价和网格价）
        prices = []
        for k in klines:
            prices.extend([k["high"], k["low"]])
        if self._current_price > 0:
            prices.append(self._current_price)
        if self._grid_price > 0:
            prices.append(self._grid_price)

        if not prices:
            return
        price_min = min(prices)
        price_max = max(prices)
        # 留 5% 上下边距
        pad = (price_max - price_min) * 0.05 if price_max > price_min else 1.0
        price_min -= pad
        price_max += pad
        if price_max <= price_min:
            price_max = price_min + 1.0
        price_range = price_max - price_min

        n = len(klines)
        candle_gap_ratio = 0.25
        candle_w = max(2, chart_w / n * (1 - candle_gap_ratio))

        def to_y(p):
            return margin_top + chart_h * (1.0 - (p - price_min) / price_range)

        def to_x(i):
            return margin_left + chart_w * (i + 0.5) / n

        # --- 网格线 & 价格标签 ---
        grid_lines = 6
        # 根据价格量级自动选择小数位
        if price_range > 0:
            auto_prec = max(0, min(8, int(-math.log10(price_range)) + 2))
        else:
            auto_prec = 2
        for gi in range(grid_lines + 1):
            frac = gi / grid_lines
            y = margin_top + chart_h * (1.0 - frac)
            price_label = price_min + frac * price_range
            self.create_line(margin_left, y, w - margin_right, y,
                             fill=GRID_COLOR, dash=(2, 4), width=1)
            self.create_text(margin_left - 5, y, text=f"{price_label:.{auto_prec}f}",
                             fill=TEXT_COLOR, font=("Consolas", 8),
                             anchor="e")

        # --- 绘制 K 线 ---
        for i, k in enumerate(klines):
            open_p, high_p, low_p, close_p = k["open"], k["high"], k["low"], k["close"]
            x = to_x(i)
            y_open  = to_y(open_p)
            y_close = to_y(close_p)
            y_high  = to_y(high_p)
            y_low   = to_y(low_p)

            is_up = close_p >= open_p
            color = UP_COLOR if is_up else DOWN_COLOR

            # 影线
            self.create_line(x, y_high, x, y_low, fill=WICK_COLOR, width=1)

            # 实体
            body_h = abs(y_close - y_open)
            if body_h < CANDLE_BODY_MIN:
                body_h = CANDLE_BODY_MIN
            y_top = min(y_open, y_close)
            self.create_rectangle(
                x - candle_w / 2, y_top,
                x + candle_w / 2, y_top + body_h,
                fill=color, outline=color, width=0,
            )

        # --- 当前价格线（黄虚线）---
        if self._current_price > 0 and price_min < self._current_price < price_max:
            y_cp = to_y(self._current_price)
            self.create_line(margin_left, y_cp, w - margin_right, y_cp,
                             fill="#fbbf24", dash=(6, 3), width=1.5)
            self.create_text(w - margin_right, y_cp,
                             text=f" {self._current_price:.{auto_prec}f}",
                             fill="#fbbf24", font=("Consolas", 9, "bold"),
                             anchor="w")

        # --- 网格参考线（白虚线）---
        if self._grid_price > 0 and price_min < self._grid_price < price_max:
            y_gp = to_y(self._grid_price)
            self.create_line(margin_left, y_gp, w - margin_right, y_gp,
                             fill="#e2e8f0", dash=(4, 6), width=1)
            self.create_text(margin_left + 5, y_gp,
                             text=f"网格 {self._grid_price:.{auto_prec}f}",
                             fill="#e2e8f0", font=("Consolas", 8),
                             anchor="sw")

        # --- 底部时间标签 ---
        time_labels = 5
        for ti in range(time_labels + 1):
            idx = int(ti * (n - 1) / max(time_labels, 1))
            if 0 <= idx < n:
                k = klines[idx]
                ts = k["time"] / 1000.0
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                if self._interval.endswith("m") or self._interval.endswith("h"):
                    label = dt.strftime("%H:%M")
                elif self._interval == "1d":
                    label = dt.strftime("%m-%d")
                else:
                    label = dt.strftime("%m-%d %H:%M")
                x_t = to_x(idx)
                self.create_text(x_t, h - margin_bottom + 20,
                                 text=label, fill=TEXT_COLOR,
                                 font=("Consolas", 8), anchor="n")


# =====================================================
# GUI 组件
# =====================================================
class Dashboard(ttk.Frame):
    def __init__(self, root):
        super().__init__(root, padding=10)
        self.root = root
        self.pack(fill="both", expand=True)
        self._create_widgets()

    def _create_widgets(self):
        self._widget_map = {}

        # ---------- 标题栏 ----------
        title_frame = ttk.Frame(self)
        title_frame.pack(fill="x", pady=(0, 4))

        self.lbl_symbol = ttk.Label(title_frame, text="CLUSDT",
                                     font=("Consolas", 14, "bold"),
                                     foreground="#2563eb")
        self.lbl_symbol.pack(side="left")
        ttk.Label(title_frame, text="📊 网格交易监控",
                  font=("Microsoft YaHei", 14, "bold")).pack(side="left", padx=10)
        self.lbl_env = ttk.Label(title_frame, text="", foreground="#6b7280")
        self.lbl_env.pack(side="right", padx=4)

        # ---------- K线图区域（占上半部分）----------
        chart_frame = ttk.Frame(self)
        chart_frame.pack(fill="both", expand=True, pady=(0, 4))

        # K线图
        self.chart = KlineChart(chart_frame, width=600, height=320)
        self.chart.pack(side="left", fill="both", expand=True)

        # 右侧面板：周期选择 + 信息
        right_panel = ttk.Frame(chart_frame, padding=(6, 0, 0, 0))
        right_panel.pack(side="right", fill="y")

        ttk.Label(right_panel, text="时间周期", font=("Microsoft YaHei", 9, "bold")).pack(pady=(0, 2))
        self._interval_var = tk.StringVar(value=config.KLINE_INTERVAL)
        self._interval_combo = ttk.Combobox(
            right_panel, textvariable=self._interval_var,
            values=KLINE_INTERVALS, state="readonly", width=6,
        )
        self._interval_combo.pack(pady=(0, 8))

        # 周期切换回调
        self._interval_var.trace_add("write", self._on_interval_change)

        # 价格标签
        self.lbl_chart_price = ttk.Label(
            right_panel, text="—",
            font=("Consolas", 13, "bold"), foreground="#fbbf24",
        )
        self.lbl_chart_price.pack(pady=(8, 2))

        # K线快捷信息
        self.lbl_ohlc = ttk.Label(right_panel, text="O:— H:— L:— C:—",
                                   font=("Consolas", 8), foreground=TEXT_COLOR)
        self.lbl_ohlc.pack(pady=(0, 4))

        # ---------- 下半部分：信息面板 ----------
        bottom = ttk.Frame(self)
        bottom.pack(fill="x")

        # 左栏：价格
        left = ttk.LabelFrame(bottom, text="💰 价格", padding=4)
        left.pack(side="left", fill="both", expand=True, padx=(0, 2))

        lbl_font = ("Consolas", 10)
        self._add_row(left, "当前价", "lbl_current_price", lbl_font, "#059669")
        self._add_row(left, "最新成交", "lbl_last_filled", lbl_font)
        self._add_row(left, "网格买入", "lbl_buy_price", lbl_font, "#2563eb")
        self._add_row(left, "网格卖出", "lbl_sell_price", lbl_font, "#dc2626")
        self._add_row(left, "网格步长", "lbl_grid_size", lbl_font)

        # 中栏：持仓/挂单
        mid = ttk.LabelFrame(bottom, text="📋 持仓&挂单", padding=4)
        mid.pack(side="left", fill="both", expand=True, padx=2)

        self._add_row(mid, "方向/杠杆", "lbl_trade_info", lbl_font)
        self._add_row(mid, "持仓数量", "lbl_position", lbl_font)
        self._add_row(mid, "挂单(买)", "lbl_buy_orders", lbl_font, "#2563eb")
        self._add_row(mid, "挂单(卖)", "lbl_sell_orders", lbl_font, "#dc2626")
        self._add_row(mid, "补仓状态", "lbl_savpos", lbl_font)

        # 右栏：参数
        right = ttk.LabelFrame(bottom, text="⚙️ 参数", padding=4)
        right.pack(side="left", fill="both", expand=True, padx=(2, 0))

        self._add_row(right, "交易数量", "lbl_trade_qty", lbl_font)
        self._add_row(right, "补仓数量", "lbl_savepos_qty", lbl_font)
        self._add_row(right, "价格上限", "lbl_upper_limit", lbl_font)
        self._add_row(right, "价格下限", "lbl_lower_limit", lbl_font)
        self._add_row(right, "价格状态", "lbl_price_status", lbl_font)

        # 底部消息栏
        msg_frame = ttk.Frame(self)
        msg_frame.pack(fill="x", pady=(4, 0))
        self.lbl_message = ttk.Label(msg_frame, text="启动中...",
                                      foreground="#6b7280",
                                      font=("Microsoft YaHei", 9))
        self.lbl_message.pack(side="left")

        # 映射 name → widget
        self._labels = {}
        for name, widget in self._widget_map.items():
            self._labels[name] = widget

    def _add_row(self, parent, label_text, widget_name, font, fg="#374151"):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=1)
        ttk.Label(row, text=label_text + " ", font=font, width=8).pack(side="left")
        val = ttk.Label(row, text="—", font=font, foreground=fg)
        val.pack(side="right")
        self._widget_map[widget_name] = val

    def _on_interval_change(self, *_):
        """周期切换时更新共享状态。"""
        new_interval = self._interval_var.get()
        set_state(kline_interval=new_interval)

    def refresh(self):
        """从共享状态刷新所有标签和K线图。"""
        s = get_state_copy()
        if not s["running"]:
            self.root.quit()
            return

        self.lbl_symbol.config(text=s.get("symbol", "—"))
        self.lbl_env.config(text="测试网" if config.TESTNET else "主网")

        def _set(name, fmt, key=None):
            key = key or name
            val = s.get(key, 0)
            if isinstance(val, float):
                text = f"{fmt(val)}" if val else "—"
            elif isinstance(val, bool):
                text = "是" if val else "否"
            else:
                text = str(val) if val else "—"
            self._labels[name].config(text=text)

        fmt4 = lambda v: f"{v:.4f}"
        fmt2 = lambda v: f"{v:.2f}"

        _set("lbl_current_price", fmt4, "current_price")
        _set("lbl_last_filled",   fmt4, "last_filled")
        _set("lbl_buy_price",     fmt4, "buy_order_price")
        _set("lbl_sell_price",    fmt4, "sell_order_price")
        _set("lbl_grid_size",     fmt4, "grid_size")

        tt = s.get("trade_type", "—")
        lev = s.get("leverage", 0)
        self._labels["lbl_trade_info"].config(
            text=f"{tt}  {lev}x" if tt != "—" else "—",
            foreground="#059669" if tt == "LONG" else ("#dc2626" if tt == "SHORT" else "#374151"),
        )

        _set("lbl_position",    fmt2, "position")
        _set("lbl_buy_orders",  str,  "buy_orders")
        _set("lbl_sell_orders", str,  "sell_orders")
        _set("lbl_savpos",      str,  "savpos_active")
        self._labels["lbl_savpos"].config(
            text="补仓中" if s.get("savpos_active") else "空闲",
            foreground="#f59e0b" if s.get("savpos_active") else "#374151",
        )

        _set("lbl_trade_qty",   fmt2, "trade_quantity")
        _set("lbl_savepos_qty", fmt2, "savepos_qty")
        _set("lbl_upper_limit", fmt2, "upper_limit")
        _set("lbl_lower_limit", fmt2, "lower_limit")

        ps = s.get("price_status", "—")
        self._labels["lbl_price_status"].config(
            text=ps,
            foreground="#059669" if ps == "区间内" else ("#dc2626" if ps == "超出区间" else "#374151"),
        )

        self.lbl_message.config(text=s.get("message", ""))

        # 当前价格大字
        cp = s.get("current_price", 0)
        if cp > 0:
            cp_prec = max(0, min(8, int(-math.log10(cp)) + 3))
        else:
            cp_prec = 2
        self.lbl_chart_price.config(
            text=f"{cp:.{cp_prec}f}" if cp else "—",
            foreground="#fbbf24" if cp else TEXT_COLOR,
        )

        # 最新K线 O/H/L/C
        klines = s.get("klines", [])
        if klines:
            last = klines[-1]
            # 根据价格量级自适应精度
            cp_val = s.get("current_price", 0) or last["close"]
            if cp_val > 0:
                p = max(0, min(8, int(-math.log10(cp_val)) + 3))
            else:
                p = 2
            self.lbl_ohlc.config(
                text=f"O:{last['open']:.{p}f} H:{last['high']:.{p}f}\nL:{last['low']:.{p}f} C:{last['close']:.{p}f}"
            )
        else:
            self.lbl_ohlc.config(text="O:— H:— L:— C:—")

        # 同步下拉框
        ki = s.get("kline_interval", config.KLINE_INTERVAL)
        if self._interval_var.get() != ki:
            self._interval_var.set(ki)

        # 更新 K 线图
        self.chart.set_data(
            klines, ki,
            grid_price=s.get("last_filled", 0),
            current_price=s.get("current_price", 0),
        )

        # 下次刷新
        ms = max(config.GUI_REFRESH_MS, 200)
        self.root.after(ms, self.refresh)


def launch():
    """启动 GUI 主循环（阻塞）。"""
    root = tk.Tk()
    root.title("网格交易监控")
    root.geometry("860x720")
    root.resizable(True, True)
    root.configure(bg="#f8fafc")

    style = ttk.Style()
    style.theme_use("clam")

    dashboard = Dashboard(root)
    root.after(config.GUI_REFRESH_MS, dashboard.refresh)
    root.mainloop()
