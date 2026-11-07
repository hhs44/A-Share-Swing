"""Synthetic mechanics only: no test fixture is a historical performance claim."""
from datetime import datetime
import json
import sqlite3

import pandas as pd
import pytest

from ashare_swing import free_data, paper
from ashare_swing.free_calendar import complete_session, is_session
from ashare_swing.free_daily import stage, publish, run
from ashare_swing.strategy import Signal


def clock(day, hour=19):
    return datetime.fromisoformat(f'{day}T{hour:02}:00:00+08:00')


def fixture_snapshot(day, opening=10.2, low=10., close=10.2):
    dates = [d for d in pd.date_range('2026-01-01', day) if is_session(d.date())]
    securities = {}
    for code in ['000001', *free_data.INDICES]:
        rows = []
        for i, d in enumerate(dates):
            price = 100+i if code in free_data.INDICES else 10.2
            rows.append(dict(date=str(d.date()), open=price, high=price+1,
                             low=price-.2, close=price, volume=2e8, amount=2e9))
        value = dict(name='SYNTHETIC ONLY', bars=rows)
        if code == '000001':
            rows[-1].update(open=opening, high=max(opening, close)+.3, low=low, close=close)
            value['quote_raw'] = dict(data=dict(f57=code, f58=value['name'], f59=2,
                f43=round(close*100), f44=round(rows[-1]['high']*100), f45=round(low*100),
                f46=round(opening*100), f51=1122, f52=918, f60=1020,
                f86=clock(day, 15).timestamp(), f189=19910403))
        securities[code] = value
    result = dict(session=day, symbols=['000001'], securities=securities,
                  retrieved_at=clock(day, 17).isoformat(), sha256='synthetic-test-only')
    free_data.validate(result, clock(day))
    return result


@pytest.fixture
def eligible(monkeypatch):
    monkeypatch.setattr(paper, 'screen', lambda code, bars, bench, day:
        Signal(code, day, float(bars.close.iloc[-1]), True, [], {'ret20': .1}))


def account(db):
    with sqlite3.connect(db) as connection:
        return json.loads(connection.execute('SELECT payload FROM account WHERE id=1').fetchone()[0])


def test_exchange_calendar_holidays_weekends_and_cutoff():
    assert complete_session(clock('2026-11-07')) == '2026-11-06'
    assert complete_session(clock('2026-10-07')) == '2026-09-30'
    assert complete_session(clock('2026-11-09', 15)) == '2026-11-06'
    assert not is_session(datetime(2026, 10, 10).date())
    with pytest.raises(ValueError, match='2026'):
        complete_session(clock('2027-01-04'))


@pytest.mark.parametrize('kind', ['missing_bar', 'old_quote', 'bad_limit', 'conflicting_close', 'future_stamp'])
def test_quality_rejects_incomplete_or_conflicting_source(kind):
    snapshot = fixture_snapshot('2026-11-06')
    security = snapshot['securities']['000001']
    if kind == 'missing_bar': security['bars'].pop(20)
    if kind == 'old_quote': security['quote_raw']['data']['f86'] = clock('2026-11-05', 15).timestamp()
    if kind == 'bad_limit': security['quote_raw']['data']['f51'] = '-'
    if kind == 'conflicting_close': security['quote_raw']['data']['f43'] = 1000
    if kind == 'future_stamp': snapshot['retrieved_at'] = clock('2026-11-09').isoformat()
    with pytest.raises((ValueError, TypeError)):
        free_data.validate(snapshot, clock('2026-11-06'))


def test_collection_failure_preserves_previous_success_pointer(tmp_path, monkeypatch):
    (tmp_path/'latest.json').write_text('previous success')
    def fail(*args): raise ValueError('upstream unavailable')
    monkeypatch.setattr(free_data, 'fetch', fail)
    with pytest.raises(ValueError):
        free_data.collect(tmp_path, '000001', clock('2026-11-06'))
    assert (tmp_path/'latest.json').read_text() == 'previous success'


def test_snapshot_tampering_rejected_before_account_write(tmp_path):
    snapshot = fixture_snapshot('2026-11-06')
    snapshot['sha256'] = 'wrong'
    path = tmp_path/'snapshot.json'; path.write_text(json.dumps(snapshot))
    with pytest.raises(ValueError, match='摘要'):
        free_data.load(path, clock('2026-11-06'))


def test_bootstrap_then_next_session_buy_idempotent_and_t_plus_one(tmp_path, eligible):
    db = tmp_path/'paper.db'
    first = paper.advance(db, fixture_snapshot('2026-11-04'), clock('2026-11-04'))
    assert first['bootstrap'] and first['trades'] == []
    assert first['account']['cash'] == 10000
    second_snapshot = fixture_snapshot('2026-11-05', low=9.4)
    second = paper.advance(db, second_snapshot, clock('2026-11-05'))
    assert [t['side'] for t in second['trades']] == ['buy']
    assert second['trades'][0]['shares'] % 100 == 0
    assert second['trades'][0]['signal_date'] == '2026-11-04'
    assert second['account']['cash'] >= 4000
    assert second['account']['positions']['000001']['age'] == 0
    second_snapshot['retrieved_at'] = clock('2026-11-05', 18).isoformat()
    assert paper.advance(db, second_snapshot, clock('2026-11-05')) == second
    with sqlite3.connect(db) as connection:
        assert connection.execute('SELECT count(*) FROM ledger').fetchone()[0] == 1


def test_gap_stop_sell_fees_and_restart_preserve_cash(tmp_path, eligible):
    db = tmp_path/'paper.db'
    for day in ('2026-11-04', '2026-11-05'):
        paper.advance(db, fixture_snapshot(day), clock(day))
    result = paper.advance(db, fixture_snapshot('2026-11-06', 9.5, 9.4, 9.6), clock('2026-11-06'))
    trade = result['trades'][0]
    assert trade['side'] == 'sell' and trade['price'] == pytest.approx(9.5*.999)
    assert trade['fee'] > 5 and trade['pnl'] < 0
    assert not result['account']['positions']
    assert account(db)['cash'] == result['account']['cash'] < 10000


def test_missed_session_refuses_retrospective_fill(tmp_path, eligible):
    db = tmp_path/'paper.db'
    paper.advance(db, fixture_snapshot('2026-11-04'), clock('2026-11-04'))
    with pytest.raises(ValueError, match='漏跑'):
        paper.advance(db, fixture_snapshot('2026-11-06'), clock('2026-11-06'))
    assert account(db)['last_session'] == '2026-11-04'


def test_existing_day_revision_rejected(tmp_path, eligible):
    db = tmp_path/'paper.db'
    paper.advance(db, fixture_snapshot('2026-11-04'), clock('2026-11-04'))
    changed = fixture_snapshot('2026-11-04', close=10.3)
    with pytest.raises(ValueError, match='重写'):
        paper.advance(db, changed, clock('2026-11-04'))
    assert account(db)['cash'] == 10000


def test_corporate_action_freezes_holding_without_partial_write(tmp_path, eligible):
    db = tmp_path/'paper.db'
    for day in ('2026-11-04', '2026-11-05'):
        paper.advance(db, fixture_snapshot(day), clock(day))
    before = account(db)
    snapshot = fixture_snapshot('2026-11-06')
    snapshot['securities']['000001']['quote_raw']['data']['f60'] = 1000
    with pytest.raises(ValueError, match='除权'):
        paper.advance(db, snapshot, clock('2026-11-06'))
    assert account(db) == before


def test_exception_after_sell_rolls_back_entire_session(tmp_path, eligible, monkeypatch):
    db = tmp_path/'paper.db'
    for day in ('2026-11-04', '2026-11-05'):
        paper.advance(db, fixture_snapshot(day), clock(day))
    before = account(db)
    def fail(*args, **kwargs): raise RuntimeError('test interruption after hypothetical sell')
    monkeypatch.setattr(paper, 'screen', fail)
    with pytest.raises(RuntimeError):
        paper.advance(db, fixture_snapshot('2026-11-06', 9.5, 9.4, 9.6), clock('2026-11-06'))
    assert account(db) == before
    with sqlite3.connect(db) as connection:
        assert connection.execute('SELECT count(*) FROM ledger').fetchone()[0] == 1


def test_report_publication_time_integrity_and_weekend_date(tmp_path, eligible):
    result = paper.advance(tmp_path/'paper.db', fixture_snapshot('2026-11-06'), clock('2026-11-06'))
    staged = stage(result, tmp_path/'staged')
    with pytest.raises(ValueError, match='19:00'):
        publish(staged, tmp_path/'published', clock('2026-11-06', 18))
    assert publish(staged, tmp_path/'published', clock('2026-11-07')).endswith('2026-11-06/report.md')
    payload = json.loads((staged/'report.json').read_text())
    payload['report']['account']['cash'] = 9000
    (staged/'report.json').write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='JSON'):
        publish(staged, tmp_path/'published', clock('2026-11-07'))


def test_scheduled_holiday_skips_without_network_or_account(tmp_path):
    result = run(tmp_path, scheduled=True, now=clock('2026-11-07'))
    assert result['status'] == 'skipped'
    assert not (tmp_path/'paper.db').exists()


@pytest.mark.parametrize('cash,expected', [(8800, 'freeze'), (8500, 'halt')])
def test_account_drawdown_blocks_pending_buy_and_halt_latches(tmp_path, eligible, cash, expected):
    db = tmp_path/'paper.db'
    paper.advance(db, fixture_snapshot('2026-11-04'), clock('2026-11-04'))
    state = account(db); state['cash'] = cash
    with sqlite3.connect(db) as connection:
        connection.execute('UPDATE account SET payload=? WHERE id=1', (json.dumps(state),))
    result = paper.advance(db, fixture_snapshot('2026-11-05'), clock('2026-11-05'))
    assert result['account']['risk'] == expected
    assert not result['trades'] and not result['account']['pending']
    if expected == 'halt':
        state = account(db); state['cash'] = 10000
        with sqlite3.connect(db) as connection:
            connection.execute('UPDATE account SET payload=? WHERE id=1', (json.dumps(state),))
        recovered = paper.advance(db, fixture_snapshot('2026-11-06'), clock('2026-11-06'))
        assert recovered['account']['risk'] == 'halt' and not recovered['account']['pending']
