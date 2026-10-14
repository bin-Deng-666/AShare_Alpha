"""Market event catch-up: detect changed issuers, refresh their complete history.

Full issuer replacement handles corrected dates, cancelled plans and deletions
within a changed issuer. Periodic full-market checks reconcile unannounced edits.
"""

from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
import hashlib
import threading

import pandas as pd
import requests

from ..paths import PARQUET_ROOT, STATE_ROOT
from ..runtime import UA, Throttle, retry
from ..storage import atomic_json, atomic_parquet, read_json
from ..pipeline.receipts import fingerprint


LAYERS = {
    "distributions": ("corporate_actions", "ex_date", "corporate_action_failures.json"),
    "equity_structure": (
        "equity_structure",
        "end_date",
        "equity_structure_coverage.json",
    ),
}


def market_receipt_path(name, codes=None):
    root = STATE_ROOT / "fetch_receipts" / "markets"
    if codes is None:
        return root / f"{name}.json"
    token = hashlib.sha256(",".join(sorted(set(codes))).encode()).hexdigest()[:16]
    return root / "subsets" / token / f"{name}.json"


def canonical_detector(frame, master):
    """Recognize a legacy BJ suffix only when issuer names independently agree."""
    if frame.empty or "name" not in frame or "name" not in master:
        return frame
    import re

    def label(value):
        if pd.isna(value):
            return None
        return re.sub(r"^(?:\*?ST|N|C)", "", str(value).strip())

    codes = set(master.code.astype(str))
    names = {
        str(row.code): {label(row.name), label(getattr(row, "cninfo_name", None))}
        - {None}
        for row in master.itertuples(index=False)
        if str(row.code).startswith("920")
    }
    aliases = {}
    for raw_code, group in (
        frame[["code", "name"]].drop_duplicates().groupby("code", sort=False)
    ):
        code = str(raw_code).zfill(6)
        candidate = "920" + code[-3:]
        observed = {label(value) for value in group["name"]}
        if (
            code not in codes
            and code.startswith(("4", "8"))
            and observed
            and observed <= names.get(candidate, set())
        ):
            aliases[code] = candidate
    if aliases:
        frame = frame.copy()
        frame["code"] = frame.code.astype(str).str.zfill(6).replace(aliases)
    return frame


def changed_detector_codes(frame, directory, keys):
    """Ignore detector rows already present with identical normalized values."""
    changed = set()
    for code, part in frame.groupby("code", sort=False):
        code = str(code).zfill(6)
        path = directory / f"{code}.parquet"
        if not path.exists():
            changed.add(code)
            continue
        old = pd.read_parquet(path)
        if old.empty or not set(keys) <= set(old) or not set(part) <= set(old):
            changed.add(code)
            continue
        observed = part.copy()
        observed["code"] = observed.code.astype(str).str.zfill(6)
        reference = old[list(observed)].copy()
        reference["_present"] = True
        joined = observed.merge(
            reference, on=keys, how="left", suffixes=("_new", "_old")
        )
        equal = joined["_present"].notna()
        for column in set(observed) - set(keys):
            a, b = joined[column + "_new"], joined[column + "_old"]
            equal &= a.eq(b) | (a.isna() & b.isna())
        if not equal.all():
            changed.add(code)
    return changed


def refresh_market_events(name, fetch, config, *, codes=None, force=False, workers=4):
    layer, event_date, report_name = LAYERS[name]
    if (
        int(config.get("event_overlap_days", 7)) < 0
        or int(config.get("event_full_refresh_days", 30)) < 1
    ):
        raise ValueError(
            "Event overlap must be nonnegative and full-refresh interval must be positive"
        )
    target = str(pd.Timestamp(config.get("end_date") or date.today()).date())
    directory = PARQUET_ROOT / layer
    master = pd.read_parquet(PARQUET_ROOT / "security_master/security_master.parquet")
    selected = master.code.astype(str).tolist() if codes is None else sorted(set(codes))
    if not set(selected) <= set(master.code.astype(str)):
        raise ValueError("Unknown securities in market-event selection")
    if not selected:
        return {"processed": 0, "http_requests": 0, "mode": "empty_selection"}
    path = market_receipt_path(name, codes)
    previous = read_json(path, {})
    # This source version is independent of scheduler, retries and worker count.
    import inspect

    version = hashlib.sha256(inspect.getsource(fetch).encode()).hexdigest()
    full_date = previous.get("full_checked_through")
    full_due = not full_date or not 0 <= (
        pd.Timestamp(target) - pd.Timestamp(full_date)
    ).days < int(config.get("event_full_refresh_days", 30))
    compatible = previous.get("source_version") == version
    checked = previous.get("checked_through") if compatible else None
    full = force or not compatible or not checked or target < checked or full_due
    requested_from = config.get("refresh_from")
    if requested_from:
        # Explicit repair is a full reconciliation, never a narrow detector.
        full = True
    throttle = Throttle(config["eastmoney_min_interval_seconds"])
    sessions = threading.local()
    request_lock = threading.Lock()
    requests_made = [0]

    class CountingSession:
        def __init__(self):
            self.inner = requests.Session()
            self.headers = self.inner.headers
            self.received_rows = 0

        def get(self, *args, **kwargs):
            with request_lock:
                requests_made[0] += 1
            response = self.inner.get(*args, **kwargs)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("Invalid market-event response object")
            if payload.get("success") is True:
                result = payload.get("result")
                if (
                    not isinstance(result, dict)
                    or "count" not in result
                    or "data" not in result
                ):
                    raise RuntimeError(
                        "Market-event success response lacks pagination evidence"
                    )
                count = int(result["count"])
                items = result["data"]
                if items is None and count == 0:
                    items = []
                if (
                    count < 0
                    or not isinstance(items, list)
                    or any(not isinstance(row, dict) for row in items)
                ):
                    raise RuntimeError("Invalid market-event rows or count")
                if items:
                    params = kwargs.get("params", {})
                    raw_dates = ["END_DATE"] if name == "equity_structure" else []
                    raw_dates.append(
                        "NOTICE_DATE"
                        if "NOTICE_DATE>=" in params.get("filter", "")
                        else (
                            "END_DATE"
                            if name == "equity_structure"
                            else "EX_DIVIDEND_DATE"
                        )
                    )
                    for raw_date in set(raw_dates):
                        values = pd.to_datetime(
                            pd.Series([row.get(raw_date) for row in items]),
                            errors="coerce",
                        )
                        if values.isna().any():
                            raise RuntimeError(
                                f"Market-event source rows lack valid {raw_date}"
                            )
                    if any(row.get("SECURITY_CODE") is None for row in items):
                        raise RuntimeError(
                            "Market-event source rows lack SECURITY_CODE"
                        )
                self.received_rows += len(items)
            return response

    def session():
        if not hasattr(sessions, "value"):
            sessions.value = CountingSession()
            sessions.value.headers.update(
                {"User-Agent": UA, "Referer": "https://data.eastmoney.com/"}
            )
        return sessions.value

    def query(code=None, field=None, lower=None):
        def attempt():
            source = session()
            before = source.received_rows
            frame = fetch(
                source,
                code,
                throttle,
                data_cutoff=target,
                refresh_from=lower,
                date_field=field,
            )
            if frame.empty:
                if source.received_rows > before:
                    raise RuntimeError(
                        "Nonempty source rows normalized to an empty event history"
                    )
                return frame
            required = ["code", field or event_date]
            if any(
                column not in frame or frame[column].isna().any() for column in required
            ):
                raise RuntimeError("Market-event rows lack security or required date")
            observed_codes = frame.code.astype(str).str.zfill(6)
            if frame.code.astype(str).str.strip().eq("").any() or (
                code is not None and not observed_codes.eq(code).all()
            ):
                raise RuntimeError(
                    "Market-event response contains an invalid or unexpected security"
                )
            return frame

        return retry(
            attempt,
            f"{name} {code or 'market'} {field or 'history'}",
            attempts=config["retries"],
            base_sleep=config["retry_base_seconds"],
        )

    outputs = {code: fingerprint([directory / f"{code}.parquet"]) for code in selected}
    valid_outputs = previous.get("outputs", {})
    lower = None
    if full:
        groups = None
        candidates = selected
        if codes is None:
            frame = query()
            if frame.empty:
                raise RuntimeError(
                    "Empty full-market event response; previous assets retained"
                )
            frame = frame.copy()
            frame["code"] = frame.code.astype(str).str.zfill(6)
            if not frame.code.isin(selected).any():
                raise RuntimeError(
                    "Full market-event response contains no registered securities"
                )
            groups = {
                code: part.reset_index(drop=True)
                for code, part in frame.groupby("code", sort=False)
            }
    else:
        lower = str(
            max(
                pd.Timestamp(config["start_date"]),
                pd.Timestamp(checked)
                - pd.Timedelta(days=int(config.get("event_overlap_days", 7))),
            ).date()
        )
        # Event date also catches scheduled events whose notice was much earlier.
        notices = query(field="notice_date", lower=lower)
        effective = query(field=event_date, lower=lower)
        detected = set()
        keys = (
            ["code", "end_date"]
            if name == "equity_structure"
            else ["code", "report_date", "ex_date", "plan_description"]
        )
        for frame in (notices, effective):
            if not frame.empty:
                detected.update(
                    changed_detector_codes(
                        canonical_detector(frame, master), directory, keys
                    )
                )
        candidates = sorted(
            (detected & set(selected))
            | {
                code
                for code in selected
                if outputs[code] != valid_outputs.get(code)
                or not (directory / f"{code}.parquet").exists()
            }
        )
        groups = {}
    # Resume actual completed issuer responses from this target, including a
    # crash between per-file publication and final market receipt publication.
    resume = previous.get("progress", {}) if compatible and not force else {}
    completed = {
        code
        for code in candidates
        if resume.get(code, {}).get("target") == target
        and resume[code].get("output") == fingerprint([directory / f"{code}.parquet"])
        and (directory / f"{code}.parquet").exists()
    }
    progress = {code: resume[code] for code in completed}
    changed = []
    failures = []
    result = {
        "mode": "full" if full else "incremental",
        "window_from": lower,
        "target": target,
        "files": len(selected),
        "processed": len(candidates) - len(completed),
        "reused": len(completed),
        "detected_codes": len(candidates),
        "changed_codes": changed,
        "failures": failures,
        "queries": 1 if full and codes is None else (0 if full else 2),
    }
    interim = {
        **previous,
        "source_version": version,
        "progress": progress,
        "status": "running",
    }
    atomic_json(interim, path)

    def publish(code, frame):
        target_path = directory / f"{code}.parquet"
        same = False
        if target_path.exists():
            old = pd.read_parquet(target_path).reset_index(drop=True)
            fresh = frame.reset_index(drop=True)
            # A single issuer often has integral cash terms whereas the market
            # batch has floats. Equal values must not trigger downstream rebuilds.
            if old.shape == fresh.shape and old.columns.equals(fresh.columns):
                same = all(
                    (
                        old[column].eq(fresh[column])
                        | (old[column].isna() & fresh[column].isna())
                    ).all()
                    for column in old
                )
        if not same and atomic_parquet(frame, target_path):
            changed.append(code)
        progress[code] = {
            "target": target,
            "output": fingerprint([directory / f"{code}.parquet"]),
        }
        if len(progress) % 25 == 0:
            atomic_json(interim, path)

    pending = [code for code in candidates if code not in completed]
    if full and groups is not None:
        for code in pending:
            publish(code, groups.get(code, frame.iloc[:0].copy()))
    else:
        # The shared throttle imposes one provider budget across issuer workers.
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(query, code): code for code in pending}
            result["queries"] += len(futures)
            for future in as_completed(futures):
                code = futures[future]
                try:
                    publish(code, future.result())
                except Exception as exc:
                    failures.append({"code": code, "error": str(exc)})
    result["changed_codes"] = sorted(changed)
    result["http_requests"] = requests_made[0]
    if failures:
        atomic_json({**interim, "status": "failed", "failures": failures}, path)
        atomic_json(result, STATE_ROOT / report_name)
        raise RuntimeError(
            f"{name}: {len(failures)} issuer histories failed; successful issuers can resume"
        )
    outputs = {code: fingerprint([directory / f"{code}.parquet"]) for code in selected}
    state = {
        "status": "success",
        "checked_through": target,
        "source_version": version,
        "full_checked_through": target if full else full_date,
        "outputs": outputs,
        "progress": {},
        "last_work": result,
    }
    atomic_json(state, path)
    atomic_json(
        {
            **result,
            "failures": [],
            "physical_read_cutoff": target,
            "completed_codes": selected,
            "output_fingerprint": fingerprint(
                [directory / f"{code}.parquet" for code in selected]
            ),
            "updated_at": pd.Timestamp.now(),
        },
        STATE_ROOT / report_name,
    )
    return result
