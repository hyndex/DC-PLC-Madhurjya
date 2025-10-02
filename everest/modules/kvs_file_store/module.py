#!/usr/bin/env python3
import json
import os
import sys
import time
import threading

_HERE = os.path.dirname(__file__)
_PY_DIR = os.path.join(_HERE, 'python')
if _PY_DIR not in sys.path:
    sys.path.insert(0, _PY_DIR)

from everest.framework import Module as EvModule, RuntimeSession  # type: ignore


class Runner:
    def __init__(self) -> None:
        self.session = RuntimeSession()
        self.m = EvModule(self.session)
        setup = self.m.say_hello()
        cfg = setup.configs.module
        self.path = str(cfg.get('path', '/var/lib/everest/kvs.json'))
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        if not os.path.exists(self.path):
            with open(self.path, 'w') as f:
                json.dump({}, f)
        self._lock = threading.Lock()
        # Bind commands
        self.m.implement_command('main', 'store', self._cmd_store)
        self.m.implement_command('main', 'load', self._cmd_load)
        self.m.implement_command('main', 'delete', self._cmd_delete)
        self.m.implement_command('main', 'exists', self._cmd_exists)

    def _read(self):
        try:
            with self._lock:
                with open(self.path, 'r') as f:
                    return json.load(f)
        except Exception:
            return {}

    def _write(self, data):
        with self._lock:
            tmp = self.path + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(data, f)
            os.replace(tmp, self.path)

    def _cmd_store(self, args):
        key = str(args.get('key', ''))
        val = args.get('value', None)
        if not key:
            return
        data = self._read()
        data[key] = val
        self._write(data)

    def _cmd_load(self, args):
        key = str(args.get('key', ''))
        if not key:
            return None
        data = self._read()
        return data.get(key)

    def _cmd_delete(self, args):
        key = str(args.get('key', ''))
        if not key:
            return
        data = self._read()
        if key in data:
            del data[key]
            self._write(data)

    def _cmd_exists(self, args):
        key = str(args.get('key', ''))
        if not key:
            return False
        data = self._read()
        return key in data


if __name__ == '__main__':
    r = Runner()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
