"""Deterministic risk sizing (research only, no brokerage connection)."""
from dataclasses import asdict, dataclass
import math
from .config import Config


@dataclass
class OrderPlan:
    code: str
    approved: bool
    shares: int
    entry_price: float
    stop_price: float
    planned_loss: float
    estimated_buy_cash: float
    reason: str

    def to_dict(self):
        return asdict(self)


def commission(gross: float, cfg: Config) -> float:
    return max(cfg.commission_minimum, gross * cfg.commission_rate)


def plan_order(code: str, ask_price: float, cash: float, existing_exposure: float = 0,
               existing_positions: int = 0, current_equity: float = 10_000,
               peak_equity: float = 10_000, cfg: Config | None = None) -> OrderPlan:
    cfg = cfg or Config()
    if not all(math.isfinite(x) for x in (ask_price,cash,existing_exposure,current_equity,peak_equity)) or ask_price <= 0 or cash < 0 or peak_equity <= 0 or current_equity <= 0 or existing_exposure < 0:
        raise ValueError("非法价格/现金/历史净值高点")
    entry = ask_price * (1 + cfg.slip_fraction)
    stop = ask_price * (1 - cfg.stop_fraction)
    dd = 1 - current_equity / peak_equity
    limit_cash = min(cfg.target_position, cfg.max_position, cash - cfg.initial_cash * cfg.min_cash_fraction,
                     cfg.initial_cash * cfg.max_exposure_fraction - existing_exposure)
    reasons = []
    if dd >= cfg.stop_equity_drawdown - 1e-10:
        reasons.append("账户回撤触发15%停机审查")
    if dd >= cfg.freeze_equity_drawdown - 1e-10:
        reasons.append(f"账户回撤{dd:.1%}触发冻结新仓")
    if existing_positions >= cfg.max_positions:
        reasons.append("已有持仓数量达到上限")
    if limit_cash <= 0:
        reasons.append("现金或总仓位不足")
    # One lot is mandatory, and a min commission applies to each side.
    max_lots = max(0, math.floor((max(0, limit_cash) - cfg.commission_minimum) / (entry * cfg.lot_size)))
    choice = None
    for lots in range(1, max_lots + 1):
        shares = lots * cfg.lot_size
        buy_gross = entry * shares
        buy_fee = commission(buy_gross, cfg) + buy_gross * 0.00002
        sell_gross = stop * (1 - cfg.slip_fraction) * shares
        sell_fee = commission(sell_gross, cfg) + sell_gross * max(cfg.sell_stamp_tax, 0.001) + sell_gross * 0.00002
        risk = buy_gross + buy_fee - sell_gross + sell_fee
        if buy_gross + buy_fee <= limit_cash and risk <= cfg.per_trade_risk:
            choice = (shares, risk, buy_gross + buy_fee)
    if choice is None:
        reasons.append("100股最小交易单位超出资金或单笔亏损预算")
    if reasons:
        return OrderPlan(code, False, 0, round(entry, 3), round(stop, 3), 0, 0, "; ".join(reasons))
    shares, risk, cost = choice
    return OrderPlan(code, True, shares, round(entry, 3), round(stop, 3), round(risk, 2), round(cost, 2), "仅为模拟计划，T+1人工核验后执行")
