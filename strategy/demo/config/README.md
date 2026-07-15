# config/

| 文件 | 用途 |
|------|------|
| [`params.py`](params.py) | **v1.0 参数**：`AlphaCore` + `LiveOverlays` + `Execution` → `Params` |
| [`versions.py`](versions.py) | 活版本预设（仅 v1.0） |
| [`../archive/config/versions_legacy.py`](../archive/config/versions_legacy.py) | 旧 flat15bp 消融预设（`safe_replace` 忽略已删字段） |

## 改哪里

| 层 | 何时改 |
|----|--------|
| `AlphaCore` | 宇宙 / 量能阈值 / 进出场主规则 |
| `LiveOverlays` | 已确认 ON 的 overlay（SOTA、Risk-On、主线扩散…） |
| `Execution` | 本金、费用、滑点 |

默认 OFF 的实验开关与对应逻辑已删除。仅保留产品门控 `enable_open_b` / `enable_open_c`（v1.0 默认只做 open_A）。

```python
from config.params import DEFAULT_PARAMS, params_summary, safe_replace
print(params_summary())
```
