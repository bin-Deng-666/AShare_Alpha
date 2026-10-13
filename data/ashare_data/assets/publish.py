"""Distribute a complete market response into canonical per-security files."""
from __future__ import annotations

from pathlib import Path
import pandas as pd

from ..storage import atomic_parquet


def publish_by_security(frame: pd.DataFrame, codes: list[str], directory: Path, on_code_done=None) -> dict:
    # Empty full-market responses are not evidence that all issuers have no events.
    if frame.empty:
        raise RuntimeError('全市场源返回空结果，保留已有文件，更新失败')
    frame = frame.copy()
    frame['code'] = frame['code'].astype(str).str.zfill(6)
    groups = {code: part.reset_index(drop=True) for code, part in frame.groupby('code', sort=False)}
    changed = []
    for code in codes:
        part = groups.get(code, frame.iloc[:0].copy())
        if atomic_parquet(part, directory / f'{code}.parquet'):
            changed.append(code)
        if on_code_done:
            on_code_done(code)
    return {'files': len(codes), 'changed_codes': changed, 'rows': len(frame)}
