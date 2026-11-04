import pandas as pd
import pytest
from ashare_swing.demo import make_demo_bars
from ashare_swing.strategy import screen, normalize_bars


def test_scan_does_not_peek_into_future():
    df = make_demo_bars()
    bench = make_demo_bars(benchmark=True)
    reference = screen('000001',df.iloc[:90],bench.iloc[:90])
    with_future = screen('000001',df,bench,asof=reference.date)
    assert reference.to_dict()==with_future.to_dict()


def test_missing_benchmark_never_passes():
    s=screen('000001',make_demo_bars(),None)
    assert not s.eligible
    assert any('基准' in reason for reason in s.reasons)


def test_bars_are_deduped_and_sorted():
    df=make_demo_bars()
    extra=pd.concat([df.iloc[::-1],df.tail(1)],ignore_index=True)
    cleaned=normalize_bars(extra)
    assert cleaned['date'].is_monotonic_increasing
    assert len(cleaned)==len(df)


def test_rejects_nonpositive_price():
    df=make_demo_bars()
    df.loc[8,'close']=0
    with pytest.raises(ValueError):
        normalize_bars(df)


def test_short_history_is_rejected():
    s=screen('000001',make_demo_bars(n=30),make_demo_bars(n=30,benchmark=True))
    assert not s.eligible
