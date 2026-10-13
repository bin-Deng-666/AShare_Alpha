#!/usr/bin/env python3
"""Ingest LHB (龙虎榜) data via akshare stock_lhb_detail_em.

Writes to:
  parquet/reference/lhb_detail/lhb_detail.parquet
  DuckDB catalog table: lhb_detail

PIT alignment: 上榜日 (date) is the availability date — LHB is published
  after market close on the same day, so it's available for T+1 trading.
  Forward return columns (上榜后N日) are EXCLUDED to prevent PIT leakage.
"""
from __future__ import annotations

from datetime import datetime
import pandas as pd

from data.ashare_data.paths import DATA_ROOT as ROOT
PARQUET = ROOT / "parquet" / "reference" / "lhb_detail"

def fetch_month(year, month, target):
    import akshare as ak
    import warnings; warnings.filterwarnings("ignore")
    from calendar import monthrange
    sd = f"{year}{month:02d}01"
    ed = min(datetime(year, month, monthrange(year, month)[1]), pd.Timestamp(target)).strftime('%Y%m%d')
    try:
        df = ak.stock_lhb_detail_em(start_date=sd, end_date=ed)
        if len(df) == 0:
            return pd.DataFrame()
        df = df.rename(columns={
            "代码": "code", "名称": "name", "上榜日": "date",
            "收盘价": "close", "涨跌幅": "pct_change",
            "龙虎榜净买额": "net_buy", "龙虎榜买入额": "buy_amount",
            "龙虎榜卖出额": "sell_amount", "龙虎榜成交额": "lhb_amount",
            "市场总成交额": "market_amount", "净买额占总成交比": "net_buy_pct",
            "成交额占总成交比": "amount_pct", "换手率": "turnover",
            "流通市值": "float_mcap", "上榜原因": "reason",
        })
        df["date"] = pd.to_datetime(df["date"])
        df["code"] = df["code"].astype(str).str.zfill(6)
        # Exclude forward-looking columns (PIT discipline)
        keep = ["date", "code", "name", "close", "pct_change",
                "net_buy", "buy_amount", "sell_amount", "lhb_amount",
                "market_amount", "net_buy_pct", "amount_pct", "turnover",
                "float_mcap", "reason"]
        df = df[keep]
        df["source"] = "eastmoney"
        df["ingest_ts"] = datetime.now().isoformat()
        return df
    except Exception as e:
        raise RuntimeError(f"LHB {year}-{month:02d} failed") from e





def refresh(config, *, workers=4):
    from .monthly_disclosures import refresh_months
    return refresh_months(PARQUET / 'lhb_detail.parquet', fetch_month,
                          ['date', 'code', 'reason'], config, workers=workers)
