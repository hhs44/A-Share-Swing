import sqlite3
from ashare_swing.demo import make_demo_bars
from ashare_swing.strategy import screen
from ashare_swing.report import persist_signals, render_markdown


def test_persists_idempotently(tmp_path):
    s=screen('000001',make_demo_bars(),make_demo_bars(benchmark=True))
    db=str(tmp_path/'signals.db')
    persist_signals(db,[s]);persist_signals(db,[s])
    with sqlite3.connect(db) as conn:
        assert conn.execute('select count(*) from signal').fetchone()[0]==1
    report=render_markdown([s],s.date,synthetic=True)
    assert '演示假数据' in report
