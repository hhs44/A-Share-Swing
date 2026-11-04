# 测试与验收记录（2026-11-04）

模拟研究，不构成投资建议。没有真实回测收益数字；全部新增回测测试使用合成数据。

## 实际执行

| 检查 | 实际结果 |
|---|---|
| 修改前 `.venv/bin/python -m pytest -q` | `10 passed in 1.45s` |
| 修改前 demo扫描 | 成功，候选2/2，演示数据=True |
| 修改后 `.venv/bin/python -m pytest -q` | `41 passed in 6.74s` |
| 修改后 `python -m ashare_swing scan --demo --asof 2026-11-04` | 成功，候选2/2，输出DEMO.md及独立DEMO.db |
| `python -m compileall -q ashare_swing tests` | 通过，退出码0 |
| `git diff --check` | 通过，无输出 |
| 不提供bundle的真实scan | 按预期失败，RuntimeError：真实扫描需要包含历史状态的bundle |
| 缺少bundle的真实backtest | 按预期失败，写入`valid:false, metrics:null`，缺少data/history/manifest.json |
| GitHub Actions CI | 以PR实际Checks为准，本文不预先声明远端通过 |

首次新增测试曾有2项断言失败：错误要求期末未平仓买单必须已卖出、以及过期时间测试误用了同日时间戳。已修正测试场景，保留期末未平仓盯市行为；最终全部通过。

## 全部测试清单

以下41个参数展开的测试均通过，可运行 `python -m pytest --collect-only -q` 核对：

- 原有10项：信号幂等、100股预算、昂贵股票拒绝、12%冻结、持仓数上限、未来数据隔离、缺少基准拒绝、同值重复行排序、非正价格拒绝、历史不足。
- `test_historical_fees`：2023-08-28印花税及2022-04-29过户费边界、最低佣金。
- `test_drawdown_boundaries`（4项）：正常、8%、12%、15%。
- `test_nonfinite_order`（4项）：NaN、无穷、负数、零。
- `test_target_and_unaffordable`：1500目标及无法买一手。
- `test_bad_bars_fail_closed`（4项）：缺失、无穷、冲突重复、OHLC错误。
- `test_session_clock`：16:30前后、周末及日历覆盖不足。
- `test_event_engine_and_future_isolation`：真实账本机制、现金、整手、次日成交、修改未来行情不改变先前结果。
- `test_limit_up_and_suspended_never_buy`：逐日权威涨停/停牌输入拦截买入。
- `test_missing_status_and_action_fail_closed`：公司行动不支持时拒绝绩效。
- `test_missing_index_fail_closed`：缺少双指数日行情拒绝。
- `test_hash_tamper`：文件变更拒绝。
- `test_daily_idempotence_and_publication`：Markdown/JSON日报、信号幂等、19:00前拒绝发布、内容篡改拒绝。
- `test_nontrading_and_stale_report`：过期采集、非完整交易日拒绝。
- `test_t_plus_one_and_limit_down_delays_exit`：买入日不卖、跌停卖不出。
- `test_liquidity_constraint`：成交量约束拦截。
- `test_ipo_and_missing_status`：IPO日期前上市状态拒绝。
- `test_demo_never_overwrites_real_signals`：演示数据库隔离。
- `test_invalid_backtest_writes_no_metrics`：无效回测无绩效。
- `test_portfolio_halt_latches_after_gap_loss`：双股票跳空损失、15%停机锁存。
- `test_outcomes_mature_only_and_idempotent`：仅写已成熟的1/5/10/15日表现，重复不增行。
- `test_one_weak_index_blocks_new_signals`：单个弱指数阻止开仓。
- `test_missing_stock_session_fails_closed`：缺少股票交易日行情拒绝。

这些测试不证明真实历史涨跌停数据本身准确；规则输入来自外部逐日权威数据，尚未完成真实数据校验。

## AKShare实际探测

已安装AKShare **1.20.25**；四个函数存在，实际远程调用均失败（ProxyError，代理连接被拒绝/Operation not permitted）：

| 调用 | 目标来源 | 结果 |
|---|---|---|
| `tool_trade_date_hist_sina()` | finance.sina.com.cn | 失败，未取得交易日历 |
| `stock_info_a_code_name()` | query.sse.com.cn | 失败，未取得证券名称 |
| `stock_zh_a_hist(symbol='000001', period='daily', start_date='20261001', end_date='20261104', adjust='')` | push2his.eastmoney.com | 失败，未取得日线或时间戳 |
| `stock_zh_index_daily(symbol='sh000300')` | finance.sina.com.cn | 失败，未取得基准 |

网络配置restricted，行情域名不在允许清单；未绕过代理，未关闭TLS，未替换为编造数据。即使放通上述域名，AKShare单独的当前名单/原始日线也不足以完成含退市和公司行动的历史审计。

## 未解决及验收结论

1. 没有获授权的逐日历史全股票池、状态和公司行动数据包；供应链、历史名称/IPO/ST/退市/停复牌/历史涨跌停真实性尚未独立核验。
2. 公司行动、现金分红/拆股/配股及退市资金处置未实现，检测到公司行动拒绝回测。
3. 2020–2023、2024–2025、2026真实扣费绩效**未完成**，不可发布总收益、年化、最大回撤或胜率为真实成绩。
4. 自动化时间表/重试/产物归档已实现，但尚未配置数据更新供应方，默认缺少数据会失败，尚未验证真实环境无人值守报告。GitHub定时任务可能延迟。
5. 日报仅每天独立空仓模拟计划，持续组合风控由回测实现；尚未实现跨日持续模拟账户。Actions cache非可靠数据库，多进程CLI及跨文件/SQLite事务仍需部署层管理。
6. 数据包manifest真实性声明需要人工/供应方审计，哈希不证明无幸存者偏差。日线成交模拟不还原队列；不支持北交所/ETF。

结论：可审阅的V1工程候选，**尚未满足完整V1验收**。PR应保持草稿供人工审阅，不合并main。不连接实盘、不产生真实交易指令。
