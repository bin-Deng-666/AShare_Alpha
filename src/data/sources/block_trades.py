#!/usr/bin/env python3
"""Ingest block trade (大宗交易) data via akshare stock_dzjy_mrtj.

stock_dzjy_mrtj = 大宗交易每日统计 (aggregated by stock per day).
NOTE: stock_dzjy_mrmx (每日明细) returns only ETF/fund codes, not stocks.

Writes to:
  parquet/reference/block_trade/block_trade.parquet
  DuckDB catalog table: block_trade

Columns: date, code, name, close, trade_price, premium_rate, trade_count,
         volume, amount, amount_mcap_ratio, source, ingest_ts
"""

from __future__ import annotations
import time
from datetime import datetime
import pandas as pd

from ashare_alpha.data.paths import DATA_ROOT as ROOT

PARQUET = ROOT / "parquet" / "reference" / "block_trade"


def fetch_month(year, month, target):
    import akshare as ak
    import warnings

    warnings.filterwarnings("ignore")
    from calendar import monthrange

    sd = f"{year}{month:02d}01"
    ed = min(
        datetime(year, month, monthrange(year, month)[1]), pd.Timestamp(target)
    ).strftime("%Y%m%d")
    try:
        time.sleep(0.3)
        df = ak.stock_dzjy_mrtj(start_date=sd, end_date=ed)
        if df is None or len(df) == 0:
            return pd.DataFrame()
        df = df.rename(
            columns={
                "交易日期": "date",
                "证券代码": "code",
                "证券简称": "name",
                "收盘价": "close",
                "成交价": "trade_price",
                "折溢率": "premium_rate",
                "成交笔数": "trade_count",
                "成交总量": "volume",
                "成交总额": "amount",
                "成交总额/流通市值": "amount_mcap_ratio",
            }
        )
        df["date"] = pd.to_datetime(df["date"])
        df["code"] = df["code"].astype(str).str.zfill(6)
        keep = [
            "date",
            "code",
            "name",
            "close",
            "trade_price",
            "premium_rate",
            "trade_count",
            "volume",
            "amount",
            "amount_mcap_ratio",
        ]
        keep = [c for c in keep if c in df.columns]
        df = df[keep]
        df["source"] = "eastmoney_dzjy_mrtj"
        df["ingest_ts"] = datetime.now().isoformat()
        return df
    except Exception as e:
        raise RuntimeError(f"Block trade {year}-{month:02d} failed") from e


def refresh(config, *, workers=4):
    from .monthly_disclosures import refresh_months

    return refresh_months(
        PARQUET / "block_trade.parquet",
        fetch_month,
        ["date", "code"],
        config,
        workers=workers,
    )
