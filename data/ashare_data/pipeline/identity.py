"""Task-specific implementation and semantic configuration identities.

Declare the files actually used by each domain, including shared formula rules.
Scheduler and request-budget changes do not invalidate previously checked facts.
"""
from __future__ import annotations
import ast
import hashlib
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
COMMON = ('storage.py', 'paths.py', 'incremental_files.py', 'parquet_metadata.py')
MODULES = {
    'securities': ('sources/securities.py',),
    'calendar': ('sources/reference.py',),
    'prices': ('sources/prices.py', 'calendar.py'),
    'indicators': ('sources/indicators.py', 'calendar.py'),
    'distributions': ('sources/corporate_actions.py', 'sources/market_events.py', 'assets/publish.py'),
    'corporate_events': ('sources/corporate_actions.py', 'sources/indicators.py', 'rules/corporate_actions.py', 'rules/prices.py'),
    'equity_structure': ('sources/equity_structure.py', 'sources/market_events.py', 'assets/publish.py'),
    'announcements': ('sources/announcements.py', 'sources/fetch_windows.py', 'pipeline/source_receipts.py'),
    'financials': ('sources/financial_statements.py', 'sources/fetch_windows.py', 'pipeline/source_receipts.py'),
    'availability': ('research/financial_availability.py',),
    'beijing_valuation': ('sources/indicators.py', 'assets/beijing_valuation.py'),
    'adjustment': ('assets/adjustment_limits.py', 'assets/incremental.py', 'rules/corporate_actions.py', 'rules/prices.py'),
    'limits': ('assets/adjustment_limits.py', 'assets/incremental.py', 'rules/corporate_actions.py', 'rules/prices.py'),
    'shares': ('assets/shares.py', 'assets/incremental.py'),
    'references': ('sources/reference.py',),
    'catalog': ('research/catalog.py', 'research/coverage.py'),
    'connect_margin': ('sources/connect_margin.py',),
    'disclosure_trades': ('sources/disclosure_trades.py', 'sources/monthly_disclosures.py'),
    'block_trades': ('sources/block_trades.py', 'sources/monthly_disclosures.py'),
    'holder_changes': ('sources/holder_changes.py',),
    'cash_yield': ('sources/cash_yield.py',),
}
CONFIG_KEYS = {
    'securities': ('include_current', 'include_delisted'),
    'prices': ('bar_adjustments', 'derive_qfq'),
    'financials': ('financial_periods',),
    'distributions': ('event_overlap_days', 'event_full_refresh_days'),
    'equity_structure': ('event_overlap_days', 'event_full_refresh_days'),
}


def source_implementation_signature(name, *, package=PACKAGE):
    # Successful facts depend on their source/parser/window rules. Queue handling,
    # orchestration and downstream publication are separate task identities.
    module='financial_statements' if name=='financials' else name
    paths=('storage.py','incremental_files.py','sources/fetch_windows.py',f'sources/{module}.py')
    digest=hashlib.sha256()
    for relative in paths:
        digest.update(relative.encode())
        digest.update((package/relative).read_bytes())
    return digest.hexdigest()


def semantic_config(name, config):
    keys = ('start_date',) + CONFIG_KEYS.get(name, ())
    return {key: config[key] for key in keys if key in config}


def _matches(test, name):
    if not isinstance(test, ast.Compare) or not isinstance(test.left, ast.Name) or test.left.id != 'name':
        return False
    value = ast.literal_eval(test.comparators[0])
    return name == value if isinstance(test.ops[0], ast.Eq) else name in value


def handler_signature(name, path):
    tree = ast.parse(path.read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'execute')
    helpers = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef))
               and getattr(node, 'name', None) != 'execute']
    before = []
    branch = None
    for node in function.body:
        if isinstance(node, ast.If):
            branch = node
            break
        before.append(node)
    while branch is not None:
        if _matches(branch.test, name):
            return ast.dump(ast.Module(body=helpers + before + branch.body, type_ignores=[]), include_attributes=False)
        branch = branch.orelse[0] if branch.orelse and isinstance(branch.orelse[0], ast.If) else None
    return name  # Temporary/custom tasks used by offline consumers.


def implementation_signature(name, *, package=PACKAGE, cache=None):
    digest = hashlib.sha256()
    cache = {} if cache is None else cache
    # Exchange identity is shared, but listing acquisition is unrelated to most tasks.
    securities = package/'sources/securities.py'
    if securities.exists():
        key = (securities, 'exchange_rules')
        if key not in cache:
            tree = ast.parse(securities.read_text())
            cache[key] = '\n'.join(ast.dump(node, include_attributes=False) for node in tree.body
                                  if isinstance(node, ast.FunctionDef) and node.name in ('exchange_for','is_a_share'))
        digest.update(cache[key].encode())
    for relative in sorted(set(COMMON + MODULES.get(name, ()))):
        path = package/relative
        if path not in cache:
            cache[path] = path.read_bytes() if path.exists() else b'missing'
        digest.update(relative.encode())
        digest.update(cache[path])
    handler = package/'pipeline/handlers.py'
    if handler.exists():
        key = (handler, name)
        if key not in cache:
            cache[key] = handler_signature(name, handler)
        digest.update(cache[key].encode())
    return digest.hexdigest()
