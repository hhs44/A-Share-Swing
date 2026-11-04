# A股波段端到端验收（非选股日报）

模拟研究，不构成投资建议

运行：2026-11-04T17:52:09.203751+08:00
端到端验收：未通过
请求行情范围：2025-01-01 至 2026-11-04
已验证最近完整交易日：未知（无法确认）
真实候选股、模拟成交、真实绩效：以summary.json为准；null表示未完成验证。

|步骤|退出码|日志|
|---|---:|---|
|01-calendar|1|[01-calendar.log](01-calendar.log)|
|01-names|1|[01-names.log](01-names.log)|
|01-stock-000001|1|[01-stock-000001.log](01-stock-000001.log)|
|01-stock-600000|1|[01-stock-600000.log](01-stock-600000.log)|
|01-hs300|1|[01-hs300.log](01-hs300.log)|
|01-szcomponent|1|[01-szcomponent.log](01-szcomponent.log)|
|02-complete-session|1|[02-complete-session.log](02-complete-session.log)|
|03-selection|1|[03-selection.log](03-selection.log)|
|04-05-lot-and-risk|0|[04-05-lot-and-risk.log](04-05-lot-and-risk.log)|
|06-backtest|1|[06-backtest.log](06-backtest.log)|
|07-paper-cli|2|[07-paper-cli.log](07-paper-cli.log)|
|08-markdown-publish|1|[08-markdown-publish.log](08-markdown-publish.log)|
|09-pytest|0|[09-pytest.log](09-pytest.log)|

单元/集成测试中的合成夹具只用于验证代码行为，不作为本次真实数据验收通过证据。
