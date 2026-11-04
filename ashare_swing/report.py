import json
import sqlite3
from pathlib import Path
from .strategy import Signal
from .risk import plan_order


def persist_signals(db_path: str, signals: list[Signal]) -> None:
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(p) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS signal (
            code TEXT NOT NULL, signal_date TEXT NOT NULL,
            eligible INTEGER NOT NULL, close REAL NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY (code, signal_date))''')
        db.executemany('''INSERT OR REPLACE INTO signal(code,signal_date,eligible,close,payload)
            VALUES (?,?,?,?,?)''', [(s.code,s.date,int(s.eligible),s.close,json.dumps(s.to_dict(),ensure_ascii=False)) for s in signals])


def render_markdown(signals: list[Signal], asof: str, synthetic: bool = False) -> str:
    label = '【演示假数据：严禁用于投资】' if synthetic else '【真实行情研究结果：不构成交易建议】'
    lines = [f'# A股波段观察日报 — {asof}', '', label, '',
             '|代码|收盘价|信号|未通过规则|模拟股数|预计止损价|', '|---|---:|---|---|---:|---:|']
    for s in signals:
        p = plan_order(s.code, s.close, cash=10_000) if s.eligible else None
        reason = '满足筛选' if s.eligible else '；'.join(s.reasons)
        lines.append(f'|{s.code}|{s.close:.2f}|{"候选" if s.eligible else "不候选"}|{reason}|{p.shares if p else 0}|{p.stop_price if p else "—"}|')
    lines += ['', '注意：信号使用收盘数据，最早下一交易日才可能交易。数据延迟、T+1、涨跌停、停牌、跳空与手续费均影响实际结果。',
              '本日报仅为研究工具输出，不连接券商，不保证收益，也不构成交易建议。']
    return '\n'.join(lines) + '\n'
