# moomoo_quant

个人 moomoo OpenAPI 多策略量化运行项目。当前阶段只使用美股 / 美股 ETF，以 JPY 作为统一记账与收益基准。历史回测是策略准入证据；日常入口是前向影子账户和组合运行控制台。

## 安全边界

- 严禁真实下单。
- 不调用 `TrdEnv.REAL`。
- 不自动解锁真实交易。
- 不修改任何实盘账户。
- 不使用融资、做空、期权或杠杆。
- 当前项目只读取行情、回测、生成理论信号和维护本地影子账本，不发送买卖订单。
- Execution Layer 只能持久化拟议订单；`execute()` 永远抛出异常。
- 默认 `QUANT_KILL_SWITCH=true`，生产 Dashboard 默认 `QUANT_ADMIN_MODE=false`。

## 安装

```powershell
cd C:\Users\71000\Documents\Codex\2026-08-10\moomoo-openapi-windows-moomoo-opend-opend
python -m pip install -r moomoo_quant\requirements.txt
```

## 启动 OpenD

1. 打开并登录 Moomoo OpenD。
2. 确认 OpenD 正在运行。
3. 本项目默认连接 `127.0.0.1:11112`，配置在 `config.py`。

## 测试连接

```powershell
python -m moomoo_quant.main quote-test
```

该命令使用 `OpenQuoteContext` 查询 `US.SPY`、`US.QQQ`、`US.AAPL` 的快照，并输出 code、name、last_price、open_price、high_price、low_price、prev_close_price、volume。

## 下载 / 更新行情

```powershell
python -m moomoo_quant.main update-data
```

历史日 K 会缓存到 `moomoo_quant\data\SPY_daily.csv`。再次执行时会从已有缓存最后日期向前重叠约 7 天增量更新，避免每次完整重下。

## 运行回测

```powershell
python -m moomoo_quant.main backtest
```

结果输出：

- `moomoo_quant\results\trades.csv`
- `moomoo_quant\results\equity_curve.csv`
- `moomoo_quant\results\equity_curve.png`
- `moomoo_quant\results\drawdown.png`
- `moomoo_quant\results\trade_return_distribution.csv`
- `moomoo_quant\results\yearly_performance.csv`
- `moomoo_quant\results\mae_mfe.csv`
- `moomoo_quant\results\monthly_returns.csv`
- `moomoo_quant\results\trades_on_spy.png`

回测同时输出策略诊断，包括 gross / net 收益拆分、手续费与滑点成本、单笔交易统计、退出原因统计、年度表现、市场暴露、MAE / MFE，以及 SPY 价格上的买卖点图。

## 策略与成交时间模型

策略只做多，最多同时一个仓位，不加仓，不摊平。初始资金简化为 `700 USD`，作为 `100,000 JPY` 的近似实验资金。每次最多使用账户资金的 25%。

指标：

- SMA 200
- RSI 5

开仓过滤：

- `close > SMA200`

买入条件：

- `RSI(5) < 25`

卖出条件满足任一：

- `RSI(5) > 55`
- 持仓达到 5 个交易日
- 收盘价较买入价下跌 2%

为了避免未来函数，回测采用明确的成交假设：第 T 日收盘后产生信号，只能在第 T+1 个交易日开盘成交。买入价为次日开盘价加滑点，卖出价为次日开盘价减滑点。手续费和滑点默认不为 0，参数集中在 `config.py`。

美股 / ETF 手续费按每次成交金额的 `0.132%` 计算，上限 `22 USD`，不足 `0.01 USD` 按 `0.01 USD`。滑点独立配置，不包含在手续费中。

## JPY Multi-Asset Trend

第二个实验策略只做行情读取和本地回测，不连接交易账户，也不发送订单：

```powershell
python -m moomoo_quant.main trend-backtest
```

标的为 SPY、QQQ、GLD、IEF。ETF 日 K 来自 moomoo OpenD，并使用 SDK 的 `AuType.QFQ` 前复权数据；OpenD 实测不支持 `FX.USDJPY`，因此 USDJPY 使用 Yahoo Finance 的 `JPY=X` 日线并缓存到 `data\USDJPY_daily.csv`。USDJPY 定义为 `1 USD = x JPY`。

信号只使用完整月份的最后一个有效 ETF 交易日。每个资产的 JPY 价格为 `USD close × USDJPY`，以 12 个月 JPY momentum 和 10 个月 JPY SMA 过滤并排名，最多等权持有两个资产。月末收盘产生的目标配置在下一个共同 ETF 交易日开盘执行。

FX 对齐严格禁止未来填充：月末信号使用该交易日当时已知的最近 USDJPY 收盘；次日开盘成交更保守地使用严格早于成交日的最近 USDJPY 收盘。缺失日期只做 backward as-of alignment，不使用 backward fill。JPY cash 收益固定为 0%。FX conversion cost 独立配置，默认首轮为 0。

主要输出：

- `results\trend_equity_curve.csv`、`trend_trades.csv`、`trend_allocations.csv`
- `results\trend_yearly_performance.csv`、`trend_robustness.csv`、`trend_oos_performance.csv`
- `results\trend_current_signal.csv`
- `results\trend_equity_curve.png`、`trend_drawdown.png`
- `results\trend_allocations.png`、`trend_asset_selection.png`

稳健性分析固定测试 9/12/15 个月 momentum 与 8/10/12 个月 SMA 的 9 个邻近组合，不据此修改基准参数。Development 截止 2018-12-31，OOS 从 2019-01-01 开始且以独立初始资金运行。Turnover 定义为累计单边成交名义金额除以期间平均账户权益。

最新完整月末信号与建议配置同时写入 `trend_current_signal.csv`，供 Dashboard 离线读取。

## Streamlit Dashboard

Dashboard 默认只读取已有的 `results` 和 `data` 文件，不会在打开页面时调用 OpenD：

```powershell
streamlit run moomoo_quant\dashboard.py
```

顶层页面为“运行总览”“机器人详情”“运行策略比较”“合并理论持仓”和“运行与安全”。只有已通过准入并获得预算的 A / B / C 会出现在运行层；被拒绝的版本只保留在机器人详情的“历史档案”中。Benchmark 只在机器人详情的“对照组”中出现，不是机器人。生产模式不显示重新运行按钮；只有显式设置 `QUANT_ADMIN_MODE=true` 才显示本地管理员入口。

每次 Trend 回测会生成唯一 `run_id`，完整结果放在 `results/runs/<run_id>/`，Dashboard 通过原子更新的 `current_run.json` 读取同一批表格、图形和危机数据。

## 多策略运行组合

```powershell
python -m moomoo_quant.main research-suite
python -m moomoo_quant.main multi-strategy-init
python -m pytest -q
```

运行层只聚合获得预算的机器人；研究拒绝策略继续占用研究槽位，但不贡献持仓：

- Robot A — `JPY Multi-Asset Trend v1`：JPY 计价的时间序列趋势，月频，预算 ¥100,000。v1 参数保持冻结。
- Robot B — `US Quality & Low Volatility v2`：QUAL / USMV 各 50% 的长期股票因子配置，半年再平衡，当前预算 ¥0。
- Robot C — `JPY Unlevered Risk Parity v1`：SPY / GLD / IEF 的 JPY 逆波动配置，月频，预算 ¥100,000。v1 参数保持冻结。

Robot B 不使用 A 的 momentum / SMA 信号，也不使用 C 的逆波动动态权重。不可变首跑档案曾记录为通过；修复完整月末和资产缓存隔离后，使用当前 OpenD 可重新获取的数据从 2013-10-30 重跑，最大回撤比同期 SPY JPY 差约 0.015 个百分点，因此预注册回撤门槛未通过。参数没有修改，当前状态为 `RESEARCH_REJECTED`，预算为 ¥0，也不会加载 Factor 行情进入生产 Runner。

获得预算的机器人由独立虚拟账户记录资金、持仓、信号、成交和净值。Portfolio Manager 只按策略预算聚合相同资产的净目标，Ledger 保留每一份持仓的策略归属，因此一个机器人退出不会卖掉另一个机器人拥有的份额。

## 被拒绝的研究档案

被拒绝的研究版本不是运行机器人，预算固定为 0：

- `Mean Reversion v1`：Gross 为正但成本后期望为负。
- `Stress Pullback Mean Reversion v2`：仅 8 笔，样本与年度分散门槛失败。
- `US Defensive Multi-Factor v1`：QUAL / VLUE / USMV 版本只未通过相对 SPY JPY 的最大回撤门槛。
- `US Quality & Low Volatility v2`：当前可复现数据下只未通过相对 SPY JPY 的最大回撤门槛；不可变首跑档案仍保留。

所有失败结果和预注册规格永久保留，但不会占用运行总览的机器人位置。

## 影子账户与每日任务

```powershell
python -m moomoo_quant.jobs.shadow_runner --run-once
```

统一 Runner 先增量更新 SPY / QQQ / GLD / IEF / QUAL / USMV 与 USDJPY 行情缓存，再按 XNYS 日历、America/New_York 时区、DST、各策略检查频率与数据完整性更新 SQLite 影子账本。它不会重发或覆盖冻结的正式回测 run。Signal、Virtual Fill、Equity 和 Reconciliation 都有唯一键，并由文件锁避免并发。它只做本地理论碎股记账，不连接交易账户。

风险检查分为 `SHADOW_SCOPE`、`PROPOSAL_SCOPE` 和始终禁用的 `EXECUTION_SCOPE`。Execution Kill switch 不会阻止本地 Virtual Fill，但陈旧行情、陈旧 FX、错误日历、版本/预算/幂等或 Ledger 不一致都会阻止。

`MoomooSimulateExecutionAdapter` 已实现 Mock 边界，但 `MOOMOO_SIMULATE_ENABLED=false` 是默认值。它固定 `TrdEnv.SIMULATE`，没有 REAL 开关，不创建交易 context；只有用户未来单独批准并提供受控 gateway 后才可能进入下一阶段。

服务器没有可用 OpenD 时，可只读取现有缓存：

```powershell
python -m moomoo_quant.main trend-backtest-cached
python -m moomoo_quant.main shadow-init
```

这两个命令不访问网络。`shadow-init` 首次运行只建立当前基线，不追溯执行历史信号；重复执行会按稳定 ID 去重。

## Ubuntu / AWS 部署

生产目录为 `/opt/stacks/moomoo-quant`，持久化数据目录为 `/opt/data/moomoo-quant`。systemd 服务：

```bash
sudo systemctl status moomoo-quant-dashboard
sudo systemctl status moomoo-quant-daily.timer
sudo journalctl -u moomoo-quant-daily.service -n 100 --no-pager
```

Streamlit 只监听 `127.0.0.1:8501`，由 Nginx 在 `https://quant.rubusoo.com` 反向代理。公网只需 80/443，不应开放 8501。如需重新启用现有 Nginx HTTP Basic Authentication，可在服务器上交互设置密码：

```bash
sudo /usr/local/sbin/set-moomoo-dashboard-password
```

密码输入由 `htpasswd` 隐藏，不写入代码、配置或命令历史。Nginx 配置修改前备份在 `/root/nginx-conf-backups/<timestamp>/`。

当前目标服务器为 x86-64，官方 OpenD 监听 `127.0.0.1:11112`。OpenD 仅供行情任务使用；本项目没有交易账户访问路径。部署细节和回滚步骤见 `docs/deployment.md` 与 `docs/migration_and_rollback.md`。
