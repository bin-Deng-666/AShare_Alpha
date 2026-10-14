"""Cash-yield source adapter.

The source adapter only fetches and normalizes data. Publication uses the shared atomic storage layer; there is no strategy dependency.
"""

from __future__ import annotations

from datetime import datetime
from ..paths import PARQUET_ROOT
import pandas as pd


def fetch_shibor_overnight() -> pd.DataFrame:
    """Fetch Shibor overnight rates as annualized decimal values."""
    import akshare as ak
    import warnings

    warnings.filterwarnings("ignore")
    frame = ak.rate_interbank(
        market="上海银行同业拆借市场",
        symbol="Shibor人民币",
        indicator="隔夜",
    )
    frame = frame.rename(columns={"报告日": "date", "利率": "rate_pct"})
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame["rate"] = pd.to_numeric(frame["rate_pct"], errors="coerce") / 100.0
    return (
        frame[["date", "rate"]]
        .dropna(subset=["date"])
        .sort_values("date")
        .reset_index(drop=True)
    )


def refresh_cash_yield(target=None) -> dict:
    """Fetch and atomically replace the cash-yield reference asset."""
    from ..storage import atomic_parquet

    frame = fetch_shibor_overnight()
    if target is not None:
        import pandas as pd

        frame = frame.loc[frame["date"] <= pd.Timestamp(target)]
    if frame.empty or frame["rate"].isna().any():
        raise RuntimeError("Shibor 返回空数据或缺失利率，保留已有文件")
    output = PARQUET_ROOT / "reference" / "cash_yield" / "gc001.parquet"
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_parquet(frame, output)
    return {
        "asset": "cash_yield_daily",
        "rows": len(frame),
        "source_asof": str(frame["date"].max().date()) if not frame.empty else None,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
