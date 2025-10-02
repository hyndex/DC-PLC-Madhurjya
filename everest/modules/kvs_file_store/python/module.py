import json
import os
import threading
from typing import Any, Dict

try:
    from everest.framework import Module  # type: ignore
except Exception:
    class Module:  # type: ignore
        def __init__(self, *_args, **_kwargs): pass
        def implement_command(self, *_args, **_kwargs): pass
        def publish_variable(self, *_args, **_kwargs): pass


class FileKVS(Module):
    def __init__(self, config: Dict[str, Any]):
        super().__init__()
        self.path = str(config.get('path', '/var/lib/everest/kvs.json'))
        self._lock = threading.Lock()
        # Ensure directory
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        # Bootstrap file
        if not os.path.exists(self.path):
            with open(self.path, 'w') as f:
                json.dump({}, f)

        # Bind commands
        self.implement_command('main', 'store', self._cmd_store)
        self.implement_command('main', 'load', self._cmd_load)
        self.implement_command('main', 'delete', self._cmd_delete)
        self.implement_command('main', 'exists', self._cmd_exists)

    def _read(self) -> Dict[str, Any]:
        try:
            with self._lock:
                with open(self.path, 'r') as f:
                    return json.load(f)
        except Exception:
            return {}

    def _write(self, data: Dict[str, Any]) -> None:
        with self._lock:
            tmp = self.path + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(data, f)
            os.replace(tmp, self.path)

    # kvs commands
    def _cmd_store(self, args: Dict[str, Any]) -> None:
        key = str(args.get('key', ''))
        val = args.get('value', None)
        if not key:
            return
        data = self._read()
        data[key] = val
        self._write(data)

    def _cmd_load(self, args: Dict[str, Any]) -> Any:
        key = str(args.get('key', ''))
        if not key:
            return None
        data = self._read()
        return data.get(key)

    def _cmd_delete(self, args: Dict[str, Any]) -> None:
        key = str(args.get('key', ''))
        if not key:
            return
        data = self._read()
        if key in data:
            del data[key]
            self._write(data)

    def _cmd_exists(self, args: Dict[str, Any]) -> bool:
        key = str(args.get('key', ''))
        if not key:
            return False
        data = self._read()
        return key in data

