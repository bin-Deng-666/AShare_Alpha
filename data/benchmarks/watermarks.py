"""Compare date-watermark scans on real Parquet files without modifying them."""
from __future__ import annotations
import argparse
import json
from time import perf_counter
import pandas as pd
from data.ashare_data.paths import PARQUET_ROOT
from data.ashare_data.parquet_metadata import latest_date


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--limit',type=int,default=1000)
    args=p.parse_args()
    files=sorted((PARQUET_ROOT/'daily_bars/adjustment=raw').glob('*.parquet'))[:args.limit]
    tick=perf_counter()
    old=[pd.to_datetime(pd.read_parquet(f,columns=['date'])['date'],errors='coerce').max().normalize() for f in files]
    scan=perf_counter()-tick
    tick=perf_counter()
    new=[latest_date(f) for f in files]
    footer=perf_counter()-tick
    tick=perf_counter()
    cached=[latest_date(f) for f in files]
    cache=perf_counter()-tick
    assert all(a==b or (pd.isna(a) and pd.isna(b)) for a,b in zip(old,new))
    assert new==cached
    print(json.dumps({'files':len(files),'results_equal':True,'read_column_seconds':scan,
                      'footer_seconds':footer,'cached_seconds':cache,'footer_speedup':scan/footer,
                      'cached_speedup':scan/cache},indent=2))

if __name__=='__main__': main()
