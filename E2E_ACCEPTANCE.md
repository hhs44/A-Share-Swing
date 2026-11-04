# 2026-11-04 真实端到端验收

模拟研究，不构成投资建议。**验收未通过。没有真实候选股、真实回测成绩或真实模拟成交记录。**

## 实际运行上下文

- 运行时间：北京时间2026-11-04 **17:52:09–17:52:25**（UTC+08:00）。
- Python 3.11；AKShare 1.20.25、pandas 2.3.3、numpy 2.4.6、pytest 9.2.0。
- 请求行情范围：2025-01-01至2026-11-04；证券000001、600000，基准sh000300、市场控制sz399001。
- 请求历史回测：2020–2023研究、2024–2025样本外、2026截至请求日；由于日历获取失败，请求截止日**不能视为已经核实的完整交易日**。
- 实际取得的行情范围、最新完整交易日、候选、绩效：全部未知/未生成，JSON为null，不能解释为零候选/零收益。
- 已使用执行环境支持的网络权限重新运行，保留代理和TLS校验。真实失败为代理HTTP **403 Forbidden**，并非仅凭缺少依赖或单元mock作出判断。

## 按要求依次验证

| 步骤 | 实际执行及结果 | 判定 |
|---|---|---|
| 真实AKShare采集 | 日历、证券名称、000001、600000、沪深300、深证成指共6次真实请求，全部ProxyError/代理403 | 阻塞；无法判断上游API本身是否正常 |
| 最近完整交易日 | 执行freshness探测，因没有calendar-all-raw.csv失败；未使用工作日推测交易所日历 | 未验证 |
| 选股策略 | 执行真实`scan --bundle data/history`，缺少manifest.json而失败 | 未生成真实候选；不是零候选 |
| 100股限制 | 独立执行市场无关算术场景：12.3元假设输入建议100股，预计现金1236.25元，含费用计划损失75.11元；1700元假设输入拒绝买一手 | 算术检查通过；不是实际行情成交 |
| 账户风险 | 现金4000元、敞口6000元、已有2只、12%冻结、15%停机均拒绝新仓；8/12/15%状态边界正确 | 独立检查通过；持续真实模拟账户未验收 |
| 历史回测 | 执行2020起backtest；缺少真实历史数据包，输出valid:false、metrics:null并非零退出 | 未完成真实绩效验证 |
| 模拟交易 | 实际执行`python -m ashare_swing paper --bundle data/history`返回退出码2：invalid choice | 持续模拟交易CLI未实现；回测内模拟不能等同此功能 |
| Markdown日报 | 真实scan失败没有日报；17:52执行publish返回“北京时间19:00后才允许发布” | 提前发布防护生效，但真实日报全链路未通过 |
| pytest | 46 passed in 8.46s | 代码行为测试通过，不替代以上验收 |

没有运行演示扫描来充当真实数据链路结果。JUnit中原有合成夹具仅用于离线机械验证。

## 本次直接修复

1. 同日10:00采集的未完成行情此前可能被16:30后扫描接受：改为必须来源时间不早于信号日16:30；发布时再次核查来源、生成时间和当前时钟顺序。
2. manifest声称日历覆盖到年底，但实际日历仅到过去日期，可能扫描旧交易日：改为统一使用严格日历覆盖检查。
3. 请求2020开始、实际只含2025的日历此前可能缩短区间仍返回valid：现在拒绝未覆盖完整请求的区间。
4. 持仓退市但仍带估值行时此前可能虚拟卖出：现在缺少退市处置规则即拒绝回测。
5. 移除测试中两处pandas未来弃用的inplace调用。
6. 新增可复现验收runner；逐条记录真实命令、时区时间、退出码、完整stdout/stderr；失败也输出诊断Markdown/JSON/JUnit，避免仅依据pytest判断成功。

新增5项回归测试全部通过：完整区间不能缩水、盘中采集时间拒绝、过期实际日历拒绝、退市持仓须处置、发布时复核来源时间。修改前41项通过，修改后46项通过。编译与git diff格式检查通过。GitHub远端CI见PR Checks，不把上一提交的结果移作本提交结果。

## 可复现命令及日志

```bash
source .venv/bin/activate
python -m ashare_swing.acceptance --output work/acceptance-new --bundle data/history --start 2025-01-01 --end 2026-11-04
# 预期：当前环境退出1，产生summary.json、ACCEPTANCE.md、每步.log和pytest.xml
python -m pytest -q
python -m compileall -q ashare_swing tests
git diff --check
```

真实执行使用 `.venv/bin/python`，输出目录 `work/acceptance-final-20261104`，每步完整argv、时间戳及退出码均在summary.json与各.log中。首次未赋网络权限的探测另返回Operation not permitted；最终归档日志为赋予受支持网络权限后的403结果。

单独复现主要步骤：

```bash
python -m ashare_swing.acceptance --probe calendar --output work/probe-raw
python -m ashare_swing.acceptance --probe names --output work/probe-raw
python -m ashare_swing.acceptance --probe stock --symbol 000001 --start 2025-01-01 --end 2026-11-04 --output work/probe-raw
python -m ashare_swing.acceptance --probe stock --symbol 600000 --start 2025-01-01 --end 2026-11-04 --output work/probe-raw
python -m ashare_swing.acceptance --probe index --symbol sh000300 --start 2025-01-01 --end 2026-11-04 --output work/probe-raw
python -m ashare_swing.acceptance --probe index --symbol sz399001 --start 2025-01-01 --end 2026-11-04 --output work/probe-raw
python -m ashare_swing.acceptance --probe freshness --output work/probe-raw
python -m ashare_swing scan --bundle data/history --output work/staged --db work/signals.db
python -m ashare_swing backtest --bundle data/history --start 2020-01-01 --end 2026-11-04 --output work/backtest.json
python -m ashare_swing paper --bundle data/history  # 当前尚无此子命令；用于能力探测
python -m ashare_swing publish --staged work/staged --output work/published
```

## 未解决问题与验收前提

- 环境需允许行情域名（至少finance.sina.com.cn、query.sse.com.cn、push2his.eastmoney.com；名称接口也可能访问深交所等上游）；当前403不允许绕过代理解决。放通后仍须实际重新验证返回时间、名称、交易日历和双指数。
- 仍无获授权的历史全股票池、历史ST/IPO/退市/停复牌/涨跌停数据供应链，manifest声明和文件哈希不能证明历史样本无偏差。
- 公司行动、现金分红/拆股/配股和退市资金结算未实现；涉及数据拒绝回测，2020–2026真实绩效仍未完成。
- 持续模拟账户、独立paper CLI和真实下一交易日模拟成交验证未实现；日报仍每天独立空仓计划。
- 真实Markdown日报生成、19:00后的发布、自动数据更新及无人值守定时报告尚未验证成功。不能把提前发布被拒绝记作日报发布通过。
- 长期SQLite需可靠持久存储，Actions cache不是可靠数据库；跨文件/SQLite原子性和多进程CLI锁仍需部署解决；日线成交容量假设不能还原开盘成交队列，北交所/ETF不支持。

PR：[hhs44/A-Share-Swing #1](https://github.com/hhs44/A-Share-Swing/pull/1)。保持草稿，不合并main，不启用实盘交易。
