"""Real end-to-end acceptance runner. No demo imports or synthetic price fallback.

python -m ashare_swing.acceptance --output work/acceptance --bundle data/history
An incomplete acceptance returns exit code 1, even if every pytest passes.
"""
import argparse
from datetime import datetime
from importlib.metadata import version
import json
from pathlib import Path
import shlex
import subprocess
import sys
from zoneinfo import ZoneInfo


def probe(kind, symbol, start, end, output):
    import akshare as ak
    import pandas as pd
    from .strategy import normalize_bars
    root=Path(output);root.mkdir(parents=True,exist_ok=True)
    if kind=='freshness':
        from .history import latest_session
        dates=pd.to_datetime(pd.read_csv(root/'calendar-all-raw.csv')['trade_date'])
        latest=latest_session(dates)
        names=pd.read_csv(root/'names-all-raw.csv',dtype={'code':str})
        details=[]
        for category,code in [('stock','000001'),('stock','600000'),('index','sh000300'),('index','sz399001')]:
            bars=normalize_bars(pd.read_csv(root/f'{category}-{code}-normalized.csv'))
            if str(bars.date.max().date())!=latest or len(bars)<61:
                raise ValueError(f'{code}最近完整交易日不一致或历史不足61日')
            info=dict(code=code,rows=len(bars),last_date=latest)
            if category=='stock':
                matched=names[names.code==code]
                if len(matched)!=1 or not isinstance(matched.iloc[0]['name'],str):
                    raise ValueError(f'{code}证券名称无法核验')
                info['name']=matched.iloc[0]['name']
            details.append(info)
        print(json.dumps(dict(latest_complete_session=latest,securities=details),ensure_ascii=False))
        return
    if kind=='calendar':
        raw=ak.tool_trade_date_hist_sina()
    elif kind=='names':
        raw=ak.stock_info_a_code_name()
    elif kind=='stock':
        raw=ak.stock_zh_a_hist(symbol=symbol,period='daily',start_date=start.replace('-',''),end_date=end.replace('-',''),adjust='')
    else:
        raw=ak.stock_zh_index_daily(symbol=symbol)
    if raw is None or raw.empty:
        raise ValueError('真实接口返回空表')
    raw.to_csv(root/f'{kind}-{symbol or "all"}-raw.csv',index=False)
    metadata=dict(kind=kind,symbol=symbol,rows=len(raw),columns=list(raw),synthetic=False,retrieved_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat())
    if kind=='calendar':
        from .history import latest_session
        dates=pd.to_datetime(raw['trade_date'])
        metadata['latest_complete_session']=latest_session(dates)
        metadata['actual_range']=[str(dates.min().date()),str(dates.max().date())]
    elif kind in ('stock','index'):
        bars=raw.rename(columns={'日期':'date','开盘':'open','最高':'high','最低':'low','收盘':'close','成交量':'volume','成交额':'amount'})
        if kind=='index' and 'amount' not in bars:bars['amount']=0
        bars=normalize_bars(bars)
        bars=bars[(bars.date>=pd.Timestamp(start)) & (bars.date<=pd.Timestamp(end))]
        if bars.empty:raise ValueError('真实接口在请求范围没有行情')
        metadata['actual_range']=[str(bars.date.min().date()),str(bars.date.max().date())]
        metadata['rows_in_range']=len(bars)
        if kind=='stock':
            bars['volume']*=100  # stock_zh_a_hist documents volume in lots of 100 shares.
            metadata['volume_unit']='shares (AKShare hands converted x100)'
        bars.to_csv(root/f'{kind}-{symbol}-normalized.csv',index=False)
    print(json.dumps(metadata,ensure_ascii=False,allow_nan=False))


def run_command(root, name, argv, timeout=30):
    start=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
    try:
        process=subprocess.run(argv,capture_output=True,text=True,timeout=timeout)
        output=process.stdout+process.stderr; code=process.returncode
    except subprocess.TimeoutExpired as exc:
        def decode(x):return x.decode(errors='replace') if isinstance(x,bytes) else (x or '')
        output=decode(exc.stdout)+decode(exc.stderr)+'\nTIMEOUT\n';code=124
    path=root/f'{name}.log'
    path.write_text(f'started_at={start}\ncommand={shlex.join(argv)}\nexit_code={code}\n'+output,encoding='utf8')
    print(f'{name}: exit={code} log={path}',flush=True)
    result=dict(name=name,started_at=start,command=argv,exit_code=code,log=path.name)
    if code==0:
        try:result['result']=json.loads(output)
        except json.JSONDecodeError:pass
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default='work/acceptance')
    parser.add_argument('--bundle',default='data/history')
    parser.add_argument('--start',default='2025-01-01')
    parser.add_argument('--end')
    parser.add_argument('--timeout',type=int,default=25)
    parser.add_argument('--probe',choices=['calendar','names','stock','index','freshness'],help=argparse.SUPPRESS)
    parser.add_argument('--symbol',default='')
    args=parser.parse_args(argv)
    now=datetime.now(ZoneInfo('Asia/Shanghai'))
    end=args.end or now.date().isoformat()
    if args.probe:
        probe(args.probe,args.symbol,args.start,end,args.output)
        return 0
    root=Path(args.output)
    if root.exists() and any(root.iterdir()):
        parser.error('验收输出目录必须为空，避免混用上次运行的真实数据与日志')
    root.mkdir(parents=True,exist_ok=True)
    py=sys.executable
    base=[py,'-m','ashare_swing.acceptance','--start',args.start,'--end',end,'--output',str(root/'raw')]
    commands=[]
    for name,kind,symbol in [('01-calendar','calendar',''),('01-names','names',''),('01-stock-000001','stock','000001'),('01-stock-600000','stock','600000'),('01-hs300','index','sh000300'),('01-szcomponent','index','sz399001')]:
        commands.append(run_command(root,name,base+['--probe',kind,'--symbol',symbol],args.timeout))
    commands.append(run_command(root,'02-complete-session',base+['--probe','freshness'],args.timeout))
    calendar=commands[0].get('result',{})
    latest=calendar.get('latest_complete_session')
    # Requested end is not evidence of a verified completed trading session.
    actual={x['name']:x.get('result',{}).get('actual_range') for x in commands}
    cli=[py,'-m','ashare_swing']
    scan=run_command(root,'03-selection',cli+['scan','--bundle',args.bundle,'--output',str(root/'staged'),'--db',str(root/'signals.db')])
    commands.append(scan)
    # These are explicit, market-independent arithmetic scenarios, not fabricated market prices.
    risk_code="""import json
from ashare_swing.risk import plan_order
from ashare_swing.backtest import risk_state
cases={'lot':plan_order('ARITHMETIC_ONLY',12.3,10000).to_dict(),'unaffordable':plan_order('ARITHMETIC_ONLY',1700,10000).to_dict(),'position_limit':plan_order('ARITHMETIC_ONLY',12.3,10000,existing_positions=2).to_dict(),'cash_floor':plan_order('ARITHMETIC_ONLY',12.3,4000).to_dict(),'exposure_limit':plan_order('ARITHMETIC_ONLY',12.3,10000,existing_exposure=6000).to_dict(),'freeze':plan_order('ARITHMETIC_ONLY',12.3,10000,current_equity=8800).to_dict(),'halt':plan_order('ARITHMETIC_ONLY',12.3,10000,current_equity=8500).to_dict()}
assert cases['lot']['approved'] and cases['lot']['shares']%100==0 and cases['lot']['planned_loss']<=100
assert all(not cases[k]['approved'] for k in cases if k!='lot')
assert [risk_state(x,10000) for x in (10000,9200,8800,8500)]==['normal','warning','freeze','halt']
print(json.dumps({'kind':'market_independent_arithmetic_only','cases':cases},ensure_ascii=False))"""
    risk=run_command(root,'04-05-lot-and-risk',[py,'-c',risk_code]);commands.append(risk)
    commands.append(run_command(root,'06-backtest',cli+['backtest','--bundle',args.bundle,'--start','2020-01-01','--end',latest or end,'--output',str(root/'backtest.json')],120))
    commands.append(run_command(root,'07-paper-cli',cli+['paper','--bundle',args.bundle]))
    commands.append(run_command(root,'08-markdown-publish',cli+['publish','--staged',str(root/'staged'),'--output',str(root/'published')]))
    tests=run_command(root,'09-pytest',[py,'-m','pytest','-q','--junitxml='+str(root/'pytest.xml')],120);commands.append(tests)
    # Success requires real collection, calendar freshness, selection, actual simulation and report.
    passed=all(x['exit_code']==0 for x in commands)
    staged=list((root/'staged').glob('*.json')) if scan['exit_code']==0 else []
    daily=json.loads(staged[0].read_text()) if len(staged)==1 else None
    backtest=json.loads((root/'backtest.json').read_text()) if (root/'backtest.json').exists() else None
    summary=dict(disclaimer='模拟研究，不构成投资建议',started_at=now.isoformat(),finished_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
                 acceptance_passed=passed,python=sys.version,versions={p:version(p) for p in ('akshare','pandas','numpy','pytest')},
                 requested_market_range=[args.start,end],requested_backtest_ranges=[['2020-01-01','2023-12-29'],['2024-01-01','2025-12-31'],['2026-01-01',latest or end]],
                 verified_latest_complete_session=latest,observed_data_ranges=actual,
                 candidates=daily.get('candidates') if daily else None,executed_paper_trades=None,performance=backtest.get('periods') if backtest and backtest.get('valid') else None,
                 data_completeness_passed=commands[6]['exit_code']==0,
                 note='null表示未完成真实验证，不等同于零候选/零交易/零收益；paper CLI尚未实现，回测模拟不等同持续模拟账户。',commands=commands)
    (root/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
    lines=['# A股波段端到端验收（非选股日报）','','模拟研究，不构成投资建议','',f'运行：{now.isoformat()}',f'端到端验收：{"通过" if passed else "未通过"}',
           f'请求行情范围：{args.start} 至 {end}',f'已验证最近完整交易日：{latest or "未知（无法确认）"}',
           '真实候选股、模拟成交、真实绩效：以summary.json为准；null表示未完成验证。','','|步骤|退出码|日志|','|---|---:|---|']
    lines += [f"|{x['name']}|{x['exit_code']}|[{x['log']}]({x['log']})|" for x in commands]
    lines += ['','单元/集成测试中的合成夹具只用于验证代码行为，不作为本次真实数据验收通过证据。']
    (root/'ACCEPTANCE.md').write_text('\n'.join(lines)+'\n',encoding='utf8')
    return 0 if passed else 1


if __name__=='__main__':
    raise SystemExit(main())
