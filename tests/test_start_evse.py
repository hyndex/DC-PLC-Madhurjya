import builtins
import importlib
import sys
import pytest


def test_missing_iso15118_raises(monkeypatch):
    """Importing start_evse without iso15118 raises a clear error."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("iso15118"):
            raise ModuleNotFoundError("No module named 'iso15118'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    for mod in list(sys.modules):
        if mod.startswith("iso15118"):
            sys.modules.pop(mod)

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("start_evse")
