"""Buy one selected security and hold it; the shared engine owns the account."""

from pathlib import Path
from backtest import Strategy, DataRequirements, OrderBatch, OrderRequest, Side
from .params import Params


class BuyAndHoldStrategy(Strategy):
    def __init__(self, params=None):
        super().__init__(Params.parse(params))
        self.has_bought = False
        self.plan = None

    def requirements(self):
        return DataRequirements(
            mode="orders",
            fields=("close", "trade_status"),
            code_dependencies=(str(Path(__file__).with_name("params.py")),),
        )

    def on_close(self, ctx):
        code = self.params.code
        positions = []
        for held in ctx.portfolio.positions.values():
            lots = [lot for lot in ctx.portfolio.lots if lot.code == held.code]
            price = held.market_value / held.quantity
            cost = held.cost_basis / held.quantity
            positions.append(
                {
                    "code": held.code,
                    "name": held.code,
                    "shares": held.quantity,
                    "avg_cost": cost,
                    "current_price": price,
                    "pnl_pct": (price / cost - 1) * 100 if cost else None,
                    "hold_days": (
                        ctx.asof.normalize() - min(lot.acquired_at for lot in lots)
                    ).days,
                }
            )
        self.plan = {
            "asof": str(ctx.asof.date()),
            "positions": positions,
            "portfolio": {
                "cash": ctx.portfolio.cash,
                "equity": ctx.portfolio.equity,
                "market_value": ctx.portfolio.market_value,
                "positions": positions,
            },
        }
        if code in ctx.portfolio.positions:
            self.has_bought = True
        if self.has_bought or ctx.pending_orders:
            return None
        day = ctx.data.cross_section()
        quoted = day.loc[day.code.eq(code) & day.trade_status.eq(True)]
        if quoted.empty or quoted.close.iloc[0] <= 0 or ctx.portfolio.cash <= 0:
            return None
        return OrderBatch(
            (
                OrderRequest(
                    code,
                    Side.BUY,
                    budget=ctx.portfolio.cash,
                    valid_sessions=20,
                    reason="buy_and_hold",
                ),
            )
        )

    def on_end(self, result_view):
        return {"plan": self.plan}
