"""Small independent strategies demonstrating the shared engine contracts."""

from ashare_alpha.backtest.strategy import Strategy
from ashare_alpha.backtest.models import (
    DataRequirements,
    TargetPortfolio,
    OrderRequest,
    OrderBatch,
    Side,
)


class EqualWeightStrategy(Strategy):
    """Periodic equal weight on presently quoted historical-universe symbols."""

    def requirements(self):
        return DataRequirements(fields=("close", "trade_status"))

    def on_start(self, ctx):
        self.last_rebalance = None

    def on_close(self, ctx):
        p = self.params or {}
        interval = p.get("rebalance_sessions", 20)
        if interval < 1:
            raise ValueError("rebalance_sessions must be positive")
        if (
            self.last_rebalance is not None
            and ctx.session_index - self.last_rebalance < interval
        ):
            return None
        day = ctx.data.cross_section()
        selected = day.loc[
            day.trade_status.eq(True)
            & day.close.gt(0)
            & day.code.isin(ctx.data.universe),
            "code",
        ].sort_values()
        selected = list(selected.head(p.get("top_n", 10)))
        self.last_rebalance = ctx.session_index
        exposure = p.get("exposure", 0.8)
        return TargetPortfolio(
            {code: exposure / len(selected) for code in selected}, reason="equal_weight"
        )


class TrendExitStrategy(Strategy):
    """Close-based trend entry and trailing exit, executed on later opens."""

    def requirements(self):
        return DataRequirements(
            mode="orders",
            fields=("close",),
            warmup_sessions=(self.params or {}).get("lookback", 3),
        )

    def on_start(self, ctx):
        self.peaks = {}

    def on_close(self, ctx):
        p = self.params or {}
        lookback = p.get("lookback", 3)
        stop = p.get("stop_fraction", 0.1)
        if not 0 < stop < 1:
            raise ValueError("stop_fraction must be between 0 and 1")
        orders = []
        pending_codes = {o.request.code for o in ctx.pending_orders}
        for lot in ctx.portfolio.lots:
            history = ctx.data.history(lot.code, lookback=1)
            if history.empty:
                continue
            price = float(history.close.iloc[-1])
            peak = max(price, self.peaks.get(lot.lot_id, price))
            self.peaks[lot.lot_id] = peak
            if price <= peak * (1 - stop) and lot.code not in pending_codes:
                orders.append(
                    OrderRequest(
                        lot.code,
                        Side.SELL,
                        quantity=lot.quantity,
                        lot_ids=(lot.lot_id,),
                        reason="trailing_exit",
                        valid_sessions=3,
                    )
                )
        history = ctx.data.history(lookback=lookback)
        for code, group in history.groupby("code", sort=True):
            if (
                code in ctx.portfolio.positions
                or code in pending_codes
                or len(group) < lookback
            ):
                continue
            if group.close.iloc[-1] > group.close.mean():
                orders.append(
                    OrderRequest(
                        code,
                        Side.BUY,
                        budget=p.get("buy_budget", 10_000),
                        reason="trend_entry",
                    )
                )
        return OrderBatch(tuple(orders))


class SleeveStrategy(Strategy):
    """Same symbol can have several tagged, independently held lots."""

    def requirements(self):
        return DataRequirements(mode="orders", fields=("close",))

    def on_start(self, ctx):
        self.entry_sessions = {}

    def on_execution(self, ctx, events):
        for event in events:
            if (
                event.lot_id
                and event.quantity
                and event.status in ("filled", "partially_filled")
            ):
                self.entry_sessions[event.lot_id] = ctx.session_index

    def on_close(self, ctx):
        p = self.params or {}
        period = p.get("holding_sessions", 3)
        sleeves = p.get("sleeves", 3)
        if period < 1 or sleeves < 1:
            raise ValueError("Positive holding_sessions and sleeves required")
        pending_lots = {lid for o in ctx.pending_orders for lid in o.request.lot_ids}
        orders = []
        for lot in ctx.portfolio.lots:
            if (
                ctx.session_index
                - self.entry_sessions.get(lot.lot_id, ctx.session_index)
                >= period
                and lot.lot_id not in pending_lots
            ):
                orders.append(
                    OrderRequest(
                        lot.code,
                        Side.SELL,
                        quantity=lot.quantity,
                        lot_ids=(lot.lot_id,),
                        reason="sleeve_exit",
                        valid_sessions=3,
                    )
                )
        sleeve = str(ctx.session_index % sleeves)
        if any(lot.tags.get("sleeve") == sleeve for lot in ctx.portfolio.lots) or any(
            o.request.tags.get("sleeve") == sleeve for o in ctx.pending_orders
        ):
            return OrderBatch(tuple(orders))
        day = ctx.data.cross_section()
        codes = sorted(set(day.code) & set(ctx.data.universe))
        if codes:
            code = codes[ctx.session_index % len(codes)]
            orders.append(
                OrderRequest(
                    code,
                    Side.BUY,
                    budget=p.get("buy_budget", 10_000),
                    tags={"sleeve": sleeve},
                    reason="sleeve_entry",
                )
            )
        return OrderBatch(tuple(orders))


class ModelScoreStrategy(Strategy):
    """Rank externally prepared predictions whose availability was declared."""

    def requirements(self):
        return DataRequirements(features=("scores",))

    def on_close(self, ctx):
        scores = ctx.data.cross_section("scores")
        p = self.params or {}
        scores = scores.loc[scores.code.isin(ctx.data.universe)].dropna(
            subset=["score"]
        )
        codes = list(
            scores.sort_values(["score", "code"], ascending=[False, True])
            .head(p.get("top_n", 10))
            .code
        )
        exposure = p.get("exposure", 0.8)
        return TargetPortfolio(
            {code: exposure / len(codes) for code in codes}, reason="model_score"
        )
