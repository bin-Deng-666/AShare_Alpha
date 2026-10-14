"""assets — adjustment limits: data acquisition and normalization."""

from __future__ import annotations
import datetime as dt
import logging
from collections import defaultdict
from typing import Dict, Optional, Sequence
import numpy as np
import pandas as pd
from ashare_alpha.data.paths import PARQUET_ROOT as PARQUET, STATE_ROOT as STATE
from ashare_alpha.data.rules.corporate_actions import (
    CORPORATE_ACTION_ASOF_UNIVERSE_RULE,
    CorporateActionFormulaStatus,
    has_supported_price_formula,
)
from ashare_alpha.data.rules.prices import round_cny_price_to_cent

from ashare_alpha.data.sources.corporate_actions import _frozen_sh_sz_universe
from ashare_alpha.data.parquet_metadata import _read_parquet_through_date
from ashare_alpha.data.storage import atomic_json
from ashare_alpha.data.storage import atomic_parquet
from ashare_alpha.data.sources.securities import exchange_for

_round_positive_price_to_cent = round_cny_price_to_cent


def _limit_pct_for_row(
    code: str,
    is_st: Optional[bool],
    trade_date: pd.Timestamp,
    *,
    list_date: Optional[pd.Timestamp] = None,
    listing_session: Optional[int] = None,
) -> Optional[float]:
    """A-share daily limit ratio by board / ST / registration-system rules."""
    if code.startswith(("8", "4", "92")):
        if listing_session == 1:
            return None
        return 0.30
    is_star = code.startswith(("688", "689"))
    is_chinext = code.startswith(("300", "301", "302"))
    registration_ipo = (
        is_star
        or (
            is_chinext
            and list_date is not None
            and list_date >= pd.Timestamp("2020-08-24")
        )
        or (
            not is_star
            and not is_chinext
            and list_date is not None
            and list_date >= pd.Timestamp("2023-04-10")
        )
    )
    if registration_ipo and listing_session is not None and listing_session <= 5:
        return None
    if is_star:
        return 0.20
    # ChiNext registration system: 20% from 2020-08-24 onward.
    if is_chinext and trade_date >= pd.Timestamp("2020-08-24"):
        return 0.20
    # From this date the main-board rule is the same with either ST status.
    if trade_date >= pd.Timestamp("2026-07-06"):
        return 0.10
    if pd.isna(is_st):
        return None
    return 0.05 if is_st else 0.10


def _corporate_action_terms_for_bars(
    bars: pd.DataFrame,
    actions: pd.DataFrame,
) -> pd.DataFrame:
    """Map one validated ex-date action record onto each raw bar."""
    result = pd.DataFrame(
        {
            "cash_dividend_per_share": 0.0,
            "share_distribution_ratio": 0.0,
            "rights_issue_ratio": 0.0,
            "rights_issue_price": 0.0,
            "observed_reference_price": np.nan,
            "price_formula_supported": False,
            "has_corporate_action": False,
            "corporate_action_formula_status": (
                CorporateActionFormulaStatus.NONE.value
            ),
        },
        index=bars.index,
    )
    if actions.empty or "ex_date" not in actions:
        return result

    action_frame = actions.copy()
    action_frame["ex_date"] = pd.to_datetime(
        action_frame["ex_date"], errors="coerce"
    ).dt.normalize()
    action_frame = action_frame.loc[action_frame["ex_date"].notna()].copy()
    if "report_date" in action_frame:
        # Some early Eastmoney rows attach a future reporting period to an
        # older ex-date. Such a plan cannot define that earlier day's price
        # reference and must not replace a chronologically valid record.
        action_frame["report_date"] = pd.to_datetime(
            action_frame["report_date"], errors="coerce"
        ).dt.normalize()
        action_frame = action_frame.loc[
            action_frame["report_date"].isna()
            | action_frame["report_date"].le(action_frame["ex_date"])
        ].copy()
    if action_frame.empty:
        return result
    for column in (
        "cash_dividend_per_10",
        "bonus_share_per_10",
        "transfer_share_per_10",
        "bonus_transfer_per_10",
        "rights_issue_per_10",
        "rights_issue_price",
        "observed_reference_price",
    ):
        if column not in action_frame:
            action_frame[column] = np.nan
        action_frame[column] = pd.to_numeric(action_frame[column], errors="coerce")
    if "formula_status" not in action_frame:
        action_frame["formula_status"] = (
            CorporateActionFormulaStatus.SUPPORTED_STANDARD.value
        )
    action_frame["formula_status"] = (
        action_frame["formula_status"]
        .fillna(CorporateActionFormulaStatus.UNSUPPORTED_MISSING_FORMULA_STATUS.value)
        .astype(str)
    )

    component_total = action_frame["bonus_share_per_10"].fillna(0.0) + action_frame[
        "transfer_share_per_10"
    ].fillna(0.0)
    share_distribution = action_frame["bonus_transfer_per_10"].where(
        action_frame["bonus_transfer_per_10"].notna(), component_total
    )
    action_frame["_cash_per_share"] = (
        action_frame["cash_dividend_per_10"].fillna(0.0) / 10.0
    )
    action_frame["_share_distribution"] = share_distribution.fillna(0.0) / 10.0
    action_frame["_rights_issue_ratio"] = (
        action_frame["rights_issue_per_10"].fillna(0.0) / 10.0
    )
    action_frame["_rights_issue_price"] = action_frame["rights_issue_price"].fillna(0.0)
    action_frame["_observed_reference_price"] = action_frame["observed_reference_price"]
    action_frame["_price_formula_supported"] = action_frame["formula_status"].map(
        has_supported_price_formula
    )
    action_frame["_has_event"] = True
    if "publish_date" in action_frame:
        action_frame["publish_date"] = pd.to_datetime(
            action_frame["publish_date"], errors="coerce"
        )
        action_frame = action_frame.sort_values(
            ["ex_date", "publish_date"], na_position="first"
        )
    action_frame = action_frame.drop_duplicates("ex_date", keep="last")
    action_by_date = action_frame.set_index("ex_date")[
        [
            "_cash_per_share",
            "_share_distribution",
            "_rights_issue_ratio",
            "_rights_issue_price",
            "_observed_reference_price",
            "_price_formula_supported",
            "_has_event",
            "formula_status",
        ]
    ]
    dates = pd.to_datetime(bars["date"]).dt.normalize()
    result["cash_dividend_per_share"] = dates.map(
        action_by_date["_cash_per_share"]
    ).fillna(0.0)
    result["share_distribution_ratio"] = dates.map(
        action_by_date["_share_distribution"]
    ).fillna(0.0)
    result["rights_issue_ratio"] = dates.map(
        action_by_date["_rights_issue_ratio"]
    ).fillna(0.0)
    result["rights_issue_price"] = dates.map(
        action_by_date["_rights_issue_price"]
    ).fillna(0.0)
    result["observed_reference_price"] = dates.map(
        action_by_date["_observed_reference_price"]
    )
    result["price_formula_supported"] = dates.map(
        action_by_date["_price_formula_supported"]
    ).eq(True)
    result["has_corporate_action"] = dates.map(action_by_date["_has_event"]).eq(True)
    result["corporate_action_formula_status"] = dates.map(
        action_by_date["formula_status"]
    ).fillna(CorporateActionFormulaStatus.NONE.value)
    return result


def _limit_reference_from_corporate_actions(
    bars: pd.DataFrame,
    actions: pd.DataFrame,
) -> tuple[pd.Series, pd.Series]:
    """Return the ex-right reference and whether an action changed its basis."""
    reference = pd.to_numeric(bars["prev_close"], errors="coerce").copy()
    terms = _corporate_action_terms_for_bars(bars, actions)
    formula_reference = (
        reference
        - terms["cash_dividend_per_share"]
        + terms["rights_issue_price"] * terms["rights_issue_ratio"]
    ) / (1.0 + terms["share_distribution_ratio"] + terms["rights_issue_ratio"])
    observed_reference = pd.to_numeric(
        terms["observed_reference_price"], errors="coerce"
    )
    candidate_reference = observed_reference.where(
        observed_reference.gt(0.0),
        formula_reference.where(terms["price_formula_supported"]),
    )
    usable = terms["has_corporate_action"] & candidate_reference.gt(0.0)
    reference.loc[usable] = candidate_reference.loc[usable]
    return reference, usable.astype(bool)


def _qfq_from_corporate_actions(
    raw_bars: pd.DataFrame,
    actions: pd.DataFrame,
    *,
    data_cutoff: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Rebuild a forward-adjusted OHLC series from raw bars and ex-date actions."""
    bars = raw_bars.copy()
    bars["date"] = pd.to_datetime(bars["date"]).dt.normalize()
    if data_cutoff is not None:
        cutoff = pd.Timestamp(data_cutoff).normalize()
        bars = bars.loc[bars["date"].le(cutoff)].copy()
        if not actions.empty and "ex_date" in actions:
            actions = actions.copy()
            actions["ex_date"] = pd.to_datetime(
                actions["ex_date"], errors="coerce"
            ).dt.normalize()
            actions = actions.loc[actions["ex_date"].le(cutoff)].copy()
    bars = bars.sort_values("date").drop_duplicates("date", keep="last")
    bars["prev_close"] = pd.to_numeric(bars["close"], errors="coerce").shift(1)
    reference, action_adjusted = _limit_reference_from_corporate_actions(bars, actions)
    action_terms = _corporate_action_terms_for_bars(bars, actions)
    event_ratio = pd.Series(1.0, index=bars.index, dtype=float)
    usable = (
        action_adjusted
        & reference.notna()
        & bars["prev_close"].notna()
        & bars["prev_close"].gt(0.0)
    )
    event_ratio.loc[usable] = reference.loc[usable] / bars.loc[usable, "prev_close"]
    if (~event_ratio.gt(0.0)).any() or (~np.isfinite(event_ratio)).any():
        raise ValueError("corporate actions produced an invalid qfq event ratio")

    # An event on day t adjusts every earlier bar, but not day t itself. Later
    # events only rescale an earlier window uniformly, so pre-event returns and
    # ratios remain unchanged; a label that crosses t receives the adjustment.
    inclusive_future_product = event_ratio.iloc[::-1].cumprod().iloc[::-1]
    bars["adjustment_factor"] = inclusive_future_product / event_ratio
    bars["event_ratio"] = event_ratio.where(action_adjusted)
    bars["corporate_action_adjusted"] = action_adjusted.astype(bool)
    bars["corporate_action_formula_status"] = action_terms[
        "corporate_action_formula_status"
    ]
    bars["rights_issue_ratio"] = action_terms["rights_issue_ratio"]
    bars["rights_issue_price"] = action_terms["rights_issue_price"]
    bars["event_reference_price"] = reference.where(action_adjusted)
    for column in ("open", "high", "low", "close"):
        bars[column] = (
            pd.to_numeric(bars[column], errors="coerce") * bars["adjustment_factor"]
        )
    bars["adjustment"] = "qfq"
    bars["source"] = "corporate_actions_adjusted+raw_bars"
    continuity_error = (
        bars["close"].shift(1)
        - bars["event_reference_price"] * bars["adjustment_factor"]
    ).where(bars["corporate_action_adjusted"])
    if continuity_error.abs().dropna().gt(1e-8).any():
        raise ValueError("qfq action factor failed the reference-price identity")
    return bars.drop(columns=["prev_close"])


def build_qfq_from_corporate_actions(
    config: Dict,
    limit: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
    force: bool = False,
    data_cutoff: str | pd.Timestamp | None = None,
    on_code_done=None,
) -> None:
    """Rebuild qfq files from raw OHLC and the repository corporate-action layer."""
    del config
    raw_dir = PARQUET / "daily_bars" / "adjustment=raw"
    qfq_dir = PARQUET / "daily_bars" / "adjustment=qfq"
    event_dir = PARQUET / "corporate_action_events"
    action_dir = event_dir
    raw_files = sorted(raw_dir.glob("*.parquet"))
    raw_files = [path for path in raw_files if exchange_for(path.stem) in ("SH", "SZ")]
    frozen_universe_codes: set[str] | None = None
    if data_cutoff is not None and not codes:
        frozen_universe_codes = set(
            _frozen_sh_sz_universe(data_cutoff)["code"].astype(str)
        )
        raw_files = [path for path in raw_files if path.stem in frozen_universe_codes]
    if codes:
        wanted = set(codes)
        raw_files = [path for path in raw_files if path.stem in wanted]
    if limit:
        raw_files = raw_files[:limit]
    built = 0
    failures: list[dict[str, str]] = []
    adjusted_rows_by_year: defaultdict[int, int] = defaultdict(int)
    event_ratios_by_year: defaultdict[int, list[float]] = defaultdict(list)
    formula_status_counts: defaultdict[str, int] = defaultdict(int)
    continuity_max_abs_error = 0.0
    for index, raw_path in enumerate(raw_files, start=1):
        code = raw_path.stem
        output = qfq_dir / raw_path.name
        if output.exists() and not force:
            continue
        try:
            raw_bars = _read_parquet_through_date(
                raw_path,
                date_column="date",
                data_cutoff=data_cutoff,
            )
            action_path = action_dir / f"{code}.parquet"
            actions = (
                _read_parquet_through_date(
                    action_path,
                    date_column="ex_date",
                    data_cutoff=data_cutoff,
                )
                if action_path.exists()
                else pd.DataFrame()
            )
            if not action_path.exists():
                raise FileNotFoundError(
                    f"corporate-action event coverage missing for {code}"
                )
            adjusted = _qfq_from_corporate_actions(
                raw_bars,
                actions,
                data_cutoff=data_cutoff,
            )
            adjusted_years = pd.to_datetime(
                adjusted.loc[adjusted["corporate_action_adjusted"], "date"]
            ).dt.year.value_counts()
            for action_year, count in adjusted_years.items():
                adjusted_rows_by_year[int(action_year)] += int(count)
            event_rows = adjusted.loc[
                adjusted["corporate_action_adjusted"], ["date", "event_ratio"]
            ].copy()
            if not event_rows.empty:
                event_rows["year"] = pd.to_datetime(event_rows["date"]).dt.year
                for action_year, values in event_rows.groupby("year")["event_ratio"]:
                    event_ratios_by_year[int(action_year)].extend(
                        pd.to_numeric(values, errors="coerce").dropna().tolist()
                    )
            for status, count in (
                adjusted["corporate_action_formula_status"].value_counts().items()
            ):
                if str(status) != CorporateActionFormulaStatus.NONE.value:
                    formula_status_counts[str(status)] += int(count)
            continuity_error = (
                adjusted["close"].shift(1)
                - adjusted["event_reference_price"] * adjusted["adjustment_factor"]
            ).where(adjusted["corporate_action_adjusted"])
            if continuity_error.notna().any():
                continuity_max_abs_error = max(
                    continuity_max_abs_error,
                    float(continuity_error.abs().max()),
                )
            atomic_parquet(adjusted, output)
            if on_code_done is not None:
                on_code_done(code)
            built += 1
            if index % 500 == 0:
                logging.info(
                    "corporate-action qfq progress: %s/%s built=%s",
                    index,
                    len(raw_files),
                    built,
                )
        except Exception as exc:  # noqa: BLE001
            failures.append({"code": code, "error": str(exc)})
    atomic_json(
        {
            "built": built,
            "failures": failures,
            "adjusted_rows_by_year": dict(sorted(adjusted_rows_by_year.items())),
            "event_ratio_by_year": {
                str(year): {
                    "count": len(values),
                    "min": min(values),
                    "median": float(np.median(values)),
                    "max": max(values),
                }
                for year, values in sorted(event_ratios_by_year.items())
                if values
            },
            "formula_status_counts": dict(sorted(formula_status_counts.items())),
            "action_source_layer": action_dir.name,
            "continuity_max_abs_error": continuity_max_abs_error,
            "physical_read_cutoff": (
                str(pd.Timestamp(data_cutoff).date())
                if data_cutoff is not None
                else None
            ),
            "frozen_universe_files": len(raw_files),
            "universe_rule": (
                CORPORATE_ACTION_ASOF_UNIVERSE_RULE
                if frozen_universe_codes is not None
                else "explicit_codes_or_current"
            ),
            "updated_at": dt.datetime.now(),
        },
        STATE / "corporate_action_qfq_coverage.json",
    )
    logging.info(
        "corporate-action qfq finished: built=%s failures=%s",
        built,
        len(failures),
    )


def build_limit_prices(
    config: Dict,
    limit: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
    force: bool = False,
    data_cutoff: str | pd.Timestamp | None = None,
    on_code_done=None,
) -> None:
    """Derive daily limit-up/down prices from raw close and board/ST rules."""
    del config
    bar_dir = PARQUET / "daily_bars" / "adjustment=raw"
    indicator_dir = PARQUET / "daily_indicators"
    event_dir = PARQUET / "corporate_action_events"
    distribution_dir = PARQUET / "corporate_actions"
    action_dir = event_dir
    output_dir = PARQUET / "limit_prices"
    output_dir.mkdir(parents=True, exist_ok=True)
    master_path = PARQUET / "security_master" / "security_master.parquet"
    list_dates: Dict[str, pd.Timestamp] = {}
    if master_path.exists():
        master = pd.read_parquet(master_path, columns=["code", "list_date"])
        list_dates = {
            str(row.code).zfill(6): pd.Timestamp(row.list_date).normalize()
            for row in master.itertuples(index=False)
            if pd.notna(row.list_date)
        }
    calendar_path = PARQUET / "reference" / "trade_calendar.parquet"
    market_dates = pd.DatetimeIndex([])
    if calendar_path.exists():
        calendar = _read_parquet_through_date(
            calendar_path,
            date_column="calendar_date",
            data_cutoff=data_cutoff,
        )
        market_dates = pd.DatetimeIndex(
            pd.to_datetime(
                calendar.loc[calendar["is_trading_day"].astype(bool), "calendar_date"]
            ).dt.normalize()
        ).sort_values()
    bar_files = sorted(bar_dir.glob("*.parquet"))
    frozen_universe_codes: set[str] | None = None
    if data_cutoff is not None and not codes:
        # BJ uses declared distribution formulas; SH/SZ uses validated events.
        master = pd.read_parquet(master_path)
        frozen_universe_codes = set(
            master.loc[master.list_date.le(pd.Timestamp(data_cutoff)), "code"].astype(
                str
            )
        )
        bar_files = [path for path in bar_files if path.stem in frozen_universe_codes]
    if codes:
        wanted = set(codes)
        bar_files = [path for path in bar_files if path.stem in wanted]
    if limit:
        bar_files = bar_files[:limit]

    built = 0
    failures = []
    for index, path in enumerate(bar_files, start=1):
        code = path.stem
        output = output_dir / f"{code}.parquet"
        if output.exists() and not force:
            continue
        try:
            bars = _read_parquet_through_date(
                path,
                date_column="date",
                data_cutoff=data_cutoff,
                columns=["date", "high", "low", "close"],
            ).copy()
            bars["date"] = pd.to_datetime(bars["date"]).dt.normalize()
            bars = bars.sort_values("date").drop_duplicates("date", keep="last")
            bars["prev_close"] = bars["close"].shift(1)
            symbol_action_dir = (
                distribution_dir if exchange_for(code) == "BJ" else action_dir
            )
            action_path = symbol_action_dir / f"{code}.parquet"
            actions = (
                _read_parquet_through_date(
                    action_path,
                    date_column="ex_date",
                    data_cutoff=data_cutoff,
                )
                if action_path.exists()
                else pd.DataFrame()
            )
            if not action_path.exists():
                raise FileNotFoundError(f"corporate-action coverage missing for {code}")
            bars["reference_price"], action_adjusted = (
                _limit_reference_from_corporate_actions(bars, actions)
            )
            action_terms = _corporate_action_terms_for_bars(bars, actions)
            has_corporate_action = action_terms["has_corporate_action"]
            bars["cash_dividend_per_share"] = action_terms[
                "cash_dividend_per_share"
            ].where(has_corporate_action, 0.0)
            bars["share_distribution_ratio"] = action_terms[
                "share_distribution_ratio"
            ].where(has_corporate_action, 0.0)
            bars["rights_issue_ratio"] = action_terms["rights_issue_ratio"].where(
                has_corporate_action, 0.0
            )
            bars["rights_issue_price"] = action_terms["rights_issue_price"].where(
                has_corporate_action, 0.0
            )
            bars["corporate_action_adjusted"] = action_adjusted
            bars["corporate_action_formula_status"] = action_terms[
                "corporate_action_formula_status"
            ]
            is_st = pd.Series(pd.NA, index=bars.index, dtype="boolean")
            indicator_path = indicator_dir / f"{code}.parquet"
            if indicator_path.exists():
                indicators = _read_parquet_through_date(
                    indicator_path,
                    date_column="date",
                    data_cutoff=data_cutoff,
                    columns=["date", "isST"],
                ).copy()
                indicators["date"] = pd.to_datetime(indicators["date"]).dt.normalize()
                bars = bars.merge(indicators, on="date", how="left")
                is_st = (
                    pd.to_numeric(bars["isST"], errors="coerce")
                    .map({0: False, 1: True})
                    .astype("boolean")
                )
                bars = bars.drop(columns=["isST"])
            list_date = list_dates.get(code)
            if list_date is not None and len(market_dates):
                list_index = int(market_dates.searchsorted(list_date, side="left"))
                listing_sessions = [
                    int(market_dates.searchsorted(date, side="left")) - list_index + 1
                    for date in bars["date"]
                ]
            else:
                listing_sessions = [None] * len(bars)
            limit_pct = [
                _limit_pct_for_row(
                    code,
                    st,
                    date,
                    list_date=list_date,
                    listing_session=listing_session,
                )
                for st, date, listing_session in zip(
                    is_st.tolist(), bars["date"].tolist(), listing_sessions
                )
            ]
            bars["limit_pct"] = pd.to_numeric(
                pd.Series(limit_pct, index=bars.index), errors="coerce"
            )
            unknown_st = pd.Series(False, index=bars.index)
            session_by_index = dict(zip(bars.index, listing_sessions))
            for i in bars.index[bars["limit_pct"].isna() & is_st.isna()]:
                unknown_st.loc[i] = (
                    _limit_pct_for_row(
                        code,
                        False,
                        bars.loc[i, "date"],
                        list_date=list_date,
                        listing_session=session_by_index[i],
                    )
                    is not None
                )
            unrestricted = bars["limit_pct"].isna() & ~unknown_st
            missing_reference = bars["reference_price"].isna() & ~unrestricted
            # Exchange limits are based on the cent-rounded ex-right reference
            # price.  Applying the percentage to the unrounded theoretical
            # value creates false one-cent boundary violations on ex dates.
            limit_basis = _round_positive_price_to_cent(bars["reference_price"])
            bars["limit_up"] = _round_positive_price_to_cent(
                limit_basis * (1.0 + bars["limit_pct"])
            )
            bars["limit_down"] = _round_positive_price_to_cent(
                limit_basis * (1.0 - bars["limit_pct"])
            )
            # First day/missing previous close and registration IPO sessions 1-5
            # have no standard daily limit.
            mask = bars["reference_price"].isna() | bars["limit_pct"].isna()
            unsupported_price_reference = ~bars["corporate_action_formula_status"].map(
                has_supported_price_formula
            )
            bars.loc[
                mask | unsupported_price_reference,
                ["limit_up", "limit_down", "limit_pct"],
            ] = pd.NA
            within_upper = (
                (pd.to_numeric(bars["high"], errors="coerce") + 1e-8)
                .round(2)
                .le(bars["limit_up"])
            )
            within_lower = (
                (pd.to_numeric(bars["low"], errors="coerce") - 1e-8)
                .round(2)
                .ge(bars["limit_down"])
            )
            invalid_boundary = (
                ~mask & ~unsupported_price_reference & ~(within_upper & within_lower)
            )
            bars["limit_validation_status"] = np.select(
                [
                    unsupported_price_reference,
                    unknown_st,
                    missing_reference,
                    unrestricted,
                    invalid_boundary,
                ],
                [
                    "unsupported_corporate_action",
                    "unknown_st_status",
                    "missing_reference_price",
                    "not_applicable",
                    "raw_ohlc_outside_limit",
                ],
                default="valid",
            )
            bars.loc[
                invalid_boundary,
                ["limit_up", "limit_down", "limit_pct"],
            ] = pd.NA
            close_at_cent = _round_positive_price_to_cent(bars["close"])
            bars["is_limit_up"] = close_at_cent.eq(bars["limit_up"])
            bars["is_limit_down"] = close_at_cent.eq(bars["limit_down"])
            unavailable = mask | unsupported_price_reference | invalid_boundary
            if unavailable.any():
                bars["is_limit_up"] = bars["is_limit_up"].astype("boolean")
                bars["is_limit_down"] = bars["is_limit_down"].astype("boolean")
                bars.loc[unavailable, ["is_limit_up", "is_limit_down"]] = pd.NA
            bars["code"] = code
            bars["reference_price_source"] = np.select(
                [unsupported_price_reference, action_adjusted],
                [
                    "unsupported_corporate_action",
                    "corporate_actions_ex_right",
                ],
                default="previous_raw_close",
            )
            if exchange_for(code) == "BJ":
                bars.loc[action_adjusted, "reference_price_source"] = (
                    "declared_distribution_formula"
                )
            bars["source"] = f"rule_derived+raw_bars+{symbol_action_dir.name}"
            atomic_parquet(
                bars[
                    [
                        "code",
                        "date",
                        "prev_close",
                        "reference_price",
                        "reference_price_source",
                        "limit_validation_status",
                        "cash_dividend_per_share",
                        "share_distribution_ratio",
                        "rights_issue_ratio",
                        "rights_issue_price",
                        "corporate_action_adjusted",
                        "corporate_action_formula_status",
                        "close",
                        "limit_up",
                        "limit_down",
                        "limit_pct",
                        "is_limit_up",
                        "is_limit_down",
                        "source",
                    ]
                ],
                output,
            )
            if on_code_done is not None:
                on_code_done(code)
            built += 1
            if index % 500 == 0:
                logging.info(
                    "limit_prices progress: %s/%s built=%s",
                    index,
                    len(bar_files),
                    built,
                )
        except Exception as exc:  # noqa: BLE001
            failures.append({"code": code, "error": str(exc)})
    atomic_json(
        {
            "built": built,
            "failures": failures,
            "physical_read_cutoff": (
                str(pd.Timestamp(data_cutoff).date())
                if data_cutoff is not None
                else None
            ),
            "frozen_universe_files": len(bar_files),
            "universe_rule": (
                CORPORATE_ACTION_ASOF_UNIVERSE_RULE
                if frozen_universe_codes is not None
                else "explicit_codes_or_current"
            ),
            "updated_at": dt.datetime.now(),
        },
        STATE / "limit_prices_coverage.json",
    )
    logging.info(
        "limit_prices finished: built=%s failures=%s",
        built,
        len(failures),
    )
