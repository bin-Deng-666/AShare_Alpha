"""Capital-aware portfolio simulation for regime_sparse (v0.1).

Execution protocol (strict T+1, no same-day fill):
  Signal day T (EOD): decide exits/entries from close[T] + signal[T] → pending
  Trade day T+1: fill at open[T+1] → MTM close[T+1] → decide next pending

Kept: open_A, core exits, Risk-On, SOTA turn-stab, crowd-traj, cash refill.
Removed: false-bull, mainline, scarcity, earnings/nb/index-tier, B/C,
         dormant exits (MFE/ATR/pyramid/R-scale/vol-target/…).

See docs/specs/execution.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from backtest.costs import (
    apply_slippage as _apply_slip,
    lot_floor as _lot_floor,
    slippage_bps_for_row as _slippage_bps_for_row,
    trade_fee as _trade_fee,
)
from config.params import DEFAULT_PARAMS, Params
from features.momentum_rank import (
    apply_kdj_score_boost,
    apply_macd_score_boost,
    enrich_momentum_rank,
)
from features.sota_factors import (
    apply_sota_score_boost,
    enrich_sota_factors,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _build_breadth_by_date(sig: pd.DataFrame) -> pd.Series:
    if sig.empty or "ind_regime" not in sig.columns:
        return pd.Series(dtype=float)
    g = sig.copy()
    g["date"] = pd.to_datetime(g["date"])
    expand = g["ind_regime"].astype(str).eq("expand")
    return expand.groupby(g["date"]).mean().rename("breadth")


def _build_risk_on_overlay(
    market: pd.DataFrame | None,
    params: Params,
    breadth: pd.Series | None = None,
) -> pd.DataFrame:
    """HS300 trend + abs mom + breadth → risk_on / exposure_ok by date."""
    empty = pd.DataFrame(columns=["risk_on", "risk_on_exposure_ok"]).rename_axis("date")
    if market is None or not params.enable_risk_on or len(market) == 0:
        return empty
    m = market.copy()
    m["date"] = pd.to_datetime(m["date"])
    m = m.sort_values("date").drop_duplicates("date")
    close = m["close"].astype(float)
    ma_f = close.rolling(params.risk_on_ma_fast, min_periods=params.risk_on_ma_fast).mean()
    ma_s = close.rolling(params.risk_on_ma_slow, min_periods=params.risk_on_ma_slow).mean()
    bull = (close > ma_s) & (ma_f > ma_s)
    abs_mom = close / close.shift(params.risk_on_abs_mom_days) - 1.0 > 0.0
    out = pd.DataFrame(
        {
            "date": m["date"].values,
            "bull": bull.fillna(False).astype(bool).values,
            "abs_mom": abs_mom.fillna(False).astype(bool).values,
        }
    ).set_index("date")
    if breadth is not None and len(breadth):
        b = breadth.copy()
        b.index = pd.to_datetime(b.index)
        out = out.join(b.rename("breadth"), how="left")
    else:
        out["breadth"] = np.nan

    risk_on = out["bull"].astype(bool)
    if params.risk_on_require_abs_mom:
        risk_on = risk_on & out["abs_mom"].astype(bool)
    if params.risk_on_require_breadth:
        br = out["breadth"]
        risk_on = risk_on & br.notna() & (br >= float(params.risk_on_breadth_min))
    out["risk_on"] = risk_on.fillna(False).astype(bool)
    out["risk_on_exposure_ok"] = out["risk_on"]
    return out


def _enrich_crowd_traj(sig: pd.DataFrame, params: Params) -> pd.DataFrame:
    if sig.empty or "crowding_score" not in sig.columns:
        out = sig.copy()
        out["crowd_delta_5"] = np.nan
        out["crowd_rising"] = False
        return out
    out = sig.sort_values(["code", "date"]).copy()
    score = pd.to_numeric(out["crowding_score"], errors="coerce")
    out["crowd_delta_5"] = score.groupby(out["code"]).diff(params.crowd_traj_lookback)
    out["crowd_rising"] = out["crowd_delta_5"].fillna(0.0) > float(params.crowd_traj_min_delta)
    return out


def _filter_opens(df: pd.DataFrame, params: Params) -> pd.DataFrame:
    """open_A only: attack mode + crowding + valuation + amount gates."""
    out = df[df["action"] == "open_A"].copy()
    if out.empty:
        return out
    if "position_mult" in out.columns:
        out = out[out["position_mult"] >= params.min_position_mult_to_buy]
    if "attack_mode" in out.columns:
        out = out[out["attack_mode"].astype(str).isin(list(params.allowed_attack_modes))]
    if out.empty:
        out["crowd_traj_entry"] = False
        return out

    if "crowding_bucket" in out.columns:
        bucket = out["crowding_bucket"].astype(str)
        low_ok = bucket.eq("low")
        traj_ok = pd.Series(False, index=out.index)
        if params.enable_crowd_traj:
            score = pd.to_numeric(out.get("crowding_score"), errors="coerce")
            rising = out["crowd_rising"].fillna(False) if "crowd_rising" in out.columns else False
            mid_ok = bucket.eq("mid")
            score_ok = score.notna() & (score <= float(params.crowd_traj_max_score))
            traj_ok = mid_ok & rising & score_ok
        out = out[low_ok | traj_ok].copy()
        out["crowd_traj_entry"] = (~low_ok.loc[out.index]) & traj_ok.loc[out.index]
    else:
        out["crowd_traj_entry"] = False

    if "valuation_bucket" in out.columns:
        out = out[out["valuation_bucket"].isin(list(params.allowed_valuation))]
    if "amt_vs_med" in out.columns:
        out = out[out["amt_vs_med"] >= params.min_amt_vs_med]
    return out


def _is_exit_stress(srow: pd.Series | None, params: Params) -> bool:
    if srow is None:
        return False
    crowd = str(srow.get("crowding_bucket", "") or "")
    pat = str(srow.get("pattern_primary", "") or "")
    shrinking = bool(srow.get("industry_shrinking", False)) or str(srow.get("ind_regime", "")) == "shrink"
    try:
        amt = float(srow.get("amt_vs_med", 0) or 0)
    except (TypeError, ValueError):
        amt = 0.0
    if crowd in ("high", "extreme"):
        return True
    if pat in ("break_down_vol", "climax_volume", "stall_high"):
        return True
    if shrinking:
        return True
    if amt >= params.climax_amt_vs_med:
        return True
    return False


def _is_thesis_failure(
    srow: pd.Series | None, pos: "Position", params: Params
) -> bool:
    if srow is None:
        return False
    pat = str(srow.get("pattern_primary", "") or "")
    crowd = str(srow.get("crowding_bucket", "") or "")
    if pat == "break_down_vol":
        return True
    if pat in ("climax_volume", "stall_high") and crowd in ("high", "extreme"):
        return True
    if pos.hold_ok_fail_streak >= params.failure_hold_ok_streak:
        return True
    return False


@dataclass
class Position:
    shares: int
    cost_total: float
    entry_date: pd.Timestamp
    entry_price: float
    reason: str
    peak_close: float = 0.0
    soft_stopped: bool = False
    stall_taken: bool = False
    hold_ok_fail_streak: int = 0


@dataclass
class PortfolioState:
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    trade_id: int = 0
    cooldown_until: dict[str, pd.Timestamp] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# main loop
# ---------------------------------------------------------------------------


def run_portfolio(
    signals: pd.DataFrame,
    bars: pd.DataFrame,
    names: dict[str, str] | None = None,
    params: Params = DEFAULT_PARAMS,
    market: pd.DataFrame | None = None,
) -> dict:
    names = names or {}
    sig = signals.copy()
    sig["date"] = pd.to_datetime(sig["date"])
    if params.enable_sota_factors and "turn_cv_20" not in sig.columns:
        sig = enrich_sota_factors(sig, params.sota_factor_lookback)
    if (params.enable_macd_rank or params.enable_kdj_rank) and "macd_rank_q" not in sig.columns:
        sig = enrich_momentum_rank(sig)
    if params.enable_crowd_traj:
        sig = _enrich_crowd_traj(sig, params)
    elif "crowd_rising" not in sig.columns:
        sig = sig.copy()
        sig["crowd_rising"] = False

    bars = bars.copy()
    bars["date"] = pd.to_datetime(bars["date"])
    px = bars.set_index(["code", "date"])
    sig_idx = sig.set_index(["date", "code"], drop=False)
    breadth = _build_breadth_by_date(sig) if params.enable_risk_on else None
    overlay = _build_risk_on_overlay(market, params, breadth=breadth)

    dates = sorted(sig["date"].unique())
    if len(dates) < 2:
        return {"error": "not enough dates"}

    def day_risk_on(dt) -> tuple[bool, bool]:
        if overlay.empty:
            return False, False
        ts = pd.Timestamp(dt)
        if ts not in overlay.index:
            prior = overlay.index[overlay.index <= ts]
            if len(prior) == 0:
                return False, False
            ts = prior[-1]
        row = overlay.loc[ts]
        return bool(row["risk_on"]), bool(row["risk_on_exposure_ok"])

    core = _filter_opens(sig, params)
    if params.enable_sota_factors and len(core):
        core = apply_sota_score_boost(core, params)
    if len(core):
        core = apply_macd_score_boost(core, params)
        core = apply_kdj_score_boost(core, params)
    if not core.empty:
        core["rank"] = core.groupby("date")["score"].rank(ascending=False, method="first")
        picks = core[core["rank"] <= params.top_n]
    else:
        picks = core

    core_codes_by_date: dict[pd.Timestamp, set[str]] = {}
    if len(core):
        for d, g in core.groupby("date"):
            core_codes_by_date[pd.Timestamp(d)] = set(g["code"].astype(str))

    exit_sig = sig[sig["action"].isin(["reduce", "avoid"])][["date", "code", "action"]]

    state = PortfolioState(cash=float(params.initial_capital))
    trades: list[dict] = []
    daily: list[dict] = []
    pending_sells: list[dict] = []
    pending_buys: list[dict] = []
    prev_equity = float(params.initial_capital)

    def lookup_sig(dt, code: str) -> pd.Series | None:
        try:
            row = sig_idx.loc[(dt, code)]
            if isinstance(row, pd.DataFrame):
                return row.iloc[0]
            return row
        except KeyError:
            return None

    def confirmed(code: str, dt) -> bool:
        if not params.require_entry_confirm or params.entry_confirm_days <= 0:
            return True
        try:
            idx = dates.index(pd.Timestamp(dt))
        except ValueError:
            return False
        need = params.entry_confirm_days
        if idx < need:
            return False
        for j in range(1, need + 1):
            prev = dates[idx - j]
            if str(code) not in core_codes_by_date.get(pd.Timestamp(prev), set()):
                return False
        return True

    def in_cooldown(code: str, dt) -> bool:
        if not params.enable_stop_cooldown:
            return False
        until = state.cooldown_until.get(str(code))
        return until is not None and pd.Timestamp(dt) <= until

    def set_cooldown(code: str, dt, reason: str) -> None:
        if not params.enable_stop_cooldown:
            return
        if reason not in ("stop_loss", "failure_exit"):
            return
        state.cooldown_until[str(code)] = pd.Timestamp(dt) + pd.Timedelta(
            days=int(params.stop_cooldown_days * 1.5)
        )

    def execute_sell(
        exec_date,
        code: str,
        frac: float,
        reason: str,
        slip_bps: float | None = None,
        signal_date=None,
    ) -> None:
        if code not in state.positions:
            return
        try:
            row = px.loc[(code, exec_date)]
        except KeyError:
            return
        raw = float(row["open"])
        if not np.isfinite(raw) or raw <= 0:
            return
        if bool(row.get("is_limit_down", False)):
            return
        sbps = 0.0 if slip_bps is None else float(slip_bps)
        price = _apply_slip(raw, "SELL", sbps)
        pos = state.positions[code]
        shares = pos.shares if frac >= 0.999 else _lot_floor(int(pos.shares * frac), params.lot_size)
        if shares < params.lot_size and frac < 0.999:
            return
        if shares <= 0:
            return
        shares = min(shares, pos.shares)
        amount = shares * price
        fee = _trade_fee(amount, "SELL", params)
        proceeds = amount - fee
        cost_cut = pos.cost_total * (shares / pos.shares)
        realized = proceeds - cost_cut
        hold_days = int((exec_date - pos.entry_date).days)
        state.cash += proceeds
        state.trade_id += 1
        left = pos.shares - shares
        trades.append(
            {
                "trade_id": state.trade_id,
                "date": exec_date,
                "signal_date": signal_date,
                "side": "SELL",
                "code": code,
                "name": names.get(code, ""),
                "shares": shares,
                "price": price,
                "amount": amount,
                "fee": fee,
                "realized_pnl": realized,
                "reason": reason,
                "cash_after": state.cash,
                "position_shares": left,
                "hold_days": hold_days,
                "avg_cost": (pos.cost_total - cost_cut) / left if left else 0.0,
                "slip_bps": sbps,
            }
        )
        set_cooldown(code, exec_date, reason)
        if left <= 0:
            del state.positions[code]
        else:
            pos.shares = left
            pos.cost_total -= cost_cut

    def execute_buy_new(exec_date, order: dict) -> None:
        code = order["code"]
        if code in state.positions:
            return
        if in_cooldown(code, exec_date):
            return
        if len(state.positions) >= params.top_n:
            return
        try:
            row = px.loc[(code, exec_date)]
        except KeyError:
            return
        raw = float(row["open"])
        if not np.isfinite(raw) or raw <= 0 or bool(row.get("is_limit_up", False)):
            return
        sbps = float(order.get("slip_bps", 0.0) or 0.0)
        price = _apply_slip(raw, "BUY", sbps)
        target_notional = min(float(order["target_notional"]), prev_equity * params.max_single_weight)
        budget = min(target_notional, state.cash * 0.995)
        if budget < price * params.lot_size:
            return
        shares = int(budget // (price * params.lot_size)) * params.lot_size
        if shares < params.lot_size:
            return
        amount = shares * price
        fee = _trade_fee(amount, "BUY", params)
        total_out = amount + fee
        if total_out > state.cash:
            fee_rate = (
                float(params.commission_bps + params.transfer_fee_bps) / 10000.0
                if params.enable_realistic_costs
                else float(params.cost_bps) / 10000.0
            )
            shares = int(state.cash / (price * (1 + fee_rate) * params.lot_size)) * params.lot_size
            if shares < params.lot_size:
                return
            amount = shares * price
            fee = _trade_fee(amount, "BUY", params)
            total_out = amount + fee
            if total_out > state.cash:
                return
        state.cash -= total_out
        state.positions[code] = Position(
            shares=shares,
            cost_total=total_out,
            entry_date=exec_date,
            entry_price=price,
            reason=order["reason"],
            peak_close=price,
        )
        state.trade_id += 1
        trades.append(
            {
                "trade_id": state.trade_id,
                "date": exec_date,
                "signal_date": order.get("signal_date"),
                "side": "BUY",
                "code": code,
                "name": names.get(code, ""),
                "shares": shares,
                "price": price,
                "amount": amount,
                "fee": fee,
                "realized_pnl": 0.0,
                "reason": order["reason"],
                "cash_after": state.cash,
                "position_shares": shares,
                "hold_days": 0,
                "avg_cost": total_out / shares,
                "slip_bps": sbps,
            }
        )

    for i, dt in enumerate(dates):
        if i > 0:
            for order in pending_sells:
                execute_sell(
                    dt,
                    order["code"],
                    float(order.get("frac", 1.0)),
                    order["reason"],
                    slip_bps=order.get("slip_bps"),
                    signal_date=order.get("signal_date"),
                )
            for order in pending_buys:
                execute_buy_new(dt, order)

        pending_sells = []
        pending_buys = []

        mv = 0.0
        for code, pos in state.positions.items():
            try:
                close = float(px.loc[(code, dt)]["close"])
            except KeyError:
                close = pos.entry_price
            if not np.isfinite(close) or close <= 0:
                close = pos.entry_price
            mv += pos.shares * close
        equity = state.cash + mv
        daily_pnl = equity - prev_equity
        daily_ret = daily_pnl / prev_equity if prev_equity > 0 else 0.0
        is_risk_on, exposure_ok = day_risk_on(dt)
        risk_on_active = bool(params.enable_risk_on and is_risk_on)
        risk_on_exposure_ok = bool(params.enable_risk_on and exposure_ok)
        daily.append(
            {
                "date": dt,
                "cash": state.cash,
                "market_value": mv,
                "equity": equity,
                "nav": equity / params.initial_capital,
                "n_positions": len(state.positions),
                "daily_pnl": daily_pnl,
                "daily_ret": daily_ret,
                "cash_ratio": state.cash / equity if equity > 0 else 1.0,
                "risk_on": int(risk_on_active),
            }
        )
        prev_equity = equity

        day_exits = exit_sig[exit_sig["date"] == dt]
        planned: dict[str, dict] = {}
        if risk_on_active and params.risk_on_exit_policy:
            tp_pct = params.risk_on_take_profit_pct
            trail_act = params.risk_on_trail_activate_pct
            trail_dd = params.risk_on_trail_drawdown_pct
            disable_tp = bool(params.risk_on_disable_tp)
        else:
            tp_pct = params.take_profit_pct
            trail_act = params.trail_activate_pct
            trail_dd = params.trail_drawdown_pct
            disable_tp = False

        for code, pos in list(state.positions.items()):
            try:
                brow = px.loc[(code, dt)]
                close = float(brow["close"])
            except KeyError:
                close = pos.entry_price
            if not np.isfinite(close) or close <= 0:
                close = pos.entry_price
            pos.peak_close = max(pos.peak_close or pos.entry_price, close)
            pnl_pct = close / pos.entry_price - 1.0 if pos.entry_price > 0 else 0.0
            hold_days = (dt - pos.entry_date).days
            srow = lookup_sig(dt, code)
            hold_ok = bool(srow.get("hold_ok", False)) if srow is not None else False
            if hold_ok:
                pos.hold_ok_fail_streak = 0
            else:
                pos.hold_ok_fail_streak += 1

            if hold_days >= params.min_hold_days_before_stop and pnl_pct <= params.stop_loss_pct:
                planned[code] = {"frac": 1.0, "reason": "stop_loss"}
                continue

            if (
                params.enable_soft_stop
                and not pos.soft_stopped
                and hold_days >= params.min_hold_days_before_stop
                and pnl_pct <= params.soft_stop_pct
                and pnl_pct > params.stop_loss_pct
            ):
                if (not params.soft_stop_require_stress) or _is_exit_stress(srow, params):
                    pos.soft_stopped = True
                    planned[code] = {"frac": params.reduce_frac, "reason": "soft_stop"}
                    continue

            peak_gain = pos.peak_close / pos.entry_price - 1.0 if pos.entry_price > 0 else 0.0
            if (not disable_tp) and pnl_pct >= tp_pct:
                planned[code] = {"frac": 1.0, "reason": "take_profit"}
                continue

            if peak_gain >= trail_act and pos.peak_close > 0:
                if close / pos.peak_close - 1.0 <= -abs(trail_dd):
                    planned[code] = {"frac": 1.0, "reason": "trail_stop"}
                    continue

            if (
                params.enable_stall_take
                and not pos.stall_taken
                and hold_days >= params.stall_take_min_days
                and pnl_pct >= params.stall_take_min_pnl
                and not hold_ok
            ):
                pos.stall_taken = True
                planned[code] = {"frac": params.reduce_frac, "reason": "stall_take"}
                continue

            hit = day_exits[day_exits["code"] == code]
            if not hit.empty:
                action = str(hit.iloc[0]["action"])
                if action == "reduce":
                    planned[code] = {"frac": params.reduce_frac, "reason": "reduce"}
                elif action == "avoid":
                    if params.avoid_mode == "full":
                        planned[code] = {"frac": 1.0, "reason": "avoid"}
                    elif params.avoid_mode == "reduce_half":
                        planned[code] = {"frac": params.reduce_frac, "reason": "avoid_half"}
                    elif params.avoid_mode == "soft" and pnl_pct < 0:
                        planned[code] = {"frac": params.reduce_frac, "reason": "avoid_soft"}
                continue

            if (
                params.enable_failure_exit
                and hold_days >= params.failure_exit_min_days
                and pnl_pct <= params.failure_exit_max_pnl
                and _is_thesis_failure(srow, pos, params)
            ):
                planned[code] = {"frac": 1.0, "reason": "failure_exit"}
                continue

            limit = params.max_hold_days
            can_extend = params.enable_hold_extend and hold_ok
            if can_extend and params.extend_requires_profit:
                can_extend = pnl_pct > 0
            if can_extend:
                limit = params.max_hold_days + params.hold_extend_days
            if risk_on_active and params.risk_on_exit_policy and (can_extend or pnl_pct > 0):
                limit = max(limit, params.max_hold_days + params.risk_on_extra_hold_days)
            if hold_days >= limit:
                planned[code] = {"frac": 1.0, "reason": "max_hold"}

        for code, od in planned.items():
            pending_sells.append(
                {
                    "code": code,
                    "frac": od["frac"],
                    "reason": od["reason"],
                    "slip_bps": _slippage_bps_for_row(lookup_sig(dt, code), params),
                    "signal_date": pd.Timestamp(dt),
                }
            )

        day_picks = (
            picks[picks["date"] == dt].sort_values("score", ascending=False)
            if len(picks)
            else picks
        )
        pos_mult = float(day_picks["position_mult"].median()) if len(day_picks) else 0.0
        if np.isnan(pos_mult):
            pos_mult = 0.0
        day_attack_ok = True
        if len(day_picks) and "attack_mode" in day_picks.columns:
            day_attack_ok = (day_picks["attack_mode"] == "full_attack").any()

        if risk_on_exposure_ok and params.risk_on_exposure and pos_mult > 0:
            pos_mult = min(pos_mult * params.risk_on_exposure_mult, params.risk_on_exposure_cap)
        if pos_mult < params.min_position_mult_to_buy and len(day_picks) == 0:
            continue

        selling = {o["code"] for o in pending_sells if o.get("frac", 1) >= 0.999}
        stay = [c for c in state.positions if c not in selling]
        max_entries = params.max_entries_per_day
        if risk_on_exposure_ok and params.risk_on_exposure:
            max_entries += params.risk_on_extra_entries
        refill_ok = (not params.cash_refill_require_full_attack) or day_attack_ok
        if (
            params.enable_cash_refill
            and refill_ok
            and equity > 0
            and (state.cash / equity) >= params.cash_refill_ratio
            and len(stay) < params.top_n
        ):
            max_entries += params.cash_refill_extra_entries
        slots = min(max(params.top_n - len(stay), 0), max_entries)
        per_slot = (equity * max(pos_mult, 0.0)) / max(params.top_n, 1)

        if pos_mult < params.min_position_mult_to_buy or slots <= 0 or len(day_picks) == 0:
            continue

        ind_map: dict = {}
        if "industry_code" in sig.columns:
            ind_map = (
                sig.loc[sig["date"] == dt, ["code", "industry_code"]]
                .drop_duplicates("code")
                .set_index("code")["industry_code"]
                .to_dict()
            )
        ind_held: dict = {}
        for c in stay:
            ind = ind_map.get(c)
            if ind is not None:
                ind_held[ind] = ind_held.get(ind, 0) + 1

        bought = 0
        for _, row in day_picks.iterrows():
            if bought >= slots:
                break
            code = str(row["code"])
            if code in state.positions and code not in selling:
                continue
            if in_cooldown(code, dt):
                continue
            if not confirmed(code, dt):
                continue
            if bool(row.get("crowd_traj_entry", False)) and not risk_on_active:
                continue
            ind = row["industry_code"] if "industry_code" in row.index else None
            if ind is not None and ind_held.get(ind, 0) >= 1:
                continue
            pending_buys.append(
                {
                    "code": code,
                    "reason": str(row["action"]),
                    "target_notional": per_slot,
                    "slip_bps": _slippage_bps_for_row(row, params),
                    "signal_date": pd.Timestamp(dt),
                }
            )
            bought += 1
            if ind is not None:
                ind_held[ind] = ind_held.get(ind, 0) + 1

    # Flush last signal day's pending on next bar session
    if pending_sells or pending_buys:
        last = pd.Timestamp(dates[-1])
        bar_dates = sorted(pd.to_datetime(bars["date"]).unique())
        nxt = [d for d in bar_dates if pd.Timestamp(d) > last]
        if nxt:
            flush_dt = pd.Timestamp(nxt[0])
            for order in pending_sells:
                execute_sell(
                    flush_dt,
                    order["code"],
                    float(order.get("frac", 1.0)),
                    order["reason"],
                    slip_bps=order.get("slip_bps"),
                    signal_date=order.get("signal_date"),
                )
            for order in pending_buys:
                execute_buy_new(flush_dt, order)
            mv = 0.0
            for code, pos in list(state.positions.items()):
                try:
                    close = float(px.loc[(code, flush_dt), "close"])
                except KeyError:
                    continue
                if not np.isfinite(close) or close <= 0:
                    continue
                mv += pos.shares * close
                pos.peak_close = max(pos.peak_close, close)
            equity = state.cash + mv
            daily_ret = equity / prev_equity - 1.0 if prev_equity > 0 else 0.0
            daily.append(
                {
                    "date": flush_dt,
                    "cash": state.cash,
                    "market_value": mv,
                    "equity": equity,
                    "nav": equity / params.initial_capital,
                    "n_positions": len(state.positions),
                    "daily_pnl": equity - prev_equity,
                    "daily_ret": daily_ret,
                    "cash_ratio": state.cash / equity if equity > 0 else 1.0,
                    "risk_on": 0,
                    "flush_t1": 1,
                }
            )

    return {
        "trades": pd.DataFrame(trades),
        "daily": pd.DataFrame(daily),
        "initial_capital": params.initial_capital,
        "ending_equity": float(daily[-1]["equity"]) if daily else params.initial_capital,
        "ending_cash": float(daily[-1]["cash"]) if daily else params.initial_capital,
        "ending_market_value": float(daily[-1]["market_value"]) if daily else 0.0,
    }
