"""Measure an unchanged incremental run using real files and temporary receipts.

All source handlers are replaced with a failure guard; this benchmark cannot
make network requests or modify market data. Temporary state models a previous
successful update, without claiming the real dataset has passed quality checks.
"""
from __future__ import annotations
import argparse
import contextlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch

from data.ashare_data.pipeline import planner, runner
from data.ashare_data.pipeline.tasks import select_tasks
from data.ashare_data.pipeline.receipts import fingerprint, task_state_path
from data.ashare_data.runtime import load_config
from data.ashare_data.storage import atomic_json, writer_lock
from data.ashare_data.paths import STATE_ROOT


def forbidden_source(*args):
    return {'status':'failed','error':'benchmark source execution forbidden','seconds':0}


def benchmark(target):
    # Fingerprints must describe one unchanged dataset. A live publisher makes
    # a no-change benchmark invalid, and child processes need their own guard.
    with writer_lock(STATE_ROOT/'data_update.lock'):
        return _benchmark(target)


def _benchmark(target):
    config = {**load_config(), 'end_date': target, 'derive_qfq': True}
    tasks = select_tasks()
    with TemporaryDirectory() as directory:
        root = Path(directory)
        with patch.object(planner, 'STATE_ROOT', root), patch.object(runner, 'STATE_ROOT', root):
            for task in tasks:
                item = next(i for i in planner.build_plan(target, config=config, only_task=task.name)
                            if i['task'] == task.name)
                outputs = planner.task_paths(task)[1]
                atomic_json({'status': 'success', 'input': item['input'],
                             'output': fingerprint(outputs), 'checked_through': target},
                            task_state_path(root, task.name))
            tick = perf_counter()
            with patch.object(runner, '_execute_task', forbidden_source):
                with contextlib.redirect_stdout(io.StringIO()):
                    result = runner.run_update(config)
            elapsed = perf_counter() - tick
            assert all(item['status'] == 'skipped' for item in result['results']), result
    return {'tasks': len(tasks), 'seconds': elapsed, 'source_calls': 0, 'all_skipped': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', default='2026-07-24')
    args = parser.parse_args()
    print(json.dumps(benchmark(args.target), indent=2))


if __name__ == '__main__':
    main()
