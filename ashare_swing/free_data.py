"""Public watchlist snapshots, never labelled a historical point-in-time universe."""
import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .free_calendar import TZ, SOURCE, complete_session, is_session
from .strategy import normalize_bars

INDICES = {'sh000300': '1.000300', 'sz399001': '0.399001'}
DEFAULT_SYMBOLS = '000001,600000'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


def client():
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    session.mount('https://', HTTPAdapter(max_retries=retry))
    return session


def get_json(url, params):
    with client() as session:
        response = session.get(url, params=params, timeout=(8, 20))
        response.raise_for_status()
        payload = response.json()
    if payload.get('rc') != 0 or not payload.get('data'):
        raise ValueError('行情接口返回错误或空数据')
    return payload


def secid(code):
    if not re.fullmatch(r'(000|001|002|003|600|601|603|605)\d{3}', code):
        raise ValueError(f'{code}: 免费版仅支持配置的沪深主板股票')
    return ('1.' if code.startswith('6') else '0.') + code


def fetch(code, target):
    identity = INDICES.get(code) or secid(code)
    raw = get_json('https://push2his.eastmoney.com/api/qt/stock/kline/get', dict(
        secid=identity, klt=101, fqt=0, beg='20250101', end=target.replace('-', ''),
        fields1='f1,f2,f3,f4,f5,f6', fields2='f51,f52,f53,f54,f55,f56,f57'))
    market, number = identity.split('.')
    if raw['data'].get('code') != number or str(raw['data'].get('market')) != market:
        raise ValueError(f'{code}: 日线证券代码/市场不一致')
    rows = []
    for line in raw['data'].get('klines', []):
        fields = line.split(',')
        if len(fields) != 7:
            raise ValueError(f'{code}: 日线字段数变化')
        day, opening, close, high, low, volume, amount = fields
        rows.append(dict(date=day, open=float(opening), close=float(close), high=float(high),
                         low=float(low), volume=float(volume)*100, amount=float(amount)))
    quote = None
    if code not in INDICES:
        quote = get_json('https://push2.eastmoney.com/api/qt/stock/get', dict(
            secid=identity, fields='f43,f44,f45,f46,f47,f48,f51,f52,f57,f58,f59,f60,f86,f189'))
    return dict(code=code, name=raw['data'].get('name'), bars=rows, raw=raw, quote_raw=quote)


def validate(snapshot, now=None):
    now = now or datetime.now(TZ)
    if now.tzinfo is None:
        raise ValueError('时钟必须带时区')
    day = snapshot['session']
    if complete_session(now) != day:
        raise ValueError('行情日期不是已核实的最近完整交易日')
    stamp = datetime.fromisoformat(snapshot['retrieved_at'])
    if stamp.tzinfo is None or stamp > now or stamp < datetime.fromisoformat(day+'T16:30:00+08:00'):
        raise ValueError('来源时间戳过期或位于未来')
    codes = snapshot['symbols']
    if not codes or len(codes) != len(set(codes)):
        raise ValueError('观察池为空或重复')
    if set(snapshot['securities']) != set(codes) | set(INDICES):
        raise ValueError('观察池/双指数不完整')
    frames = {}
    for code, security in snapshot['securities'].items():
        original = security['bars']
        frame = normalize_bars(pd.DataFrame(original))
        if len(frame) != len(original) or len(frame) < 61:
            raise ValueError(f'{code}: 日线重复或不足61日')
        if str(frame.iloc[-1].date.date()) != day or (frame.date > pd.Timestamp(day)).any():
            raise ValueError(f'{code}: 日线日期不一致')
        expected = [str(d.date()) for d in pd.date_range('2026-01-01', day) if is_session(d.date())]
        actual = frame[frame.date >= '2026-01-01'].date.dt.strftime('%Y-%m-%d').tolist()
        # Missing/suspended stock bars are not silently filled; block this run.
        if actual != expected:
            raise ValueError(f'{code}: 2026交易日覆盖缺失或包含非交易日')
        frames[code] = frame
        if code in INDICES:
            continue
        secid(code)
        quote = (security.get('quote_raw') or {}).get('data') or {}
        if quote.get('f57') != code or quote.get('f58') != security['name']:
            raise ValueError(f'{code}: 报价代码/证券名称不一致')
        quote_day = datetime.fromtimestamp(float(quote['f86']), TZ)
        if quote_day.date().isoformat() != day or quote_day.hour < 15 or quote_day > now:
            raise ValueError(f'{code}: 报价时间不是本次收盘')
        scale = 10 ** int(quote['f59'])
        numbers = {key: float(quote[key])/scale for key in ('f43','f44','f45','f46','f51','f52','f60')}
        if not all(math.isfinite(x) and x > 0 for x in numbers.values()):
            raise ValueError(f'{code}: 报价或涨跌停字段缺失')
        for key, col in [('f43','close'),('f44','high'),('f45','low'),('f46','open')]:
            if abs(numbers[key] - float(frame.iloc[-1][col])) > .005:
                raise ValueError(f'{code}: 报价与日线{col}冲突')
        if numbers['f52'] > numbers['f45'] or numbers['f51'] < numbers['f44']:
            raise ValueError(f'{code}: 行情超出报价涨跌停区间')
        security['status'] = dict(name=security['name'], limit_up=numbers['f51'],
            limit_down=numbers['f52'], previous_close=numbers['f60'],
            ipo_date=datetime.strptime(str(quote['f189']), '%Y%m%d').date().isoformat(),
            restricted=any(x in security['name'].upper() for x in ('ST', '退')),
            corporate_action=abs(numbers['f60'] - float(frame.iloc[-2].close)) > .005)
    return frames


def collect(directory, symbols=DEFAULT_SYMBOLS, now=None):
    now = now or datetime.now(TZ)
    target = complete_session(now)
    codes = sorted(set(symbols.split(','))) if isinstance(symbols, str) else sorted(set(symbols))
    for code in codes:
        secid(code)
    with ThreadPoolExecutor(max_workers=4) as pool:
        securities = dict(zip(codes+list(INDICES), pool.map(lambda code: fetch(code, target), codes+list(INDICES))))
    snapshot = dict(schema=1, kind='free_watchlist_forward_only', session=target,
        retrieved_at=datetime.now(TZ).isoformat(), source='Eastmoney public daily/quote API',
        calendar_source=SOURCE, calendar_through='2026-12-31', symbols=codes,
        historical_universe_verified=False, securities=securities)
    validate(snapshot, datetime.now(TZ))
    snapshot['sha256'] = digest(snapshot)
    root = Path(directory); root.mkdir(parents=True, exist_ok=True)
    path = root / (target+'-'+snapshot['sha256'][:16]+'.json')
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf8')
    pointer = root/'latest.json.tmp'
    pointer.write_text(json.dumps({'path': path.name, 'sha256': snapshot['sha256']}))
    pointer.replace(root/'latest.json')
    return path


def load(path, now=None):
    snapshot = json.loads(Path(path).read_text())
    expected = snapshot.pop('sha256')
    if digest(snapshot) != expected:
        raise ValueError('采集文件摘要校验失败')
    snapshot['sha256'] = expected
    validate(snapshot, now)
    return snapshot
