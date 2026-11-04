"""Auditable, offline point-in-time data contract. Never infer past status from today."""
import hashlib
import json
from pathlib import Path
from datetime import datetime, time
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
from .strategy import normalize_bars

DISCLAIMER = '模拟研究，不构成投资建议'


def latest_session(calendar, now=None):
    now = now or datetime.now(ZoneInfo('Asia/Shanghai'))
    if now.tzinfo is None:
        raise ValueError('时钟必须带时区')
    local = now.astimezone(ZoneInfo('Asia/Shanghai'))
    dates = pd.DatetimeIndex(pd.to_datetime(calendar)).normalize()
    if dates.empty or dates.max().date() < local.date():
        raise ValueError('交易日历覆盖不足，需覆盖当前日期及以后')
    eligible = dates[(dates.date < local.date()) | ((dates.date == local.date()) & (local.time() >= time(16, 30)))]
    if eligible.empty:
        raise ValueError('没有完整交易日')
    return eligible.max().strftime('%Y-%m-%d')


class History:
    def __init__(self, directory):
        root = Path(directory)
        self.meta = json.loads((root / 'manifest.json').read_text())
        for key in ('source', 'license', 'retrieved_at', 'calendar_through'):
            if not self.meta.get(key):
                raise ValueError(f'缺少数据溯源: {key}')
        retrieved = pd.Timestamp(self.meta['retrieved_at'])
        if retrieved.tzinfo is None:
            raise ValueError('retrieved_at 必须带时区')
        for flag in ('point_in_time_universe', 'includes_delisted', 'corporate_actions_verified'):
            if self.meta.get(flag) is not True:
                raise ValueError(f'无效回测: {flag} 未验证')
        for filename in ('calendar.csv', 'bars.csv', 'status.csv', 'indices.csv'):
            digest = hashlib.sha256((root / filename).read_bytes()).hexdigest()
            if self.meta.get('sha256', {}).get(filename) != digest:
                raise ValueError(f'文件哈希不一致: {filename}')
        self.calendar = pd.DatetimeIndex(pd.to_datetime(pd.read_csv(root/'calendar.csv')['date']))
        if self.calendar.has_duplicates or not self.calendar.is_monotonic_increasing:
            raise ValueError('日历重复或乱序')
        self.bars = pd.read_csv(root/'bars.csv', dtype={'code': str})
        self.status = pd.read_csv(root/'status.csv', dtype={'code': str})
        self.indices = pd.read_csv(root/'indices.csv', dtype={'code': str})
        required = {'code','name','board','ipo_date','date','listed','st','delisted','suspended','limit_up','limit_down','action_factor'}
        if not required <= set(self.status):
            raise ValueError(f'历史状态缺少: {required-set(self.status)}')
        for df in (self.bars, self.status, self.indices):
            df['date'] = pd.to_datetime(df['date'])
            if df.duplicated(['code','date']).any() or not df.date.isin(self.calendar).all():
                raise ValueError('重复行情或非交易日行情')
        for df in (self.bars, self.indices):
            for _, group in df.groupby('code'):
                normalize_bars(group)
        self.status['ipo_date'] = pd.to_datetime(self.status['ipo_date'])
        if self.status[['name','board','ipo_date']].isna().any().any():
            raise ValueError('缺少证券名称/类别/IPO日期')
        if not self.status.board.isin(['main','chinext','star']).all():
            raise ValueError('暂不支持的证券类别（仅沪深A股）')
        if ((self.status.listed == 1) & (self.status.date < self.status.ipo_date)).any():
            raise ValueError('IPO日期前不允许上市状态')
        if not self.bars.set_index(['date','code']).index.isin(self.status.set_index(['date','code']).index).all():
            raise ValueError('行情缺少逐日历史状态')
        for field in ('listed','st','delisted','suspended'):
            if not self.status[field].isin([0,1]).all():
                raise ValueError(f'未知状态: {field}')
        if not np.isfinite(self.status[['limit_up','limit_down','action_factor']].to_numpy(dtype=float)).all():
            raise ValueError('未知涨跌停或公司行动')
        if (self.status.limit_down <= 0).any() or (self.status.limit_up < self.status.limit_down).any():
            raise ValueError('涨跌停价非法')
        # Until cash dividends/splits are implemented, refuse any affected dataset.
        if (self.status.action_factor != 1).any():
            raise ValueError('无效回测: 含公司行动，尚不支持现金分红/拆股记账')
        self.status = self.status.set_index(['date','code']).sort_index()
        self.bars = self.bars.set_index(['date','code']).sort_index()
        self.indices = self.indices.set_index(['date','code']).sort_index()

    def validate_range(self, start, end):
        sessions = self.calendar[(self.calendar >= pd.Timestamp(start)) & (self.calendar <= pd.Timestamp(end))]
        if sessions.empty or pd.Timestamp(end) not in sessions:
            raise ValueError('结束日期不是交易日或区间为空')
        # Require explicit status coverage for the entire supplied historical universe.
        codes=self.status.index.get_level_values('code').unique()
        for date in sessions:
            if date not in self.status.index.get_level_values('date'):
                raise ValueError(f'{date}: 缺少历史股票池')
            states = self.status.loc[date]
            if set(states.index) != set(codes):
                raise ValueError(f'{date}: 历史股票池有证券状态缺失')
            active = states[(states.listed == 1) & (states.delisted == 0)]
            for code in active.index:
                if (date,code) not in self.bars.index:
                    raise ValueError(f'{date} {code}: 缺少行情（停牌也须显式记录）')
            for code in ('sh000300','sz399001'):
                if (date,code) not in self.indices.index:
                    raise ValueError(f'{date}: 缺少双指数 {code}')
        return sessions

    def frame(self, table, code, date):
        df = getattr(self, table)
        if code not in df.index.get_level_values('code'):
            raise ValueError(f'缺少证券: {code}')
        df = df.xs(code, level='code').loc[:date].reset_index()
        expected=self.calendar[(self.calendar>=df.date.min()) & (self.calendar<=pd.Timestamp(date))]
        if len(df)!=len(expected) or not df.date.isin(expected).all():
            raise ValueError(f'{code}: 历史序列缺少交易日，停牌必须显式记录')
        return df
