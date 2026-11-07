"""Transactional, persistent, forward-only paper account. No broker connectivity."""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

from .backtest import fees, risk_state
from .config import Config
from .free_calendar import TZ, next_session
from .free_data import INDICES, digest, validate
from .history import DISCLAIMER
from .risk import plan_order
from .strategy import screen


def semantic_hash(snapshot):
    return digest(dict(session=snapshot['session'], symbols=snapshot['symbols'],
        securities={code: {k: value[k] for k in ('name', 'bars', 'status') if k in value}
                    for code, value in snapshot['securities'].items()}))


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30, isolation_level=None)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=FULL')
    db.execute('CREATE TABLE IF NOT EXISTS account(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS sessions(day TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, report TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY, day TEXT, code TEXT, side TEXT, payload TEXT NOT NULL)')
    return db


def required_symbols(path):
    if not Path(path).exists():
        return set()
    db = connect(path)
    try:
        row = db.execute('SELECT payload FROM account WHERE id=1').fetchone()
        if not row:
            return set()
        state = json.loads(row[0])
        return set(state['positions']) | {x['code'] for x in state['pending']}
    finally:
        db.close()


def advance(path, snapshot, now=None):
    now = now or datetime.now(TZ)
    frames = validate(snapshot, now)
    fingerprint = semantic_hash(snapshot)
    day = snapshot['session']
    db = connect(path)
    try:
        db.execute('BEGIN IMMEDIATE')
        same = db.execute('SELECT fingerprint, report FROM sessions WHERE day=?', (day,)).fetchone()
        if same:
            if same[0] != fingerprint:
                raise ValueError('已记账交易日的数据被修改，禁止自动重写历史账本')
            db.commit()
            return json.loads(same[1])
        row = db.execute('SELECT payload FROM account WHERE id=1').fetchone()
        state = json.loads(row[0]) if row else dict(cash=10000., peak=10000., positions={},
            pending=[], halted=False, last_session=None, created_at=now.isoformat())
        last = state['last_session']
        if last and next_session(last) != day:
            raise ValueError('模拟账户漏跑交易日或日期倒退；需要逐日真实快照恢复，禁止用今日状态回填')
        positions = state['positions']
        missing = (set(positions) | {x['code'] for x in state['pending']}) - set(frames)
        if missing:
            raise ValueError(f'持仓/待成交证券缺少行情: {sorted(missing)}')
        cfg = Config()
        ledger, rejected = [], []
        def bar(code):
            return frames[code].iloc[-1]
        def status(code):
            return snapshot['securities'][code]['status']
        def exposure(field):
            return sum(float(bar(c)[field])*p['shares'] for c, p in positions.items())
        def record(code, side, shares, price, fee, **extra):
            item = dict(date=day, code=code, side=side, shares=shares, price=price,
                        fee=fee, cash=state['cash'], **extra)
            ledger.append(item)
            db.execute('INSERT INTO ledger(day,code,side,payload) VALUES (?,?,?,?)',
                       (day, code, side, json.dumps(item, ensure_ascii=False)))
        def reject(code, side, reason):
            rejected.append(dict(code=code, side=side, reason=reason))
        for code, position in positions.items():
            st = status(code)
            historical = frames[code].set_index('date')
            if (st['corporate_action'] or abs(st['previous_close']-position['last_close']) > .005
                    or pd.Timestamp(last) not in historical.index
                    or abs(float(historical.loc[pd.Timestamp(last), 'close'])-position['last_close']) > .005):
                raise ValueError(f'{code}: 持仓涉及除权/分红或行情修订，冻结记账并人工核实资金处置')
        opening_equity = state['cash'] + exposure('open')
        opening_risk = risk_state(opening_equity, state['peak'])
        state['halted'] |= opening_risk == 'halt'
        for code, p in list(positions.items()):
            b, st = bar(code), status(code)
            age = p['age'] + 1
            stop_hit = float(b.low) <= p['stop']
            should_sell = stop_hit or p['exit'] or state['halted'] or age >= 15
            if not should_sell:
                continue
            if p['bought_on'] >= day:
                reject(code, 'sell', 'T+1禁止当日卖出'); continue
            if b.volume <= 0 or b.low <= st['limit_down'] or p['shares'] > b.volume*.01:
                reject(code, 'sell', '停牌/触及跌停/容量限制'); continue
            price = max(float(b.low), min(float(b.open), p['stop']) * (1-cfg.slip_fraction)) if stop_hit else max(float(b.low), float(b.open)*(1-cfg.slip_fraction))
            gross = price*p['shares']; fee = fees(gross, day, 'sell', cfg)
            state['cash'] = round(state['cash'] + gross-fee, 8)
            record(code, 'sell', p['shares'], price, fee, pnl=gross-fee-p['cost'],
                   reason='stop' if stop_hit else 'exit')
            del positions[code]
        for order in state['pending']:
            code = order['code']
            if code in positions:
                continue
            b, st = bar(code), status(code)
            if last is None or order['signal_date'] != last:
                raise ValueError('待成交信号日期不是上一已记账交易日')
            if (state['halted'] or opening_risk in ('freeze','halt') or st['restricted']
                    or st['corporate_action'] or b.volume <= 0 or b.high >= st['limit_up']):
                reject(code, 'buy', '账户冻结/风险警示/公司行动/涨停/停牌'); continue
            # Reject a materially higher opening instead of chasing a stale signal.
            if b.open > order['reference']*1.01:
                reject(code, 'buy', '次日开盘超过信号参考价1%'); continue
            exp = exposure('open')
            plan = plan_order(code, float(b.open), state['cash'], exp, len(positions),
                              state['cash']+exp, state['peak'], cfg)
            price = float(b.open)*(1+cfg.slip_fraction)
            if (not plan.approved or plan.shares > b.volume*.01 or price > b.high or price >= st['limit_up']):
                reject(code, 'buy', plan.reason if not plan.approved else '容量/成交价格区间限制'); continue
            gross = price*plan.shares; fee = fees(gross, day, 'buy', cfg)
            cost = gross+fee
            stop = float(b.open)*(1-cfg.stop_fraction)
            sell_gross = stop*(1-cfg.slip_fraction)*plan.shares
            if (cost-sell_gross+fees(sell_gross, day, 'sell', cfg) > cfg.per_trade_risk
                    or state['cash']-cost < 4000 or exp+gross > 6000 or cost > cfg.max_position):
                reject(code, 'buy', '实际费用后风险/资金预算不足'); continue
            state['cash'] = round(state['cash']-cost, 8)
            positions[code] = dict(shares=plan.shares, cost=cost, stop=stop, age=-1,
                bought_on=day, peak_close=float(b.close), last_close=float(b.close), exit=False)
            record(code, 'buy', plan.shares, price, fee, signal_date=order['signal_date'])
        equity = state['cash']+exposure('close')
        state['peak'] = max(state['peak'], equity)
        risk = risk_state(equity, state['peak'])
        state['halted'] |= risk == 'halt'
        signals = []
        market = all(float(frames[c].close.iloc[-1]) > frames[c].close.tail(20).mean() > frames[c].close.tail(60).mean() for c in INDICES)
        for code in snapshot['symbols']:
            st = status(code)
            signal = screen(code, frames[code], frames['sh000300'], day)
            extra = []
            if not market: extra.append('双指数市场环境未通过')
            if st['restricted']: extra.append('风险警示证券')
            if st['corporate_action']: extra.append('当日存在公司行动或前收盘异常')
            if (pd.Timestamp(day)-pd.Timestamp(st['ipo_date'])).days < 365:
                extra.append('免费版保守排除上市未满365自然日证券')
            signal.reasons.extend(extra)
            signal.eligible &= not extra
            signals.append(signal.to_dict())
        signals.sort(key=lambda s: (not s['eligible'], -s['features'].get('ret20', -1), s['code']))
        state['pending'] = [dict(code=s['code'], signal_date=day, reference=s['close']) for s in signals
                            if s['eligible'] and s['code'] not in positions] if not state['halted'] and risk != 'freeze' else []
        for code, p in positions.items():
            close = float(bar(code).close)
            p['age'] += 1
            p['last_close'] = close
            p['peak_close'] = max(p['peak_close'], close)
            entry = p['cost']/p['shares']
            if p['peak_close'] >= entry*1.08:
                p['stop'] = max(p['stop'], p['peak_close']*.95)
            series = frames[code].close
            below10 = (float(series.iloc[-1]) < float(series.tail(10).mean())-1e-8
                       and float(series.iloc[-2]) < float(series.iloc[:-1].tail(10).mean())-1e-8)
            p['exit'] = (p['age'] >= 5 and close < series.tail(20).mean()) or below10 or status(code)['restricted']
        state['last_session'] = day
        state['equity'] = equity
        state['risk'] = 'halt' if state['halted'] else risk
        report = dict(schema=1, disclaimer=DISCLAIMER, kind='免费观察池持续模拟，非全市场回测',
            session=day, generated_at=now.isoformat(), source_retrieved_at=snapshot['retrieved_at'],
            source_sha256=snapshot.get('sha256'), data_fingerprint=fingerprint,
            symbols=snapshot['symbols'], account=state, signals=signals, trades=ledger,
            rejected=rejected, bootstrap=last is None,
            limitations=['当前配置观察池，不代表全市场筛选', '无财务/公告评分，日线容量不还原真实成交队列',
                         '未支持公司行动资金处置；持仓异常时冻结，不伪造成交', '尚未完成2020–2026历史全市场回测'])
        encoded = json.dumps(report, ensure_ascii=False, allow_nan=False)
        db.execute('INSERT OR REPLACE INTO account(id,payload) VALUES(1,?)',
                   (json.dumps(state, ensure_ascii=False, allow_nan=False),))
        db.execute('INSERT INTO sessions(day,fingerprint,report) VALUES(?,?,?)', (day, fingerprint, encoded))
        db.commit()
        return report
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
