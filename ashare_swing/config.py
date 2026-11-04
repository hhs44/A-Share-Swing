from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    initial_cash: float = 10_000
    target_position: float = 1_500
    max_position: float = 2_000
    max_exposure_fraction: float = 0.60
    max_positions: int = 2
    min_cash_fraction: float = 0.40
    lot_size: int = 100
    stop_fraction: float = 0.05
    per_trade_risk: float = 100
    stop_equity_drawdown: float = 0.15
    warn_equity_drawdown: float = 0.08
    freeze_equity_drawdown: float = 0.12
    commission_rate: float = 0.0003  # illustrative, actual broker varies
    commission_minimum: float = 5.0
    sell_stamp_tax: float = 0.0005    # current example, historical tax varies
    slip_fraction: float = 0.001     # assumed one-way slippage
