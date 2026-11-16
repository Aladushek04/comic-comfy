"""Веб-слой comic-comfy: отдельные зависимости, ничего не запускает само."""
import sys
from pathlib import Path

# Корень репозитория добавит tools\settings.py при первом импорте settings —
# это единственный такой insert. Здесь только tools: ui.py импортирует settings,
# а сам settings лежит в tools.
_TOOLS = Path(__file__).resolve().parent.parent / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))
