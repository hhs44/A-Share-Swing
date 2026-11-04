"""Point-in-time, daily-close-only scanner logic.

A signal at trading day T may only be traded at T+1 or later.
Input bars must be arranged with dates ascending and only dates <= T.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
import pandas as pd
import numpy as np

REQUIRED = {"date", "open", "high", "low", "close", "volume", "amount"}


@dataclass
class Signal:
    code: str
    date: str
    close: float
    eligible: bool
    reasons: list[str]
    features: dict

    def to_dict(self):
        return asdict(self)


def normalize_bars(frame: pd.DataFrame) -> pd.DataFrame:
    missing = REQUIRED.difference(frame.columns)
    if missing:
        raise ValueError(f"K线缺少字段: {sorted(missing)}")
    df = frame.copy()
    df["date"] = pd.to_datetime(df["date"])
    if df["date"].isna().any():
        raise ValueError("非法日期")
    duplicates = df[df.duplicated("date", keep=False)]
    if not duplicates.empty and (duplicates.groupby("date")[list(REQUIRED - {"date"})].nunique(dropna=False) > 1).any().any():
        raise ValueError("同日行情冲突")
    df = df.sort_values("date").drop_duplicates(subset=["date"], keep="last")
    for col in REQUIRED - {"date"}:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if not np.isfinite(df[list(REQUIRED - {"date"})].to_numpy()).all():
        raise ValueError("行情包含缺失或非有限值")
    if ((df.high < df[["open", "close", "low"]].max(axis=1)) | (df.low > df[["open", "close", "high"]].min(axis=1))).any():
        raise ValueError("OHLC区间非法")
    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("非正行情价格")
    if (df[["volume", "amount"]] < 0).any().any():
        raise ValueError("负成交量或成交额")
    return df.reset_index(drop=True)


def screen(code: str, bars: pd.DataFrame, benchmark: pd.DataFrame | None = None,
           asof: str | None = None) -> Signal:
    df = normalize_bars(bars)
    if asof:
        df = df[df["date"] <= pd.Timestamp(asof)].copy()
    if df.empty:
        raise ValueError(f"{code} 查询日期没有K线")
    row = df.iloc[-1]
    date = row["date"].strftime("%Y-%m-%d")
    if len(df) < 61:
        return Signal(code, date, float(row["close"]), False, ["历史数据不足61个交易日"], {})

    close = df["close"]
    volume = df["volume"]
    # All breakout references exclude signal day T.
    ma20 = float(close.tail(20).mean())
    ma60 = float(close.tail(60).mean())
    prev_max20 = float(close.iloc[-21:-1].max())
    volume_ref5 = float(volume.iloc[-6:-1].mean())
    volume_ratio = float(volume.iloc[-1] / volume_ref5) if volume_ref5 > 0 else 0.0
    ret20 = float(close.iloc[-1] / close.iloc[-21] - 1)
    today_change = float(close.iloc[-1] / close.iloc[-2] - 1)
    benchmark_return = None
    if benchmark is not None:
        b = normalize_bars(benchmark)
        b = b[b["date"] <= row["date"]]
        if len(b) >= 21 and b.iloc[-1]["date"] == row["date"]:
            benchmark_return = float(b.iloc[-1]["close"] / b.iloc[-21]["close"] - 1)

    tests = {
        "趋势收盘价>MA20>MA60": bool(row["close"] > ma20 > ma60),
        "突破此前20日最高收盘价": bool(row["close"] > prev_max20),
        "成交量相对前5日为1.2–2.5倍": bool(1.2 <= volume_ratio <= 2.5),
        "20日涨幅超过基准": bool(benchmark_return is not None and ret20 > benchmark_return),
        "收盘价不高于MA20的108%": bool(row["close"] <= ma20 * 1.08),
        "当日涨幅不超过5%": bool(today_change <= 0.05),
        "20日平均成交额超过1亿元": bool(df["amount"].tail(20).mean() > 1e8),

    }
    if benchmark_return is None:
        tests["基准行情完整且与信号日期一致"] = False
    features = {
        "conditions": tests, "preferred_price": bool(8 <= float(row["close"]) <= 20), "ma20": round(ma20, 4), "ma60": round(ma60, 4),
        "prior_high20": round(prev_max20, 4), "volume_ratio": round(volume_ratio, 4),
        "ret20": round(ret20, 5), "benchmark_ret20": None if benchmark_return is None else round(benchmark_return, 5),
        "today_change": round(today_change, 5), "avg_amount20": round(float(df["amount"].tail(20).mean()), 2)
    }
    reasons = [key for key, passes in tests.items() if not passes]
    return Signal(code, date, round(float(row["close"]), 4), not reasons, reasons, features)
