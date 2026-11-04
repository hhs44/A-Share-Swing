"""Deterministic synthetic bars for an offline smoke test; NOT real market data."""
import pandas as pd
import numpy as np


def make_demo_bars(asof: str='2026-11-04', n: int=120, *, benchmark: bool=False) -> pd.DataFrame:
    dates = pd.bdate_range(end=pd.Timestamp(asof), periods=n)
    t = np.arange(n, dtype=float)
    if benchmark:
        price = 12.0 + t * 0.011
        volume = np.full(n, 1_000_000.)
    else:
        price = 10.0 + t * 0.016 + np.sin(t * 0.32) * 0.12
        price[-1] = price[-2] + 0.20
        volume = np.full(n, 800_000.)
        volume[-1] = 1_250_000.
    return pd.DataFrame({'date':dates, 'open':price-0.03, 'high':price+0.08,
                         'low':price-0.08,'close':price,'volume':volume,
                         'amount':volume * price * 20})
