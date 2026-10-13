"""Live catch-up acceptance: update, retry failures, repeat, then audit."""
from __future__ import annotations

from datetime import datetime
from time import perf_counter

from ..paths import DATA_ROOT, STATE_ROOT
from ..storage import atomic_json, read_json, writer_lock
from ..research.validation import validate_dataset, build_manifest
from .runner import run_update


def run_acceptance(config, *, workers=4, parallel_tasks=4):
    with writer_lock(STATE_ROOT/'data_acceptance.lock'):
        return _run_acceptance(config,workers=workers,parallel_tasks=parallel_tasks)


def _run_acceptance(config, *, workers, parallel_tasks):
    path = STATE_ROOT/'acceptance.json'
    started = perf_counter()
    report = {'target':config['end_date'],'started_at':datetime.now().isoformat(),
              'status':'running','phase':'update','runs':[]}
    atomic_json(report,path)
    try:
        for attempt in range(2):
            result = run_update(config,workers=workers,parallel_tasks=parallel_tasks)
            report['runs'].append({'batch_id':result['batch_id'],'status':result['status'],
                                  'seconds':result['seconds'],
                                  'failed_tasks':[r['task'] for r in result['results'] if r['status'] in ('failed','blocked')]})
            atomic_json(report,path)
            if result['status']=='success':
                break
        if result['status']=='success':
            report['phase']='repeat'
            atomic_json(report,path)
            repeat = run_update(config,workers=workers,parallel_tasks=parallel_tasks)
            report['repeat']={'batch_id':repeat['batch_id'],'seconds':repeat['seconds'],
                              'status':repeat['status'],
                              'all_skipped':repeat['status']=='success' and bool(repeat['results']) and all(r['status']=='skipped' for r in repeat['results'])}
        report['phase']='audit'
        atomic_json(report,path)
        with writer_lock(STATE_ROOT/'data_update.lock'):
            validate_dataset(config)
            build_manifest(config)
        quality = read_json(DATA_ROOT/'quality_report.json',{})
        report['quality_status']=quality.get('status')
        report['issues']=quality.get('issues',[])
        report['status']=('pass' if result['status']=='success'
                          and report.get('repeat',{}).get('all_skipped')
                          and quality.get('status')=='pass' else 'review')
        report['phase']='finished'
    except BaseException as exc:
        report['status']='interrupted' if isinstance(exc,(KeyboardInterrupt,SystemExit)) else 'failed'
        report['error']=str(exc) or type(exc).__name__
        raise
    finally:
        report['seconds']=round(perf_counter()-started,3)
        report['finished_at']=datetime.now().isoformat()
        atomic_json(report,path)
    return report
