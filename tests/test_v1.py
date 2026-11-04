"""All fixtures are synthetic; these tests establish mechanics, never real returns."""
import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import pytest
from ashare_swing.history import History, latest_session
from ashare_swing.backtest import fees, risk_state, run
from ashare_swing.strategy import normalize_bars, screen
from ashare_swing.demo import make_demo_bars
from ashare_swing.risk import plan_order
from ashare_swing.daily import scan_bundle, publish


@pytest.fixture
def bundle(tmp_path):
    dates=pd.bdate_range('2025-01-01',periods=130)
    bars=[]; states=[]; indices=[]
    for i,date in enumerate(dates):
        price=10+i*.01
        volume=1500000 if i%8==0 else 1000000
        bars.append(dict(code='000001',date=str(date.date()),open=price,high=price*1.02,low=price*.98,close=price,volume=volume,amount=2e8))
        states.append(dict(code='000001',name='测试股份',board='main',ipo_date='1991-04-03',date=str(date.date()),listed=1,st=0,delisted=0,suspended=0,limit_up=price*1.1,limit_down=price*.9,action_factor=1))
        for code in ('sh000300','sz399001'):
            p=100+i*.01
            indices.append(dict(code=code,date=str(date.date()),open=p,high=p,low=p,close=p,volume=1e8,amount=1e9))
    for name,rows in [('bars',bars),('status',states),('indices',indices),('calendar',[{'date':str(d.date())} for d in dates])]:
        pd.DataFrame(rows).to_csv(tmp_path/f'{name}.csv',index=False)
    meta=dict(source='SYNTHETIC TEST ONLY',license='test',retrieved_at='2025-07-01T16:35:00+08:00',calendar_through='2025-12-31',point_in_time_universe=True,includes_delisted=True,corporate_actions_verified=True)
    meta['sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.glob('*.csv')}
    (tmp_path/'manifest.json').write_text(json.dumps(meta))
    return tmp_path,dates


def rewrite(root,name,fn):
    path=root/f'{name}.csv'
    frame=pd.read_csv(path,dtype={'code':str}); changed=fn(frame); frame=(changed if isinstance(changed,pd.DataFrame) else frame); frame.to_csv(path,index=False)
    meta=json.loads((root/'manifest.json').read_text());meta['sha256'][path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
    (root/'manifest.json').write_text(json.dumps(meta))


def test_historical_fees():
    assert fees(10000,'2023-08-25','sell')==pytest.approx(15.1)
    assert fees(10000,'2023-08-28','sell')==pytest.approx(10.1)
    assert fees(1000,'2020-01-01','buy')==pytest.approx(5.02)
    assert fees(1000,'2022-04-29','buy')==pytest.approx(5.01)


@pytest.mark.parametrize('value,expected',[(10000,'normal'),(9200,'warning'),(8800,'freeze'),(8500,'halt')])
def test_drawdown_boundaries(value,expected):
    assert risk_state(value,10000)==expected


@pytest.mark.parametrize('value',[float('nan'),float('inf'),-1,0])
def test_nonfinite_order(value):
    with pytest.raises(ValueError): plan_order('000001',value,10000)


def test_target_and_unaffordable():
    assert plan_order('000001',7,10000).estimated_buy_cash <=1500
    assert not plan_order('000001',20,10000).approved


@pytest.mark.parametrize('kind',['nan','infinite','conflict','ohlc'])
def test_bad_bars_fail_closed(kind):
    frame=make_demo_bars()
    if kind=='nan': frame.loc[5,'close']=np.nan
    if kind=='infinite': frame.loc[5,'volume']=np.inf
    if kind=='ohlc': frame.loc[5,'high']=1
    if kind=='conflict':
        other=frame.tail(1).copy(); other['close']+=1; frame=pd.concat([frame,other])
    with pytest.raises(ValueError): normalize_bars(frame)


def test_session_clock():
    calendar=['2026-11-02','2026-11-03','2026-11-04','2026-11-05','2026-11-06','2026-11-09']
    def at(text): return datetime.fromisoformat(text).replace(tzinfo=ZoneInfo('Asia/Shanghai'))
    assert latest_session(calendar,at('2026-11-04T16:29'))=='2026-11-03'
    assert latest_session(calendar,at('2026-11-04T16:30'))=='2026-11-04'
    assert latest_session(calendar,at('2026-11-08T19:00'))=='2026-11-06'
    with pytest.raises(ValueError): latest_session(calendar,at('2026-11-10T19:00'))


def test_event_engine_and_future_isolation(bundle):
    root,days=bundle; history=History(root)
    start,end=str(days[70].date()),str(days[110].date())
    result=run(history,start,end)
    assert result['ledger']
    for item in result['ledger']:
        assert item['cash']>=4000
        assert item['shares']%100==0
        if item['side']=='buy': assert item['date']>item['signal_date']
    for buy in [x for x in result['ledger'] if x['side']=='buy']:
        exits=[x for x in result['ledger'] if x['side']=='sell' and x['date']>buy['date']]
        if buy['date'] < str(days[90].date()): assert exits
    history.bars.loc[(days[120],'000001'),'close']=999
    assert run(history,start,end)==result


def test_limit_up_and_suspended_never_buy(bundle):
    root,days=bundle
    rewrite(root,'status',lambda df:df.__setitem__('limit_up',df['limit_down']*1.1))
    assert not run(History(root),str(days[70].date()),str(days[100].date()))['ledger']
    rewrite(root,'status',lambda df:df.__setitem__('suspended',1))
    assert not run(History(root),str(days[70].date()),str(days[100].date()))['ledger']


def test_missing_status_and_action_fail_closed(bundle):
    root,days=bundle
    rewrite(root,'status',lambda df:df.__setitem__('action_factor',2))
    with pytest.raises(ValueError,match='公司行动'): History(root)


def test_missing_index_fail_closed(bundle):
    root,days=bundle
    rewrite(root,'indices',lambda df:df.drop(df[df.date==str(days[90].date())].index))
    with pytest.raises(ValueError,match='双指数'): run(History(root),str(days[70].date()),str(days[100].date()))


def test_hash_tamper(bundle):
    root,_=bundle
    with (root/'bars.csv').open('a') as f:f.write('\n')
    with pytest.raises(ValueError,match='哈希'): History(root)


def test_daily_idempotence_and_publication(bundle,tmp_path):
    import sqlite3
    root,days=bundle; day=str(days[-1].date())
    meta=json.loads((root/'manifest.json').read_text());meta['retrieved_at']=day+'T16:35:00+08:00'; (root/'manifest.json').write_text(json.dumps(meta))
    now=datetime.fromisoformat(day+'T19:00:00+08:00')
    stage=root/'stage'; db=root/'test.db'; out=root/'published'
    a=scan_bundle(root,stage,str(db),now=now); b=scan_bundle(root,stage,str(db),now=now)
    assert a==b
    publish(stage,out,now=now)
    assert (out/f'{day}.json').exists()
    with sqlite3.connect(db) as conn:assert conn.execute('SELECT count(*) FROM signal').fetchone()[0]==1
    with pytest.raises(ValueError):publish(stage,out,now=now.replace(hour=18))
    (stage/f'{day}.md').write_text('tampered')
    with pytest.raises(ValueError):publish(stage,out,now=now)


def test_nontrading_and_stale_report(bundle):
    root,days=bundle
    now=datetime.fromisoformat(str(days[-1].date())+'T19:00:00+08:00')
    meta=json.loads((root/'manifest.json').read_text());meta['retrieved_at']='2025-06-30T16:35:00+08:00';(root/'manifest.json').write_text(json.dumps(meta))
    with pytest.raises(ValueError,match='时间戳'):scan_bundle(root,now=now)
    with pytest.raises(ValueError,match='最近完整'):scan_bundle(root,asof='2025-01-04',now=now)


def test_t_plus_one_and_limit_down_delays_exit(bundle):
    root,days=bundle
    baseline=run(History(root),str(days[70].date()),str(days[110].date()))
    buy=next(x for x in baseline['ledger'] if x['side']=='buy')
    exit=next(x for x in baseline['ledger'] if x['side']=='sell')
    buyday=pd.Timestamp(buy['date']); exitday=pd.Timestamp(exit['date'])
    # Signal-day buy cannot execute; buy-day stop cannot sell until a later session.
    def modify_bars(df):
        mask=df.date==buy['date'];df.loc[mask,'low']=df.loc[mask,'open']*.92
    rewrite(root,'bars',modify_bars)
    result=run(History(root),str(days[70].date()),str(days[110].date()))
    assert all(x['date']>buy['date'] for x in result['ledger'] if x['side']=='sell')
    def block_exit(df):
        mask=df.date==exit['date'];df.loc[mask,'limit_down']=df.loc[mask,'limit_up']/1.1*.98
    rewrite(root,'status',block_exit)
    result=run(History(root),str(days[70].date()),str(days[110].date()))
    assert any(x['date']==exit['date'] and x['side']=='sell' for x in result['rejected'])
    assert not any(x['date']==exit['date'] and x['side']=='sell' for x in result['ledger'])


def test_liquidity_constraint(bundle):
    root,days=bundle
    rewrite(root,'bars',lambda df:df.__setitem__('volume',df.volume/100000))
    result=run(History(root),str(days[70].date()),str(days[100].date()))
    assert not result['ledger']


def test_ipo_and_missing_status(bundle):
    root,days=bundle
    rewrite(root,'status',lambda df:df.__setitem__('ipo_date','2030-01-01'))
    with pytest.raises(ValueError,match='IPO'):History(root)


def test_demo_never_overwrites_real_signals(tmp_path):
    from ashare_swing.cli import main
    main(['scan','--demo','--asof','2026-11-04','--output',str(tmp_path/'report'),'--db',str(tmp_path/'signals.db')])
    assert not (tmp_path/'signals.db').exists()
    assert (tmp_path/'signals-DEMO.db').exists()


def test_invalid_backtest_writes_no_metrics(tmp_path):
    from ashare_swing.cli import main
    out=tmp_path/'invalid.json'
    with pytest.raises(FileNotFoundError):main(['backtest','--bundle',str(tmp_path/'missing'),'--end','2026-11-04','--output',str(out)])
    result=json.loads(out.read_text())
    assert not result['valid'] and result['metrics'] is None


def test_portfolio_halt_latches_after_gap_loss(bundle):
    root,days=bundle
    # Two explicitly separate synthetic securities; a discontinuous crash verifies account controls.
    for name in ('bars','status'):
        path=root/f'{name}.csv';df=pd.read_csv(path,dtype={'code':str});other=df.copy();other['code']='600000'
        pd.concat([df,other]).to_csv(path,index=False)
        meta=json.loads((root/'manifest.json').read_text());meta['sha256'][path.name]=hashlib.sha256(path.read_bytes()).hexdigest();(root/'manifest.json').write_text(json.dumps(meta))
    def crash(df):
        mask=df.date>=str(days[80].date())
        df.loc[mask,['open','high','low','close']]*=.1
    rewrite(root,'bars',crash)
    def limits(df):
        mask=df.date>=str(days[80].date());df.loc[mask,['limit_up','limit_down']]*=.1
    rewrite(root,'status',limits)
    result=run(History(root),str(days[70].date()),str(days[100].date()))
    assert sum(x['side']=='buy' for x in result['ledger'])==2
    assert any(x['state']=='halt' for x in result['equity'])
    assert result['equity'][-1]['state']=='halt'
    assert all(x['date']<str(days[80].date()) for x in result['ledger'] if x['side']=='buy')


def test_outcomes_mature_only_and_idempotent(bundle):
    import sqlite3
    from ashare_swing.daily import record_outcomes
    from ashare_swing.report import persist_signals
    from ashare_swing.backtest import signals_at
    root,days=bundle; history=History(root);db=str(root/'outcomes.db')
    persist_signals(db,signals_at(history,days[70]))
    record_outcomes(history,db,str(days[74].date()))
    with sqlite3.connect(db) as conn:assert conn.execute('SELECT horizon FROM outcome').fetchall()==[(1,)]
    record_outcomes(history,db,str(days[90].date()));record_outcomes(history,db,str(days[90].date()))
    with sqlite3.connect(db) as conn:assert conn.execute('SELECT count(*) FROM outcome').fetchone()[0]==4


def test_one_weak_index_blocks_new_signals(bundle):
    from ashare_swing.backtest import signals_at
    root,days=bundle
    def weaken(df):
        mask=df.code=='sz399001'
        for col in ('open','high','low','close'):df.loc[mask,col]=df.loc[mask,col].iloc[::-1].to_numpy()
    rewrite(root,'indices',weaken)
    signals=signals_at(History(root),days[80])
    assert signals and not any(s.eligible for s in signals)
    assert '双指数' in signals[0].reasons[-1]


def test_missing_stock_session_fails_closed(bundle):
    root,days=bundle
    rewrite(root,'bars',lambda df:df.drop(df[df.date==str(days[90].date())].index))
    with pytest.raises(ValueError,match='缺少行情'):run(History(root),str(days[70].date()),str(days[100].date()))


def test_full_backtest_range_cannot_silently_shrink(bundle):
    root,days=bundle
    with pytest.raises(ValueError,match='完整请求区间'):
        run(History(root),'2020-01-01',str(days[-1].date()))


def test_intraday_source_timestamp_rejected(bundle):
    root,days=bundle;day=str(days[-1].date())
    meta=json.loads((root/'manifest.json').read_text());meta['retrieved_at']=day+'T10:00:00+08:00';(root/'manifest.json').write_text(json.dumps(meta))
    with pytest.raises(ValueError,match='时间戳'):
        scan_bundle(root,now=datetime.fromisoformat(day+'T19:00:00+08:00'))


def test_calendar_coverage_claim_cannot_mask_old_calendar(bundle):
    root,days=bundle
    # Claimed through December, but actual calendar ends in July: reject November scan.
    with pytest.raises(ValueError,match='交易日历覆盖不足'):
        scan_bundle(root,now=datetime.fromisoformat('2025-11-04T19:00:00+08:00'))


def test_delisted_holding_requires_explicit_settlement(bundle):
    root,days=bundle
    result=run(History(root),str(days[70].date()),str(days[100].date()))
    buy=next(x for x in result['ledger'] if x['side']=='buy')
    nextday=days[days>pd.Timestamp(buy['date'])][0]
    def delist(df):df.loc[df.date==str(nextday.date()),'delisted']=1
    rewrite(root,'status',delist)
    with pytest.raises(ValueError,match='退市'):
        run(History(root),str(days[70].date()),str(days[100].date()))


def test_publication_checks_source_time_even_if_md_hash_matches(bundle):
    root,days=bundle;day=str(days[-1].date());now=datetime.fromisoformat(day+'T19:00:00+08:00')
    stage=root/'stage';scan_bundle(root,stage,str(root/'signals.db'),now=now)
    result=json.loads((stage/f'{day}.json').read_text());result['source']['retrieved_at']=day+'T10:00:00+08:00'
    (stage/f'{day}.json').write_text(json.dumps(result))
    with pytest.raises(ValueError,match='时间戳'):
        publish(stage,root/'out',now=now)
