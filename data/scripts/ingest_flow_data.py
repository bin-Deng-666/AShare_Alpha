#!/usr/bin/env python3
"""Ingest market-level northbound (沪深股通) + SSE margin aggregates.

Sources:
  - Eastmoney RPT_MUTUAL_DEAL_HISTORY
      001=沪股通 003=深股通 005=北向合计
      002/004/006=南向（港股通）；NET 单位：百万元
    Note: 北向 NET/BUY/SELL 自 2024-08-19 起上游断供为 NaN（南向仍可用）。
  - akshare stock_margin_sse — 上交所两融市场汇总（元）

Writes:
  parquet/reference/northbound_market_daily.parquet
  parquet/reference/margin_market_daily.parquet

Optionally refreshes DuckDB catalog views when --catalog is set.
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "parquet"
REF = PARQUET / "reference"
STATE = ROOT / "state"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
DATACENTER = "https://datacenter-web.eastmoney.com/api/data/v1/get"

MUTUAL_LABELS = {
    "001": "hgt",  # 沪股通
    "002": "hk_sh",  # 港股通(沪)
    "003": "sgt",  # 深股通
    "004": "hk_sz",  # 港股通(深)
    "005": "north",  # 北向合计
    "006": "south",  # 南向合计
}


def atomic_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    df.to_parquet(temp, index=False)
    temp.replace(path)


def em_get(session: requests.Session, params: Dict, throttle: float = 0.35) -> dict:
    time.sleep(throttle)
    r = session.get(DATACENTER, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_mutual_type(session: requests.Session, mutual_type: str) -> pd.DataFrame:
    rows: List[dict] = []
    page = 1
    pages = 1
    while page <= pages:
        payload = em_get(
            session,
            {
                "reportName": "RPT_MUTUAL_DEAL_HISTORY",
                "columns": "ALL",
                "filter": f'(MUTUAL_TYPE="{mutual_type}")',
                "pageNumber": str(page),
                "pageSize": "500",
                "sortColumns": "TRADE_DATE",
                "sortTypes": "-1",
                "source": "WEB",
                "client": "WEB",
            },
        )
        result = payload.get("result") or {}
        chunk = result.get("data") or []
        pages = int(result.get("pages") or 1)
        rows.extend(chunk)
        logging.info(
            "mutual %s page %s/%s got %s (total %s)",
            mutual_type,
            page,
            pages,
            len(chunk),
            len(rows),
        )
        if not chunk:
            break
        page += 1
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["TRADE_DATE"]).dt.normalize()
    out = pd.DataFrame(
        {
            "date": df["date"],
            "mutual_type": mutual_type,
            "channel": MUTUAL_LABELS.get(mutual_type, mutual_type),
            "net_deal_amt_m": pd.to_numeric(df["NET_DEAL_AMT"], errors="coerce"),
            "buy_amt_m": pd.to_numeric(df["BUY_AMT"], errors="coerce"),
            "sell_amt_m": pd.to_numeric(df["SELL_AMT"], errors="coerce"),
            "fund_inflow_m": pd.to_numeric(df.get("FUND_INFLOW"), errors="coerce"),
            "deal_amt_m": pd.to_numeric(df.get("DEAL_AMT"), errors="coerce"),
            "accum_deal_amt_m": pd.to_numeric(df.get("ACCUM_DEAL_AMT"), errors="coerce"),
            "hold_market_cap": pd.to_numeric(df.get("HOLD_MARKET_CAP"), errors="coerce"),
            "index_close": pd.to_numeric(df.get("INDEX_CLOSE_PRICE"), errors="coerce"),
            "index_chg_pct": pd.to_numeric(df.get("INDEX_CHANGE_RATE"), errors="coerce"),
            "source": "eastmoney_RPT_MUTUAL_DEAL_HISTORY",
            "ingested_at": pd.Timestamp(dt.datetime.now()),
        }
    )
    # 百万元 → 亿元
    for col in ("net_deal_amt_m", "buy_amt_m", "sell_amt_m", "fund_inflow_m", "deal_amt_m"):
        yi = col.replace("_m", "_yi")
        out[yi] = out[col] / 100.0
    return out.sort_values("date").drop_duplicates(["date", "mutual_type"], keep="last")


def ingest_northbound(session: requests.Session) -> Path:
    frames = []
    for mt in ("001", "003", "005", "002", "004", "006"):
        frames.append(fetch_mutual_type(session, mt))
    long_df = pd.concat([f for f in frames if not f.empty], ignore_index=True)
    path_long = REF / "hsgt_mutual_deal_history.parquet"
    atomic_parquet(long_df, path_long)

    # Wide market panel focused on northbound (+ south for completeness)
    north = long_df[long_df["mutual_type"] == "005"][
        ["date", "net_deal_amt_yi", "buy_amt_yi", "sell_amt_yi", "hold_market_cap"]
    ].rename(
        columns={
            "net_deal_amt_yi": "north_net_yi",
            "buy_amt_yi": "north_buy_yi",
            "sell_amt_yi": "north_sell_yi",
            "hold_market_cap": "north_hold_mcap",
        }
    )
    hgt = long_df[long_df["mutual_type"] == "001"][["date", "net_deal_amt_yi"]].rename(
        columns={"net_deal_amt_yi": "hgt_net_yi"}
    )
    sgt = long_df[long_df["mutual_type"] == "003"][["date", "net_deal_amt_yi"]].rename(
        columns={"net_deal_amt_yi": "sgt_net_yi"}
    )
    south = long_df[long_df["mutual_type"] == "006"][["date", "net_deal_amt_yi"]].rename(
        columns={"net_deal_amt_yi": "south_net_yi"}
    )
    wide = (
        north.merge(hgt, on="date", how="outer")
        .merge(sgt, on="date", how="outer")
        .merge(south, on="date", how="outer")
        .sort_values("date")
        .reset_index(drop=True)
    )
    # Reconstruct north from hgt+sgt when north is null but components exist
    recon = wide["hgt_net_yi"].fillna(0.0) + wide["sgt_net_yi"].fillna(0.0)
    both = wide["hgt_net_yi"].notna() | wide["sgt_net_yi"].notna()
    wide["north_net_yi"] = wide["north_net_yi"].where(wide["north_net_yi"].notna(), recon.where(both))
    wide["source"] = "eastmoney_mutual_005"
    wide["ingested_at"] = pd.Timestamp(dt.datetime.now())
    path = REF / "northbound_market_daily.parquet"
    atomic_parquet(wide, path)
    valid = int(wide["north_net_yi"].notna().sum())
    logging.info(
        "northbound wide %s rows valid_net=%s range %s→%s -> %s",
        len(wide),
        valid,
        wide["date"].min(),
        wide["date"].max(),
        path,
    )
    return path


def ingest_margin_sse(start: str = "20190101", end: Optional[str] = None) -> Path:
    import akshare as ak

    end = end or dt.date.today().strftime("%Y%m%d")
    raw = ak.stock_margin_sse(start_date=start, end_date=end)
    if raw is None or raw.empty:
        raise RuntimeError("stock_margin_sse returned empty")
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(raw["信用交易日期"]).dt.normalize(),
            "rzye": pd.to_numeric(raw["融资余额"], errors="coerce"),
            "rzmre": pd.to_numeric(raw["融资买入额"], errors="coerce"),
            "rqyl": pd.to_numeric(raw["融券余量"], errors="coerce"),
            "rqye": pd.to_numeric(raw["融券余量金额"], errors="coerce"),
            "rqmcl": pd.to_numeric(raw["融券卖出量"], errors="coerce"),
            "rzrqye": pd.to_numeric(raw["融资融券余额"], errors="coerce"),
            "market": "SSE",
            "source": "akshare_stock_margin_sse",
            "ingested_at": pd.Timestamp(dt.datetime.now()),
        }
    ).sort_values("date").drop_duplicates("date", keep="last")
    path = REF / "margin_market_daily.parquet"
    atomic_parquet(df, path)
    logging.info(
        "margin SSE %s rows range %s→%s -> %s",
        len(df),
        df["date"].min(),
        df["date"].max(),
        path,
    )
    return path


def refresh_catalog_views() -> None:
    """Attach flow tables into existing DuckDB catalog."""
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("duckdb required for --catalog") from exc

    catalog = ROOT / "catalog" / "quant.duckdb"
    if not catalog.exists():
        raise SystemExit(f"missing catalog {catalog}")
    nb = (REF / "northbound_market_daily.parquet").as_posix()
    mg = (REF / "margin_market_daily.parquet").as_posix()
    con = duckdb.connect(str(catalog))
    try:
        if Path(nb).exists():
            con.execute(
                f"""
                CREATE OR REPLACE VIEW northbound_market_daily AS
                SELECT
                  CAST(date AS DATE) AS date,
                  CAST(north_net_yi AS DOUBLE) AS north_net_yi,
                  CAST(north_buy_yi AS DOUBLE) AS north_buy_yi,
                  CAST(north_sell_yi AS DOUBLE) AS north_sell_yi,
                  CAST(hgt_net_yi AS DOUBLE) AS hgt_net_yi,
                  CAST(sgt_net_yi AS DOUBLE) AS sgt_net_yi,
                  CAST(south_net_yi AS DOUBLE) AS south_net_yi,
                  CAST(north_hold_mcap AS DOUBLE) AS north_hold_mcap,
                  CAST(source AS VARCHAR) AS source
                FROM read_parquet('{nb}')
                """
            )
        if Path(mg).exists():
            con.execute(
                f"""
                CREATE OR REPLACE VIEW margin_market_daily AS
                SELECT
                  CAST(date AS DATE) AS date,
                  CAST(rzye AS DOUBLE) AS rzye,
                  CAST(rzmre AS DOUBLE) AS rzmre,
                  CAST(rqye AS DOUBLE) AS rqye,
                  CAST(rqyl AS DOUBLE) AS rqyl,
                  CAST(rqmcl AS DOUBLE) AS rqmcl,
                  CAST(rzrqye AS DOUBLE) AS rzrqye,
                  CAST(market AS VARCHAR) AS market,
                  CAST(source AS VARCHAR) AS source
                FROM read_parquet('{mg}')
                """
            )
        logging.info("catalog views refreshed in %s", catalog)
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-northbound", action="store_true")
    parser.add_argument("--skip-margin", action="store_true")
    parser.add_argument("--catalog", action="store_true", help="refresh DuckDB views")
    parser.add_argument("--margin-start", default="20190101")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Referer": "https://data.eastmoney.com/"})

    if not args.skip_northbound:
        ingest_northbound(session)
    if not args.skip_margin:
        ingest_margin_sse(start=args.margin_start)
    if args.catalog:
        refresh_catalog_views()
    logging.info("done")


if __name__ == "__main__":
    main()
