"""Run ready dependency tasks in isolated processes under one publication lock."""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from contextlib import ExitStack
from datetime import datetime
import multiprocessing
import os
from time import perf_counter
from uuid import uuid4

from ..paths import STATE_ROOT
from ..storage import atomic_json, read_json, writer_lock
from .handlers import execute
from .planner import PlanningContext, build_plan, task_paths
from .receipts import fingerprint, task_state_path
from .tasks import select_tasks


def _configure_worker_logging():
    import logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')


def _execute_task(name, config, codes, workers, force):
    # Each process owns its source SDK sessions. The parent owns run/task state.
    started = perf_counter()
    try:
        detail = execute(name, config, codes, workers, force)
        return {'status': 'success', 'detail': detail,
                'seconds': round(perf_counter() - started, 3)}
    except Exception as exc:
        if name=='announcements' and config.get('_ready_queue') is not None:
            config['_ready_queue'].put({'end':True,'error':str(exc)})
        return {'status': 'failed', 'error': str(exc),
                'seconds': round(perf_counter() - started, 3)}


def run_update(config, *, names=None, codes=None, workers=4, force=False, parallel_tasks=4):
    if workers < 1 or parallel_tasks < 1:
        raise ValueError('workers and parallel_tasks must be positive')
    target = config['end_date']
    tasks = select_tasks(names)
    batch = datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid4().hex[:8]
    results, done, failed = [], set(), set()
    pending = {task.name: task for task in tasks}
    running = {}
    started = perf_counter()
    report = {'batch_id': batch, 'target': target, 'results': results,
              'status': 'running', 'pid': os.getpid(),
              'parallel_tasks': parallel_tasks, 'workers_per_task': workers,
              'started_at': datetime.now().isoformat()}
    report_path = STATE_ROOT / 'runs' / f'{batch}.json'

    def publish(launching=None):
        active = [task.name for task, _ in running.values()]
        if launching is not None:
            active.append(launching)
        report['current_tasks'] = active
        if active:
            report['current_task'] = active[0]
        else:
            report.pop('current_task', None)
        atomic_json(report, report_path)

    def finish(task, result):
        done.add(task.name)
        if result['status'] in ('failed', 'blocked'):
            failed.add(task.name)
        results.append({'task': task.name, **result})
        if result['status'] != 'skipped':
            print(f"[data] {task.name} {result['status']} {result.get('seconds', 0)}s", flush=True)
        publish()

    with writer_lock(STATE_ROOT / 'data_update.lock'):
        context = PlanningContext()
        publish()
        # Spawn isolates global SDK sockets and nested per-security executors.
        # No processes are started when every task is skipped.
        channel=None
        with ExitStack() as stack:
            pool=stack.enter_context(ProcessPoolExecutor(max_workers=parallel_tasks, initializer=_configure_worker_logging,
                                 mp_context=multiprocessing.get_context('spawn')))
            while pending or running:
                progressed = False
                candidates=list(pending.values())
                if channel is not None:
                    candidates.sort(key=lambda task:task.name!='financials')
                for task in candidates:
                    handoff=task.name=='financials' and channel is not None
                    dependencies=set(task.dependencies)-({'announcements'} if handoff else set())
                    if any(dep in failed for dep in dependencies):
                        del pending[task.name]
                        progressed = True
                        finish(task, {'status': 'blocked', 'seconds': 0})
                        continue
                    if not dependencies <= done:
                        continue
                    # Catalog sees all requested publications, including optional
                    # sources without a direct edge in the core registry.
                    if task.name == 'catalog':
                        if running or len(pending) != 1:
                            continue
                        if failed:
                            del pending[task.name]
                            progressed = True
                            finish(task, {'status': 'blocked', 'seconds': 0})
                            continue
                    if running:
                        context.invalidate()
                    item = next(i for i in build_plan(
                        target, names=[task.name], config=config, codes=codes, force=force,
                        only_task=task.name, context=context) if i['task'] == task.name)
                    if item['action'] == 'skip' and not handoff:
                        del pending[task.name]
                        progressed = True
                        finish(task, {'status': 'skipped', 'seconds': 0})
                        continue
                    resources = set(getattr(task, 'resources', ()))
                    busy = {resource for active, _ in running.values()
                            for resource in getattr(active, 'resources', ())}
                    if len(running) >= parallel_tasks or resources & busy:
                        continue
                    del pending[task.name]
                    progressed = True
                    context.invalidate()
                    print(f'[data] {task.name} starting', flush=True)
                    publish(launching=task.name)
                    runtime_config=config
                    if task.name=='announcements' and 'financials' in pending and parallel_tasks>1:
                        manager=stack.enter_context(multiprocessing.Manager())
                        channel=manager.Queue()
                        runtime_config={**config,'_ready_queue':channel}
                    elif handoff:
                        runtime_config={**config,'_ready_queue':channel}
                        item['_handoff']=True
                    future = pool.submit(_execute_task, task.name, runtime_config, codes, workers, force)
                    running[future] = (task, item)
                if not running:
                    if pending and progressed:
                        continue
                    if pending:
                        raise RuntimeError(f'No ready tasks; invalid dependencies: {list(pending)}')
                    break
                completed, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in completed:
                    task, item = running.pop(future)
                    context.invalidate()
                    response = {}
                    try:
                        response = future.result()
                        if response['status'] != 'success':
                            raise RuntimeError(response['error'])
                        if item.get('_handoff'):
                            # Producer has published all handoff inputs. Never
                            # stamp the moving input snapshot seen at launch.
                            item=next(i for i in build_plan(target,names=[task.name],config=config,
                                codes=codes,only_task=task.name,context=context) if i['task']==task.name)
                        _, outputs = task_paths(task)
                        if not outputs:
                            raise RuntimeError(f'{task.name}: no output files')
                        state = {'status': 'success', 'checked_through': target,
                                 'input': item['input'], 'output': fingerprint(outputs),
                                 'batch': batch, 'finished_at': datetime.now().isoformat()}
                        atomic_json(state, task_state_path(STATE_ROOT, task.name, codes))
                        result = {'status': 'success', 'seconds': response['seconds'],
                                  **response['detail']}
                    except Exception as exc:
                        if task.name=='announcements' and channel is not None:
                            channel.put({'end':True,'error':str(exc)})
                        result = {'status': 'failed', 'error': str(exc),
                                  'seconds': response.get('seconds', 0)}
                        path = task_state_path(STATE_ROOT, task.name, codes)
                        previous = read_json(path, {})
                        atomic_json({**previous, 'status': 'failed', 'last_error': str(exc),
                                     'failed_batch': batch}, path)
                    finish(task, result)
        report['finished_at'] = datetime.now().isoformat()
        report['seconds'] = round(perf_counter() - started, 3)
        report['status'] = 'failed' if failed else 'success'
        publish()
    return report
