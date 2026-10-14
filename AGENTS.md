# Project instructions

## Layout and imports

- `src/` is the Python package `ashare_alpha`, mapped explicitly in `pyproject.toml`. Import `ashare_alpha.data`, `ashare_alpha.backtest`, etc.; do not add `sys.path` shims or recreate the old top-level framework packages.
- `strategy/` contains concrete strategies. General research infrastructure belongs in `src/research/`.
- Framework tests live in `tests/`; browser tools live in `tests/browser/`. A private strategy may keep its own tests beside its source.
- Read the relevant ADRs in `docs/adr/` before changing module boundaries or persistence contracts. Record superseding decisions explicitly.

## Data and research

- Use `ASHARE_WORKSPACE` / `ashare_alpha.paths` for runtime paths. Source checkouts default to `workspace/`; installed applications use the configured or user data directory.
- Acquire the shared publication lock before materializing canonical inputs. Preserve source quality flags, content fingerprints and failed runs.
- Decisions may only use information available at their timestamp. Unknown trading status, corporate actions, missing quotes or unavailable execution dates must not silently become valid observations.
- A completed backtest is not proof of alpha. Report costs, execution failures, stale valuations and research limitations. Do not tune against a purported untouched holdout after inspecting it.

## Publication boundary

- Public Git history and wheels contain framework code plus the single buy-and-hold demo.
- Private strategies, datasets, trained models, caches, run results and legacy research remain ignored in `strategy/` (outside the demo), `workspace/` or `.local_research/`.
- Do not publish private research through issues, pull requests or other external messages without explicit user authorization.
- Before publishing source, run the publication and distribution checks. Use explicit package declarations when adding a public subpackage.

## Verification

```bash
uv sync --frozen --extra dev
uv run ashare-alpha check --quiet
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
npm test --prefix tests/browser
uv build
python scripts/check_architecture.py
python scripts/check_docs.py
python scripts/check_publication.py
python scripts/check_distribution.py
```

Run the subset appropriate to the change first. Packaging changes also require installing the wheel outside the checkout and running the demo there. Supports Python 3.10–3.13 on macOS/Linux; do not mix legacy MootDX dependencies into the application environment.
