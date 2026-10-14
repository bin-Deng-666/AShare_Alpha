"""sources — equity structure: data acquisition and normalization."""

from __future__ import annotations
import datetime as dt
from typing import Dict, Optional, Sequence
import pandas as pd
import requests
from ashare_alpha.data.paths import PARQUET_ROOT as PARQUET, STATE_ROOT as STATE
from ashare_alpha.data.runtime import UA

from ashare_alpha.data.runtime import Throttle
from ashare_alpha.data.storage import atomic_json
from ashare_alpha.data.runtime import retry


def fetch_equity_structure(
    session: requests.Session,
    code: str | None,
    throttle: Throttle,
    data_cutoff=None,
    refresh_from=None,
    date_field=None,
) -> pd.DataFrame:
    """Fetch capital history or a bounded notice/effective-date detector."""
    output_columns = [
        "code",
        "name",
        "end_date",
        "notice_date",
        "change_reason",
        "total_shares",
        "float_shares",
        "unlimited_shares",
        "limited_shares",
        "listed_a_shares",
        "free_shares",
        "source",
    ]
    rows = []
    page_number = 1
    expected_count = None
    filters = [f'(SECURITY_CODE="{code}")'] if code is not None else []
    if refresh_from is not None:
        source_field = {"notice_date": "NOTICE_DATE", "end_date": "END_DATE"}.get(
            date_field
        )
        if source_field is None or data_cutoff is None:
            raise ValueError(
                "Equity detector requires a bounded notice_date or end_date"
            )
        lower = pd.Timestamp(refresh_from).normalize()
        cutoff = pd.Timestamp(data_cutoff).normalize()
        filters.extend(
            [
                f"({source_field}>='{lower.date()}')",
                f"({source_field}<='{cutoff.date()}')",
            ]
        )
    while True:
        throttle.wait()
        response = session.get(
            "https://datacenter-web.eastmoney.com/api/data/v1/get",
            params={
                "reportName": "RPT_F10_EH_EQUITY",
                "columns": "ALL",
                "filter": "".join(filters),
                "pageNumber": str(page_number),
                "pageSize": "500",
                "sortColumns": "END_DATE",
                "sortTypes": "-1",
                "source": "WEB",
                "client": "WEB",
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("success"):
            message = payload.get("message") or ""
            if message == "返回数据为空" and page_number == 1:
                return pd.DataFrame(columns=output_columns)
            raise RuntimeError(message or "Eastmoney returned failure")
        result = payload.get("result") or {}
        if expected_count is None:
            expected_count = result.get("count")
        items = result.get("data") or []
        rows.extend(items)
        total_pages = int(result.get("pages") or 1)
        if not items or page_number >= total_pages:
            break
        page_number += 1
    if expected_count is not None and len(rows) != int(expected_count):
        raise RuntimeError(
            f"Incomplete equity pagination: {len(rows)}/{expected_count}"
        )
    if not rows:
        return pd.DataFrame(columns=output_columns)

    frame = pd.DataFrame(rows)
    rename = {
        "SECURITY_CODE": "code",
        "SECURITY_NAME_ABBR": "name",
        "END_DATE": "end_date",
        "NOTICE_DATE": "notice_date",
        "CHANGE_REASON": "change_reason",
        "TOTAL_SHARES": "total_shares",
        "UNLIMITED_SHARES": "unlimited_shares",
        "LIMITED_SHARES": "limited_shares",
        "LISTED_A_SHARES": "listed_a_shares",
        "FREE_SHARES": "free_shares",
    }
    frame = frame.rename(columns=rename)
    for column in (
        "code",
        "name",
        "end_date",
        "notice_date",
        "change_reason",
        "total_shares",
        "unlimited_shares",
        "limited_shares",
        "listed_a_shares",
        "free_shares",
    ):
        if column not in frame:
            frame[column] = None
    frame["end_date"] = pd.to_datetime(frame["end_date"], errors="coerce")
    frame["notice_date"] = pd.to_datetime(frame["notice_date"], errors="coerce")
    for column in (
        "total_shares",
        "unlimited_shares",
        "limited_shares",
        "listed_a_shares",
        "free_shares",
    ):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    # Prefer 流通A股; fall back to unlimited shares. Avoid FREE_SHARES alone —
    # for some names it equals total capital rather than tradable A float.
    frame["float_shares"] = frame["listed_a_shares"].where(
        frame["listed_a_shares"].notna() & (frame["listed_a_shares"] > 0),
        frame["unlimited_shares"],
    )
    frame["float_shares"] = frame["float_shares"].where(
        frame["float_shares"].notna() & (frame["float_shares"] > 0),
        pd.NA,
    )
    frame["source"] = "eastmoney_rpt_f10_eh_equity"
    if refresh_from is not None:
        observed = frame[date_field].dropna()
        if observed.lt(lower).any() or observed.gt(cutoff).any():
            raise RuntimeError("Equity response exceeded detection window")
    return (
        frame[output_columns]
        .dropna(subset=["end_date"])
        .sort_values("end_date")
        .drop_duplicates(["code", "end_date"], keep="last")
        .reset_index(drop=True)
    )


def refresh_equity_structure(config, codes=None, *, force=False, workers=4):
    from .market_events import refresh_market_events

    return refresh_market_events(
        "equity_structure",
        fetch_equity_structure,
        config,
        codes=codes,
        force=force,
        workers=workers,
    )


def build_equity_structure(
    config: Dict,
    limit: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
    force: bool = False,
    on_code_done=None,
) -> None:
    """Download historical capital-structure events (total + float shares)."""
    universe = pd.read_parquet(PARQUET / "security_master" / "security_master.parquet")
    if codes:
        universe = universe[universe["code"].isin(set(codes))]
    if limit:
        universe = universe.head(limit)
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": UA,
            "Referer": "https://data.eastmoney.com/",
        }
    )
    from ..assets.publish import publish_by_security

    selected = universe["code"].astype(str).tolist()
    output_dir = PARQUET / "equity_structure"
    if not force:
        selected = [c for c in selected if not (output_dir / f"{c}.parquet").exists()]
    if not selected:
        return
    throttle = Throttle(config["eastmoney_min_interval_seconds"])
    with session:
        frame = retry(
            lambda: fetch_equity_structure(session, None, throttle),
            "market equity structure",
            attempts=config["retries"],
            base_sleep=config["retry_base_seconds"],
        )
    result = publish_by_security(frame, selected, output_dir, on_code_done)
    atomic_json(
        {**result, "failures": [], "updated_at": dt.datetime.now()},
        STATE / "equity_structure_coverage.json",
    )
