from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict


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
    else:
        handler = logging.StreamHandler()

    if fmt_env == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    root.addHandler(handler)

