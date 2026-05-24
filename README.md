# 网格交易机器人

基于 Binance 合约的网格交易机器人，支持双向网格、突破追单、补仓管理和 GUI 实时监控面板。

## 功能特性

- **双向网格交易** — 支持 LONG（做多）和 SHORT（做空）方向
- **多交易对** — 一个配置文件中定义多个交易对，随时切换
- **配置热加载** — 运行时修改 `config.ini` 自动生效，无需重启
- **突破追单** — 价格偏离网格超出阈值时市价追单
- **补仓管理** — 持仓不足时自动挂单补仓，成交后追单至队列
- **GUI 监控面板** — 实时显示价格、持仓、网格状态和 K 线图
- **交易对精度对齐** — 自动根据 Binance 规则对齐价格和数量精度

## 环境要求

- Python 3.8+
- Binance 合约账户（支持测试网）

## 安装

```bash
# 克隆项目
git clone git@github.com:senxl/bacode.git
cd bacode

# 创建虚拟环境（可选）
python -m venv .venv
source .venv/bin/activate   # Linux / Mac
# .venv\Scripts\activate    # Windows

# 安装依赖
pip install python-binance
```

## 快速开始

1. **复制配置文件**，填入你的 API 密钥：

```bash
cp config.example.ini config.ini
```

2. **编辑 `config.ini`**，至少填写以下内容：

```ini
[general]
api_key = 你的API密钥
api_secret = 你的API密钥
testnet = true          # 测试网设为 true，实盘改为 false
active_symbol = ETHUSDT # 当前交易对
```

3. **运行机器人**：

```bash
python main.py
```

4. **启用 GUI 面板**（可选），在 `config.ini` 中设置：

```ini
[gui]
enabled = true
```

## 配置说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `trade_type` | 交易方向：`LONG` / `SHORT` | SHORT |
| `leverage` | 杠杆倍数（1~125） | 20 |
| `trade_quantity` | 每次下单数量 | 0.15 |
| `savepos_multiplier` | 补仓倍数（补仓量 = trade_quantity × 此值） | 5 |
| `grid_size` | 网格步长（买卖价间距） | 0.1 |
| `grid_size_over` | 突破倍数（偏离超过 grid_size × 此值时追单） | 1.5 |
| `price_upper_limit` | 价格上限（超出后暂停） | 110 |
| `price_lower_limit` | 价格下限（低于后暂停） | 50 |
| `poll_interval` | 主循环间隔（秒） | 0.3 |
| `reload_check_interval` | 配置热重载检测间隔（秒） | 3.0 |

## 交易逻辑

1. **初始化** — 设置杠杆，获取当前价，挂双边网格限价单
2. **补仓监控** — 持仓不足时挂补仓单，成交后追单
3. **网格维护** — 任一方向无挂单时，以最新成交价重建网格
4. **突破处理** — 价格突破网格 → 取消挂单 → 市价吃单 → 重新锁定网格

## 项目结构

```
bacode/
├── main.py              # 入口文件
├── config.example.ini   # 配置文件模板
├── src/
│   ├── config.py        # 配置管理和热加载
│   ├── contract.py      # 合约精度查询
│   ├── grid.py          # 网格交易编排器
│   ├── gui.py           # GUI 监控面板
│   ├── orders.py        # 订单操作（挂单/撤单/市价）
│   ├── position.py      # 持仓查询
│   └── strategy.py      # 网格状态机与策略判断
└── .gitignore
```

## 免责声明

本软件仅用于学习和研究目的。加密货币交易存在高风险，使用前请充分了解风险。作者不对任何交易损失负责。
