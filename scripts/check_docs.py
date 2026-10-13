"""Check links in published Markdown; ignored local research is outside scope."""

from pathlib import Path
import re
import subprocess
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
errors = []
checked = 0
paths = subprocess.check_output(
    ["git", "ls-files", "*.md"], cwd=ROOT, text=True
).splitlines()
for name in paths:
    path = ROOT / name
    if not path.exists():
        continue
    for match in re.finditer(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
        target = match.group(1).strip("<>").split("#", 1)[0]
        if not target or re.match(r"^[a-zA-Z][\w+.-]*:", target):
            continue
        checked += 1
        if not (path.parent / unquote(target)).exists():
            errors.append(f"{path.relative_to(ROOT)}: missing {target}")
if errors:
    raise SystemExit("\n".join(errors))
print(f"Documentation links passed ({checked} targets)")
