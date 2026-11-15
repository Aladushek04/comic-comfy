"""Веб-слой comic-comfy: отдельные зависимости, ничего не запускает само."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _path in (str(_ROOT), str(_ROOT / "tools")):
    if _path not in sys.path:
        sys.path.insert(0, _path)
