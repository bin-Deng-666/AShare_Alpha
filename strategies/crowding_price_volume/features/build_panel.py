"""Build daily feature + signal panel via DuckDB."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from config.params import CACHE_DIR, CATALOG_PATH, DEFAULT_PARAMS, SAMPLE_START, Params
from lib.db import ensure_dirs, register_params, tech_sql


def build_signal_panel(
    start: str = SAMPLE_START,
    end: str | None = None,
    params: Params = DEFAULT_PARAMS,
    out_path: Path | None = None,
) -> Path:
    """Materialize signal panel to parquet; returns path."""
    ensure_dirs()
    tag_end = end or "latest"
    out_path = out_path or (CACHE_DIR / f"signals_{start}_{tag_end}.parquet")

    con = duckdb.connect()
    con.execute(f"ATTACH '{CATALOG_PATH}' AS quant (READ_ONLY)")
    _run_pipeline(con, start, end, params, out_path)
    con.close()
    return out_path


def _run_pipeline(
    con: duckdb.DuckDBPyConnection,
    start: str,
    end: str | None,
    params: Params,
    out_path: Path,
) -> None:
    tech = tech_sql("i.industry_name")
    end_filter = f"AND d.date <= DATE '{end}'" if end else ""
    register_params(con, params)

    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE base AS
    SELECT
      d.code, d.date, d.exchange, d.open, d.high, d.low, d.close,
      d.amount, d.volume, d.turnover_pct, d.pct_change,
      d.pe_ttm, d.pb_mrq, d.ps_ttm, d.is_st, d.trade_status,
      d.float_market_cap, d.is_limit_up, d.is_limit_down,
      d.limit_up, d.limit_down,
      sm.list_date, sm.name,
      i.industry_code, i.industry_name,
      ({tech}) AS is_tech
    FROM quant.stock_daily d
    JOIN quant.security_master sm USING (code)
    LEFT JOIN quant.industry_membership i
      ON d.code = i.code
     AND i.classification_system = 'CSRC'
     AND i.industry_level = '1'
     AND d.date >= i.valid_from
     AND (i.valid_to IS NULL OR d.date < i.valid_to)
    CROSS JOIN _params p
    WHERE d.date >= DATE '{start}'
      {end_filter}
      AND coalesce(d.trade_status, true)
      AND coalesce(d.is_st, false) = false
      AND coalesce(sm.name, '') NOT LIKE '%ST%'
      AND d.amount IS NOT NULL AND d.close IS NOT NULL
      AND d.amount >= p.min_amount
      AND coalesce(d.float_market_cap, 0) >= p.min_float_mcap
      AND (NOT p.exclude_bj OR d.exchange <> 'BJ')
      AND (sm.list_date IS NULL OR d.date >= sm.list_date + (p.min_list_days * INTERVAL '1' DAY))
      AND coalesce(i.industry_name, '') <> ''
    """)

    n = con.execute("SELECT count(*) FROM base").fetchone()[0]
    print(f"[panel] base rows={n:,}")

    con.execute("""
    CREATE OR REPLACE TEMP TABLE stock_feat AS
    SELECT
      b.*,
      avg(b.amount) OVER (PARTITION BY b.code ORDER BY b.date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS amt_ma20,
      avg(b.amount) OVER (PARTITION BY b.code ORDER BY b.date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS amt_ma5,
      median(b.amount) OVER (PARTITION BY b.code ORDER BY b.date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) AS amt_med60,
      avg(b.close) OVER (PARTITION BY b.code ORDER BY b.date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS ma20,
      avg(b.close) OVER (PARTITION BY b.code ORDER BY b.date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) AS ma60,
      max(b.high) OVER (PARTITION BY b.code ORDER BY b.date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS high20,
      b.close / nullif(lag(b.close, 5) OVER (PARTITION BY b.code ORDER BY b.date), 0) - 1 AS ret_5,
      b.close / nullif(lag(b.close, 20) OVER (PARTITION BY b.code ORDER BY b.date), 0) - 1 AS ret_20,
      (b.high - greatest(b.open, b.close)) / nullif(b.high - b.low, 0) AS upper_shadow_ratio
    FROM base b
    """)

    con.execute("""
    CREATE OR REPLACE TEMP TABLE stock_feat2 AS
    SELECT
      s.*,
      s.amount / nullif(s.amt_med60, 0) AS amt_vs_med,
      s.close / nullif(s.high20, 0) - 1 AS dist_high20,
      s.amt_ma5 / nullif(s.amt_ma20, 0) AS amt_ma_ratio,
      max(CASE WHEN s.amount / nullif(s.amt_med60, 0) < p.dry_amt_vs_med THEN 1 ELSE 0 END) OVER (
        PARTITION BY s.code ORDER BY s.date ROWS BETWEEN 9 PRECEDING AND 1 PRECEDING
      ) AS had_dry_up,
      percent_rank() OVER (PARTITION BY s.date, s.industry_code ORDER BY coalesce(s.ret_20, -999)) AS crowd_ret_pct,
      percent_rank() OVER (PARTITION BY s.date, s.industry_code ORDER BY coalesce(s.turnover_pct, -999)) AS crowd_turn_pct,
      percent_rank() OVER (
        PARTITION BY s.date, s.industry_code
        ORDER BY CASE WHEN s.is_tech THEN coalesce(s.ps_ttm, s.pb_mrq) ELSE coalesce(s.pe_ttm, s.pb_mrq) END NULLS FIRST
      ) AS val_pct_raw
    FROM stock_feat s
    CROSS JOIN _params p
    """)

    con.execute("""
    CREATE OR REPLACE TEMP TABLE ind_amt AS
    SELECT date, industry_code, industry_name, sum(amount) AS ind_amount, count(*) AS n_stocks
    FROM base GROUP BY 1,2,3
    """)
    con.execute("""
    CREATE OR REPLACE TEMP TABLE mkt_amt AS
    SELECT date, sum(amount) AS mkt_amount, count(*) AS n_univ FROM base GROUP BY 1
    """)
    con.execute("""
    CREATE OR REPLACE TEMP TABLE mkt_regime AS
    SELECT date, mkt_amount,
      median(mkt_amount) OVER (ORDER BY date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) AS mkt_med60,
      avg(mkt_amount) OVER (ORDER BY date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS mkt_ma5,
      avg(mkt_amount) OVER (ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS mkt_ma20
    FROM mkt_amt
    """)
    con.execute("""
    CREATE OR REPLACE TEMP TABLE ind_regime AS
    SELECT
      i.date, i.industry_code, i.industry_name, i.ind_amount, m.mkt_amount,
      i.ind_amount / nullif(m.mkt_amount, 0) AS ind_vs_mkt,
      median(i.ind_amount) OVER (PARTITION BY i.industry_code ORDER BY i.date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) AS ind_med60,
      avg(i.ind_amount) OVER (PARTITION BY i.industry_code ORDER BY i.date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS ind_ma5,
      avg(i.ind_amount) OVER (PARTITION BY i.industry_code ORDER BY i.date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS ind_ma20,
      median(i.ind_amount / nullif(m.mkt_amount, 0)) OVER (
        PARTITION BY i.industry_code ORDER BY i.date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
      ) AS ind_vs_mkt_med60,
      m.mkt_med60, m.mkt_ma5, m.mkt_ma20
    FROM ind_amt i
    JOIN mkt_regime m USING (date)
    """)
    con.execute("""
    CREATE OR REPLACE TEMP TABLE regime_attack AS
    SELECT
      r.*,
      r.mkt_amount / nullif(r.mkt_med60, 0) AS mkt_vs_med,
      r.ind_amount / nullif(r.ind_med60, 0) AS ind_vs_med,
      CASE
        WHEN r.mkt_amount / nullif(r.mkt_med60, 0) >= p.climax_vs_med THEN 'climax_mkt'
        WHEN r.mkt_amount / nullif(r.mkt_med60, 0) >= p.expand_vs_med
         AND r.mkt_ma5 / nullif(r.mkt_ma20, 0) >= p.expand_ma_ratio THEN 'expand'
        WHEN r.mkt_amount / nullif(r.mkt_med60, 0) <= p.shrink_vs_med
         AND r.mkt_ma5 / nullif(r.mkt_ma20, 0) <= p.shrink_ma_ratio THEN 'shrink'
        ELSE 'neutral'
      END AS mkt_regime,
      CASE
        WHEN r.ind_amount / nullif(r.ind_med60, 0) >= p.climax_vs_med THEN 'climax_ind'
        WHEN (r.ind_amount / nullif(r.ind_med60, 0) >= p.expand_vs_med
              OR r.ind_vs_mkt / nullif(r.ind_vs_mkt_med60, 0) >= p.expand_vs_med)
         AND r.ind_ma5 / nullif(r.ind_ma20, 0) >= p.expand_ma_ratio THEN 'expand'
        WHEN r.ind_amount / nullif(r.ind_med60, 0) <= p.shrink_vs_med
         AND r.ind_ma5 / nullif(r.ind_ma20, 0) <= p.shrink_ma_ratio THEN 'shrink'
        ELSE 'neutral'
      END AS ind_regime
    FROM ind_regime r
    CROSS JOIN _params p
    """)
    con.execute("""
    CREATE OR REPLACE TEMP TABLE regime_attack2 AS
    SELECT *,
      CASE
        WHEN mkt_regime = 'shrink' AND ind_regime = 'shrink' THEN 'defend'
        WHEN mkt_regime = 'shrink' THEN 'no_new_long'
        WHEN mkt_regime = 'climax_mkt' AND ind_regime IN ('climax_ind', 'expand') THEN 'defend'
        WHEN mkt_regime = 'climax_mkt' THEN 'no_new_long'
        WHEN mkt_regime = 'expand' AND ind_regime IN ('expand', 'climax_ind') THEN 'full_attack'
        WHEN mkt_regime = 'expand' AND ind_regime = 'neutral' THEN 'structure_attack'
        WHEN mkt_regime = 'expand' AND ind_regime = 'shrink' THEN 'quality_only'
        WHEN mkt_regime = 'neutral' AND ind_regime IN ('expand', 'climax_ind') THEN 'structure_attack'
        WHEN mkt_regime = 'neutral' AND ind_regime = 'neutral' THEN 'quality_only'
        ELSE 'no_new_long'
      END AS attack_mode,
      CASE
        WHEN mkt_regime = 'expand' AND ind_regime IN ('expand', 'climax_ind') THEN 1.0
        WHEN mkt_regime = 'expand' OR ind_regime = 'expand' THEN 0.7
        WHEN mkt_regime = 'neutral' AND ind_regime = 'neutral' THEN 0.4
        ELSE 0.0
      END AS position_mult
    FROM regime_attack
    """)

    con.execute("""
    CREATE OR REPLACE TEMP TABLE labeled AS
    SELECT
      s.*,
      r.mkt_regime, r.ind_regime, r.attack_mode, r.position_mult,
      r.mkt_vs_med, r.ind_vs_med,
      (s.crowd_ret_pct + s.crowd_turn_pct) / 2.0 AS crowding_score,
      CASE
        WHEN (s.crowd_ret_pct + s.crowd_turn_pct) / 2.0 >= 0.90 THEN 'extreme'
        WHEN (s.crowd_ret_pct + s.crowd_turn_pct) / 2.0 >= 0.70 THEN 'high'
        WHEN (s.crowd_ret_pct + s.crowd_turn_pct) / 2.0 >= 0.40 THEN 'mid'
        ELSE 'low'
      END AS crowding_bucket,
      CASE
        WHEN s.is_tech AND s.ps_ttm IS NULL AND s.pb_mrq IS NULL THEN NULL
        WHEN (NOT s.is_tech) AND s.pe_ttm IS NULL AND s.pb_mrq IS NULL THEN NULL
        WHEN s.is_tech AND coalesce(s.ps_ttm, s.pb_mrq) <= 0 THEN NULL
        WHEN (NOT s.is_tech) AND coalesce(s.pe_ttm, s.pb_mrq) <= 0 THEN NULL
        ELSE s.val_pct_raw
      END AS val_pct,
      CASE
        WHEN s.is_tech AND s.ps_ttm IS NULL AND s.pb_mrq IS NULL THEN 'invalid'
        WHEN (NOT s.is_tech) AND s.pe_ttm IS NULL AND s.pb_mrq IS NULL THEN 'invalid'
        WHEN s.is_tech AND coalesce(s.ps_ttm, s.pb_mrq) <= 0 THEN 'invalid'
        WHEN (NOT s.is_tech) AND coalesce(s.pe_ttm, s.pb_mrq) <= 0 THEN 'invalid'
        WHEN s.val_pct_raw >= p.val_extreme_pct THEN 'extreme_expensive'
        WHEN s.val_pct_raw >= 0.80 THEN 'expensive'
        WHEN s.val_pct_raw <= 0.30 THEN 'cheap'
        ELSE 'neutral'
      END AS valuation_bucket,
      CASE
        WHEN s.close < s.ma20 AND coalesce(s.pct_change, 0) / 100.0 <= p.break_ret AND s.amt_vs_med >= 1.3 THEN 'break_down_vol'
        WHEN s.amt_vs_med >= p.climax_amt_vs_med
             AND (coalesce(s.upper_shadow_ratio, 0) >= 0.4 OR coalesce(s.pct_change, 0) < 1.0)
             AND s.dist_high20 >= p.stall_dist_high THEN 'climax_volume'
        WHEN s.dist_high20 >= p.stall_dist_high AND s.amt_vs_med < 1.0 AND coalesce(s.ret_5, 0) < 0.03 THEN 'stall_high'
        WHEN s.amt_vs_med >= p.thrust_amt_vs_med AND s.close > s.ma20
             AND coalesce(s.pct_change, 0) > 0 AND coalesce(s.ret_5, 0) > 0 THEN 'thrust_up'
        WHEN s.amt_vs_med <= p.dry_amt_vs_med AND abs(coalesce(s.ret_5, 0)) < 0.06 AND s.close >= s.ma60 * 0.92 THEN 'dry_up_base'
        WHEN s.amt_vs_med >= 1.1 AND s.crowd_ret_pct < 0.55 AND coalesce(s.ret_5, 0) > 0.02 AND s.close > s.ma20 THEN 'rotation_wake'
        ELSE 'none'
      END AS pattern_primary
    FROM stock_feat2 s
    JOIN regime_attack2 r ON s.date = r.date AND s.industry_code = r.industry_code
    CROSS JOIN _params p
    """)

    con.execute("""
    CREATE OR REPLACE TEMP TABLE leaders AS
    SELECT date, industry_code, code AS leader_code
    FROM (
      SELECT date, industry_code, code,
             row_number() OVER (PARTITION BY date, industry_code ORDER BY coalesce(ret_20, -999) DESC) AS rn
      FROM labeled
    ) t WHERE rn = 1
    """)
    con.execute("""
    CREATE OR REPLACE TEMP TABLE leader_pattern AS
    SELECT l.date, l.industry_code, l.leader_code, lb.pattern_primary AS leader_pattern
    FROM leaders l
    JOIN labeled lb ON l.date = lb.date AND l.leader_code = lb.code
    """)

    con.execute("""
    CREATE OR REPLACE TEMP TABLE signals AS
    SELECT
      x.*,
      lp.leader_code,
      lp.leader_pattern,
      CASE
        WHEN x.pattern_primary = 'break_down_vol' THEN 'avoid'
        WHEN x.crowding_bucket = 'extreme'
             AND x.pattern_primary IN ('stall_high', 'climax_volume', 'break_down_vol') THEN 'avoid'
        WHEN x.valuation_bucket = 'extreme_expensive'
             AND x.crowding_bucket IN ('high', 'extreme')
             AND x.pattern_primary IN ('stall_high', 'climax_volume', 'break_down_vol') THEN 'avoid'
        WHEN x.attack_mode = 'defend'
             AND x.pattern_primary IN ('stall_high', 'climax_volume', 'break_down_vol') THEN 'avoid'
        WHEN x.crowding_bucket IN ('high', 'extreme')
             AND x.pattern_primary IN ('stall_high', 'climax_volume') THEN 'reduce'
        WHEN x.attack_mode IN ('defend', 'no_new_long') THEN 'flat'
        WHEN x.is_limit_up THEN 'flat'
        WHEN x.valuation_bucket IN ('extreme_expensive', 'invalid') THEN 'flat'
        WHEN x.attack_mode IN ('full_attack', 'structure_attack')
             AND coalesce(x.had_dry_up, 0) = 1
             AND x.pattern_primary = 'thrust_up'
             AND x.crowding_bucket = 'low'
             AND x.valuation_bucket IN ('cheap', 'neutral') THEN 'open_A'
        WHEN x.attack_mode IN ('full_attack', 'structure_attack')
             AND x.ind_regime = 'expand'
             AND x.pattern_primary = 'thrust_up'
             AND x.crowding_bucket = 'low'
             AND x.valuation_bucket = 'cheap' THEN 'open_B'
        WHEN x.attack_mode IN ('full_attack', 'structure_attack')
             AND lp.leader_pattern IN ('stall_high', 'climax_volume')
             AND x.pattern_primary IN ('rotation_wake', 'thrust_up')
             AND x.crowding_bucket = 'low'
             AND x.valuation_bucket IN ('cheap', 'neutral')
             AND x.code <> lp.leader_code THEN 'open_C'
        WHEN x.pattern_primary = 'thrust_up'
             AND x.crowding_bucket IN ('low', 'mid')
             AND x.attack_mode NOT IN ('defend', 'no_new_long')
             AND x.ind_regime <> 'shrink' THEN 'hold_candidate'
        ELSE 'flat'
      END AS action,
      CASE
        WHEN x.pattern_primary = 'thrust_up'
             AND x.crowding_bucket IN ('low', 'mid')
             AND x.attack_mode NOT IN ('defend', 'no_new_long')
             AND x.ind_regime <> 'shrink' THEN true
        ELSE false
      END AS hold_ok,
      (x.ind_regime = 'shrink') AS industry_shrinking,
      CASE
        WHEN x.pattern_primary = 'thrust_up' AND x.crowding_bucket IN ('low', 'mid') THEN
          (CASE x.attack_mode WHEN 'full_attack' THEN 1.0 WHEN 'structure_attack' THEN 0.75 ELSE 0.5 END)
          * (1.15 - coalesce(x.val_pct, 0.5))
          * (1.0 - coalesce((x.crowd_ret_pct + x.crowd_turn_pct)/2.0, 0.5) * 0.5)
          * least(coalesce(x.amt_vs_med, 1.0), 3.0)
        ELSE coalesce(x.amt_vs_med, 0) * 0.01
      END AS score
    FROM labeled x
    LEFT JOIN leader_pattern lp ON x.date = lp.date AND x.industry_code = lp.industry_code
    """)

    out_escaped = str(out_path).replace("'", "''")
    con.execute(f"""
    COPY (
      SELECT
        code, name, date, exchange, industry_code, industry_name, is_tech,
        open, high, low, close, amount, pct_change, turnover_pct,
        pe_ttm, pb_mrq, ps_ttm, float_market_cap,
        is_limit_up, is_limit_down,
        mkt_regime, ind_regime, attack_mode, position_mult,
        pattern_primary, crowding_score, crowding_bucket,
        val_pct, valuation_bucket,
        had_dry_up, leader_code, leader_pattern,
        action, score, hold_ok, industry_shrinking,
        amt_vs_med, ret_5, ret_20, dist_high20
      FROM signals
    ) TO '{out_escaped}' (FORMAT PARQUET)
    """)
    n_sig = con.execute("SELECT count(*) FROM signals").fetchone()[0]
    print(f"[panel] signals rows={n_sig:,} -> {out_path}")


def load_signals(path: Path) -> pd.DataFrame:
    path_s = str(path).replace("'", "''")
    return duckdb.sql(f"SELECT * FROM read_parquet('{path_s}')").df()
