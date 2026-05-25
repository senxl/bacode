"""
配置管理模块
从 config.ini 读取，按交易对分 section，支持运行时热重载。
修改 ini 文件后无需重启 bot，下次轮询自动生效。

格式：
  [general]         — 全局设置 + active_symbol
  [CLUSDT]          — 对应 symbol 的专属参数
  [gui]             — GUI 面板开关与参数
"""
import os
import logging
from configparser import ConfigParser
from threading import RLock
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# INI 文件路径
# ---------------------------------------------------------------------------
_CONFIG_DIR = Path(os.getenv("BACODE_CONFIG_DIR", Path(__file__).resolve().parent.parent))
_INI_PATH = _CONFIG_DIR / "config.ini"

# ---------------------------------------------------------------------------
# 线程安全的配置缓存
# ---------------------------------------------------------------------------
_lock = RLock()
_parser = ConfigParser()
_last_mtime: float = 0.0

# —— 全局参数 ——
API_KEY: str = ""
API_SECRET: str = ""
TESTNET: bool = False
ACTIVE_SYMBOL: str = "CLUSDT"

POLL_INTERVAL: float = 0.3
PRICE_OOR_SLEEP: float = 5.0
ERROR_SLEEP: float = 1.0
RELOAD_CHECK_INTERVAL: float = 3.0

MIN_NOTIONAL: float = 5.0

# —— GUI ——
GUI_ENABLED: bool = False
GUI_REFRESH_MS: int = 500
KLINE_INTERVAL: str = "5m"
KLINE_LIMIT: int = 50


# —— 按 symbol 缓存的交易参数 ——
class SymbolConfig:
    __slots__ = (
        "trade_type", "leverage", "trade_quantity", "savepos_multiplier",
        "grid_size", "grid_size_over", "price_upper_limit", "price_lower_limit",
        "grid_mode", "atr_period", "atr_multiplier", "atr_update_interval", "atr_change_threshold",
    )
    def __init__(self):
        self.trade_type: str = "SHORT"
        self.leverage: int = 20
        self.trade_quantity: float = 0.15
        self.savepos_multiplier: float = 5.0
        self.grid_size: float = 0.1
        self.grid_size_over: float = 1.5
        self.price_upper_limit: float = 110.0
        self.price_lower_limit: float = 50.0
        # —— ATR 动态步长 ——
        self.grid_mode: str = "fixed"          # fixed / atr
        self.atr_period: int = 14              # ATR 计算周期
        self.atr_multiplier: float = 0.5       # grid_size = ATR × multiplier
        self.atr_update_interval: float = 300  # ATR 刷新间隔（秒），默认 5 分钟
        self.atr_change_threshold: float = 0.1 # ATR 变化超过此比例才更新步长（10%）


_all_symbols: dict[str, SymbolConfig] = {}


# ---------------------------------------------------------------------------
# 便捷访问器（始终指向当前 active_symbol 的参数）
# ---------------------------------------------------------------------------
def _current() -> SymbolConfig:
    """获取当前激活交易对的配置（线程安全）。"""
    with _lock:
        return _all_symbols.setdefault(ACTIVE_SYMBOL, SymbolConfig())


def get_trade_type() -> str:
    return _current().trade_type

def get_leverage() -> int:
    return _current().leverage

def get_trade_quantity() -> float:
    return _current().trade_quantity

def get_savepos_multiplier() -> float:
    return _current().savepos_multiplier

def get_grid_size() -> float:
    return _current().grid_size

def get_grid_size_over() -> float:
    return _current().grid_size_over

def get_price_upper_limit() -> float:
    return _current().price_upper_limit

def get_price_lower_limit() -> float:
    return _current().price_lower_limit

def get_grid_mode() -> str:
    return _current().grid_mode

def get_atr_period() -> int:
    return _current().atr_period

def get_atr_multiplier() -> float:
    return _current().atr_multiplier

def get_atr_update_interval() -> float:
    return _current().atr_update_interval

def get_atr_change_threshold() -> float:
    return _current().atr_change_threshold

def get_symbol() -> str:
    with _lock:
        return ACTIVE_SYMBOL


# ---------------------------------------------------------------------------
# 加载 / 热重载
# ---------------------------------------------------------------------------
def _load_ini() -> bool:
    """读取 INI 文件并刷新缓存。返回 True 表示有变更。"""
    global _last_mtime, _all_symbols

    try:
        mtime = _INI_PATH.stat().st_mtime
    except FileNotFoundError:
        logger.error("配置文件不存在: %s", _INI_PATH)
        return False

    if mtime <= _last_mtime:
        return False

    with _lock:
        try:
            mtime = _INI_PATH.stat().st_mtime
        except FileNotFoundError:
            return False
        if mtime <= _last_mtime:
            return False

        _parser.read(_INI_PATH, encoding="utf-8")

        global API_KEY, API_SECRET, TESTNET, ACTIVE_SYMBOL
        global POLL_INTERVAL, PRICE_OOR_SLEEP, ERROR_SLEEP, RELOAD_CHECK_INTERVAL
        global GUI_ENABLED, GUI_REFRESH_MS, KLINE_INTERVAL, KLINE_LIMIT

        # —— general ——
        API_KEY    = _parser.get("general", "api_key",    fallback="")
        API_SECRET = _parser.get("general", "api_secret", fallback="")
        TESTNET    = _parser.getboolean("general", "testnet", fallback=False)
        ACTIVE_SYMBOL = _parser.get("general", "active_symbol", fallback="CLUSDT")

        POLL_INTERVAL        = _parser.getfloat("general", "poll_interval",          fallback=0.3)
        PRICE_OOR_SLEEP      = _parser.getfloat("general", "price_oor_sleep",        fallback=5.0)
        ERROR_SLEEP          = _parser.getfloat("general", "error_sleep",            fallback=1.0)
        RELOAD_CHECK_INTERVAL = _parser.getfloat("general", "reload_check_interval", fallback=3.0)

        # —— gui ——
        GUI_ENABLED   = _parser.getboolean("gui", "enabled",    fallback=False)
        GUI_REFRESH_MS = _parser.getint("gui", "refresh_ms",    fallback=500)
        KLINE_INTERVAL = _parser.get("gui", "kline_interval", fallback="5m")
        KLINE_LIMIT   = _parser.getint("gui", "kline_limit",   fallback=50)

        # —— 按 symbol 加载独立配置 ——
        _all_symbols.clear()
        for section in _parser.sections():
            if section.lower() in ("general", "gui"):
                continue
            # section 名即交易对名
            sc = SymbolConfig()
            sc.trade_type          = _parser.get(section, "trade_type",          fallback="SHORT").upper()
            sc.leverage            = _parser.getint(section, "leverage",          fallback=20)
            sc.trade_quantity      = _parser.getfloat(section, "trade_quantity",  fallback=0.15)
            sc.savepos_multiplier  = _parser.getfloat(section, "savepos_multiplier", fallback=5.0)
            sc.grid_size           = _parser.getfloat(section, "grid_size",        fallback=0.1)
            sc.grid_size_over      = _parser.getfloat(section, "grid_size_over",   fallback=1.5)
            sc.price_upper_limit   = _parser.getfloat(section, "price_upper_limit", fallback=110.0)
            sc.price_lower_limit   = _parser.getfloat(section, "price_lower_limit", fallback=50.0)
            sc.grid_mode           = _parser.get(section, "grid_mode", fallback="fixed").lower()
            sc.atr_period          = _parser.getint(section, "atr_period", fallback=14)
            sc.atr_multiplier      = _parser.getfloat(section, "atr_multiplier", fallback=0.5)
            sc.atr_update_interval = _parser.getfloat(section, "atr_update_interval", fallback=300.0)
            sc.atr_change_threshold = _parser.getfloat(section, "atr_change_threshold", fallback=0.1)
            _all_symbols[section] = sc

        _last_mtime = mtime

        symbols_str = ", ".join(_all_symbols.keys())
        logger.info("♻️ 配置已热重载 | 活跃交易对: %s | 可用: %s | GUI: %s | 文件: %s",
                     ACTIVE_SYMBOL, symbols_str, "开启" if GUI_ENABLED else "关闭", _INI_PATH)
        return True


def try_reload() -> bool:
    """如果 INI 有修改则重新加载。返回 True 表示本次有变更。"""
    return _load_ini()


def validate() -> list[str]:
    """校验必要配置，返回错误列表。"""
    errors = []
    if not API_KEY:
        errors.append("[general] api_key 未设置")
    if not API_SECRET:
        errors.append("[general] api_secret 未设置")
    cur = _current()
    if cur.trade_type not in ("LONG", "SHORT"):
        errors.append(f"[{ACTIVE_SYMBOL}] trade_type 必须为 LONG 或 SHORT，当前: {cur.trade_type}")
    if cur.grid_size <= 0:
        errors.append(f"[{ACTIVE_SYMBOL}] grid_size 必须 > 0")
    if cur.trade_quantity <= 0:
        errors.append(f"[{ACTIVE_SYMBOL}] trade_quantity 必须 > 0")
    return errors


def get_api_credentials() -> tuple[str, str, bool]:
    """线程安全地读取 API 凭据。"""
    with _lock:
        return API_KEY, API_SECRET, TESTNET


# ---------- 初始化加载 ----------
_load_ini()
