from ashare_swing.risk import plan_order


def test_one_lot_and_budget():
    p=plan_order('000001',12.3,10_000)
    assert p.approved
    assert p.shares%100==0
    assert p.planned_loss<=100
    assert p.estimated_buy_cash<=2000


def test_expensive_stock_cannot_be_forced_into_portfolio():
    p=plan_order('600519',1700,10_000)
    assert not p.approved
    assert p.shares==0


def test_freezes_when_drawdown_reaches_twelve_percent():
    p=plan_order('000001',12,10_000,peak_equity=10_000,current_equity=8800)
    assert not p.approved
    assert '冻结' in p.reason


def test_number_of_positions_limit():
    p=plan_order('000001',12,10_000,existing_positions=2)
    assert not p.approved
