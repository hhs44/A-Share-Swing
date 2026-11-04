"""Daily event engine: prior-close signals, next-open fills, explicit cash ledger."""
from dataclasses import asdict
import pandas as pd
from .config import Config
from .risk import plan_order, commission
from .strategy import screen
from .history import DISCLAIMER


def fees(gross, day, side, cfg=None):
    cfg = cfg or Config()
    day = pd.Timestamp(day)
    stamp = (0.001 if day < pd.Timestamp('2023-08-28') else 0.0005) if side == 'sell' else 0
    transfer = 0.00002 if day < pd.Timestamp('2022-04-29') else 0.00001
    return commission(gross,cfg) + gross * (stamp + transfer)


def risk_state(equity, peak):
    dd = max(0,1-equity/peak)
    return 'halt' if dd >= .15-1e-10 else 'freeze' if dd >= .12-1e-10 else 'warning' if dd >= .08-1e-10 else 'normal'


def signals_at(history, day):
    bench = history.frame('indices','sh000300',day)
    environment = True
    for code in ('sh000300','sz399001'):
        frame = history.frame('indices',code,day)
        if len(frame) < 60 or frame.iloc[-1].date != day:
            raise ValueError('指数历史不足60日或日期错配')
        close = frame.close
        environment &= bool(close.iloc[-1] > close.tail(20).mean() > close.tail(60).mean())
    results = []
    for code, state in history.status.loc[day].iterrows():
        if not state.listed or state.st or state.delisted or state.suspended:
            continue
        bars = history.frame('bars',code,day)
        s = screen(code,bars,bench,asof=str(day.date()))
        s.features['market_allowed'] = environment
        s.features['score'] = sum(s.features.get('conditions',{}).values()) + (8 <= s.close <= 20)
        if not environment:
            s.eligible = False
            s.reasons.append('双指数环境不允许新仓')
        results.append(s)
    return sorted(results,key=lambda s:(not s.eligible,-s.features['score'],-s.features.get('ret20',-1),s.code))


def run(history, start, end, cfg=None):
    cfg = cfg or Config()
    days = history.validate_range(start,end)
    cash = peak = cfg.initial_cash
    positions, pending, ledger, curve, rejected = {}, [], [], [], []
    halted = False
    for i, day in enumerate(days):
        # Opening equity uses only known opening marks. Drawdown halt stays latched.
        def row(code):
            return history.bars.loc[(day,code)]
        for code in positions:
            if (day,code) not in history.bars.index or (day,code) not in history.status.index:
                raise ValueError(f'持仓{code}缺少行情/退市处置，不允许消失')
        equity_open = cash + sum(row(c).open*p['shares'] for c,p in positions.items())
        state = risk_state(equity_open,peak)
        halted |= state == 'halt'
        for code,p in list(positions.items()):
            b = row(code); st = history.status.loc[(day,code)]
            age = i-p['session']
            # Stop is intraday, gap down executes at open; T+1 always enforced.
            stop_hit = b.low <= p['stop']
            if age < 1 or not (stop_hit or age >= 15 or p['exit'] or halted):
                continue
            if st.suspended or b.volume <= 0 or b.low <= st.limit_down or p['shares'] > b.volume*.01:
                rejected.append({'date':str(day.date()),'code':code,'side':'sell','reason':'停牌/跌停/成交量限制'})
                continue
            price = min(b.open,p['stop']) if stop_hit else b.open
            price = max(b.low,price*(1-cfg.slip_fraction))
            gross = price*p['shares']; fee = fees(gross,day,'sell',cfg)
            cash += gross-fee
            ledger.append(dict(date=str(day.date()),code=code,side='sell',shares=p['shares'],price=price,fee=fee,cash=cash,pnl=gross-fee-p['cost']))
            del positions[code]
        for s in pending:
            code = s.code
            if code in positions:
                continue
            if (day,code) not in history.status.index or (day,code) not in history.bars.index:
                raise ValueError('待成交信号缺少次日证券状态')
            b=row(code); st=history.status.loc[(day,code)]
            if halted or state in ('halt','freeze') or not st.listed or st.st or st.delisted or st.suspended or b.high >= st.limit_up or b.volume <= 0:
                rejected.append({'date':str(day.date()),'code':code,'side':'buy','reason':'风控/状态/涨停限制'})
                continue
            exposure=sum(row(c).open*p['shares'] for c,p in positions.items())
            p=plan_order(code,b.open,cash,exposure,len(positions),cash+exposure,peak,cfg)
            if not p.approved or p.shares > b.volume*.01:
                rejected.append({'date':str(day.date()),'code':code,'side':'buy','reason':p.reason if not p.approved else '成交量限制'})
                continue
            price=b.open*(1+cfg.slip_fraction)
            if price > b.high or price >= st.limit_up:
                continue
            gross=price*p.shares; fee=fees(gross,day,'buy',cfg)
            cost=gross+fee
            stop=b.open*(1-cfg.stop_fraction)
            exit_gross=stop*(1-cfg.slip_fraction)*p.shares
            if cost-exit_gross+fees(exit_gross,day,'sell',cfg) > cfg.per_trade_risk or cost > min(cfg.max_position,cash-cfg.initial_cash*.4,cfg.initial_cash*.6-exposure):
                continue
            cash-=cost
            positions[code]=dict(shares=p.shares,cost=cost,stop=stop,session=i,exit=False)
            ledger.append(dict(date=str(day.date()),code=code,side='buy',shares=p.shares,price=price,fee=fee,cash=cash,signal_date=s.date))
        equity=cash+sum(row(c).close*p['shares'] for c,p in positions.items())
        peak=max(peak,equity); state=risk_state(equity,peak); halted |= state=='halt'
        curve.append(dict(date=str(day.date()),equity=equity,cash=cash,drawdown=1-equity/peak,state='halt' if halted else state))
        pending=[s for s in signals_at(history,day) if s.eligible] if not halted and state!='freeze' else []
        for c,p in positions.items():
            bars=history.frame('bars',c,day)
            p['exit'] = i-p['session'] >= 5 and bars.close.iloc[-1] < bars.close.tail(20).mean()
    sells=[x['pnl'] for x in ledger if x['side']=='sell']
    elapsed=max(1,(days[-1]-days[0]).days)
    total=curve[-1]['equity']/cfg.initial_cash-1
    index=history.frame('indices','sh000300',days[-1]).set_index('date').loc[days]
    return dict(disclaimer=DISCLAIMER,valid=True,source=history.meta,period=[start,end],assumptions=asdict(cfg),
        metrics=dict(total_return=total,annualized=(1+total)**(365.25/elapsed)-1,max_drawdown=max(x['drawdown'] for x in curve),
                     trades=len(sells),pnl=sells,win_rate=sum(p>0 for p in sells)/len(sells) if sells else None,
                     benchmark_return=float(index.close.iloc[-1]/index.close.iloc[0]-1)),
        ledger=ledger,equity=curve,rejected=rejected,open_positions=positions)
