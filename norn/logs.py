"""Structured operational logs: one JSON object per line, no user content.

Log what happened and how long it took, never what someone chose or wrote:
no journals, readings, questions, session IDs, record tokens, IPs or headers.
"""

import json
import logging
import sys
import time

_RESERVED = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record):
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
                 + f".{int(record.msecs):03d}Z",
                 "level": record.levelname.lower(), "logger": record.name, "event": record.getMessage()}
        entry.update({key: value for key, value in vars(record).items() if key not in _RESERVED})
        if record.exc_info and record.exc_info[0] is not None:
            # Type only: tracebacks and messages can carry request data.
            entry["error_type"] = record.exc_info[0].__name__
        return json.dumps(entry, ensure_ascii=False, default=str, separators=(",", ":"))


class TextFormatter(logging.Formatter):
    def format(self, record):
        fields = " ".join(f"{key}={value}" for key, value in vars(record).items() if key not in _RESERVED)
        stamp = time.strftime("%H:%M:%S", time.localtime(record.created))
        return f"{stamp} {record.levelname.lower():5} {record.getMessage()}" + (f" {fields}" if fields else "")


def configure(level="info", fmt="json", stream=None):
    """Install one handler on the "norn" logger; repeated calls replace it."""
    logger = logging.getLogger("norn")
    for handler in list(logger.handlers):
        if getattr(handler, "_norn", False):
            logger.removeHandler(handler)
    handler = logging.StreamHandler(stream or sys.stderr)
    handler._norn = True
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    logger.addHandler(handler)
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    logger.propagate = False
    return logger


def event(logger, name, level=logging.INFO, **fields):
    """`event(log, "analysis.completed", rounds=12)` -> one structured line."""
    logger.log(level, name, extra=fields)
