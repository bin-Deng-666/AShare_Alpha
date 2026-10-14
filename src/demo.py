"""Offline, synthetic inputs demonstrating decisions and real account execution."""

import argparse
import json
from pathlib import Path
import pandas as pd
from ashare_alpha.backtest import BacktestConfig, BacktestEngine, MarketData
from strategy.demo import BuyAndHoldStrategy


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    days = pd.bdate_range("2024-01-02", periods=12)
    rows = []
    for index, day in enumerate(days):
        for code, base in (("000001", 10),):
            price = base + index * 0.05
            rows.append(
                {
                    "code": code,
                    "date": day,
                    "open": price,
                    "close": price + 0.02,
                    "high": price + 0.1,
                    "low": price - 0.1,
                    "volume": 1_000_000,
                    "amount": price * 1_000_000,
                    "trade_status": True,
                    "is_st": False,
                    "limit_up": price * 1.1,
                    "limit_down": price * 0.9,
                    "limit_validation_status": "valid",
                    "corporate_action_formula_status": "none",
                }
            )
    data = MarketData(
        pd.DataFrame(rows),
        calendar=days,
        metadata={"source": "synthetic_demo", "calendar": "synthetic_weekdays"},
    )
    result = BacktestEngine(data).run(
        BuyAndHoldStrategy,
        strategy_params={"code": "000001"},
        config=BacktestConfig(start=str(days[0].date()), end=str(days[-1].date())),
    )
    result.save(args.output, inputs=data)
    print(
        json.dumps(
            {
                "status": result.status,
                "output": str(args.output),
                "source": "synthetic_demo",
                "trades": len(result.trades),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
