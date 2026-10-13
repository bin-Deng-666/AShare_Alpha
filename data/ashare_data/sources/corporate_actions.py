"""sources — corporate actions: data acquisition and normalization."""
from __future__ import annotations
import datetime as dt
import json
import logging
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional, Sequence
import numpy as np
import pandas as pd
import requests
from data.ashare_data.paths import PARQUET_ROOT as PARQUET, STATE_ROOT as STATE
from data.ashare_data.rules.corporate_actions import CORPORATE_ACTION_ASOF_SOURCE, CORPORATE_ACTION_ASOF_UNIVERSE_RULE, CorporateActionFormulaStatus, has_supported_price_formula, requires_conservative_invalidation
from data.ashare_data.rules.prices import cny_cent_key
from data.ashare_data.runtime import UA

try:
    import akshare as ak
except ImportError:
    ak = None
try:
    import baostock as bs
except ImportError:
    bs = None

from data.ashare_data.runtime import Throttle
from data.ashare_data.parquet_metadata import _read_parquet_through_date
from data.ashare_data.storage import atomic_json
from data.ashare_data.storage import atomic_parquet
from data.ashare_data.sources.securities import exchange_for
from data.ashare_data.sources.securities import is_a_share
from data.ashare_data.runtime import retry
CNINFO_ALLOTMENT_FIELD_INDEX = {
    "rights_issue_price": 8,
    "rights_issue_per_10": 9,
    "shares_before_10k": 10,
    "ex_date": 18,
    "code": 21,
    "shares_after_10k": 50,
}


CORPORATE_ACTION_EVENT_COLUMNS = [
    "code",
    "ex_date",
    "cash_dividend_per_10",
    "bonus_transfer_per_10",
    "rights_issue_per_10",
    "rights_issue_price",
    "share_consolidation_ratio",
    "shares_before",
    "shares_after",
    "observed_reference_price",
    "formula_status",
    "source",
]



def fetch_corporate_actions(
    session: requests.Session,
    code: str | None,
    throttle: Throttle,
    data_cutoff: str | pd.Timestamp | None = None,
    refresh_from: str | pd.Timestamp | None = None,
    date_field: str | None = None,
) -> pd.DataFrame:
    """Fetch distribution facts or a bounded change-detection window."""
    output_columns = [
        "code", "name", "report_date", "plan_notice_date", "publish_date",
        "notice_date", "record_date", "ex_date", "cash_dividend_per_10",
        "bonus_share_per_10", "transfer_share_per_10",
        "bonus_transfer_per_10", "total_shares", "progress",
        "plan_description", "source",
    ]
    rows = []
    page_number = 1
    expected_count = None
    filter_parts = []
    if code is not None:
        filter_parts.append(f'(SECURITY_CODE="{str(code).zfill(6)}")')
    if data_cutoff is not None:
        cutoff = pd.Timestamp(data_cutoff).normalize()
        if date_field != 'notice_date':
            filter_parts.append(
                f"(EX_DIVIDEND_DATE<='{cutoff.date().isoformat()}')"
            )
    if refresh_from is not None:
        source_field={'notice_date':'NOTICE_DATE','ex_date':'EX_DIVIDEND_DATE'}.get(date_field)
        if source_field is None:
            raise ValueError('Distribution detection requires notice_date or ex_date')
        lower=pd.Timestamp(refresh_from).normalize()
        filter_parts.append(f"({source_field}>='{lower.date()}')")
        if data_cutoff is not None:
            filter_parts.append(f"({source_field}<='{cutoff.date()}')")
    filter_expression = "".join(filter_parts)
    while True:
        throttle.wait()
        response = session.get(
            "https://datacenter-web.eastmoney.com/api/data/v1/get",
            params={
                "reportName": "RPT_SHAREBONUS_DET",
                "columns": "ALL",
                "filter": filter_expression,
                "pageNumber": str(page_number),
                "pageSize": "500",
                "sortColumns": "EX_DIVIDEND_DATE",
                "sortTypes": "-1",
                "source": "WEB",
                "client": "WEB",
            },
            timeout=20,
        )
        response.raise_for_status()
        response.encoding = "utf-8"
        payload = response.json()
        if not payload.get("success"):
            message = payload.get("message") or ""
            if message == "返回数据为空" and page_number == 1:
                return pd.DataFrame(columns=output_columns)
            raise RuntimeError(message or "Eastmoney returned failure")
        result = payload.get("result") or {}
        if expected_count is None:
            expected_count = result.get('count')
        items = result.get("data") or []
        rows.extend(items)
        total_pages = int(result.get("pages") or 1)
        if not items or page_number >= total_pages:
            break
        page_number += 1
    if expected_count is not None and len(rows) != int(expected_count):
        raise RuntimeError(f'Incomplete corporate-action pagination: {len(rows)}/{expected_count}')
    if not rows:
        return pd.DataFrame(columns=output_columns)

    frame = pd.DataFrame(rows)
    rename = {
        "SECURITY_CODE": "code",
        "SECURITY_NAME_ABBR": "name",
        "REPORT_DATE": "report_date",
        "PLAN_NOTICE_DATE": "plan_notice_date",
        "PUBLISH_DATE": "publish_date",
        "NOTICE_DATE": "notice_date",
        "EQUITY_RECORD_DATE": "record_date",
        "EX_DIVIDEND_DATE": "ex_date",
        "PRETAX_BONUS_RMB": "cash_dividend_per_10",
        "BONUS_RATIO": "bonus_share_per_10",
        "IT_RATIO": "transfer_share_per_10",
        "BONUS_IT_RATIO": "bonus_transfer_per_10",
        "TOTAL_SHARES": "total_shares",
        "ASSIGN_PROGRESS": "progress",
        "IMPL_PLAN_PROFILE": "plan_description",
    }
    frame = frame.rename(columns=rename)
    columns = [
        "code", "name", "report_date", "plan_notice_date", "publish_date",
        "notice_date", "record_date", "ex_date", "cash_dividend_per_10",
        "bonus_share_per_10", "transfer_share_per_10",
        "bonus_transfer_per_10", "total_shares", "progress",
        "plan_description",
    ]
    for column in columns:
        if column not in frame:
            frame[column] = None
    for column in (
        "report_date", "plan_notice_date", "publish_date", "notice_date",
        "record_date", "ex_date",
    ):
        frame[column] = pd.to_datetime(frame[column], errors="coerce")
    for column in (
        "cash_dividend_per_10", "bonus_share_per_10",
        "transfer_share_per_10", "bonus_transfer_per_10", "total_shares",
    ):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["source"] = "eastmoney"
    result = (
        frame[columns + ["source"]]
        .sort_values(["ex_date", "publish_date"], na_position="last")
        .drop_duplicates(
            ["code", "report_date", "ex_date", "plan_description"], keep="last"
        )
    )
    if data_cutoff is not None and date_field != 'notice_date':
        observed = result["ex_date"].dropna()
        if observed.gt(cutoff).any():
            raise RuntimeError("Eastmoney violated the server-side data cutoff")
    if refresh_from is not None:
        observed=result[date_field].dropna()
        if observed.lt(lower).any() or (data_cutoff is not None and observed.gt(cutoff).any()):
            raise RuntimeError('Distribution response exceeded detection window')
    return result


def refresh_distributions(config, codes=None, *, force=False, workers=4):
    from .market_events import refresh_market_events
    return refresh_market_events('distributions',fetch_corporate_actions,config,
                                 codes=codes,force=force,workers=workers)


def build_corporate_actions(
    config: Dict,
    limit: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
    force: bool = False,
    on_code_done=None,
) -> None:
    universe = pd.read_parquet(PARQUET / "security_master" / "security_master.parquet")
    if codes:
        universe = universe[universe["code"].isin(set(codes))]
    if limit:
        universe = universe.head(limit)
    session = requests.Session()
    session.headers.update({
        "User-Agent": UA,
        "Referer": "https://data.eastmoney.com/",
    })
    from ..assets.publish import publish_by_security

    selected = universe['code'].astype(str).tolist()
    if not force:
        selected = [c for c in selected if not (PARQUET / 'corporate_actions' / f'{c}.parquet').exists()]
    if not selected:
        return
    throttle = Throttle(config["eastmoney_min_interval_seconds"])
    cutoff = str(pd.Timestamp(config.get('end_date') or dt.date.today()).date())
    with session:
        frame = retry(
            lambda: fetch_corporate_actions(session, None, throttle, data_cutoff=cutoff),
            'market corporate actions', attempts=config['retries'],
            base_sleep=config['retry_base_seconds'],
        )
    result = publish_by_security(frame, selected, PARQUET / 'corporate_actions', on_code_done)
    from ..pipeline.receipts import fingerprint
    outputs = [PARQUET / 'corporate_actions' / f'{code}.parquet' for code in selected]
    atomic_json({**result, 'failures': [], 'updated_at': dt.datetime.now(),
                 'physical_read_cutoff': cutoff, 'completed_codes': selected,
                 'output_fingerprint': fingerprint(outputs)},
                STATE / 'corporate_action_failures.json')


def completed_distributions(codes, cutoff):
    """Reuse a successful server-bounded distribution fetch, never unverified files."""
    from ..storage import read_json
    from ..pipeline.receipts import fingerprint
    record = read_json(STATE / 'corporate_action_failures.json', {})
    completed = record.get('completed_codes', [])
    if (record.get('failures') or record.get('physical_read_cutoff') != str(pd.Timestamp(cutoff).date())
            or not set(codes).issubset(completed)):
        return None
    outputs = [PARQUET / 'corporate_actions' / f'{code}.parquet' for code in completed]
    if not outputs or not all(path.exists() for path in outputs):
        return None
    if fingerprint(outputs) != record.get('output_fingerprint'):
        return None
    frames = [pd.read_parquet(PARQUET / 'corporate_actions' / f'{code}.parquet') for code in codes]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _empty_corporate_action_events() -> pd.DataFrame:
    """Return an empty, parquet-safe schema for complete per-symbol coverage."""
    return pd.DataFrame(
        {
            "code": pd.Series(dtype="string"),
            "ex_date": pd.Series(dtype="datetime64[ns]"),
            "cash_dividend_per_10": pd.Series(dtype="float64"),
            "bonus_transfer_per_10": pd.Series(dtype="float64"),
            "rights_issue_per_10": pd.Series(dtype="float64"),
            "rights_issue_price": pd.Series(dtype="float64"),
            "share_consolidation_ratio": pd.Series(dtype="float64"),
            "shares_before": pd.Series(dtype="float64"),
            "shares_after": pd.Series(dtype="float64"),
            "observed_reference_price": pd.Series(dtype="float64"),
            "formula_status": pd.Series(dtype="string"),
            "source": pd.Series(dtype="string"),
        }
    )


def _sharebonus_events(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize server-bounded Eastmoney cash/share distributions."""
    if frame.empty:
        return _empty_corporate_action_events()
    events = frame.copy()
    events["code"] = events["code"].astype(str).str.zfill(6)
    events["ex_date"] = pd.to_datetime(
        events["ex_date"], errors="coerce"
    ).dt.normalize()
    events = events.loc[events["ex_date"].notna()].copy()
    if events.empty:
        return _empty_corporate_action_events()
    for column in (
        "cash_dividend_per_10",
        "bonus_share_per_10",
        "transfer_share_per_10",
        "bonus_transfer_per_10",
    ):
        if column not in events:
            events[column] = np.nan
        events[column] = pd.to_numeric(events[column], errors="coerce")
    component_total = (
        events["bonus_share_per_10"].fillna(0.0)
        + events["transfer_share_per_10"].fillna(0.0)
    )
    distribution = events["bonus_transfer_per_10"].where(
        events["bonus_transfer_per_10"].notna(), component_total
    ).fillna(0.0)
    cash = events["cash_dividend_per_10"].fillna(0.0)
    malformed = cash.lt(0.0) | distribution.lt(0.0)
    status = np.select(
        [malformed, cash.gt(0.0) | distribution.gt(0.0)],
        [
            CorporateActionFormulaStatus.UNSUPPORTED_MALFORMED_TERMS.value,
            CorporateActionFormulaStatus.SUPPORTED_STANDARD.value,
        ],
        default=CorporateActionFormulaStatus.NO_PRICE_ADJUSTMENT.value,
    )
    result = pd.DataFrame(
        {
            "code": events["code"],
            "ex_date": events["ex_date"],
            "cash_dividend_per_10": cash,
            "bonus_transfer_per_10": distribution,
            "rights_issue_per_10": 0.0,
            "rights_issue_price": 0.0,
            "share_consolidation_ratio": 0.0,
            "shares_before": np.nan,
            "shares_after": np.nan,
            "observed_reference_price": np.nan,
            "formula_status": status,
            "source": "eastmoney_sharebonus_server_asof",
        }
    )
    return (
        result[CORPORATE_ACTION_EVENT_COLUMNS]
        .sort_values(["code", "ex_date"])
        .drop_duplicates(["code", "ex_date"], keep="last")
        .reset_index(drop=True)
    )


def fetch_cninfo_allotment_events(
    code: str,
    data_cutoff: str | pd.Timestamp,
) -> pd.DataFrame:
    """Fetch rights issues from CNInfo with server-side start/end dates."""
    if ak is None:
        raise RuntimeError("AkShare is required for CNInfo allotment events")
    if not _check_mini_racer():
        logging.warning(
            "Skipping CNInfo allotment events for %s — MiniRacer V8 engine "
            "is unavailable (run pip install --force-reinstall mini-racer)",
            code,
        )
        return _empty_corporate_action_events()
    cutoff = pd.Timestamp(data_cutoff).normalize()
    raw = ak.stock_allotment_cninfo(
        symbol=str(code).zfill(6),
        start_date="19700101",
        end_date=cutoff.strftime("%Y%m%d"),
    )
    if raw is None or raw.empty:
        return _empty_corporate_action_events()
    if raw.shape[1] <= max(CNINFO_ALLOTMENT_FIELD_INDEX.values()):
        raise RuntimeError("CNInfo allotment response schema is incomplete")
    response_codes = (
        raw.iloc[:, CNINFO_ALLOTMENT_FIELD_INDEX["code"]]
        .dropna()
        .astype(str)
        .str.zfill(6)
    )
    if not response_codes.empty and not response_codes.eq(
        str(code).zfill(6)
    ).all():
        raise RuntimeError("CNInfo allotment response code does not match request")
    ex_date = pd.to_datetime(
        raw.iloc[:, CNINFO_ALLOTMENT_FIELD_INDEX["ex_date"]],
        errors="coerce",
    ).dt.normalize()
    through_cutoff = ex_date.notna() & ex_date.le(cutoff)
    frame = raw.loc[through_cutoff].copy()
    ex_date = ex_date.loc[through_cutoff]
    if frame.empty:
        return _empty_corporate_action_events()
    rights_price = pd.to_numeric(
        frame.iloc[:, CNINFO_ALLOTMENT_FIELD_INDEX["rights_issue_price"]],
        errors="coerce",
    )
    rights_ratio = pd.to_numeric(
        frame.iloc[:, CNINFO_ALLOTMENT_FIELD_INDEX["rights_issue_per_10"]],
        errors="coerce",
    )
    shares_before = pd.to_numeric(
        frame.iloc[:, CNINFO_ALLOTMENT_FIELD_INDEX["shares_before_10k"]],
        errors="coerce",
    ) * 10_000.0
    shares_after = pd.to_numeric(
        frame.iloc[:, CNINFO_ALLOTMENT_FIELD_INDEX["shares_after_10k"]],
        errors="coerce",
    ) * 10_000.0
    malformed = (
        rights_ratio.isna()
        | rights_ratio.le(0.0)
        | rights_price.isna()
        | rights_price.lt(0.0)
    )
    result = pd.DataFrame(
        {
            "code": str(code).zfill(6),
            "ex_date": ex_date.to_numpy(),
            "cash_dividend_per_10": 0.0,
            "bonus_transfer_per_10": 0.0,
            "rights_issue_per_10": rights_ratio.to_numpy(),
            "rights_issue_price": rights_price.to_numpy(),
            "share_consolidation_ratio": 0.0,
            "shares_before": shares_before.to_numpy(),
            "shares_after": shares_after.to_numpy(),
            "observed_reference_price": np.nan,
            "formula_status": np.where(
                malformed.to_numpy(),
                CorporateActionFormulaStatus.UNSUPPORTED_MALFORMED_TERMS.value,
                CorporateActionFormulaStatus.RIGHTS_ISSUE_PRICE_ONLY.value,
            ),
            "source": "cninfo_allotment_server_asof",
        }
    )
    if pd.to_datetime(result["ex_date"]).gt(cutoff).any():
        raise RuntimeError("CNInfo violated the server-side data cutoff")
    return (
        result[CORPORATE_ACTION_EVENT_COLUMNS]
        .sort_values("ex_date")
        .drop_duplicates("ex_date", keep="last")
        .reset_index(drop=True)
    )


def _combine_corporate_action_events(*frames: pd.DataFrame) -> pd.DataFrame:
    """Combine same-day public-source terms without dropping unsupported rows."""
    available = [frame for frame in frames if not frame.empty]
    if not available:
        return _empty_corporate_action_events()
    combined = pd.concat(available, ignore_index=True)
    records: list[dict[str, object]] = []
    for (code, ex_date), group in combined.groupby(
        ["code", "ex_date"], sort=True, dropna=False
    ):
        numeric = {}
        for column in (
            "cash_dividend_per_10",
            "bonus_transfer_per_10",
            "rights_issue_per_10",
            "share_consolidation_ratio",
        ):
            numeric[column] = float(
                pd.to_numeric(group[column], errors="coerce").fillna(0.0).sum()
            )
        rights_prices = pd.to_numeric(
            group["rights_issue_price"], errors="coerce"
        ).dropna()
        before = pd.to_numeric(group["shares_before"], errors="coerce").dropna()
        after = pd.to_numeric(group["shares_after"], errors="coerce").dropna()
        observed = pd.to_numeric(
            group["observed_reference_price"], errors="coerce"
        ).dropna()
        statuses = group["formula_status"].map(str)
        if statuses.eq(
            CorporateActionFormulaStatus.UNSUPPORTED_MALFORMED_TERMS.value
        ).any():
            formula_status = (
                CorporateActionFormulaStatus.UNSUPPORTED_MALFORMED_TERMS.value
            )
        elif numeric["rights_issue_per_10"] > 0.0:
            formula_status = CorporateActionFormulaStatus.RIGHTS_ISSUE_PRICE_ONLY.value
        elif (
            numeric["cash_dividend_per_10"] > 0.0
            or numeric["bonus_transfer_per_10"] > 0.0
        ):
            formula_status = CorporateActionFormulaStatus.SUPPORTED_STANDARD.value
        elif statuses.map(requires_conservative_invalidation).any():
            formula_status = (
                CorporateActionFormulaStatus.UNSUPPORTED_MISSING_FORMULA_STATUS.value
            )
        else:
            formula_status = CorporateActionFormulaStatus.NO_PRICE_ADJUSTMENT.value
        records.append(
            {
                "code": str(code).zfill(6),
                "ex_date": pd.Timestamp(ex_date).normalize(),
                **numeric,
                "rights_issue_price": (
                    float(rights_prices.iloc[-1]) if not rights_prices.empty else 0.0
                ),
                "shares_before": float(before.iloc[-1]) if not before.empty else np.nan,
                "shares_after": float(after.iloc[-1]) if not after.empty else np.nan,
                "observed_reference_price": (
                    float(observed.iloc[-1]) if not observed.empty else np.nan
                ),
                "formula_status": formula_status,
                "source": "+".join(sorted(set(group["source"].map(str)))),
            }
        )
    return pd.DataFrame.from_records(
        records, columns=CORPORATE_ACTION_EVENT_COLUMNS
    ).sort_values("ex_date").reset_index(drop=True)


def reference_prices_from_local(code, start_date, cutoff):
    """Reuse raw-reference observations already fetched with daily indicators.

    Return None unless every stored raw trading date in the requested range has
    a positive observation. Missing history falls back to the source query.
    """
    raw_path = PARQUET / 'daily_bars' / 'adjustment=raw' / f'{code}.parquet'
    indicator_path = PARQUET / 'daily_indicators' / f'{code}.parquet'
    cache_path = PARQUET / 'reference' / 'exchange_reference_prices' / f'{code}.parquet'
    if not raw_path.exists():
        return None
    from ..parquet_metadata import _read_parquet_through_date
    expected = _read_parquet_through_date(raw_path, date_column='date', data_cutoff=cutoff, columns=['date'])
    expected['date'] = pd.to_datetime(expected['date']).dt.normalize()
    expected = expected[expected['date'] >= pd.Timestamp(start_date)].drop_duplicates('date')
    if expected.empty:
        return None
    frames = []
    if cache_path.exists():
        frames.append(_read_parquet_through_date(cache_path, date_column='date', data_cutoff=cutoff))
    if indicator_path.exists():
        import pyarrow.parquet as pq
        if 'preclose' in pq.read_schema(indicator_path).names:
            indicators = _read_parquet_through_date(indicator_path, date_column='date', data_cutoff=cutoff, columns=['date','preclose'])
            indicators = indicators.rename(columns={'preclose':'observed_reference_price'})
            # Older rows lack preclose during the schema migration; don't replace
            # valid cached observations with those null values.
            indicators = indicators[pd.to_numeric(indicators['observed_reference_price'], errors='coerce').gt(0)]
            frames.append(indicators)
    if not frames:
        return None
    frame = pd.concat(frames, ignore_index=True)
    frame['date'] = pd.to_datetime(frame['date']).dt.normalize()
    frame = frame.drop_duplicates('date', keep='last')
    aligned = expected.merge(frame, on='date', how='left', validate='one_to_one')
    if not pd.to_numeric(aligned['observed_reference_price'].iloc[1:], errors='coerce').gt(0).all():
        return None
    return aligned.sort_values('date').reset_index(drop=True)


def fetch_baostock_preclose(
    code: str,
    start_date: str | pd.Timestamp,
    data_cutoff: str | pd.Timestamp,
    *,
    use_local: bool = True,
) -> pd.DataFrame:
    """Reuse complete local observations or fetch missing raw reference history."""
    local = reference_prices_from_local(code, start_date, data_cutoff) if use_local else None
    if local is not None:
        return local
    if bs is None:
        raise RuntimeError("BaoStock is required for reference-price validation")
    start = pd.Timestamp(start_date).normalize()
    cutoff = pd.Timestamp(data_cutoff).normalize()
    cache_path = PARQUET / 'reference' / 'exchange_reference_prices' / f'{code}.parquet'
    previous = pd.DataFrame()
    if cache_path.exists() and use_local:
        previous = pd.read_parquet(cache_path)
        previous['date'] = pd.to_datetime(previous['date'])
        if not previous.empty and previous['date'].max() <= cutoff:
            start = max(start, previous['date'].max() - pd.Timedelta(days=5))
        else:
            # A historical cutoff must not reuse a later observation snapshot.
            previous = pd.DataFrame()
    symbol = (
        f"sh.{str(code).zfill(6)}"
        if exchange_for(str(code).zfill(6)) == "SH"
        else f"sz.{str(code).zfill(6)}"
    )
    from .indicators import query_baostock_frame
    frame = query_baostock_frame(lambda: bs.query_history_k_data_plus(
        symbol,
        "date,preclose",
        start_date=str(start.date()),
        end_date=str(cutoff.date()),
        frequency="d",
        adjustflag="3",
    )).rename(columns={'preclose':'observed_reference_price'})
    if frame.empty:
        return pd.DataFrame(
            {
                "date": pd.Series(dtype="datetime64[ns]"),
                "observed_reference_price": pd.Series(dtype="float64"),
            }
        )
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame["observed_reference_price"] = pd.to_numeric(
        frame["observed_reference_price"], errors="coerce"
    )
    frame = frame.dropna(subset=["date"]).drop_duplicates("date", keep="last")
    if frame["date"].gt(cutoff).any():
        raise RuntimeError("BaoStock violated the server-side data cutoff")
    frame = frame.sort_values('date').reset_index(drop=True)
    if not previous.empty:
        frame = pd.concat([previous, frame], ignore_index=True).drop_duplicates('date', keep='last').sort_values('date').reset_index(drop=True)
    atomic_parquet(frame, cache_path)
    return frame


def _apply_reference_price_validation(
    code: str,
    raw_bars: pd.DataFrame,
    events: pd.DataFrame,
    observed_references: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Detect every unexplained exchange-reference adjustment before cutoff."""
    bars = raw_bars[["date", "close"]].copy()
    bars["date"] = pd.to_datetime(bars["date"], errors="coerce").dt.normalize()
    bars["close"] = pd.to_numeric(bars["close"], errors="coerce")
    bars = bars.dropna(subset=["date"]).sort_values("date")
    bars = bars.drop_duplicates("date", keep="last")
    bars["previous_raw_close"] = bars["close"].shift(1)
    references = observed_references.copy()
    references["date"] = pd.to_datetime(
        references["date"], errors="coerce"
    ).dt.normalize()
    references["observed_reference_price"] = pd.to_numeric(
        references["observed_reference_price"], errors="coerce"
    )
    bars = bars.merge(references, on="date", how="left", validate="one_to_one")
    eligible = bars["previous_raw_close"].gt(0.0)
    missing = eligible & ~bars["observed_reference_price"].gt(0.0)

    validated = events.copy()
    if validated.empty:
        validated = _empty_corporate_action_events()
    validated["ex_date"] = pd.to_datetime(
        validated["ex_date"], errors="coerce"
    ).dt.normalize()
    event_by_date = {
        pd.Timestamp(row.ex_date): index
        for index, row in validated.iterrows()
        if pd.notna(row.ex_date)
    }
    detected_consolidations = 0
    detected_unclassified = 0
    reference_mismatches = 0
    reference_gap_invalidations = 0

    def numeric_or_zero(value: object) -> float:
        numeric = pd.to_numeric(value, errors="coerce")
        return 0.0 if pd.isna(numeric) else float(numeric)

    def append_synthetic_reference_event(
        frame: pd.DataFrame,
        *,
        trade_date: pd.Timestamp,
        observed_reference_price: float,
        status: CorporateActionFormulaStatus,
        source: str,
    ) -> pd.DataFrame:
        new_event = pd.DataFrame.from_records(
            [
                {
                    "code": str(code).zfill(6),
                    "ex_date": trade_date,
                    "cash_dividend_per_10": 0.0,
                    "bonus_transfer_per_10": 0.0,
                    "rights_issue_per_10": 0.0,
                    "rights_issue_price": 0.0,
                    "share_consolidation_ratio": np.nan,
                    "shares_before": np.nan,
                    "shares_after": np.nan,
                    "observed_reference_price": observed_reference_price,
                    "formula_status": status.value,
                    "source": source,
                }
            ],
            columns=CORPORATE_ACTION_EVENT_COLUMNS,
        )
        if frame.empty:
            result = new_event.copy()
        else:
            result = pd.concat([frame, new_event], ignore_index=True)
        event_by_date[trade_date] = int(result.index[-1])
        return result

    for row in bars.loc[missing].itertuples(index=False):
        trade_date = pd.Timestamp(row.date)
        event_index = event_by_date.get(trade_date)
        gap_status = (
            CorporateActionFormulaStatus.UNSUPPORTED_MISSING_REFERENCE_PRICE
            if event_index is None
            else (
                CorporateActionFormulaStatus
                .UNSUPPORTED_DECLARED_ACTION_MISSING_REFERENCE_PRICE
            )
        )
        if event_index is None:
            validated = append_synthetic_reference_event(
                validated,
                trade_date=trade_date,
                observed_reference_price=np.nan,
                status=gap_status,
                source="baostock_preclose_server_asof_gap",
            )
        else:
            validated.loc[event_index, "observed_reference_price"] = np.nan
            validated.loc[event_index, "formula_status"] = gap_status.value
            current_source = str(validated.loc[event_index, "source"])
            if "baostock_preclose_server_asof_gap" not in current_source:
                validated.loc[event_index, "source"] = (
                    current_source + "+baostock_preclose_server_asof_gap"
                )
        reference_gap_invalidations += 1

    # The overwhelming majority of days have no event and unchanged reference.
    # Vectorize that decision; Python iteration is reserved for events/anomalies.
    previous_cents = np.floor(bars['previous_raw_close'] * 100.0 + 0.5 + 1e-8)
    observed_cents = np.floor(bars['observed_reference_price'] * 100.0 + 0.5 + 1e-8)
    event_day = bars['date'].isin(event_by_date)
    needs_validation = eligible & ~missing & (event_day | previous_cents.ne(observed_cents))
    for row in bars.loc[needs_validation].itertuples(index=False):
        trade_date = pd.Timestamp(row.date)
        previous_close = float(row.previous_raw_close)
        observed = float(row.observed_reference_price)
        event_index = event_by_date.get(trade_date)
        if event_index is None:
            expected = previous_close
            if cny_cent_key(expected) == cny_cent_key(observed):
                continue
            status = (
                CorporateActionFormulaStatus.UNSUPPORTED_SHARE_CONSOLIDATION
                if cny_cent_key(observed) > cny_cent_key(previous_close)
                else CorporateActionFormulaStatus.UNSUPPORTED_UNCLASSIFIED_SHARE_CHANGE
            )
            if status is CorporateActionFormulaStatus.UNSUPPORTED_SHARE_CONSOLIDATION:
                detected_consolidations += 1
            else:
                detected_unclassified += 1
            validated = append_synthetic_reference_event(
                validated,
                trade_date=trade_date,
                observed_reference_price=observed,
                status=status,
                source="baostock_preclose_server_asof",
            )
            continue

        event = validated.loc[event_index]
        cash = numeric_or_zero(event.get("cash_dividend_per_10")) / 10.0
        distribution = (
            numeric_or_zero(event.get("bonus_transfer_per_10")) / 10.0
        )
        rights = numeric_or_zero(event.get("rights_issue_per_10")) / 10.0
        rights_price = numeric_or_zero(event.get("rights_issue_price"))
        status = str(event.get("formula_status"))
        expected = previous_close
        if has_supported_price_formula(status):
            expected = (
                previous_close - cash + rights_price * rights
            ) / (1.0 + distribution + rights)
        validated.loc[event_index, "observed_reference_price"] = observed
        current_source = str(validated.loc[event_index, "source"])
        if "baostock_preclose_server_asof" not in current_source:
            validated.loc[event_index, "source"] = (
                current_source + "+baostock_preclose_server_asof"
            )
        if cny_cent_key(expected) != cny_cent_key(observed):
            validated.loc[event_index, "formula_status"] = (
                CorporateActionFormulaStatus.UNSUPPORTED_REFERENCE_MISMATCH.value
            )
            reference_mismatches += 1

    validated = (
        validated[CORPORATE_ACTION_EVENT_COLUMNS]
        .sort_values("ex_date")
        .drop_duplicates("ex_date", keep="last")
        .reset_index(drop=True)
    )
    return validated, {
        "raw_reference_rows": int(eligible.sum()),
        "checked_reference_rows": int((eligible & ~missing).sum()),
        "missing_reference_rows": int(missing.sum()),
        "reference_gap_invalidations": reference_gap_invalidations,
        "detected_share_consolidations": detected_consolidations,
        "detected_unclassified_share_changes": detected_unclassified,
        "reference_mismatches": reference_mismatches,
    }


def _frozen_sh_sz_universe(
    data_cutoff: str | pd.Timestamp,
    codes: Optional[Sequence[str]] = None,
) -> pd.DataFrame:
    """Return the current master restricted to symbols listed by the cutoff."""
    cutoff = pd.Timestamp(data_cutoff).normalize()
    universe = pd.read_parquet(
        PARQUET / "security_master" / "security_master.parquet",
        columns=["code", "exchange", "list_date"],
    )
    universe["code"] = universe["code"].astype(str).str.zfill(6)
    universe["list_date"] = pd.to_datetime(
        universe["list_date"], errors="coerce"
    ).dt.normalize()
    universe = universe.loc[
        universe["code"].map(is_a_share)
        & universe["exchange"].isin(["SH", "SZ"])
        & universe["list_date"].notna()
        & universe["list_date"].le(cutoff)
    ].drop_duplicates("code")
    if codes:
        wanted = {str(code).zfill(6) for code in codes}
        universe = universe.loc[universe["code"].isin(wanted)]
    return universe.sort_values("code").reset_index(drop=True)


_REFERENCE_SESSION_ACTIVE = False


def _close_reference_session():
    global _REFERENCE_SESSION_ACTIVE
    if _REFERENCE_SESSION_ACTIVE and bs is not None:
        bs.logout()
    _REFERENCE_SESSION_ACTIVE = False


def _initialize_reference_worker(parquet_root):
    from multiprocessing.util import Finalize
    global _REFERENCE_SESSION_ACTIVE, PARQUET
    PARQUET = Path(parquet_root)
    _REFERENCE_SESSION_ACTIVE = False
    Finalize(None, _close_reference_session, exitpriority=10)


def _validate_corporate_event(arguments):
    """Validate and atomically publish one security in its own source process."""
    global _REFERENCE_SESSION_ACTIVE
    code, events, config, cutoff, force = arguments
    source = 'local'
    try:
        allow_local = not (force or config.get('refresh_from'))
        observed = reference_prices_from_local(code, config['start_date'], cutoff) if allow_local else None
        if observed is None:
            source = 'source'
            def query():
                global _REFERENCE_SESSION_ACTIVE
                if not _REFERENCE_SESSION_ACTIVE:
                    if bs is None:
                        raise RuntimeError('BaoStock is required for missing reference-price history')
                    login = bs.login()
                    if login.error_code != '0':
                        raise RuntimeError(login.error_msg or login.error_code)
                    _REFERENCE_SESSION_ACTIVE = True
                return fetch_baostock_preclose(code, config['start_date'], cutoff, use_local=allow_local)
            observed = retry(query, f'BaoStock preclose {code}', attempts=config['retries'],
                             base_sleep=config['retry_base_seconds'])
        raw_path = PARQUET / 'daily_bars' / 'adjustment=raw' / f'{code}.parquet'
        if not raw_path.exists():
            raise FileNotFoundError(f'Raw price history missing for {code}; event validation is incomplete')
        bars = _read_parquet_through_date(raw_path, date_column='date', data_cutoff=cutoff,
                                          columns=['date','close'])
        frame, diagnostics = _apply_reference_price_validation(code, bars, events, observed)
        atomic_parquet(frame, PARQUET/'corporate_action_events'/f'{code}.parquet')
        return {'code':code, 'status':'success', 'reference_source':source,
                'empty':frame.empty, 'diagnostics':diagnostics,
                'formula_status_counts':{str(k):int(v) for k,v in frame['formula_status'].value_counts().items()}}
    except Exception as exc:
        return {'code':code, 'status':'failed', 'reference_source':source, 'error':str(exc)}


def build_corporate_action_events(
    config: Dict,
    limit: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
    force: bool = False,
    data_cutoff: str | pd.Timestamp | None = None,
    max_workers: int = 4,
) -> None:
    """Persist a server-side-as-of cash/share-distribution and rights layer."""
    cutoff = (
        pd.Timestamp(data_cutoff).normalize()
        if data_cutoff is not None
        else pd.Timestamp.today().normalize()
    )
    universe = _frozen_sh_sz_universe(cutoff, codes)
    if limit:
        universe = universe.head(limit)
    expected_codes = universe["code"].tolist()
    state_path = STATE / "corporate_action_event_coverage.json"
    previous_state: dict[str, object] = {}
    if state_path.exists() and not force:
        previous_state = json.loads(state_path.read_text(encoding="utf-8"))
    state_matches = (
        previous_state.get("source") == CORPORATE_ACTION_ASOF_SOURCE
        and previous_state.get("query_boundary") == "server_side_end_date"
        and previous_state.get("universe_rule")
        == CORPORATE_ACTION_ASOF_UNIVERSE_RULE
        and previous_state.get("physical_read_cutoff") == str(cutoff.date())
    )
    previous_completed = (
        set(previous_state.get("completed_codes", [])) if state_matches else set()
    )
    from ..pipeline.receipts import fingerprint
    from ..rules import corporate_actions as action_rules
    import hashlib
    version = hashlib.sha256(
        Path(__file__).read_bytes() + Path(action_rules.__file__).read_bytes()
        + str(config['start_date']).encode()
    ).hexdigest()
    def input_signature(code):
        return version + fingerprint([
            PARQUET / 'daily_bars' / 'adjustment=raw' / f'{code}.parquet',
            PARQUET / 'daily_indicators' / f'{code}.parquet',
            PARQUET / 'corporate_actions' / f'{code}.parquet',
            PARQUET / 'reference' / 'exchange_reference_prices' / f'{code}.parquet',
        ])
    records = previous_state.get('receipts', {}) if state_matches else {}
    output_dir = PARQUET / 'corporate_action_events'
    reusable_codes = {
        code for code in expected_codes
        if code in previous_completed and (output_dir / f'{code}.parquet').exists()
        and records.get(code, {}).get('input') == input_signature(code)
        and records.get(code, {}).get('output') == fingerprint([output_dir / f'{code}.parquet'])
    }
    codes_to_fetch = [
        code for code in expected_codes if force or code not in reusable_codes
    ]
    failures: list[dict[str, str]] = []
    work = {'distribution_downloads': 0, 'rights_securities': len(codes_to_fetch),
            'reference_local_securities': 0, 'reference_source_securities': 0,
            'reused_event_securities': len(reusable_codes)}
    reference_sources = {}
    completed_codes = set(reusable_codes)
    standard_query_rows = 0
    reference_validation: defaultdict[str, int] = defaultdict(int)
    if reusable_codes:
        standard_query_rows = int(previous_state.get("standard_query_rows", 0))
    preclose_completed_codes = set(
        previous_state.get("preclose_completed_codes", [])
    ) & reusable_codes

    def _write_state(interim: bool = False) -> None:
        """Persist coverage state; interim writes make a kill mid-loop
        resumable (same keys as the final write, details recomputed at end)."""
        if interim:
            empty_list: list[str] = []
            status_summary: dict[str, int] = {}
        else:
            empty_list = []
            status_summary = {}
            counts: defaultdict[str, int] = defaultdict(int)
            for code in sorted(completed_codes):
                record = records[code]
                if record.get('empty'):
                    empty_list.append(code)
                for status, count in record.get('formula_status_counts', {}).items():
                    counts[str(status)] += int(count)
            status_summary = dict(sorted(counts.items()))
        reference_validation.clear()
        for code in sorted(completed_codes):
            for key, value in records[code].get('diagnostics', {}).items():
                reference_validation[key] += int(value)
        atomic_json(
            {
                'work': work,
                "files_expected": int(len(expected_codes)),
                "failures": list(failures),
                "empty_codes": empty_list,
                "formula_status_counts": status_summary,
                "completed_codes": sorted(completed_codes),
                'receipts': {code: records[code] for code in sorted(completed_codes)},
                "preclose_completed_codes": sorted(preclose_completed_codes),
                "standard_query_rows": standard_query_rows,
                "reference_validation": dict(sorted(reference_validation.items())),
                "preclose_query_start": str(
                    pd.Timestamp(config["start_date"]).date()
                ),
                "source": CORPORATE_ACTION_ASOF_SOURCE,
                "query_boundary": "server_side_end_date",
                "universe_rule": CORPORATE_ACTION_ASOF_UNIVERSE_RULE,
                "physical_read_cutoff": str(cutoff.date()),
                "interim": bool(interim),
                "updated_at": dt.datetime.now(),
            },
            state_path,
        )

    if codes_to_fetch:
        standard_actions = None if force else completed_distributions(expected_codes, cutoff)
        if standard_actions is None:
            work['distribution_downloads'] = 1
            session = requests.Session()
            session.headers.update(
                {'User-Agent': UA, 'Referer': 'https://data.eastmoney.com/'}
            )
            throttle = Throttle(config['eastmoney_min_interval_seconds'])
            with session:
                standard_actions = retry(
                    lambda: fetch_corporate_actions(session, None, throttle, data_cutoff=cutoff),
                    'server-as-of corporate actions', attempts=config['retries'],
                    base_sleep=config['retry_base_seconds'],
                )
        standard_actions = standard_actions.loc[
            standard_actions["code"].astype(str).str.zfill(6).isin(
                set(expected_codes)
            )
        ].copy()
        standard_events = _sharebonus_events(standard_actions)
        standard_query_rows = int(len(standard_events))
        standard_by_code = {
            str(code): group.reset_index(drop=True)
            for code, group in standard_events.groupby("code", sort=False)
        }

        def fetch_rights(code: str) -> pd.DataFrame:
            return retry(
                lambda: fetch_cninfo_allotment_events(code, cutoff),
                f"CNInfo allotment events {code}",
                attempts=config["retries"],
                base_sleep=config["retry_base_seconds"],
            )

        combined_by_code: dict[str, pd.DataFrame] = {}
        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
            futures = {
                executor.submit(fetch_rights, code): code
                for code in codes_to_fetch
            }
            for index, future in enumerate(as_completed(futures), start=1):
                code = futures[future]
                try:
                    rights = future.result()
                    frame = _combine_corporate_action_events(
                        standard_by_code.get(
                            code, _empty_corporate_action_events()
                        ),
                        rights,
                    )
                    if not frame.empty and pd.to_datetime(frame["ex_date"]).gt(
                        cutoff
                    ).any():
                        raise RuntimeError(
                            "corporate-action event exceeded the data cutoff"
                        )
                    combined_by_code[code] = frame
                except Exception as exc:  # noqa: BLE001
                    failures.append({"code": code, "error": str(exc)})
                if index % 250 == 0:
                    logging.info(
                        "server-as-of corporate-action progress: %s/%s",
                        index,
                        len(codes_to_fetch),
                    )

        arguments = [(code,combined_by_code[code],config,cutoff,force)
                     for code in codes_to_fetch if code in combined_by_code]

        def completed(result, index):
            code = result['code']
            reference_sources[code] = result['reference_source']
            if result['status'] == 'success':
                records[code] = {
                    'input': input_signature(code),
                    'output': fingerprint([output_dir / f'{code}.parquet']),
                    'empty': result['empty'],
                    'formula_status_counts': result['formula_status_counts'],
                    'diagnostics': result['diagnostics'],
                }
                preclose_completed_codes.add(code)
                completed_codes.add(code)
            else:
                failures.append({'code':code,'error':result['error']})
            if index % 50 == 0:
                _write_state(interim=True)
            if index % 250 == 0:
                logging.info('server-as-of preclose validation: %s/%s',index,len(arguments))

        if max_workers <= 1:
            try:
                for index, argument in enumerate(arguments,start=1):
                    completed(_validate_corporate_event(argument),index)
            finally:
                _close_reference_session()
        elif arguments:
            import multiprocessing
            worker_count = min(4,max_workers,len(arguments))
            with ProcessPoolExecutor(max_workers=worker_count,
                    mp_context=multiprocessing.get_context('spawn'),
                    initializer=_initialize_reference_worker, initargs=(PARQUET,)) as pool:
                futures = {pool.submit(_validate_corporate_event,arg):arg[0] for arg in arguments}
                for index,future in enumerate(as_completed(futures),start=1):
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = {'code':futures[future],'status':'failed','reference_source':'source','error':str(exc)}
                    completed(result,index)

        work['reference_local_securities'] = sum(value == 'local' for value in reference_sources.values())
        work['reference_source_securities'] = sum(value == 'source' for value in reference_sources.values())

    _write_state()


@lru_cache(maxsize=1)
def _check_mini_racer() -> bool:
    """Verify that the MiniRacer V8 engine is functional.

    akshare's CNInfo endpoints (e.g. ``stock_allotment_cninfo``) internally use
    MiniRacer to evaluate JavaScript.  If ``py-mini-racer`` (old) and
    ``mini-racer`` (new) are both installed, their files collide in the shared
    ``py_mini_racer/`` directory and every ``eval`` call fails with
    ``dlsym(..., mr_eval_context): symbol not found``.

    The check runs on first use and is cached for the process, so a broken
    source runtime is reported before issuing thousands of per-stock requests.
    """
    try:
        from py_mini_racer import MiniRacer
        MiniRacer().eval("1+1")
        return True
    except Exception as exc:  # noqa: BLE001
        logging.warning(
            "MiniRacer V8 engine check failed — CNInfo endpoints "
            "(allotment events, etc.) will be skipped. "
            "Fix: pip uninstall py-mini-racer && pip install --force-reinstall mini-racer. "
            "Root cause: %s",
            exc,
        )
        return False
