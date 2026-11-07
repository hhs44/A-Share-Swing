import argparse
import datetime as dt
import json
from pathlib import Path
import pandas as pd
from .data import download_stock, download_benchmark
from .demo import make_demo_bars
from .strategy import screen
from .report import render_markdown, persist_signals


def main(argv=None):
    parser=argparse.ArgumentParser(description='A-share swing research CLI: never places orders')
    subs=parser.add_subparsers(dest='command', required=True)
    scan=subs.add_parser('scan',help='Scan configured codes after close')
    scan.add_argument('--symbols',default='000001,600000',help='comma-separated mainboard codes')
    scan.add_argument('--asof')
    scan.add_argument('--bundle',help='verified point-in-time dataset directory')
    scan.add_argument('--benchmark',default='sh000300')
    scan.add_argument('--demo',action='store_true',help='synthetic prices: only validate execution')
    scan.add_argument('--output',default='reports')
    scan.add_argument('--db',default='data/signals.db')
    back=subs.add_parser('backtest')
    back.add_argument('--bundle',required=True)
    back.add_argument('--start',default='2020-01-01')
    back.add_argument('--end',required=True)
    back.add_argument('--output',default='reports/backtest.json')
    pub=subs.add_parser('publish')
    pub.add_argument('--staged',default='reports/staged')
    pub.add_argument('--output',default='reports/published')
    collection=subs.add_parser('collect-free',help='Collect and validate public watchlist data')
    collection.add_argument('--output',default='data/free-workflow/snapshots')
    collection.add_argument('--symbols',default='000001,600000')
    paper=subs.add_parser('paper',help='Advance persistent forward-paper account using a verified free snapshot')
    paper.add_argument('--snapshot',required=True)
    paper.add_argument('--db',default='data/free-workflow/paper.db')
    paper.add_argument('--output',default='data/free-workflow/staged')
    args=parser.parse_args(argv)
    if args.command=='collect-free':
        from .free_data import collect
        print(collect(args.output,args.symbols))
        return
    if args.command=='paper':
        from .free_data import load
        from .paper import advance
        from .free_daily import lock, stage
        with lock(Path(args.db).parent):
            report=advance(args.db,load(args.snapshot))
            print(stage(report,args.output)/'report.md')
        return
    if args.command=='publish':
        from .daily import publish
        publish(args.staged,args.output)
        return
    if args.command=='backtest':
        from .history import History, DISCLAIMER
        from .backtest import run
        from .daily import atomic_write
        try:
            history=History(args.bundle)
            periods=[('research','2020-01-01','2023-12-29'),('out_of_sample','2024-01-01','2025-12-31'),('2026','2026-01-01',args.end)]
            results={name:run(history,max(start,args.start),min(end,args.end)) for name,start,end in periods if max(start,args.start)<=min(end,args.end)}
            if not results:
                raise ValueError('没有有效回测区间')
            payload=dict(disclaimer=DISCLAIMER,valid=True,periods=results)
        except Exception as exc:
            atomic_write(args.output,json.dumps(dict(disclaimer=DISCLAIMER,valid=False,error=str(exc),metrics=None),ensure_ascii=False,indent=2))
            raise
        atomic_write(args.output,json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False))
        return
    if args.command=='scan':
        if not args.demo:
            if not args.bundle:
                raise RuntimeError('真实扫描需要 --bundle：含历史股票池/交易日历/状态/双指数的已校验数据；AKShare单独日线不足以保证可信')
            from .daily import scan_bundle
            result=scan_bundle(args.bundle,args.output,args.db,args.asof)
            print(f"完成：{result['date']}，模拟研究，不构成投资建议")
            return
        asof=pd.Timestamp(args.asof or dt.datetime.now().strftime('%Y-%m-%d')).strftime('%Y-%m-%d')
        start=(pd.Timestamp(asof)-pd.Timedelta(days=450)).strftime('%Y%m%d')
        end=pd.Timestamp(asof).strftime('%Y%m%d')
        symbols=[x.strip() for x in args.symbols.split(',') if x.strip()]
        benchmark=make_demo_bars(asof,benchmark=True) if args.demo else download_benchmark(args.benchmark,start,end)
        results=[]
        for code in symbols:
            bars=make_demo_bars(asof) if args.demo else download_stock(code,start,end)
            if not args.demo:
                # Never silently use an earlier bar in an as-of report.
                if bars.iloc[-1]['date'] < pd.Timestamp(asof) or benchmark.iloc[-1]['date'] < pd.Timestamp(asof):
                    raise RuntimeError('数据不是指定交易日。非交易日请传入最近已完成交易日，不要生成伪实时报告。')
            results.append(screen(code,bars,benchmark,asof=asof))
        results.sort(key=lambda s:(not s.eligible, -(s.features.get('ret20',-1) - (s.features.get('benchmark_ret20') or 0))))
        out=Path(args.output)
        out.mkdir(parents=True,exist_ok=True)
        report_file=out/f'{asof}{"-DEMO" if args.demo else ""}.md'
        report_file.write_text(render_markdown(results,asof,args.demo),encoding='utf8')
        persist_signals(str(Path(args.db).with_name(Path(args.db).stem+'-DEMO.db')),results)
        print(f'完成：{report_file.absolute()}；候选 {sum(s.eligible for s in results)}/{len(results)}；演示数据={args.demo}')


if __name__ == '__main__':
    main()
