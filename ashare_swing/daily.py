"""Idempotent staged reports; publish only verified, same-session artifacts."""
import hashlib
import json
import sqlite3
import pandas as pd
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo
from .backtest import signals_at, risk_state
from .history import History, latest_session, DISCLAIMER
from .risk import plan_order
from .report import persist_signals


def atomic_write(path, data):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(data,encoding='utf8'); temp.replace(path)


def scan_bundle(directory, output='reports/staged', db='data/signals.db', asof=None, now=None):
    history=History(directory)
    now=now or datetime.now(ZoneInfo('Asia/Shanghai'))
    if pd.Timestamp(history.meta['calendar_through']).date() < now.astimezone(ZoneInfo('Asia/Shanghai')).date():
        raise ValueError('日历有效期不足')
    latest=latest_session(history.calendar,now)
    day=asof or latest
    if day != latest:
        raise ValueError('日报只能使用最近完整交易日；历史研究请用 backtest')
    history.validate_range(day,day)
    stamp=datetime.fromisoformat(history.meta['retrieved_at'])
    cutoff=datetime.fromisoformat(day+'T16:30:00+08:00')
    if stamp > now or stamp < cutoff:
        raise ValueError('数据来源时间戳过期或位于未来')
    # Deliberately explicit fresh paper portfolio; never masquerade as live account state.
    signals=signals_at(history,pd.Timestamp(day))
    candidates=signals[:5]
    cash=10000.; exposure=0.; positions=0; entries=[]
    for s in candidates:
        plan=plan_order(s.code,s.close,cash,exposure,positions) if s.eligible else None
        if plan and plan.approved:
            cash-=plan.estimated_buy_cash; exposure+=plan.shares*plan.entry_price; positions+=1
        entries.append(dict(signal=s.to_dict(),plan=plan.to_dict() if plan else None))
    result=dict(disclaimer=DISCLAIMER,date=day,generated_at=now.isoformat(),source=history.meta,
                portfolio=dict(kind='每日独立空仓模拟计划，非持续持仓账户',cash=cash,exposure=exposure,positions=positions,state=risk_state(10000,10000)),
                candidates=entries,eligible_count=sum(s.eligible for s in signals),
                note='候选不足3只时不补造；不合格观察项不可交易。T收盘信号，最早T+1核验后模拟成交。')
    lines=[f'# A股波段日报 {day}',DISCLAIMER,f"来源：{history.meta['source']}；采集：{history.meta['retrieved_at']}",result['note'],
           '模拟组合：'+json.dumps(result['portfolio'],ensure_ascii=False)]
    for entry in entries:
        s=entry['signal']; p=entry['plan']
        lines += [f"## {s['code']} {'候选' if s['eligible'] else '不可交易'}",json.dumps(s['features'],ensure_ascii=False),
                  '未通过：'+('；'.join(s['reasons']) or '无'),
                  '模拟计划：'+(json.dumps(p,ensure_ascii=False) if p else '0股'),
                  f"模拟买入参考区间：{s['close']*.99:.3f}–{s['close']*1.01:.3f}；次日跳空必须重新计算风险"]
    md='\n\n'.join(lines)+'\n'
    result['markdown_sha256']=hashlib.sha256(md.encode()).hexdigest()
    persist_signals(db,signals)
    record_outcomes(history,db,day)
    atomic_write(Path(output)/f'{day}.md',md)
    atomic_write(Path(output)/f'{day}.json',json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    return result


def record_outcomes(history, db_path, asof):
    import pandas as pd
    with sqlite3.connect(db_path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS outcome(code TEXT, signal_date TEXT, horizon INTEGER, observed_date TEXT, close_return REAL, PRIMARY KEY(code,signal_date,horizon))')
        for code,date,close in db.execute('SELECT code,signal_date,close FROM signal').fetchall():
            if code not in history.bars.index.get_level_values('code'):
                continue
            bars=history.frame('bars',code,pd.Timestamp(asof)).set_index('date')
            if pd.Timestamp(date) not in bars.index:
                continue
            future=history.calendar[history.calendar>pd.Timestamp(date)]
            for n in (1,5,10,15):
                if len(future)>=n and future[n-1] <= pd.Timestamp(asof) and future[n-1] in bars.index:
                    db.execute('INSERT OR REPLACE INTO outcome VALUES (?,?,?,?,?)',(code,date,n,str(future[n-1].date()),float(bars.loc[future[n-1],'close']/close-1)))


def publish(staged, output, now=None):
    now=now or datetime.now(ZoneInfo('Asia/Shanghai'))
    if now.tzinfo is None:
        raise ValueError('时钟必须带时区')
    local=now.astimezone(ZoneInfo('Asia/Shanghai'))
    if local.time()<time(19):
        raise ValueError('北京时间19:00后才允许发布')
    day=local.date().isoformat()
    root=Path(staged)
    result=json.loads((root/f'{day}.json').read_text())
    md=(root/f'{day}.md').read_text()
    if result['date'] != day or result.get('disclaimer')!=DISCLAIMER or hashlib.sha256(md.encode()).hexdigest()!=result['markdown_sha256']:
        raise ValueError('报告日期/摘要/声明校验失败')
    stamp=pd.Timestamp(result['source']['retrieved_at'])
    generated=pd.Timestamp(result['generated_at'])
    cutoff=pd.Timestamp(day+'T16:30:00+08:00')
    if stamp.tzinfo is None or generated.tzinfo is None or not (cutoff <= stamp <= generated <= pd.Timestamp(now)):
        raise ValueError('报告来源/生成时间戳不完整或过期')
    for ext in ('md','json'):
        atomic_write(Path(output)/f'{day}.{ext}',(root/f'{day}.{ext}').read_text())
