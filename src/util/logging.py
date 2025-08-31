from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict



class SafeRecordFilter(logging.Filter):
    """Filter that tolerates bad logger calls with stray args.

    If a library calls logger.error(f"msg", exc) without placeholders, the
    base formatter would raise TypeError. We preflight getMessage() and
    drop args if it would fail so logging continues gracefully.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.getMessage()
        except TypeError:
            # If msg has no %-placeholders but args were provided, drop args.
            # If an exception object was passed as the sole arg, preserve it as exc_info.
            try:
                if isinstance(record.args, tuple) and len(record.args) == 1 and isinstance(record.args[0], BaseException):
                    ex = record.args[0]
                    record.exc_info = (ex.__class__, ex, ex.__traceback__)
            except Exception:
                pass
            record.args = ()
        return True
class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "name": record.name,
            "msg": record.getMessage(),
        }
        # Attach arbitrary extra fields if present
        for key, val in record.__dict__.items():
            if key in ("args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
                       "levelname", "levelno", "lineno", "module", "msecs", "message", "msg",
                       "name", "pathname", "process", "processName", "relativeCreated", "stack_info",
                       "thread", "threadName"):
                continue
            if key.startswith("_"):
                continue
            payload[key] = val
        return json.dumps(payload, separators=(",", ":"))


def setup_logging(default_level: str | int = "INFO") -> None:
    """Configure root logging based on environment variables.

    EVSE_LOG_LEVEL: DEBUG|INFO|WARNING|ERROR|CRITICAL (default INFO)
    EVSE_LOG_FORMAT: text|json (default text)
    EVSE_LOG_FILE: path to log file (optional, else stdout)
    """
    level_env = os.environ.get("EVSE_LOG_LEVEL", str(default_level)).upper()
    level = getattr(logging, level_env, logging.INFO) if isinstance(level_env, str) else level_env
    fmt_env = os.environ.get("EVSE_LOG_FORMAT", "text").lower()
    file_env = os.environ.get("EVSE_LOG_FILE")

    root = logging.getLogger()
    # Clear existing handlers to avoid duplicate logs in reloads/tests
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(level)

    handler: logging.Handler
    if file_env:
        handler = logging.FileHandler(file_env)
        # Normalize misused logger calls that pass stray args
        handler.addFilter(SafeRecordFilter())
    else:
        handler = logging.StreamHandler()
        # Normalize misused logger calls that pass stray args
        handler.addFilter(SafeRecordFilter())

    if fmt_env == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    root.addHandler(handler)



