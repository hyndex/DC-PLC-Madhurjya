"""Local shim to expose the bundled ISO 15118 stack for tests."""
from __future__ import annotations

import pkgutil
from pathlib import Path

_base_dir = Path(__file__).resolve().parent
_pkg_root = _base_dir.parent / "src" / "iso15118" / "iso15118"
_current_path = list(globals().get("__path__", []))

if _pkg_root.is_dir():
    search_path = [str(_pkg_root)] + [p for p in _current_path if p != str(_pkg_root)]
else:  # pragma: no cover - fallback to default path resolution
    search_path = _current_path or [str(_base_dir)]

__path__ = search_path
__all__ = [module.name for module in pkgutil.iter_modules(__path__)]
