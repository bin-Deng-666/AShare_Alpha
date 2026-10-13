"""Rebuild only securities whose upstream data or calculation code changed."""
from __future__ import annotations
from ..pipeline.identity import implementation_signature, semantic_config

from ..paths import PARQUET_ROOT as PARQUET, STATE_ROOT as STATE
from ..pipeline.receipts import BuildReceipts, fingerprint


def rebuild_changed(name, builder, config, codes, inputs, output, *, force=False):
    receipts = BuildReceipts(STATE / 'builds' / f'{name}.json')
    version=implementation_signature(name)
    import json
    version+=json.dumps(semantic_config(name,config),sort_keys=True,default=str)
    changed = []
    signatures = {}
    for code in codes:
        source_files = [PARQUET / pattern.format(code=code) for pattern in inputs]
        signature = version + fingerprint(source_files)
        target = PARQUET / output.format(code=code)
        # Historical bounded rebuilds cannot reuse a result built through a later date.
        old_end = receipts.entries.get(code, {}).get('end_date')
        backwards = bool(old_end and config.get('end_date') and config['end_date'] < old_end)
        if not force and not backwards and receipts.current(code, signature, [target]):
            continue
        signatures[code] = signature
        changed.append(code)
    if changed:
        completed = set()
        def completed_code(code):
            target = PARQUET / output.format(code=code)
            if code not in signatures or not target.exists():
                raise RuntimeError(f'{name}: invalid completion for {code}')
            completed.add(code)
            receipts.record(code, signatures[code], [target], flush=False)
            receipts.entries[code]['end_date'] = config.get('end_date')
            if len(completed) % 100 == 0:
                receipts.flush()
        try:
            builder(config, codes=changed, force=True, on_code_done=completed_code)
        finally:
            receipts.flush()
        missing = set(changed) - completed
        if missing:
            raise RuntimeError(f'{name}: {len(missing)} 个证券没有成功产物，未推进状态: {sorted(missing)[:10]}')
    return {'processed': len(changed), 'skipped': len(codes) - len(changed), 'changed_codes': changed}
