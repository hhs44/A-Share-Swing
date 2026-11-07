"""Reviewed exchange calendar; unknown years fail closed instead of guessing."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Shanghai')
SOURCE = 'https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20251222_10802510.shtml'
HOLIDAYS = [('01-01', '01-03'), ('02-15', '02-23'), ('04-04', '04-06'),
            ('05-01', '05-05'), ('06-19', '06-21'), ('09-25', '09-27'), ('10-01', '10-07')]


def is_session(day):
    if day.year != 2026:
        raise ValueError('交易日历仅核实2026年，请先更新交易所年度休市安排')
    return day.weekday() < 5 and not any(a <= day.strftime('%m-%d') <= b for a, b in HOLIDAYS)


def complete_session(now=None):
    now = now or datetime.now(TZ)
    if now.tzinfo is None:
        raise ValueError('时钟必须带时区')
    local = now.astimezone(TZ)
    day = local.date()
    if local.time() < time(16, 30):
        day -= timedelta(days=1)
    while not is_session(day):
        day -= timedelta(days=1)
    return day.isoformat()


def next_session(day):
    day = datetime.fromisoformat(day).date() + timedelta(days=1)
    while not is_session(day):
        day += timedelta(days=1)
    return day.isoformat()
