"""AKShare market-data adapter; remote data is never silently replaced by fabricated prices."""
from pathlib import Path
from datetime import datetime
import pandas as pd
from .strategy import normalize_bars


def download_stock(code: str, start: str, end: str, cache_dir: str = 'data/cache') -> pd.DataFrame:
    import akshare as ak
    cache = Path(cache_dir) / f'{code}_{start}_{end}_raw.csv'
    if cache.exists():
        return normalize_bars(pd.read_csv(cache))
    raw = ak.stock_zh_a_hist(symbol=code, period='daily', start_date=start, end_date=end, adjust='')
    if raw is None or raw.empty:
        raise RuntimeError(f'{code} 数据源返回空行情，请确认网络、日期和接口')
    fields = {'日期':'date','开盘':'open','最高':'high','最低':'low','收盘':'close','成交量':'volume','成交额':'amount'}
    df = normalize_bars(raw.rename(columns=fields))
    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache, index=False)
    return df


def download_benchmark(symbol: str, start: str, end: str, cache_dir: str='data/cache') -> pd.DataFrame:
    import akshare as ak
    cache = Path(cache_dir) / f'{symbol}_{start}_{end}.csv'
    if cache.exists():
        return normalize_bars(pd.read_csv(cache))
    raw = ak.stock_zh_index_daily(symbol=symbol)
    if raw is None or raw.empty:
        raise RuntimeError(f'基准指数{symbol}返回空数据')
    # Index API may lack amount. The scanner does not screen index liquidity.
    if 'amount' not in raw.columns:
        raw['amount'] = 0.0
    df = normalize_bars(raw)
    df = df[(df['date'] >= pd.Timestamp(start)) & (df['date'] <= pd.Timestamp(end))]
    if df.empty:
        raise RuntimeError('基准指数在指定日期区间没有数据')
    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache, index=False)
    return df


def validate_asof(stock: pd.DataFrame, benchmark: pd.DataFrame, asof: str) -> None:
    expected = pd.Timestamp(asof)
    if stock.iloc[-1]['date'] < expected or benchmark.iloc[-1]['date'] < expected:
        raise RuntimeError(f'数据未更新至{asof}。禁止以旧行情冒充当日行情。')
