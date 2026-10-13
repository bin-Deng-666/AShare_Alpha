"""Read update progress and quality independently of strategy or WebUI code."""
from __future__ import annotations

import fcntl
from pathlib import Path

from ..paths import DATA_ROOT, STATE_ROOT
from ..storage import read_json
from .tasks import TASKS
from .receipts import task_state_path


def writer_active(root=STATE_ROOT):
    path = Path(root) / 'data_update.lock'
    if not path.exists():
        return False
    with path.open('rb') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        else:
            fcntl.flock(stream, fcntl.LOCK_UN)
            return False


def read_status(*, state_root=STATE_ROOT, data_root=DATA_ROOT):
    state_root, data_root = Path(state_root), Path(data_root)
    active = writer_active(state_root)
    paths = sorted((state_root/'runs').glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)
    latest = read_json(paths[0],{}) if paths else None
    if latest and latest.get('status') == 'running' and not active:
        latest = {**latest,'status':'interrupted'}
    quality_path = data_root/'quality_report.json'
    quality = read_json(quality_path,{})
    # Any source publication after an audit makes that audit historical.
    quality_current = bool(quality_path.exists() and not active and
                           (not paths or quality_path.stat().st_mtime >= paths[0].stat().st_mtime))
    tasks = []
    for task in TASKS:
        receipt = read_json(task_state_path(state_root,task.name),{})
        tasks.append({'task':task.name,'status':receipt.get('status','never_run'),
                      'checked_through':receipt.get('checked_through'),
                      'last_error':receipt.get('last_error')})
    return {'writer_active':active,'latest_run':latest,'tasks':tasks,
            'quality':{'status':quality.get('status','not_audited'),
                       'generated_at':quality.get('generated_at'),
                       'current_for_latest_run':quality_current,
                       'security_count':quality.get('security_count'),
                       'bar_max_date':quality.get('bar_max_date'),
                       'issues':quality.get('issues',[])},
            'acceptance':read_json(state_root/'acceptance.json',None)}
