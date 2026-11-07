"""Collect -> validate -> transactional paper ledger -> staged/published report."""
import argparse
from contextlib import contextmanager
from datetime import datetime
import fcntl
import hashlib
import json
import sqlite3
from pathlib import Path

from .daily import atomic_write
from .free_calendar import TZ, complete_session, is_session
from .free_data import DEFAULT_SYMBOLS, collect, load
from .paper import advance, required_symbols


@contextmanager
def lock(root):
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    with (root/'workflow.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def markdown(report):
    account = report['account']
    lines = [f"# 免费观察池模拟日报 · {report['session']}", '', report['disclaimer'], '',
        f"行情日期：{report['session']}；采集时间：{report['source_retrieved_at']}",
        f"观察池：{', '.join(report['symbols'])}（配置样本，不是全市场选股）", '',
        f"模拟净值：{account['equity']:.2f}元；现金：{account['cash']:.2f}元；风险状态：{account['risk']}",
        f"历史净值高点：{account['peak']:.2f}元；回撤：{max(0, 1-account['equity']/account['peak']):.2%}", '',
        '首次初始化，仅生成下一交易日观察信号。' if report['bootstrap'] else '账户延续上一已记账交易日，未重置本金。', '',
        '## 持仓', '']
    if not account['positions']:
        lines.append('空仓。')
    for code, p in account['positions'].items():
        lines.append(f"- {code}：{p['shares']}股，买入日{p['bought_on']}，收盘{p['last_close']:.3f}，保护线{p['stop']:.3f}")
    lines += ['', '## 今日模拟成交', '']
    if not report['trades']:
        lines.append('无成交；不是实盘交易。')
    for t in report['trades']:
        lines.append(f"- {t['side']} {t['code']} {t['shares']}股 × {t['price']:.3f}，费用{t['fee']:.2f}元")
    lines += ['', '## 观察与拒绝原因', '']
    for signal in report['signals']:
        lines.append(f"- {signal['code']}：" + ('技术条件通过，待下一交易日重新核验' if signal['eligible'] else '；'.join(signal['reasons'])))
    for rejected in report['rejected']:
        lines.append(f"- 拒绝{rejected['side']} {rejected['code']}：{rejected['reason']}")
    lines += ['', '## 边界', ''] + ['- '+value for value in report['limitations']]
    return '\n'.join(lines)+'\n'


def stage(report, root):
    root = Path(root)/report['session']
    root.mkdir(parents=True, exist_ok=True)
    body = markdown(report)
    payload = dict(report=report, markdown_sha256=hashlib.sha256(body.encode()).hexdigest())
    atomic_write(root/'report.md', body)
    atomic_write(root/'report.json', json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))
    return root


def publish(staged, output, now=None):
    now = now or datetime.now(TZ)
    if now.tzinfo is None or now.astimezone(TZ).hour < 19:
        raise ValueError('免费日报只允许北京时间19:00后发布')
    root = Path(staged)
    payload = json.loads((root/'report.json').read_text())
    report = payload['report']
    if report['session'] != complete_session(now):
        raise ValueError('发布报告不是最近完整交易日')
    generated = datetime.fromisoformat(report['generated_at'])
    source = datetime.fromisoformat(report['source_retrieved_at'])
    cutoff = datetime.fromisoformat(report['session']+'T16:30:00+08:00')
    if generated.tzinfo is None or source.tzinfo is None or not cutoff <= source <= generated <= now:
        raise ValueError('发布报告来源或生成时间异常')
    if hashlib.sha256((root/'report.md').read_bytes()).hexdigest() != payload['markdown_sha256']:
        raise ValueError('日报摘要不一致')
    if markdown(report) != (root/'report.md').read_text():
        raise ValueError('日报JSON与正文不一致')
    for ext in ('json', 'md'):
        atomic_write(Path(output)/report['session']/('report.'+ext), (root/('report.'+ext)).read_text())
    return str(Path(output)/report['session']/'report.md')


def run(root='data/free-workflow', symbols=DEFAULT_SYMBOLS, publish_report=False, now=None, scheduled=False):
    now = now or datetime.now(TZ)
    root = Path(root)
    with lock(root):
        try:
            local = now.astimezone(TZ)
            if scheduled and (not is_session(local.date()) or local.hour < 16 or (local.hour == 16 and local.minute < 30)):
                result = dict(status='skipped', reason='非交易日或尚未完成收盘采集', checked_at=now.isoformat())
                atomic_write(root/'last-run.json', json.dumps(result, ensure_ascii=False, indent=2))
                atomic_write(root/'last-run.md', '# 免费工作流已跳过\n\n'+result['checked_at']+'\n\n'+result['reason']+'\n')
                return result
            db = root/'paper.db'
            selected = set(symbols.split(',')) | required_symbols(db)
            snapshot_path = collect(root/'snapshots', selected, now)
            snapshot = load(snapshot_path, now=datetime.now(TZ))
            report = advance(db, snapshot, now=datetime.now(TZ))
            backup = root/'backups'/(report['session']+'.db')
            backup.parent.mkdir(parents=True, exist_ok=True)
            temporary = backup.with_suffix('.tmp')
            with sqlite3.connect(db) as source, sqlite3.connect(temporary) as destination:
                source.backup(destination)
            temporary.replace(backup)
            staged = stage(report, root/'staged')
            published = publish(staged, root/'published', now) if publish_report else None
            result = dict(status='passed', session=report['session'], snapshot=str(snapshot_path),
                report=str(staged/'report.md'), published=published, account=report['account'],
                checked_at=datetime.now(TZ).isoformat())
            atomic_write(root/'last-run.json', json.dumps(result, ensure_ascii=False, indent=2))
            atomic_write(root/'last-run.md', '# 免费工作流运行通过\n\n行情日期：'+report['session']+'\n\n日报：'+str(staged/'report.md')+'\n\n已发布：'+str(published or '尚未发布，等待19:00后')+'\n')
            return result
        except Exception as exc:
            failure = dict(status='failed', error=str(exc), checked_at=datetime.now(TZ).isoformat(),
                           note='本次运行失败；不能将之前的日报视作本次成功。账户事务已提交的交易日可幂等重试。')
            atomic_write(root/'last-run.json', json.dumps(failure, ensure_ascii=False, indent=2))
            atomic_write(root/'last-run.md', '# 免费工作流运行失败\n\n'+failure['checked_at']+'\n\n'+str(exc)+'\n\n'+failure['note']+'\n')
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='data/free-workflow')
    parser.add_argument('--symbols', default=DEFAULT_SYMBOLS)
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--scheduled', action='store_true')
    args = parser.parse_args(argv)
    auto_publish = args.scheduled and datetime.now(TZ).hour >= 19
    result = run(args.root, args.symbols, args.publish or auto_publish, scheduled=args.scheduled)
    print(json.dumps({k:v for k,v in result.items() if k != 'account'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
