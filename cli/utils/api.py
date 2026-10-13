"""Public daily-plan API; class-based adapters supply their own engine orders."""

from pathlib import Path
import inspect
from .registry import get_predictor, list_registered
from .sink import write_run_bundle

from ashare_alpha.paths import CODE_ROOT, WORKSPACE_ROOT

REPO_ROOT = CODE_ROOT
OUTPUT_DIR = WORKSPACE_ROOT / "cli/output"


def list_strategies():
    return list_registered()


def predict_next(
    strategy, asof=None, *, rebuild=False, panel=None, write=True, capital=None
):
    fn = get_predictor(strategy)
    kwargs = {"asof": asof, "rebuild": rebuild, "panel": panel}
    if "capital" in inspect.signature(fn).parameters:
        kwargs["capital"] = capital
    result = fn(**kwargs)
    result.setdefault("strategy", strategy)
    result.setdefault("exec_hint", "T+1_open")
    if "exec_date" not in result:
        result["exec_date"] = None
    if result["exec_date"] is None:
        result["exec_hint"] = "next_session_unconfirmed"
        warning = "交易日历尚未确认下一交易日。"
        if warning not in result.setdefault("warnings", []):
            result["warnings"].append(warning)
    result.setdefault("format_version", 1)
    from ashare_alpha.contracts import validate_plan

    validate_plan(result)
    if result.get("orders") is None:
        result["orders"] = {
            "source": "strategy_class_model_account",
            "buy": result.get("buy", []),
            "sell": result.get("reduce", []),
            "satellite": [],
        }
    output_root = Path(result.pop("output_root", OUTPUT_DIR / strategy))
    if write:
        directory_date = result["exec_date"] or result["asof"]
        paths = write_run_bundle(
            output_root / str(directory_date).replace("-", ""), result
        )
        result["paths"] = {key: str(value) for key, value in paths.items()}
    else:
        result.setdefault("paths", {})
    return result
