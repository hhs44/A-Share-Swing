import argparse
import datetime as dt
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
    scan.add_argument('--asof',default=dt.datetime.now().strftime('%Y-%m-%d'))
    scan.add_argument('--benchmark',default='sh000300')
    scan.add_argument('--demo',action='store_true',help='synthetic prices: only validate execution')
    scan.add_argument('--output',default='reports')
    scan.add_argument('--db',default='data/signals.db')
    args=parser.parse_args(argv)
    if args.command=='scan':
        asof=pd.Timestamp(args.asof).strftime('%Y-%m-%d')
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
        persist_signals(args.db,results)
        print(f'完成：{report_file.absolute()}；候选 {sum(s.eligible for s in results)}/{len(results)}；演示数据={args.demo}')


if __name__ == '__main__':
    main()
