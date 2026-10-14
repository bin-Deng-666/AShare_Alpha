"""Conservative capacity proxy; daily turnover does not prove auction liquidity."""

import math
import numpy as np


class ParticipationBudget:
    def __init__(self, market, config):
        self.market = market
        self.config = config
        self.used = {}
        self.limits = {}

    def reset(self):
        self.used.clear()
        self.limits.clear()

    def remaining(self, code, session):
        """Cents, shared by every order/side; None means missing observations."""
        if code not in self.limits:
            count = self.config.lookback_sessions
            indices = self.market._code_indices.get(code, np.array([], dtype=int))
            if session < count or "amount" not in self.market._bars:
                self.limits[code] = None
            else:
                cutoff = self.market.calendar[session].value
                stop = np.searchsorted(self.market._date_ns[indices], cutoff)
                history = self.market._bars.iloc[indices[max(0, stop - count) : stop]]
                expected = self.market.calendar[session - count : session]
                values = history.amount.to_numpy(dtype=float, na_value=np.nan)
                if (
                    len(history) != count
                    or not np.array_equal(history.date.to_numpy(), expected.to_numpy())
                    or not np.isfinite(values).all()
                    or (values < 0).any()
                ):
                    self.limits[code] = None
                else:
                    self.limits[code] = math.floor(
                        float(np.median(values)) * self.config.participation_rate * 100
                    )
        limit = self.limits[code]
        return None if limit is None else max(0, limit - self.used.get(code, 0))

    def consume(self, code, notional_cents):
        self.used[code] = self.used.get(code, 0) + notional_cents
