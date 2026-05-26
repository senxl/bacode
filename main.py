"""
网格交易机器人 —— 入口文件

用法:
  1. 编辑 config.ini 填入 API 密钥和参数
  2. python main.py
  3. 运行时直接修改 config.ini，bot 每几秒自动检测并热加载新配置
  4. 切换交易对：修改 [general] active_symbol，保存后自动切换
  5. GUI 面板：在 [gui] enabled = true 即可显示
"""
import sys
import logging
from binance.client import Client
from src import config
from src.grid import run

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logging.getLogger("src").setLevel(logging.INFO)

logger = logging.getLogger(__name__)


def main():
    # 首次加载（已在 import config 时完成）
    errors = config.validate()
    if errors:
        for e in errors:
            logger.error("配置错误: %s", e)
        sys.exit(1)

    api_key, api_secret, testnet = config.get_api_credentials()
    client = Client(api_key, api_secret, testnet=testnet)
    logger.info(
        "🚀 启动网格交易 | 交易对=%s | 方向=%s | 网格=%s | 数量=%s | 杠杆=%sx | 环境=%s | GUI=%s",
        config.get_symbol(), config.get_trade_type(), config.get_grid_size(),
        config.get_trade_quantity(), config.get_leverage(),
        "测试网" if testnet else "主网",
        "开启" if config.GUI_ENABLED else "关闭",
    )
    run(client)


if __name__ == "__main__":
    main()
